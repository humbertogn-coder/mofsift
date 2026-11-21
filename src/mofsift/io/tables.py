"""Working tables: one CSV per stage in the work folder, indexed by record ID.

CSV is used for the working tables because it needs no extra dependency and is easy to
inspect and to edit by hand. Column types are declared by the stages, so a table read
back from disk is identical to the one that was written: missing values stay missing
(empty cells) and are never turned into a default number or into the text "nan".
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from pathlib import Path

import pandas as pd

ID_COLUMN = "id"

STAGE_FILES: dict[str, str] = {
    "generate": "01_generate.csv",
    "qc": "02_qc.csv",
    "describe": "03_describe.csv",
    "score": "04_score.csv",
    "filter": "05_filter.csv",
}
"""File name of each stage's output table, in pipeline order."""

_ALLOWED_TYPES = {"string", "Int64", "Float64", "boolean"}
_COLUMN_TYPES: dict[str, str] = {}


class TableError(ValueError):
    """A working table is missing, malformed or inconsistent."""


def register_columns(types: Mapping[str, str]) -> None:
    """Declare the pandas dtype of columns a stage writes (string, Int64, Float64 or boolean)."""
    for name, dtype in types.items():
        if dtype not in _ALLOWED_TYPES:
            raise ValueError(f"column {name}: unsupported dtype {dtype!r}")
        if _COLUMN_TYPES.get(name, dtype) != dtype:
            raise ValueError(f"column {name} is already registered as {_COLUMN_TYPES[name]}, not {dtype}")
        _COLUMN_TYPES[name] = dtype


def column_types() -> dict[str, str]:
    return dict(_COLUMN_TYPES)


def stage_table_path(workdir: str | Path, stage: str) -> Path:
    try:
        return Path(workdir) / STAGE_FILES[stage]
    except KeyError:
        raise TableError(f"stage {stage!r} has no working table") from None


def normalise(table: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with registered columns cast to their declared dtype and a string ID index."""
    out = table.copy()
    for col in out.columns:
        dtype = _COLUMN_TYPES.get(col)
        if dtype is not None and str(out[col].dtype) != dtype:
            out[col] = out[col].astype(dtype)
    out.index = out.index.astype("string")
    out.index.name = ID_COLUMN
    return out


def _check_ids(table: pd.DataFrame, what: str) -> None:
    if table.index.name != ID_COLUMN:
        raise TableError(f"{what}: the table must be indexed by '{ID_COLUMN}'")
    if table.index.isna().any():
        raise TableError(f"{what}: some rows have no {ID_COLUMN}")
    if not table.index.is_unique:
        dup = sorted(set(table.index[table.index.duplicated()]))
        raise TableError(f"{what}: duplicate {ID_COLUMN} values: {', '.join(map(str, dup[:5]))}")


def write_table(table: pd.DataFrame, path: str | Path) -> Path:
    """Write a working table atomically (LF line endings, UTF-8, empty cell = missing)."""
    path = Path(path)
    out = normalise(table)
    _check_ids(out, str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        out.to_csv(fh, index=True, na_rep="", lineterminator="\n")
    os.replace(tmp, path)
    return path


def read_table(path: str | Path) -> pd.DataFrame:
    """Read a working table. Only empty cells are missing values."""
    path = Path(path)
    if not path.exists():
        raise TableError(f"{path}: table not found")
    try:
        header = list(pd.read_csv(path, nrows=0, encoding="utf-8").columns)
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as err:
        raise TableError(f"{path}: cannot read the table ({err})") from err
    if ID_COLUMN not in header:
        raise TableError(f"{path}: no '{ID_COLUMN}' column")
    dtypes = {c: _COLUMN_TYPES[c] for c in header if c in _COLUMN_TYPES}
    dtypes[ID_COLUMN] = "string"
    try:
        table = pd.read_csv(path, dtype=dtypes, keep_default_na=False, na_values=[""], encoding="utf-8")
    except (ValueError, TypeError, OverflowError, pd.errors.ParserError) as err:
        raise TableError(f"{path}: cannot read the table ({err})") from err
    table = table.set_index(ID_COLUMN)
    _check_ids(table, str(path))
    return table


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
