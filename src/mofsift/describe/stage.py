"""The describe stage: composition, pore geometry and xTB descriptors.

Three sub-stages, each with its own status columns:

* ``describe.composition``  from the formula; no optional tool;
* ``describe.geometry``     Zeo++ (pyzeo or the ``network`` binary);
* ``describe.xtb``          periodic GFN-xTB (tblite).

Geometry and xTB are slow, so every result is stored per structure (see ``cache``) and
reused. That is also how a cluster is used: jobs compute blocks of the table
(``block=(i, n)``) and write only those files; a later call assembles the table from them,
and that call does not need the tools to be installed.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

import pandas as pd

from mofsift.chem import count_symbols, parse_formula
from mofsift.config import Config
from mofsift.describe import cache, composition, geometry, xtb
from mofsift.describe.isolate import WorkerError
from mofsift.io.structures import StructureError, read_structure
from mofsift.io.tables import register_columns, sha256_file
from mofsift.plan import StagePlan
from mofsift.stages import (
    STATUS_FAILED,
    STATUS_OK,
    STATUS_SKIPPED,
    check_contract,
    status_columns,
)

STAGE = "describe"
SUBSTAGES = ("composition", "geometry", "xtb")
VALUE_COLUMNS = {
    "composition": composition.COLUMNS,
    "geometry": geometry.COLUMNS,
    "xtb": xtb.COLUMNS,
}
for _name, _columns in VALUE_COLUMNS.items():
    _s, _r = status_columns(f"describe.{_name}")
    register_columns({**_columns, _s: "string", _r: "string"})

Backend = Callable[[Path, dict[str, Any], float | None], dict[str, Any]]
Progress = Callable[[str, int, int, str, str, str | None], None]
"""Called per structure with (sub-stage, position, total, name, status, detail)."""


@dataclass
class SubstageReport:
    name: str
    counts: dict[str, int]
    computed: int = 0
    from_cache: int = 0
    reasons: dict[str, int] = field(default_factory=dict)
    not_run_because: str | None = None  # set when the tool is missing or the sub-stage is disabled


@dataclass
class DescribeResult:
    table: pd.DataFrame
    reports: dict[str, SubstageReport]


def parse_only(value: str | None) -> tuple[str, ...]:
    """``"geometry,xtb"`` -> ("geometry", "xtb"). None or empty means every sub-stage."""
    if value is None or not value.strip():
        return SUBSTAGES
    names = tuple(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
    unknown = [n for n in names if n not in SUBSTAGES]
    if unknown:
        raise ValueError(f"unknown sub-stage(s): {', '.join(unknown)}; choose from {', '.join(SUBSTAGES)}")
    return names


def parse_block(value: str | None) -> tuple[int, int] | None:
    """``"3/20"`` -> (3, 20): the third of twenty blocks."""
    if value is None:
        return None
    try:
        index, total = (int(part) for part in value.split("/"))
    except ValueError:
        raise ValueError(f"block must look like 3/20, got {value!r}") from None
    if total < 1 or not 1 <= index <= total:
        raise ValueError(f"block {value}: the first number must be between 1 and the second")
    return index, total


def default_backends(cfg: Config) -> dict[str, Backend]:
    geo = cfg.describe.geometry
    if geo.tool == "zeopp":
        executable = str(cfg.tools.zeopp) if cfg.tools.zeopp is not None else "network"
        geometry_backend: Backend = partial(geometry.compute_zeopp, executable=executable)
    else:
        geometry_backend = geometry.compute_pyzeo
    return {"geometry": geometry.with_fallback(geometry_backend), "xtb": xtb.compute_tblite}


def _value(row: pd.Series, column: str) -> Any:
    value = row.get(column, pd.NA)
    return None if pd.isna(value) else value


def _has_structure(row: pd.Series) -> bool:
    return _value(row, "cif_path") is not None and _value(row, "generate_status") in (None, STATUS_OK)


def _attach(table: pd.DataFrame, rows: list[dict[str, Any]], name: str) -> pd.DataFrame:
    s_col, r_col = status_columns(f"describe.{name}")
    types = {**VALUE_COLUMNS[name], s_col: "string", r_col: "string"}
    clash = [c for c in types if c in table.columns]
    if clash:
        raise ValueError(f"the input table already has columns written by describe: {', '.join(clash)}")
    added = pd.DataFrame(rows, columns=list(types), index=table.index).astype(types)
    out = pd.concat([table, added], axis=1)
    check_contract(table, out, f"describe.{name}")
    return out


def _report(name: str, rows: list[dict[str, Any]]) -> SubstageReport:
    s_col, r_col = status_columns(f"describe.{name}")
    counts = {s: sum(r[s_col] == s for r in rows) for s in (STATUS_OK, STATUS_SKIPPED, STATUS_FAILED)}
    reasons: dict[str, int] = {}
    for r in rows:
        if r[s_col] != STATUS_OK:
            reasons[r[r_col]] = reasons.get(r[r_col], 0) + 1
    return SubstageReport(name, counts, reasons=dict(sorted(reasons.items())))


# ------------------------------------------------------------- composition
def _composition(
    table: pd.DataFrame, workdir: Path, enabled: bool
) -> tuple[list[dict[str, Any]], SubstageReport]:
    s_col, r_col = status_columns("describe.composition")
    rows = []
    for _, row in table.iterrows():
        out: dict[str, Any] = dict.fromkeys([*composition.COLUMNS, s_col, r_col], pd.NA)
        formula = _value(row, "formula")
        try:
            if not enabled:
                raise LookupError("disabled in the config")
            if formula is not None:
                counts = parse_formula(formula)
            elif _has_structure(row):
                counts = count_symbols(read_structure(workdir / row["cif_path"]).get_chemical_symbols())
            else:
                raise LookupError("no structure")
            values = composition.composition_features(counts)
            out.update({k: (pd.NA if v is None else v) for k, v in values.items()})
            out[s_col] = STATUS_OK
        except LookupError as err:
            out[s_col], out[r_col] = STATUS_SKIPPED, str(err)
        except (ValueError, StructureError) as err:
            out[s_col], out[r_col] = STATUS_FAILED, f"cannot read the composition: {err}"
        rows.append(out)
    report = _report("composition", rows)
    if not enabled:
        report.not_run_because = "disabled in the config"
    return rows, report


# ---------------------------------------------------------- geometry / xtb
def _slow_substage(
    name: str,
    table: pd.DataFrame,
    cfg: Config,
    workdir: Path,
    *,
    options: dict[str, Any],
    timeout: float | None,
    plan: StagePlan,
    selected: bool,
    block: tuple[int, int] | None,
    force: bool,
    backend: Backend,
    max_atoms: int | None,
    progress: Progress | None,
) -> tuple[list[dict[str, Any]], SubstageReport]:
    s_col, r_col = status_columns(f"describe.{name}")
    columns = VALUE_COLUMNS[name]
    disabled = plan.action == "skip" and plan.reason == "disabled in the config"
    has_qc = "qc_pass" in table.columns
    rows: list[dict[str, Any]] = []
    computed = cached = 0
    total = len(table)
    for position, (record_id, row) in enumerate(table.iterrows()):
        out: dict[str, Any] = dict.fromkeys([*columns, s_col, r_col], pd.NA)
        label = _value(row, "name") or str(record_id)

        def skip(reason: str, out: dict[str, Any] = out) -> None:
            out[s_col], out[r_col] = STATUS_SKIPPED, reason

        n_atoms = _value(row, "n_atoms")
        if disabled:
            skip("disabled in the config")
        elif not _has_structure(row):
            skip("no structure")
        elif cfg.describe.only_qc_passed and has_qc and _value(row, "qc_pass") is not True:
            skip("did not pass qc" if _value(row, "qc_pass") is False else "qc did not run")
        elif max_atoms is not None and n_atoms is not None and n_atoms > max_atoms:
            skip(f"{n_atoms} atoms; describe.{name}.max_atoms is {max_atoms}")
        else:
            cif = workdir / row["cif_path"]
            path = cache.cache_path(workdir, name, str(record_id))
            try:
                key = cache.cache_key(name, options, sha256_file(cif))
            except OSError:
                out[s_col], out[r_col] = STATUS_FAILED, "structure file not found"
                rows.append(out)
                continue
            in_block = block is None or position % block[1] == block[0] - 1
            wanted = selected and in_block and plan.action == "run"
            stored = None if (force and wanted) else cache.load(path, key)
            source = "from an earlier run"
            if stored is None and wanted:
                started = time.monotonic()
                extra: dict[str, Any] = {"settings": options}
                try:
                    values = backend(cif, options, timeout)
                    missing = [c for c in columns if values.get(c) is None]
                    if missing:
                        raise WorkerError(f"no result for {', '.join(missing)}", values)
                    status, kept = STATUS_OK, {c: values[c] for c in columns}
                except WorkerError as err:
                    status = STATUS_FAILED
                    kept = {c: err.partial[c] for c in columns if err.partial.get(c) is not None}
                    extra["reason"] = err.reason
                extra["seconds"] = round(time.monotonic() - started, 1)
                stored = cache.store(path, key, status, kept, **extra)
                computed += 1
                source = None
            elif stored is not None:
                cached += 1
            if stored is not None:
                out.update(stored["values"])
                out[s_col] = stored["status"]
                if stored["status"] != STATUS_OK:
                    out[r_col] = stored.get("reason") or "failed"
                if progress is not None:
                    took = f"{stored['seconds']:g} s" if source is None and "seconds" in stored else source
                    detail = took if stored["status"] == STATUS_OK else out[r_col]
                    progress(name, position + 1, total, label, stored["status"], detail)
            elif plan.action != "run":
                skip(plan.reason or "not available")
            elif not selected:
                skip("not requested in this call")
            else:
                skip("outside the block of this call")
        rows.append(out)
    report = _report(name, rows)
    report.computed, report.from_cache = computed, cached
    if plan.action != "run":
        report.not_run_because = plan.reason
    return rows, report


def run(
    table: pd.DataFrame,
    cfg: Config,
    workdir: str | Path,
    *,
    plans: Mapping[str, StagePlan],
    only: tuple[str, ...] = SUBSTAGES,
    block: tuple[int, int] | None = None,
    force: bool = False,
    backends: Mapping[str, Backend] | None = None,
    progress: Progress | None = None,
) -> DescribeResult:
    """Add the descriptor columns.

    ``plans`` says, per sub-stage (``describe.geometry`` ...), whether its tool is available.
    ``only`` limits what is computed in this call; results already stored are used either
    way. ``block=(i, n)`` computes every n-th structure starting at the i-th. ``force``
    recomputes what this call is asked to compute.
    """
    workdir = Path(workdir)
    if "cif_path" not in table.columns and "formula" not in table.columns:
        raise ValueError("the describe stage needs a 'cif_path' or a 'formula' column")
    backends = {**default_backends(cfg), **(backends or {})}
    desc = cfg.describe
    reports: dict[str, SubstageReport] = {}

    rows, reports["composition"] = _composition(table, workdir, desc.composition)
    out = _attach(table, rows, "composition")

    geo, xt = desc.geometry, desc.xtb
    slow = (
        (
            "geometry",
            geometry.settings(geo.probe_radius, geo.high_accuracy, geo.fallback),
            geo.timeout_minutes,
            None,
        ),
        (
            "xtb",
            xtb.settings(xt.method, xt.periodic, xt.spin, xt.electronic_temperature, xt.max_iterations),
            xt.timeout_minutes,
            xt.max_atoms,
        ),
    )
    for name, options, minutes, max_atoms in slow:
        rows, reports[name] = _slow_substage(
            name,
            out,
            cfg,
            workdir,
            options=options,
            timeout=minutes * 60 if minutes else None,
            plan=plans[f"describe.{name}"],
            selected=name in only,
            block=block,
            force=force,
            backend=backends[name],
            max_atoms=max_atoms,
            progress=progress,
        )
        out = _attach(out, rows, name)
    return DescribeResult(out, reports)
