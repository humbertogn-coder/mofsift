"""Run stages from a config file: resolve folders, read and write the working tables.

Each stage reads the table of the stage before it and writes its own, so a run can be
stopped and resumed at any stage. Next to each table a small ``.meta.json`` records what
produced it (version, config hash, tool versions, counts). The export reads these records
to say in the manifest what produced the bundle, and to warn when a table is older than
the table or the config it depends on.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import pandas as pd

from mofsift import __version__, describe, export, filtering, generate, qc, score
from mofsift.config import Config, export_dir, load_config, resolve_path
from mofsift.filters import FilterError
from mofsift.io.tables import TableError, read_table, sha256_file, stage_table_path, write_table
from mofsift.plan import plan_stages
from mofsift.stages import status_counts

IMPLEMENTED = ("generate", "qc", "describe", "score", "filter", "export")
"""Stages that this version can run, in order."""
TABLE_STAGES = ("generate", "qc", "describe", "score", "filter")
"""The stages that write a working table, in order: each one reads the table before it."""
RUN_LOG = "run.log"
_STAGE_SECTIONS: dict[str, str] = {
    "generate": "'seed' or 'generate'",
    "qc": "'qc'",
    "describe": "'describe' (or tools.zeopp)",
    "score": "'score'",
    "filter": "'filter'",
}
"""The part of the config each stage reads, as it is named in a warning."""


class RunnerError(RuntimeError):
    """A stage cannot run (for example its input table does not exist yet)."""


class InvalidFilterError(RunnerError):
    """A filter of the config cannot be applied to the table (unknown column, wrong type)."""


def stage_config_hash(cfg: Config, stage: str) -> str:
    """SHA-256 of the part of the config that ``stage`` reads.

    Changing a filter does not make the descriptors out of date; this hash says which
    tables a change of the config does affect.
    """
    dump = cfg.model_dump(mode="json")
    if stage == "generate":
        part = {"seed": dump["seed"], "generate": dump["generate"]}
    elif stage == "describe":
        part = {"describe": dump["describe"], "tools.zeopp": dump["tools"]["zeopp"]}
    else:
        part = {stage: dump[stage]}
    text = json.dumps(part, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
        "stage_config_hash": stage_config_hash(run.cfg, outcome.stage),
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


def run_filter(run: Run) -> StageOutcome:
    """Mark the rows that satisfy the filters of the config. No row is removed."""
    source = stage_table_path(run.workdir, score.STAGE)
    if not source.exists():
        raise RunnerError(
            f"{source} does not exist; run 'mofsift score {run.config_path}' first "
            "(or 'mofsift run' for all stages)"
        )
    try:
        result = filtering.run(read_table(source), run.cfg, run.workdir)
    except FilterError as err:
        raise InvalidFilterError(str(err)) from err
    except (TableError, ValueError) as err:
        raise RunnerError(f"{source}: {err}") from err
    path = write_table(result.table, stage_table_path(run.workdir, filtering.STAGE))
    outcome = StageOutcome(
        stage=filtering.STAGE,
        table=result.table,
        path=path,
        counts=status_counts(result.table, filtering.STAGE),
        details={
            "filters": [r.as_dict() for r in result.reports],
            "selected": result.n_selected,
            "rejected": len(result.table) - result.n_selected,
            "input_sha256": sha256_file(source),
        },
    )
    _write_meta(run, outcome, {})
    if result.reports:
        log_event(
            run,
            f"filter: {len(result.table)} rows; {result.n_selected} satisfy every filter, "
            f"{len(result.table) - result.n_selected} do not",
        )
        for report in result.reports:
            log_event(
                run,
                f"filter:   {report.expression}: {report.passed} true, {report.rejected} false, "
                f"{report.undecided} undecided (missing value)",
            )
    else:
        log_event(run, f"filter: no filters in the config; all {len(result.table)} rows are kept")
    return outcome


@dataclass
class ExportOutcome:
    out_dir: Path
    manifest: dict[str, Any]
    n_enumerated: int
    n_selected: int
    n_structures: int
    warnings: list[str] = field(default_factory=list)


def read_stage_metas(run: Run) -> dict[str, dict[str, Any]]:
    """The ``.meta.json`` record of every table stage that has one."""
    metas: dict[str, dict[str, Any]] = {}
    for stage in TABLE_STAGES:
        path = stage_table_path(run.workdir, stage).with_suffix(".meta.json")
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(meta, dict):
            metas[stage] = meta
    return metas


def chain_warnings(run: Run, metas: dict[str, dict[str, Any]]) -> list[str]:
    """What is out of step in the work folder: a table older than its input or than the config."""
    warnings: list[str] = []
    previous: Path | None = None
    for stage in TABLE_STAGES:
        table = stage_table_path(run.workdir, stage)
        meta = metas.get(stage)
        if not table.exists():
            warnings.append(f"{table.name} is not in the work folder")
        elif meta is None:
            warnings.append(f"{table.name} has no .meta.json; what produced it is not recorded")
        else:
            if meta.get("table_sha256") != sha256_file(table):
                warnings.append(f"{table.name} was changed after 'mofsift {stage}' wrote it")
            recorded = meta.get("stage_config_hash")
            if recorded is not None and recorded != stage_config_hash(run.cfg, stage):
                warnings.append(
                    f"{table.name} was made before {_STAGE_SECTIONS[stage]} in the config was "
                    f"changed; run 'mofsift {stage}' and the stages after it again"
                )
            used = (meta.get("details") or {}).get("input_sha256")
            if previous is not None and previous.exists() and used and used != sha256_file(previous):
                warnings.append(
                    f"{table.name} was made from an older {previous.name}; run 'mofsift {stage}' "
                    "and the stages after it again"
                )
        previous = table
    return warnings


def _stays_inside(relative: str) -> bool:
    """True for a relative path without ``..``: a ``cif_path`` that cannot leave the work folder."""
    posix, windows = PurePosixPath(relative.replace("\\", "/")), PureWindowsPath(relative)
    return not (posix.is_absolute() or windows.is_absolute() or windows.drive or ".." in posix.parts)


def run_export(run: Run, out_dir: str | Path | None = None, *, force: bool = False) -> ExportOutcome:
    """Write the rows that passed the filters as a bundle (table, records, structures, manifest)."""
    source = stage_table_path(run.workdir, filtering.STAGE)
    if not source.exists():
        raise RunnerError(
            f"{source} does not exist; run 'mofsift filter {run.config_path}' first "
            "(or 'mofsift run' for all stages)"
        )
    try:
        table = read_table(source)
    except TableError as err:
        raise RunnerError(str(err)) from err
    cfg = run.cfg
    target = Path(out_dir) if out_dir is not None else export_dir(run.config_path, cfg)
    metas = read_stage_metas(run)
    warnings = chain_warnings(run, metas)
    chosen = table[filtering.selected(table)]

    structure_files: dict[str, Path] = {}
    if "cif_path" in chosen.columns:
        for record_id, relative in chosen["cif_path"].dropna().items():
            if not _stays_inside(str(relative)):
                raise RunnerError(f"{source}: cif_path of {record_id} is not a path inside the work folder")
            structure_files[str(record_id)] = run.workdir / str(relative)

    # the filters that were applied to the table, which a changed config no longer tells
    reports = (metas.get(filtering.STAGE, {}).get("details") or {}).get("filters")
    if not isinstance(reports, list):
        reports = []
    tools: dict[str, str | None] = {}
    for meta in metas.values():
        tools.update({k: v for k, v in (meta.get("tools") or {}).items() if v})
    reference = (metas.get(score.STAGE, {}).get("details") or {}).get("reference")
    third_party = []
    if reference and any(str(c).startswith("precedent_") for c in chosen.columns):
        third_party.append(
            {
                "used_for": "precedent_* columns (counts computed by mofsift, not the original data)",
                "source": reference.get("source"),
                "licence": reference.get("licence"),
                "cite": reference.get("cite"),
            }
        )
    try:
        result = export.write_bundle(
            chosen,
            target,
            project=cfg.project,
            structure_files=structure_files,
            table_format=cfg.export.table_format,
            viewer_bundle=cfg.export.viewer_bundle,
            include_cifs=cfg.export.include_cifs,
            enumerated=len(table),
            config=cfg.model_dump(mode="json"),
            config_hash=cfg.config_hash(),
            filters=reports,
            stages=[metas[s] for s in TABLE_STAGES if s in metas],
            tools=tools,
            third_party=third_party,
            warnings=warnings,
            force=force,
        )
    except export.BundleError as err:
        raise RunnerError(str(err)) from err
    log_event(
        run,
        f"export: {result.n_records} of {len(table)} rows, {result.n_structures} structure file(s) "
        f"-> {result.out_dir}",
    )
    for warning in result.warnings:
        log_event(run, f"export: warning: {warning}")
    return ExportOutcome(
        result.out_dir, result.manifest, len(table), result.n_records, result.n_structures, result.warnings
    )
