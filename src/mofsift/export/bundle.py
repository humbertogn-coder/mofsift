"""Write and read a bundle: the table, the records for a viewer, the structures and a manifest.

A bundle is one folder::

    manifest.json          what is in the bundle and what produced it
    candidates.csv         the selected rows, every column (or candidates.parquet)
    candidates.json        the same rows as JSON records, for a viewer
    structures/<id>.cif    one structure file per record that has one

``manifest.json`` lists every other file with its size and SHA-256, carries the column
dictionary, the config, the filters and what each stage reported. Everything except the
time stamp in the manifest is a function of the input table, so two exports of the same
work folder differ in that one line only.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from mofsift import __version__
from mofsift.columns import PANDAS_TYPES, describe_columns, json_type
from mofsift.io.tables import ID_COLUMN, column_types, normalise, sha256_file, write_table

BUNDLE_KIND = "mofsift-bundle"
BUNDLE_SCHEMA_VERSION = "1.0.0"
MANIFEST_FILE = "manifest.json"
RECORDS_FILE = "candidates.json"
TABLE_STEM = "candidates"
STRUCTURES_DIR = "structures"
TABLE_FORMATS = ("csv", "parquet")
ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"
"""Record IDs become file names, so only these characters are accepted in a bundle."""

_ID_RE = re.compile(ID_PATTERN)
_JUNK = {"desktop.ini", "Thumbs.db", ".DS_Store"}
_CHUNK = 5000


class BundleError(RuntimeError):
    """A bundle cannot be written, or a folder cannot be read as a bundle."""


@dataclass
class Bundle:
    """A bundle read from disk."""

    path: Path
    manifest: dict[str, Any]
    table: pd.DataFrame


@dataclass
class BundleResult:
    out_dir: Path
    manifest: dict[str, Any]
    n_records: int
    n_structures: int
    warnings: list[str] = field(default_factory=list)


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


_PART_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def is_safe_relative(path: str) -> bool:
    """True for the name of a file inside a bundle: ``a/b.cif``, with plain characters only.

    No absolute path, no ``..``, no backslash and no colon, so that a name read from a
    manifest can never point outside the folder on any operating system.
    """
    parts = path.split("/") if isinstance(path, str) else [""]
    return all(_PART_RE.match(part) and part not in (".", "..") for part in parts)


# ------------------------------------------------------------------ JSON files
def read_json_strict(path: str | Path) -> Any:
    """Read JSON, refusing the non-standard constants NaN and Infinity."""

    def constant(name: str) -> Any:
        raise ValueError(f"non-standard JSON constant {name}")

    try:
        return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=constant)
    except (OSError, ValueError) as err:
        raise BundleError(f"{path}: cannot read ({err})") from err


def write_json(path: Path, payload: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, allow_nan=False)
        fh.write("\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------------- tables
def table_file_name(table_format: str) -> str:
    if table_format not in TABLE_FORMATS:
        raise BundleError(f"unknown table format {table_format!r}")
    return f"{TABLE_STEM}.{table_format}"


def parquet_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("pyarrow") is not None


def _write_table_file(table: pd.DataFrame, path: Path, table_format: str) -> None:
    if table_format == "csv":
        write_table(table, path)
        return
    if not parquet_available():
        raise BundleError("table_format: parquet needs pyarrow (pip install 'mofsift[parquet]')")
    tmp = path.with_name(path.name + ".tmp")
    normalise(table).to_parquet(tmp, engine="pyarrow", index=True)
    os.replace(tmp, path)


def read_table_file(path: Path, table_format: str, columns: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    """Read the table of a bundle with the column types its manifest declares."""
    types = {str(c["name"]): PANDAS_TYPES[c["type"]] for c in columns if c.get("type") in PANDAS_TYPES}
    try:
        if table_format == "parquet":
            if not parquet_available():
                raise BundleError(f"{path}: reading a parquet table needs pyarrow")
            table = pd.read_parquet(path, engine="pyarrow")
            if table.index.name != ID_COLUMN and ID_COLUMN in table.columns:
                table = table.set_index(ID_COLUMN)
        else:
            header = list(pd.read_csv(path, nrows=0, encoding="utf-8").columns)
            if ID_COLUMN not in header:
                raise BundleError(f"{path}: no '{ID_COLUMN}' column")
            dtypes = {c: types[c] for c in header if c in types}
            dtypes[ID_COLUMN] = "string"
            table = pd.read_csv(
                path, dtype=dtypes, keep_default_na=False, na_values=[""], encoding="utf-8"
            ).set_index(ID_COLUMN)
    except (
        OSError,
        ValueError,
        TypeError,
        OverflowError,
        pd.errors.ParserError,
        pd.errors.EmptyDataError,
    ) as err:
        raise BundleError(f"{path}: cannot read the table ({err})") from err
    for name, dtype in types.items():
        if name in table.columns and str(table[name].dtype) != dtype:
            try:
                table[name] = table[name].astype(dtype)
            except (TypeError, ValueError, OverflowError) as err:
                raise BundleError(f"{path}: column {name} is not of type {dtype} ({err})") from err
    table.index = table.index.astype("string")
    table.index.name = ID_COLUMN
    return table


# --------------------------------------------------------------------- records
def _json_values(series: pd.Series) -> tuple[list[Any], bool]:
    """Python values of one column (None where missing) and whether a non-finite number was dropped."""
    kind = json_type(series.dtype)
    missing = series.isna().to_numpy(dtype=bool)
    if kind == "number":
        numbers = series.astype("Float64").to_numpy(dtype=float, na_value=np.nan)
        finite = np.isfinite(numbers)
        dropped = bool((~finite & ~missing).any())
        return [float(v) if ok else None for v, ok in zip(numbers, finite, strict=True)], dropped
    cast = {"integer": int, "boolean": bool}.get(kind, str)
    return [
        None if gone else cast(v) for v, gone in zip(series.to_numpy(dtype=object), missing, strict=True)
    ], False


def iter_records(table: pd.DataFrame, chunk: int = _CHUNK) -> Iterator[tuple[dict[str, Any], set[str]]]:
    """Yield one JSON record per row, with the columns whose non-finite numbers became null."""
    names = [str(c) for c in table.columns]
    for start in range(0, len(table), chunk):
        part = table.iloc[start : start + chunk]
        columns, dropped = [], set()
        for name in names:
            values, lost = _json_values(part[name])
            columns.append(values)
            if lost:
                dropped.add(name)
        for i, record_id in enumerate(part.index):
            yield (
                {ID_COLUMN: str(record_id), **{n: col[i] for n, col in zip(names, columns, strict=True)}},
                dropped,
            )


def _write_records(path: Path, project: str, table: pd.DataFrame) -> set[str]:
    """Write the records, one per line, and return the columns that lost a non-finite number."""
    head = {"schema_version": BUNDLE_SCHEMA_VERSION, "project": project, "n_records": len(table)}
    lost: set[str] = set()
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(head, ensure_ascii=False)[:-1] + ', "records": [')
        for i, (record, dropped) in enumerate(iter_records(table)):
            lost |= dropped
            fh.write(("\n" if i == 0 else ",\n") + json.dumps(record, ensure_ascii=False, allow_nan=False))
        fh.write("\n]}\n")
    os.replace(tmp, path)
    return lost


def read_records(path: Path) -> dict[str, Any]:
    payload = read_json_strict(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise BundleError(f"{path}: not a records file (no 'records' list)")
    return payload


# ------------------------------------------------------------------ the folder
def _listing(out_dir: Path) -> list[Path]:
    return [p for p in out_dir.iterdir() if p.name not in _JUNK]


def existing_manifest(out_dir: Path) -> dict[str, Any] | None:
    """The manifest of the bundle in ``out_dir``, or None when the folder holds no bundle."""
    path = out_dir / MANIFEST_FILE
    if not path.is_file():
        return None
    try:
        manifest = read_json_strict(path)
    except BundleError:
        return None
    return manifest if isinstance(manifest, dict) and manifest.get("kind") == BUNDLE_KIND else None


def prepare_out_dir(out_dir: Path, project: str, *, force: bool = False) -> None:
    """Make ``out_dir`` ready for a new bundle.

    An older bundle of the same project is replaced (only the files its manifest lists are
    removed). A bundle of another project is replaced only with ``force``. A folder that
    holds anything else is never touched.
    """
    if out_dir.exists() and not out_dir.is_dir():
        raise BundleError(f"{out_dir} exists and is not a folder")
    out_dir.mkdir(parents=True, exist_ok=True)
    if not _listing(out_dir):
        return
    old = existing_manifest(out_dir)
    if old is None:
        raise BundleError(
            f"{out_dir} is not empty and does not hold a mofsift bundle; choose another folder "
            "(export.out_dir, or --out)"
        )
    if old.get("project") != project and not force:
        raise BundleError(
            f"{out_dir} holds the bundle of project {old.get('project')!r}; choose another folder, "
            "or use --force to replace it"
        )
    files = old.get("files")
    listed = [n for n in (files if isinstance(files, dict) else {}) if is_safe_relative(n)]
    structures = out_dir / STRUCTURES_DIR
    if old.get("incomplete"):
        # an export that did not finish: its manifest lists nothing, so remove what it can have written
        listed += [RECORDS_FILE, *(table_file_name(f) for f in TABLE_FORMATS)]
        if structures.is_dir():
            listed += [
                f"{STRUCTURES_DIR}/{p.name}" for p in structures.glob("*.cif") if is_safe_relative(p.name)
            ]
    for name in [*listed, MANIFEST_FILE]:
        target = out_dir / name
        if target.is_file():
            target.unlink()
    if structures.is_dir() and not any(structures.iterdir()):
        structures.rmdir()


def _copy_structure(source: Path, target: Path) -> dict[str, Any]:
    digest, size = hashlib.sha256(), 0
    tmp = target.with_name(target.name + ".tmp")
    with open(source, "rb") as src, open(tmp, "wb") as dst:
        for chunk in iter(lambda: src.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
            dst.write(chunk)
    os.replace(tmp, target)
    return {"bytes": size, "sha256": digest.hexdigest()}


def _file_entry(path: Path) -> dict[str, Any]:
    return {"bytes": path.stat().st_size, "sha256": sha256_file(path)}


# --------------------------------------------------------------------- notices
def notices(columns: Sequence[str]) -> list[str]:
    """Plain statements a reader of the bundle must not miss, chosen by what the table holds."""
    names = set(columns)
    out = [
        "Every structure is hypothetical: assembled from building blocks or supplied by the user. "
        "None is claimed to be stable or synthesisable.",
    ]
    if "qc_pass" in names:
        out.append(
            "qc_pass means the geometry passed geometric and compositional checks. It is not a "
            "statement about stability."
        )
    if "xtb_gap" in names:
        out.append(
            "xtb_gap is a semi-empirical proxy computed on an unrelaxed geometry, not a DFT or "
            "measured band gap. For open-shell transition-metal frameworks treat it as unvalidated; "
            "rows with xtb_unpaired_electrons other than 0 are not comparable with the others."
        )
        out.append("A band gap, from any method, is not electrical conductivity.")
    if any(n.startswith(("linker_s", "precedent_")) or n == "score_aggregate" for n in names):
        out.append(
            "The score columns are separate indicators of synthetic accessibility. None of them, "
            "and not score_aggregate, is a probability of synthesis."
        )
    return out


# ------------------------------------------------------------------- the write
def write_bundle(
    table: pd.DataFrame,
    out_dir: str | Path,
    *,
    project: str,
    structure_files: Mapping[str, Path] | None = None,
    table_format: str = "csv",
    viewer_bundle: bool = True,
    include_cifs: bool = True,
    enumerated: int | None = None,
    config: Mapping[str, Any] | None = None,
    config_hash: str | None = None,
    filters: Sequence[Mapping[str, Any]] = (),
    stages: Sequence[Mapping[str, Any]] = (),
    tools: Mapping[str, str | None] | None = None,
    third_party: Sequence[Mapping[str, Any]] = (),
    sources: Sequence[Mapping[str, Any]] = (),
    warnings: Sequence[str] = (),
    force: bool = False,
) -> BundleResult:
    """Write ``table`` (the rows to export, indexed by record ID) as a bundle in ``out_dir``.

    ``structure_files`` maps a record ID to its structure file. The ``cif_path`` column of
    the bundle is written from it: ``structures/<id>.cif`` for a record whose file was
    copied, empty otherwise, so a path in a bundle never points at nothing.
    """
    out_dir = Path(out_dir)
    table = _tidy(normalise(table))
    if not table.index.is_unique:
        raise BundleError("record IDs are not unique")
    bad = [i for i in table.index if not _ID_RE.match(str(i))]
    if bad:
        raise BundleError(f"record IDs cannot be used as file names: {', '.join(map(str, bad[:5]))}")
    file_name = table_file_name(table_format)
    if table_format == "parquet" and not parquet_available():
        raise BundleError("table_format: parquet needs pyarrow (pip install 'mofsift[parquet]')")
    # everything that can be wrong with the input is checked before the folder is touched
    provenance = _json_safe(
        {
            "config": dict(config) if config is not None else None,
            "filters": [dict(f) for f in filters],
            "stages": [dict(s) for s in stages],
            "tools": {"python": platform.python_version(), **dict(tools or {})},
            "sources": [dict(s) for s in sources],
            "third_party": [dict(t) for t in third_party],
        }
    )
    prepare_out_dir(out_dir, project, force=force)
    # If the export stops half way, this marks the folder as an unfinished bundle of this
    # project: validate rejects it, and the next export replaces it.
    write_json(
        out_dir / MANIFEST_FILE,
        {
            "kind": BUNDLE_KIND,
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "project": project,
            "incomplete": True,
        },
    )

    notes = list(warnings)
    files: dict[str, dict[str, Any]] = {}
    copied: dict[str, str] = {}
    if include_cifs and structure_files:
        target_dir = out_dir / STRUCTURES_DIR
        missing: list[str] = []
        for record_id in map(str, table.index):
            source = structure_files.get(record_id)
            if source is None:
                continue
            if not Path(source).is_file():
                missing.append(record_id)
                continue
            target_dir.mkdir(exist_ok=True)
            relative = f"{STRUCTURES_DIR}/{record_id}.cif"
            files[relative] = _copy_structure(Path(source), out_dir / relative)
            copied[record_id] = relative
        if missing:
            notes.append(
                f"{len(missing)} structure file(s) named in the table were not found and are not in "
                f"the bundle: {', '.join(missing[:5])}{' ...' if len(missing) > 5 else ''}"
            )
    table = table.copy()
    # replaces the work-folder paths (appended if the table had no such column)
    table["cif_path"] = pd.Series(
        [copied.get(i) for i in map(str, table.index)], index=table.index, dtype="string"
    )
    n_structures = len(copied)

    _write_table_file(table, out_dir / file_name, table_format)
    files[file_name] = _file_entry(out_dir / file_name)
    records_entry = None
    if viewer_bundle:
        lost = _write_records(out_dir / RECORDS_FILE, project, table)
        if lost:
            notes.append(
                "non-finite numbers cannot be written as JSON and are empty in "
                f"{RECORDS_FILE}: {', '.join(sorted(lost))}"
            )
        files[RECORDS_FILE] = _file_entry(out_dir / RECORDS_FILE)
        records_entry = {
            "path": RECORDS_FILE,
            "n_records": len(table),
            "sha256": files[RECORDS_FILE]["sha256"],
        }

    manifest: dict[str, Any] = {
        "kind": BUNDLE_KIND,
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "mofsift_version": __version__,
        "project": project,
        "created_utc": utc_now(),
        "config_hash": config_hash,
        "config": provenance["config"],
        "counts": {"enumerated": enumerated, "selected": len(table), "structures": n_structures},
        "filters": provenance["filters"],
        "stages": provenance["stages"],
        "tools": provenance["tools"],
        "columns": describe_columns(table),
        "table": {
            "path": file_name,
            "format": table_format,
            "n_rows": len(table),
            "n_columns": len(table.columns),
            "sha256": files[file_name]["sha256"],
        },
        "records": records_entry,
        "structures": (
            {"directory": STRUCTURES_DIR, "format": "cif", "n_files": n_structures} if n_structures else None
        ),
        "files": dict(sorted(files.items())),
        "sources": provenance["sources"],
        "notices": notices([str(c) for c in table.columns]),
        "third_party": provenance["third_party"],
        "warnings": notes,
    }
    write_json(out_dir / MANIFEST_FILE, manifest)
    return BundleResult(out_dir, manifest, len(table), n_structures, notes)


def _json_safe(payload: Any) -> Any:
    """A copy of ``payload`` that is standard JSON: a non-finite number becomes text.

    A config may hold ``.inf`` (a threshold that never applies, for example). JSON has no
    such number, so the manifest says ``"Infinity"``, ``"-Infinity"`` or ``"NaN"`` instead.
    """
    if isinstance(payload, float) and not math.isfinite(payload):
        return "NaN" if math.isnan(payload) else ("Infinity" if payload > 0 else "-Infinity")
    if isinstance(payload, Mapping):
        return {str(key): _json_safe(value) for key, value in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [_json_safe(value) for value in payload]
    return payload


def _tidy(table: pd.DataFrame) -> pd.DataFrame:
    """Give the columns no stage declared a definite type, and make empty text a missing value.

    A column of a hand-made table is read with whatever type its values suggest: a count
    with one empty cell arrives as floating point, true/false values with an empty cell as
    objects. Here each gets the type its values have, so that the same column has the same
    type in every bundle. An empty cell is how a table file writes a missing value, so empty
    text cannot be told from one and is stored as missing in the records as well.
    """
    out = table.copy()
    declared = column_types()
    for name in out.columns:
        if str(name) not in declared:
            out[name] = out[name].convert_dtypes()
        if isinstance(out[name].dtype, pd.StringDtype):
            out[name] = out[name].astype("string")
            out[name] = out[name].mask(out[name] == "")
    return out


# -------------------------------------------------------------------- the read
def read_manifest(path: str | Path) -> dict[str, Any]:
    """Read ``manifest.json`` of the bundle in ``path``; a clear error when it is not a bundle."""
    path = Path(path)
    if not path.is_dir():
        raise BundleError(f"{path}: not a folder")
    if not (path / MANIFEST_FILE).is_file():
        raise BundleError(f"{path}: no {MANIFEST_FILE}; this folder is not a mofsift bundle")
    manifest = read_json_strict(path / MANIFEST_FILE)
    if not isinstance(manifest, dict) or manifest.get("kind") != BUNDLE_KIND:
        raise BundleError(f"{path / MANIFEST_FILE}: not the manifest of a mofsift bundle")
    return manifest


def read_bundle(path: str | Path) -> Bundle:
    """Read the manifest and the table of a bundle. Use ``validate_bundle`` to check it."""
    path = Path(path)
    manifest = read_manifest(path)
    try:
        entry = manifest["table"]
        name, table_format, columns = entry["path"], entry["format"], manifest["columns"]
    except (KeyError, TypeError) as err:
        raise BundleError(f"{path / MANIFEST_FILE}: the manifest does not describe a table") from err
    if not isinstance(name, str) or not is_safe_relative(name) or not (path / name).is_file():
        raise BundleError(f"{path}: the table {name!r} named in the manifest is not in the bundle")
    return Bundle(path, manifest, read_table_file(path / name, table_format, columns))
