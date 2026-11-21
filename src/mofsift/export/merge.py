"""Merge bundles into one.

Typical uses: several configs (different building blocks, different linkers) shown as one
set, or the same config run on two machines where each has tools the other lacks.

Rows are matched by record ID. Columns are the union of the columns of the sources. For a
record that is in more than one bundle:

* a value that one source has and the other lacks is taken from the one that has it;
* the status of a stage is taken from the source where the stage ran (``ok`` before
  ``failed`` before ``skipped``), together with its reason;
* two different values for the same column are a conflict. By default the merge stops and
  lists them; ``on_conflict`` ``first`` or ``last`` picks a side and records how many
  values were decided that way;
* two different structure files are a conflict too, and then the values are not mixed:
  the whole record is taken from the first (or last) bundle, because descriptors computed
  on two different geometries do not describe one structure.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from mofsift.columns import JSON_TYPES, PANDAS_TYPES
from mofsift.export.bundle import (
    MANIFEST_FILE,
    Bundle,
    BundleError,
    BundleResult,
    read_bundle,
    write_bundle,
)
from mofsift.export.validate import validate_bundle
from mofsift.io.tables import sha256_file
from mofsift.stages import STATUS_FAILED, STATUS_OK, STATUS_SKIPPED

OnConflict = Literal["error", "first", "last"]
_RANK = {STATUS_OK: 3, STATUS_FAILED: 2, STATUS_SKIPPED: 1}
_SHOWN = 10


class MergeError(BundleError):
    """The bundles cannot be merged (one is invalid, types differ, or values conflict)."""


@dataclass(frozen=True)
class Conflict:
    record_id: str
    column: str
    first: Any
    second: Any
    second_bundle: str

    def __str__(self) -> str:
        return (
            f"{self.record_id}: {self.column} is {self.first!r}, but {self.second!r} in {self.second_bundle}"
        )


def _plain(value: Any) -> Any:
    """A NumPy scalar as the Python number it holds, so that messages read 0.1 and not np.float64(0.1)."""
    return value.item() if hasattr(value, "item") else value


def _status_pairs(columns: Sequence[str]) -> dict[str, str]:
    """Status column -> its reason column, for the pairs present in ``columns``."""
    names = set(columns)
    return {
        c: c[: -len("_status")] + "_skip_reason"
        for c in columns
        if c.endswith("_status") and c[: -len("_status")] + "_skip_reason" in names
    }


def _differs(a: pd.Series, b: pd.Series) -> pd.Series:
    """Rows where both values are present and not the same."""
    both = (a.notna() & b.notna()).to_numpy(dtype=bool)
    out = np.zeros(len(a), dtype=bool)
    if both.any():
        left, right = a[both], b[both]
        if pd.api.types.is_float_dtype(a.dtype):
            x, y = left.to_numpy(dtype=float), right.to_numpy(dtype=float)
            out[both] = ~np.isclose(x, y, rtol=1e-12, atol=0.0, equal_nan=True)
        else:
            out[both] = left.to_numpy(dtype=object) != right.to_numpy(dtype=object)
    return pd.Series(out, index=a.index)


def _combine(
    merged: pd.DataFrame, incoming: pd.DataFrame, label: str, on_conflict: OnConflict
) -> tuple[pd.DataFrame, list[Conflict]]:
    """Add ``incoming`` to ``merged`` (same columns in both). Returns the table and the conflicts."""
    if merged.empty:
        return incoming.copy(), []
    common = merged.index.intersection(incoming.index)
    new = incoming.loc[incoming.index.difference(merged.index, sort=False)]
    conflicts: list[Conflict] = []
    if len(common):
        pairs = _status_pairs(list(merged.columns))
        paired = set(pairs) | set(pairs.values())
        for status, reason in pairs.items():
            a, b = merged.loc[common, status], incoming.loc[common, status]
            better = (b.map(_RANK).fillna(0) > a.map(_RANK).fillna(0)).to_numpy(dtype=bool)
            ids = common[better]
            merged.loc[ids, status] = incoming.loc[ids, status]
            merged.loc[ids, reason] = incoming.loc[ids, reason]
        for column in merged.columns:
            if column in paired or column == "cif_path":
                continue
            a, b = merged.loc[common, column], incoming.loc[common, column]
            differs = _differs(a, b).to_numpy(dtype=bool)
            for record_id in common[differs]:
                conflicts.append(
                    Conflict(str(record_id), str(column), _plain(a[record_id]), _plain(b[record_id]), label)
                )
            take = a.isna().to_numpy(dtype=bool) & b.notna().to_numpy(dtype=bool)
            if on_conflict == "last":
                take |= differs
            ids = common[take]
            merged.loc[ids, column] = incoming.loc[ids, column]
    return pd.concat([merged, new], axis=0), conflicts


def _unified_columns(bundles: Sequence[Bundle]) -> dict[str, str]:
    """Union of the columns (first appearance decides the order) with one pandas dtype each."""
    types: dict[str, str] = {}
    for bundle in bundles:
        for entry in bundle.manifest["columns"][1:]:
            name, dtype = entry["name"], PANDAS_TYPES[entry["type"]]
            known = types.setdefault(name, dtype)
            if {known, dtype} == {"Int64", "Float64"}:
                types[name] = "Float64"  # whole numbers in one bundle, decimals in the other
            elif known != dtype:
                raise MergeError(
                    f"column {name} holds {JSON_TYPES[known]} values in one bundle and "
                    f"{JSON_TYPES[dtype]} values in {bundle.path}; they cannot be one column"
                )
    return types


def _source_entry(bundle: Bundle) -> dict[str, Any]:
    manifest = bundle.manifest
    return {
        "project": manifest["project"],
        "config_hash": manifest["config_hash"],
        "config": manifest["config"],
        "mofsift_version": manifest["mofsift_version"],
        "created_utc": manifest["created_utc"],
        "selected": manifest["counts"]["selected"],
        "enumerated": manifest["counts"]["enumerated"],
        "filters": manifest["filters"],
        "tools": manifest["tools"],
        "manifest_sha256": sha256_file(bundle.path / MANIFEST_FILE),
        "sources": manifest["sources"],
    }


def merge_bundles(
    sources: Sequence[str | Path],
    out_dir: str | Path,
    *,
    project: str = "merged",
    on_conflict: OnConflict = "error",
    table_format: str = "csv",
    viewer_bundle: bool = True,
    include_cifs: bool = True,
    force: bool = False,
) -> BundleResult:
    """Merge the bundles in ``sources`` (in this order) into a new bundle in ``out_dir``."""
    out_dir = Path(out_dir)
    paths = [Path(s) for s in sources]
    if len(paths) < 2:
        raise MergeError("give at least two bundles to merge")
    if len({p.resolve() for p in paths}) != len(paths):
        raise MergeError("the same bundle is listed twice")
    if out_dir.resolve() in {p.resolve() for p in paths}:
        raise MergeError("the output folder is one of the bundles to merge; choose another folder")

    bundles: list[Bundle] = []
    for path in paths:
        check = validate_bundle(path)
        if not check.ok:
            listed = "\n".join(f"  - {p}" for p in check.problems[:_SHOWN])
            raise MergeError(f"{path} is not a valid bundle; nothing was merged:\n{listed}")
        bundles.append(read_bundle(path))

    types = _unified_columns(bundles)
    merged = pd.DataFrame(
        {name: pd.Series(dtype=dtype) for name, dtype in types.items()},
        index=pd.Index([], name="id", dtype="string"),
    )
    structure_files: dict[str, Path] = {}
    structure_sha: dict[str, str] = {}
    conflicts: list[Conflict] = []
    for bundle in bundles:
        label = str(bundle.path)
        table = bundle.table.reindex(columns=list(types)).astype(types)
        own: dict[str, tuple[Path, str]] = {}
        if "cif_path" in bundle.table.columns:
            listed = bundle.manifest["files"]
            for record_id, relative in bundle.table["cif_path"].dropna().items():
                own[str(record_id)] = (bundle.path / str(relative), listed[str(relative)]["sha256"])
        # The same ID with another structure file is another geometry. Its values are not
        # mixed with the ones already there: one bundle gives the whole record.
        different = [i for i, (_, sha) in own.items() if structure_sha.get(i, sha) != sha]
        for record_id in different:
            conflicts.append(
                Conflict(
                    record_id, "structure file", structure_sha[record_id][:12], own[record_id][1][:12], label
                )
            )
        if different and on_conflict == "last":
            merged.loc[different, :] = table.loc[different, :]
        merged, found = _combine(merged, table.drop(index=different), label, on_conflict)
        conflicts += found
        for record_id, (path, sha) in own.items():
            if record_id not in structure_sha or (record_id in different and on_conflict == "last"):
                structure_files[record_id], structure_sha[record_id] = path, sha
    merged = merged.astype(types)

    warnings: list[str] = []
    if conflicts:
        shown = "\n".join(f"  - {c}" for c in conflicts[:_SHOWN])
        more = f"\n  ... and {len(conflicts) - _SHOWN} more" if len(conflicts) > _SHOWN else ""
        if on_conflict == "error":
            raise MergeError(
                f"{len(conflicts)} value(s) differ between the bundles for the same record; nothing "
                f"was written:\n{shown}{more}\n"
                "Use --on-conflict first (or last) to keep the value of the first (or last) bundle. "
                "A record whose structure file differs is then taken whole from that bundle."
            )
        warnings.append(
            f"{len(conflicts)} value(s) differed between the sources for the same record; the value "
            f"of the {on_conflict} bundle was kept"
        )

    tools: dict[str, str | None] = {}
    for bundle in bundles:
        for name, version in bundle.manifest["tools"].items():
            known = tools.get(name)
            versions = {v for v in (known or "").split(" / ") if v} | ({version} if version else set())
            tools[name] = " / ".join(sorted(versions)) or None
    tools.pop("python", None)
    third_party: list[dict[str, Any]] = []
    for bundle in bundles:
        third_party += [t for t in bundle.manifest["third_party"] if t not in third_party]
    for bundle in bundles:
        warnings += [f"{bundle.manifest['project']}: {w}" for w in bundle.manifest["warnings"]]

    return write_bundle(
        merged,
        out_dir,
        project=project,
        structure_files=structure_files,
        table_format=table_format,
        viewer_bundle=viewer_bundle,
        include_cifs=include_cifs,
        enumerated=None,
        tools=tools,
        third_party=third_party,
        sources=[_source_entry(b) for b in bundles],
        warnings=warnings,
        force=force,
    )
