"""JSON Schemas of a bundle: the manifest and one candidate record.

The packaged copies in ``mofsift/schemas/`` are what other programs (a viewer, a
validator in another language) should read. Regenerate them after changing a column or
the manifest::

    python -m mofsift.export.schemas src/mofsift/schemas
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# importing the stages registers the type of every column they write
from mofsift import describe, filtering, generate, qc, score  # noqa: F401
from mofsift.columns import COLUMN_INFO, ID_DESCRIPTION, JSON_TYPES, STATUS_COLUMNS
from mofsift.export.bundle import (
    BUNDLE_KIND,
    ID_PATTERN,
    RECORDS_FILE,
    STRUCTURES_DIR,
    TABLE_FORMATS,
    TABLE_STEM,
)
from mofsift.io.tables import column_types
from mofsift.stages import STATUSES

_DRAFT = "https://json-schema.org/draft/2020-12/schema"
_BASE = "https://github.com/humbertogn-coder/mofsift/schemas/"
_SHA256 = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
_COUNT = {"type": "integer", "minimum": 0}
_MAYBE_COUNT = {"type": ["integer", "null"], "minimum": 0}
_MAYBE_TEXT = {"type": ["string", "null"]}
CIF_PATH_PATTERN = rf"^{STRUCTURES_DIR}/[A-Za-z0-9][A-Za-z0-9_.-]{{0,127}}\.cif$"
SAFE_PATH_PATTERN = r"^(?!/)(?!.*(^|/)\.\.(/|$))[A-Za-z0-9_.\-/]+$"


def candidate_json_schema() -> dict[str, Any]:
    """Schema of one record of ``candidates.json`` (and of one row of the table)."""
    types = column_types()
    properties: dict[str, Any] = {
        "id": {"type": "string", "pattern": ID_PATTERN, "description": ID_DESCRIPTION}
    }
    for name, info in COLUMN_INFO.items():
        entry: dict[str, Any] = {"type": [JSON_TYPES[types[name]], "null"]}
        if name in STATUS_COLUMNS:
            entry = {"enum": [*STATUSES, None]}
        elif name == "cif_path":
            entry["pattern"] = CIF_PATH_PATTERN
        entry["description"] = info.description + (f" Unit: {info.unit}." if info.unit else "")
        properties[name] = entry
    return {
        "$schema": _DRAFT,
        "$id": _BASE + "candidate.schema.json",
        "title": "mofsift candidate record",
        "description": "One candidate of a mofsift bundle. null means the value was not computed.",
        "type": "object",
        "required": ["id"],
        "properties": properties,
        "additionalProperties": {"type": ["string", "number", "boolean", "null"]},
    }


def manifest_json_schema() -> dict[str, Any]:
    """Schema of ``manifest.json``."""
    column = {
        "type": "object",
        "required": ["name", "type", "group", "unit", "description"],
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "type": {"enum": sorted(JSON_TYPES.values())},
            "group": _MAYBE_TEXT,
            "unit": _MAYBE_TEXT,
            "description": _MAYBE_TEXT,
        },
    }
    filter_report = {
        "type": "object",
        "required": ["expression", "passed", "rejected", "undecided"],
        "properties": {
            "expression": {"type": "string"},
            "passed": _MAYBE_COUNT,
            "rejected": _MAYBE_COUNT,
            "undecided": _MAYBE_COUNT,
        },
    }
    source = {
        "type": "object",
        "required": [
            "project",
            "config_hash",
            "mofsift_version",
            "created_utc",
            "selected",
            "manifest_sha256",
        ],
        "properties": {
            "project": {"type": "string"},
            "config_hash": {"type": ["string", "null"]},
            "mofsift_version": {"type": "string"},
            "created_utc": {"type": "string"},
            "selected": _COUNT,
            "manifest_sha256": _SHA256,
        },
    }
    return {
        "$schema": _DRAFT,
        "$id": _BASE + "manifest.schema.json",
        "title": "mofsift bundle manifest",
        "type": "object",
        "required": [
            "kind",
            "schema_version",
            "mofsift_version",
            "project",
            "created_utc",
            "config_hash",
            "config",
            "counts",
            "filters",
            "stages",
            "tools",
            "columns",
            "table",
            "records",
            "structures",
            "files",
            "sources",
            "notices",
            "third_party",
            "warnings",
        ],
        "properties": {
            "kind": {"const": BUNDLE_KIND},
            "schema_version": {"type": "string", "pattern": r"^1\.[0-9]+\.[0-9]+$"},
            "mofsift_version": {"type": "string"},
            "project": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]*$"},
            "created_utc": {
                "type": "string",
                "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$",
            },
            "config_hash": {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"},
            "config": {
                "type": ["object", "null"],
                "description": "The validated config of the run; null for a merged bundle.",
            },
            "counts": {
                "type": "object",
                "required": ["enumerated", "selected", "structures"],
                "properties": {
                    "enumerated": {**_MAYBE_COUNT, "description": "Rows before the filters."},
                    "selected": {**_COUNT, "description": "Rows in this bundle."},
                    "structures": {**_COUNT, "description": "Records with a structure file."},
                },
            },
            "filters": {"type": "array", "items": filter_report},
            "stages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["stage"],
                    "properties": {"stage": {"type": "string"}},
                },
            },
            "tools": {"type": "object", "additionalProperties": _MAYBE_TEXT},
            "columns": {"type": "array", "minItems": 1, "items": column},
            "table": {
                "type": "object",
                "required": ["path", "format", "n_rows", "n_columns", "sha256"],
                "properties": {
                    "path": {"enum": [f"{TABLE_STEM}.{f}" for f in TABLE_FORMATS]},
                    "format": {"enum": list(TABLE_FORMATS)},
                    "n_rows": _COUNT,
                    "n_columns": _COUNT,
                    "sha256": _SHA256,
                },
            },
            "records": {
                "type": ["object", "null"],
                "required": ["path", "n_records", "sha256"],
                "properties": {"path": {"const": RECORDS_FILE}, "n_records": _COUNT, "sha256": _SHA256},
            },
            "structures": {
                "type": ["object", "null"],
                "required": ["directory", "format", "n_files"],
                "properties": {
                    "directory": {"const": STRUCTURES_DIR},
                    "format": {"const": "cif"},
                    "n_files": _COUNT,
                },
            },
            "files": {
                "type": "object",
                "description": "Every file of the bundle except the manifest, by relative path.",
                "propertyNames": {"pattern": SAFE_PATH_PATTERN},
                "additionalProperties": {
                    "type": "object",
                    "required": ["bytes", "sha256"],
                    "properties": {"bytes": _COUNT, "sha256": _SHA256},
                },
            },
            "sources": {
                "type": "array",
                "description": "The bundles a merged bundle was made from; empty otherwise.",
                "items": source,
            },
            "notices": {"type": "array", "items": {"type": "string"}},
            "third_party": {"type": "array", "items": {"type": "object"}},
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
    }


SCHEMAS = {
    "candidate.schema.json": candidate_json_schema,
    "manifest.schema.json": manifest_json_schema,
}


def write_schemas(folder: str | Path) -> list[Path]:
    """Write both schemas into ``folder``."""
    written = []
    for name, build in SCHEMAS.items():
        path = Path(folder) / name
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(build(), fh, indent=2, sort_keys=True)
            fh.write("\n")
        written.append(path)
    return written


if __name__ == "__main__":  # pragma: no cover
    if len(sys.argv) != 2:
        sys.exit("usage: python -m mofsift.export.schemas FOLDER")
    for written_path in write_schemas(sys.argv[1]):
        print(written_path)
