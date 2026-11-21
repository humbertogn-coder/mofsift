"""The column dictionary must describe exactly the columns the stages write."""

from __future__ import annotations

import pandas as pd

from mofsift import runner  # noqa: F401  (importing the stages registers their columns)
from mofsift.columns import COLUMN_INFO, STATUS_COLUMNS, describe_columns
from mofsift.io.tables import column_types


def test_every_registered_column_is_described_and_nothing_else():
    assert set(COLUMN_INFO) == set(column_types())


def test_descriptions_are_complete():
    groups = {
        "generate",
        "qc",
        "describe.composition",
        "describe.geometry",
        "describe.xtb",
        "score",
        "filter",
    }
    for name, info in COLUMN_INFO.items():
        assert info.group in groups, name
        assert info.description.endswith(".") and len(info.description) > 10, name
    assert {c.removesuffix("_status") for c in STATUS_COLUMNS} == {g.replace(".", "_") for g in groups}
    assert COLUMN_INFO["xtb_gap"].unit == "eV" and COLUMN_INFO["lcd"].unit == "angstrom"


def test_describe_columns_follows_the_table():
    table = pd.DataFrame(
        {
            "xtb_gap": pd.array([1.0], dtype="Float64"),
            "my_note": ["x"],
            "n_atoms": pd.array([3], dtype="Int64"),
        },
        index=pd.Index(["a"], name="id"),
    )
    entries = describe_columns(table)
    assert [e["name"] for e in entries] == ["id", "xtb_gap", "my_note", "n_atoms"]
    assert [e["type"] for e in entries] == ["string", "number", "string", "integer"]
    # a column no stage writes keeps its type and has no description
    assert entries[2] == {
        "name": "my_note",
        "type": "string",
        "group": None,
        "unit": None,
        "description": None,
    }
