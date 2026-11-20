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

from mofsift import __version__, describe, generate, qc, score
from mofsift.config import Config, load_config, resolve_path
from mofsift.io.tables import TableError, read_table, sha256_file, stage_table_path, write_table
from mofsift.plan import plan_stages
from mofsift.stages import status_counts

IMPLEMENTED = ("generate", "qc", "describe", "score")
RUN_LOG = "run.log"
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
    warnings: list[str] = field(default_factory=list)
    written: bool = True  # False when a call computed a block only and left the table alone


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


def log_event(run: Run, message: str) -> None:
    """Append one line to ``<workdir>/run.log``: what ran, what was skipped and why."""
    run.workdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(run.workdir / RUN_LOG, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(f"{stamp} {message}\n")


def _counts_text(counts: dict[str, int]) -> str:
    return f"{counts['ok']} ok, {counts['skipped']} skipped, {counts['failed']} failed"


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
        details={"source": run.cfg.generate.source, "reused": result.reused, "linkers": result.linkers},
    )
    _write_meta(run, outcome, {**result.tools, "ase": _version("ase")})
    log_event(run, f"generate: {len(result.table)} rows, {_counts_text(outcome.counts)}")
    for note in result.notes:
        log_event(run, f"generate: note: {note}")
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
    log_event(
        run,
        f"qc: {len(checked)} rows, {_counts_text(outcome.counts)}; "
        f"{outcome.details['passed']} passed, {outcome.details['rejected']} rejected",
    )
    return outcome


def run_describe(
    run: Run,
    available: dict[str, bool],
    *,
    only: tuple[str, ...] = describe.SUBSTAGES,
    block: tuple[int, int] | None = None,
    force: bool = False,
    progress: Any = None,
    backends: Any = None,
) -> StageOutcome:
    """Add the descriptors to the qc table.

    ``available`` says which tools are installed (by tool name, as in ``mofsift doctor``).
    With ``block`` only that part of the table is computed and stored per structure; the
    table itself is written by a later call without ``block``.
    """
    source = stage_table_path(run.workdir, qc.STAGE)
    if not source.exists():
        raise RunnerError(
            f"{source} does not exist; run 'mofsift qc {run.config_path}' first "
            "(or 'mofsift run' for all stages)"
        )
    plans = {p.stage: p for p in plan_stages(run.cfg, available)}
    blocked = [p for name, p in plans.items() if name.startswith("describe.") and p.action == "error"]
    if blocked:
        raise RunnerError("; ".join(f"{p.stage} cannot run: {p.reason}" for p in blocked))
    try:
        table = read_table(source)
        result = describe.run(
            table,
            run.cfg,
            run.workdir,
            plans=plans,
            only=only,
            block=block,
            force=force,
            progress=progress,
            backends=backends,
        )
    except (TableError, ValueError) as err:
        raise RunnerError(f"{source}: {err}") from err

    path = stage_table_path(run.workdir, describe.STAGE)
    reports = {
        name: {
            "status_counts": rep.counts,
            "computed": rep.computed,
            "from_cache": rep.from_cache,
            "reasons": rep.reasons,
            "not_run_because": rep.not_run_because,
        }
        for name, rep in result.reports.items()
    }
    warnings = [
        f"describe.{name} did not run: {rep.not_run_because}"
        for name, rep in result.reports.items()
        if name != "composition"
        and rep.not_run_because
        and rep.not_run_because != "disabled in the config"
        and rep.counts["ok"] == 0
    ]
    outcome = StageOutcome(
        stage=describe.STAGE,
        table=result.table,
        path=path,
        counts=result.reports["composition"].counts,
        details={
            "substages": reports,
            "only": list(only),
            "block": f"{block[0]}/{block[1]}" if block else None,
            "input_sha256": sha256_file(source),
        },
        warnings=warnings,
        written=block is None,
    )
    scope = f" (block {block[0]}/{block[1]})" if block else ""
    for name, rep in result.reports.items():
        extra = f"; computed {rep.computed}, reused {rep.from_cache}" if name != "composition" else ""
        log_event(run, f"describe.{name}{scope}: {_counts_text(rep.counts)}{extra}")
        for reason, n in rep.reasons.items():
            log_event(run, f"describe.{name}{scope}:   {n} x {reason}")
    if block is None:
        write_table(result.table, path)
        tools = {d: _version(d) for d in ("ase", "pymatgen", "pyzeo", "tblite")}
        _write_meta(run, outcome, tools)
    return outcome


def run_score(run: Run) -> StageOutcome:
    """Add the synthetic-accessibility components to the describe table."""
    source = stage_table_path(run.workdir, describe.STAGE)
    if not source.exists():
        raise RunnerError(
            f"{source} does not exist; run 'mofsift describe {run.config_path}' first "
            "(or 'mofsift run' for all stages)"
        )
    try:
        result = score.run(read_table(source), run.cfg, run.workdir)
    except (TableError, ValueError) as err:
        raise RunnerError(f"{source}: {err}") from err
    path = write_table(result.table, stage_table_path(run.workdir, score.STAGE))
    outcome = StageOutcome(
        stage=score.STAGE,
        table=result.table,
        path=path,
        counts=status_counts(result.table, score.STAGE),
        details={
            "rows_with_a_value": result.available,
            "reference": result.reference,
            "aggregate": dict(run.cfg.score.aggregate.weights) if run.cfg.score.aggregate.enabled else None,
            "input_sha256": sha256_file(source),
        },
    )
    _write_meta(run, outcome, {"rdkit": _version("rdkit")})
    available = ", ".join(f"{name} {n}" for name, n in result.available.items())
    log_event(
        run,
        f"score: {len(result.table)} rows, {_counts_text(outcome.counts)}; rows with a value: {available}",
    )
    return outcome
