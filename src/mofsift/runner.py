"""Run stages from a config file: resolve folders, read and write the working tables.

Each stage reads the table of the stage before it and writes its own, so a run can be
stopped and resumed at any stage. Next to each table a small ``.meta.json`` records what
produced it (version, config hash, tool versions, counts).
"""

from __future__ import annotations

import importlib.metadata
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from mofsift import __version__, generate, qc
from mofsift.config import Config, load_config, resolve_path
from mofsift.io.tables import TableError, read_table, sha256_file, stage_table_path, write_table
from mofsift.stages import status_counts

IMPLEMENTED = ("generate", "qc")
"""Stages that this version can run, in order."""


class RunnerError(RuntimeError):
    """A stage cannot run (for example its input table does not exist yet)."""


@dataclass
class Run:
    """A config file together with the folders it refers to."""

    config_path: Path
    cfg: Config
    config_dir: Path
    workdir: Path

    @classmethod
    def load(cls, config_path: str | Path) -> Run:
        config_path = Path(config_path)
        cfg = load_config(config_path)
        return cls(
            config_path=config_path,
            cfg=cfg,
            config_dir=config_path.resolve().parent,
            workdir=resolve_path(config_path, cfg.workdir),
        )


@dataclass
class StageOutcome:
    stage: str
    table: pd.DataFrame
    path: Path
    counts: dict[str, int]
    notes: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


def _version(dist: str) -> str | None:
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return None


def _write_meta(run: Run, outcome: StageOutcome, tools: dict[str, str | None]) -> Path:
    path = outcome.path.with_suffix(".meta.json")
    payload = {
        "stage": outcome.stage,
        "mofsift_version": __version__,
        "project": run.cfg.project,
        "config_hash": run.cfg.config_hash(),
        "table": outcome.path.name,
        "table_sha256": sha256_file(outcome.path),
        "n_rows": len(outcome.table),
        "status_counts": outcome.counts,
        "details": outcome.details,
        "notes": outcome.notes,
        "tools": tools,
        "finished_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def run_generate(
    run: Run, *, force: bool = False, progress: generate.stage.Progress | None = None, adapter: Any = None
) -> StageOutcome:
    """Create the candidate table and the structure files."""
    result = generate.run(
        run.cfg, run.workdir, run.config_dir, force=force, adapter=adapter, progress=progress
    )
    path = write_table(result.table, stage_table_path(run.workdir, generate.STAGE))
    outcome = StageOutcome(
        stage=generate.STAGE,
        table=result.table,
        path=path,
        counts=status_counts(result.table, generate.STAGE),
        notes=result.notes,
        details={"source": run.cfg.generate.source, "reused": result.reused},
    )
    _write_meta(run, outcome, {**result.tools, "ase": _version("ase")})
    return outcome


def run_qc(run: Run) -> StageOutcome:
    """Check the structures listed in the generate table."""
    source = stage_table_path(run.workdir, generate.STAGE)
    if not source.exists():
        raise RunnerError(
            f"{source} does not exist; run 'mofsift generate {run.config_path}' first "
            "(or 'mofsift run' for all stages)"
        )
    try:
        table = read_table(source)
    except TableError as err:
        raise RunnerError(str(err)) from err
    try:
        checked = qc.run(table, run.cfg, run.workdir)
    except ValueError as err:  # the table cannot be used as input (for example no cif_path column)
        raise RunnerError(f"{source}: {err}") from err
    path = write_table(checked, stage_table_path(run.workdir, qc.STAGE))
    passed = checked["qc_pass"]  # nullable boolean: missing where the checks did not run
    outcome = StageOutcome(
        stage=qc.STAGE,
        table=checked,
        path=path,
        counts=status_counts(checked, qc.STAGE),
        details={
            "passed": int(passed.sum()),
            "rejected": int((~passed).sum()),
            "input_sha256": sha256_file(source),
        },
    )
    _write_meta(run, outcome, {"ase": _version("ase")})
    return outcome
