"""Check that a folder is a complete, self-consistent bundle.

``validate_bundle`` needs nothing but the folder: it does not look at a config or a work
folder, so a bundle can be checked where it is used. It tests the manifest against its
JSON Schema, every listed file against its size and SHA-256, the table against the column
dictionary, every record against the candidate schema and against its table row, and
every structure path against the files that are there.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema
import pandas as pd

from mofsift.export.bundle import (
    _JUNK,
    BUNDLE_SCHEMA_VERSION,
    MANIFEST_FILE,
    STRUCTURES_DIR,
    BundleError,
    _json_values,
    is_safe_relative,
    read_manifest,
    read_records,
    read_table_file,
)
from mofsift.io.tables import ID_COLUMN

MAX_LISTED = 40
_FORBIDDEN = re.compile(r"(^|/)(POTCAR|WAVECAR|CHGCAR)[^/]*$|\.(pkl|pickle|joblib)$", re.IGNORECASE)
_CIF_MARKS = (b"_cell_length_a", b"_atom_site_")


@dataclass
class Validation:
    path: Path
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "valid": self.ok,
            "problems": self.problems,
            "notes": self.notes,
            **self.summary,
        }


def packaged_schema(name: str) -> dict[str, Any]:
    """One of the schemas shipped with the package: ``manifest`` or ``candidate``."""
    text = resources.files("mofsift.schemas").joinpath(f"{name}.schema.json").read_text("utf-8")
    return json.loads(text)


_TYPE_TESTS: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: (
        (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, float) and v.is_integer())
    ),
}


def _value_test(schema: Any) -> Callable[[Any], bool] | None:
    """A plain function that accepts what ``schema`` accepts, for schemas of type, enum and pattern."""
    if not isinstance(schema, dict) or set(schema) - {"type", "enum", "pattern", "description"}:
        return None
    types = schema.get("type")
    tests = [_TYPE_TESTS[t] for t in ([types] if isinstance(types, str) else types or [])]
    allowed = schema.get("enum")
    pattern = re.compile(schema["pattern"]) if "pattern" in schema else None

    def accepts(value: Any) -> bool:
        if tests and not any(test(value) for test in tests):
            return False
        if allowed is not None and not any(value == a and type(value) is type(a) for a in allowed):
            return False
        return not (pattern is not None and isinstance(value, str) and not pattern.search(value))

    return accepts


def quick_record_test(schema: dict[str, Any]) -> Callable[[Any], bool] | None:
    """A fast equivalent of ``schema`` for one record, or None when the schema needs the full validator.

    Checking a record with ``jsonschema`` takes about a millisecond, which is minutes for a large
    bundle. The candidate schema only uses ``type``, ``enum`` and ``pattern`` on its properties, so
    the same decision can be made directly. A record this test refuses is then given to
    ``jsonschema`` for the message.
    """
    known = {
        "$schema",
        "$id",
        "title",
        "description",
        "type",
        "required",
        "properties",
        "additionalProperties",
    }
    if set(schema) - known or schema.get("type") != "object":
        return None
    properties = {name: _value_test(sub) for name, sub in schema.get("properties", {}).items()}
    extra = _value_test(schema.get("additionalProperties", {}))
    if extra is None or None in properties.values():
        return None
    required = list(schema.get("required", []))

    def accepts(record: Any) -> bool:
        if not isinstance(record, dict) or any(name not in record for name in required):
            return False
        return all(properties.get(name, extra)(value) for name, value in record.items())  # type: ignore[misc]

    return accepts


def _where(error: jsonschema.ValidationError) -> str:
    return ".".join(str(p) for p in error.absolute_path) or "(top level)"


def _digest(path: Path) -> tuple[int, str, bool]:
    """Size, SHA-256, and whether the content looks like a CIF with a cell and atom sites."""
    digest, size, seen = hashlib.sha256(), 0, [False] * len(_CIF_MARKS)
    tail = b""
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
            window = tail + chunk
            seen = [s or mark in window for s, mark in zip(seen, _CIF_MARKS, strict=True)]
            tail = chunk[-32:]
    return size, digest.hexdigest(), all(seen)


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        return (
            isinstance(a, (int, float))
            and isinstance(b, (int, float))
            and not isinstance(a, bool)
            and not isinstance(b, bool)
            and math.isclose(a, b, rel_tol=1e-12, abs_tol=0.0)
        )
    return type(a) is type(b) and a == b


def validate_bundle(path: str | Path) -> Validation:
    """Check the bundle in ``path``. Raises ``BundleError`` only when ``path`` is not a folder."""
    path = Path(path)
    result = Validation(path)
    if not path.is_dir():
        raise BundleError(f"{path}: not a folder")
    problems: list[str] = []
    try:
        manifest = read_manifest(path)
    except BundleError as err:
        result.problems.append(str(err))
        return result

    # ---- the manifest itself
    if manifest.get("incomplete"):
        result.problems.append(
            f"{MANIFEST_FILE}: the export that wrote this folder did not finish; run it again"
        )
        return result
    validator = jsonschema.Draft202012Validator(packaged_schema("manifest"))
    schema_errors = sorted(validator.iter_errors(manifest), key=lambda e: list(map(str, e.absolute_path)))
    problems += [f"{MANIFEST_FILE}: {_where(e)}: {e.message[:200]}" for e in schema_errors]
    if schema_errors:
        _finish(result, problems)
        return result  # the rest of the checks rely on the structure of the manifest
    if manifest["schema_version"].split(".")[0] != BUNDLE_SCHEMA_VERSION.split(".")[0]:
        problems.append(f"{MANIFEST_FILE}: schema_version {manifest['schema_version']} is not supported")
    result.summary = {
        "project": manifest["project"],
        "mofsift_version": manifest["mofsift_version"],
        "records": manifest["counts"]["selected"],
        "structures": manifest["counts"]["structures"],
    }

    # ---- every listed file: there, right size, right content
    files: dict[str, Any] = manifest["files"]
    looks_like_cif: dict[str, bool] = {}
    for name, entry in files.items():
        target = path / name
        if not is_safe_relative(name):
            problems.append(f"{name}: not a path inside the bundle")
        elif not target.is_file():
            problems.append(f"{name}: listed in the manifest but not in the bundle")
        else:
            size, sha, cif = _digest(target)
            looks_like_cif[name] = cif
            if size != entry["bytes"] or sha != entry["sha256"]:
                problems.append(f"{name}: the file differs from the one the manifest describes")
    for part in ("table", "records"):
        entry = manifest[part]
        if entry is not None and (
            entry["path"] not in files or files[entry["path"]]["sha256"] != entry["sha256"]
        ):
            problems.append(f"{MANIFEST_FILE}: {part}.path or {part}.sha256 does not match 'files'")

    # ---- files that should not travel, and files nobody listed
    for found in sorted(p for p in path.rglob("*") if p.is_file()):
        relative = found.relative_to(path).as_posix()
        if _FORBIDDEN.search(relative):
            problems.append(f"{relative}: restricted or unsafe file type in a bundle")
        elif (
            relative != MANIFEST_FILE
            and relative not in files
            and found.name not in _JUNK
            and not found.name.startswith(".")
        ):
            result.notes.append(f"{relative}: in the folder but not listed in the manifest")

    # ---- the table
    table_entry = manifest["table"]
    table = None
    if (path / table_entry["path"]).is_file():
        try:
            table = read_table_file(path / table_entry["path"], table_entry["format"], manifest["columns"])
        except BundleError as err:
            problems.append(str(err))
    if table is not None:
        problems += _check_table(table, manifest)
        if manifest["records"] is not None and (path / manifest["records"]["path"]).is_file():
            problems += _check_records(path, table, manifest)
        problems += _check_structures(table, manifest, looks_like_cif)

    # ---- counts that must add up
    counts = manifest["counts"]
    if counts["enumerated"] is not None:
        if counts["selected"] > counts["enumerated"]:
            problems.append(f"{MANIFEST_FILE}: counts.selected is larger than counts.enumerated")
        for report in manifest["filters"]:
            parts = [report["passed"], report["rejected"], report["undecided"]]
            if None not in parts and sum(parts) != counts["enumerated"]:
                problems.append(
                    f"{MANIFEST_FILE}: the counts of filter {report['expression']!r} do not add up "
                    "to counts.enumerated"
                )
    _finish(result, problems)
    return result


def _finish(result: Validation, problems: list[str]) -> None:
    result.problems = problems[:MAX_LISTED]
    if len(problems) > MAX_LISTED:
        result.problems.append(f"... and {len(problems) - MAX_LISTED} more problem(s)")


def _check_table(table: pd.DataFrame, manifest: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    entry, name = manifest["table"], manifest["table"]["path"]
    declared = [c["name"] for c in manifest["columns"]]
    if declared[:1] != [ID_COLUMN] or declared[1:] != [str(c) for c in table.columns]:
        problems.append(f"{name}: its columns are not the ones listed in the manifest, in the same order")
    if len(table) != entry["n_rows"] or len(table.columns) != entry["n_columns"]:
        problems.append(
            f"{name}: {len(table)} rows x {len(table.columns)} columns, not what the manifest says"
        )
    if len(table) != manifest["counts"]["selected"]:
        problems.append(f"{name}: the number of rows differs from counts.selected")
    if table.index.isna().any() or not table.index.is_unique:
        problems.append(f"{name}: record IDs are missing or not unique")
    return problems


def _check_records(path: Path, table: pd.DataFrame, manifest: dict[str, Any]) -> list[str]:
    name = manifest["records"]["path"]
    try:
        payload = read_records(path / name)
    except BundleError as err:
        return [str(err)]
    problems: list[str] = []
    records = payload["records"]
    if len(records) != manifest["records"]["n_records"] or payload.get("n_records") != len(records):
        problems.append(f"{name}: the number of records differs from the one declared")
    if payload.get("project") != manifest["project"]:
        problems.append(f"{name}: project differs from the manifest")
    if str(payload.get("schema_version", "")).split(".")[0] != BUNDLE_SCHEMA_VERSION.split(".")[0]:
        problems.append(f"{name}: schema_version {payload.get('schema_version')!r} is not supported")

    schema = packaged_schema("candidate")
    validator = jsonschema.Draft202012Validator(schema)
    quick = quick_record_test(schema)
    expected_keys = [ID_COLUMN, *map(str, table.columns)]
    for position, record in enumerate(records):
        label = (
            record.get("id", f"record {position + 1}")
            if isinstance(record, dict)
            else f"record {position + 1}"
        )
        if quick is None or not quick(record):
            for error in validator.iter_errors(record):
                problems.append(f"{name}: {label}: {_where(error)}: {error.message[:160]}")
        if isinstance(record, dict) and list(record) != expected_keys:
            problems.append(f"{name}: {label}: its keys are not the columns of the table, in the same order")
        if len(problems) > MAX_LISTED:
            return problems
    ids = [r.get("id") if isinstance(r, dict) else None for r in records]
    if ids != [str(i) for i in table.index]:
        problems.append(f"{name}: the records are not the rows of the table, in the same order")
        return problems
    for column in table.columns:
        expected, _ = _json_values(table[column])
        for record, value in zip(records, expected, strict=True):
            if not _same(record.get(str(column)), value):
                problems.append(
                    f"{name}: {record['id']}: {column} is {record.get(str(column))!r} "
                    f"but the table has {value!r}"
                )
                break  # one example per column is enough
    return problems


def _check_structures(
    table: pd.DataFrame, manifest: dict[str, Any], looks_like_cif: dict[str, bool]
) -> list[str]:
    problems: list[str] = []
    files = manifest["files"]
    listed = {n for n in files if n.startswith(f"{STRUCTURES_DIR}/")}
    used: set[str] = set()
    if "cif_path" in table.columns:
        for record_id, value in table["cif_path"].dropna().items():
            expected = f"{STRUCTURES_DIR}/{record_id}.cif"
            if value != expected:
                problems.append(f"{record_id}: cif_path is {value!r}, expected {expected!r}")
            elif value not in files:
                problems.append(f"{record_id}: {value} is not listed in the manifest")
            elif looks_like_cif.get(value) is False:
                problems.append(f"{value}: no unit cell or no atom sites; not a usable CIF")
            used.add(str(value))
    for extra in sorted(listed - used):
        problems.append(f"{extra}: listed in the manifest but no record points at it")
    n = len(used)
    declared = manifest["structures"]["n_files"] if manifest["structures"] else 0
    if n != declared or n != manifest["counts"]["structures"]:
        problems.append(
            f"{MANIFEST_FILE}: {n} record(s) have a structure file; structures.n_files and "
            "counts.structures must say the same"
        )
    return problems
