from __future__ import annotations

import pandas as pd
import pytest

import mofsift.generate
import mofsift.qc  # noqa: F401
from mofsift.io.tables import (
    TableError,
    normalise,
    read_table,
    register_columns,
    sha256_file,
    stage_table_path,
    write_table,
)


def _table() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "name": ["a", "nan", "None"],  # text that looks like a missing value must survive
            "topology": ["pcu", "NA", pd.NA],
            "n_atoms": [53, pd.NA, 7],
            "node_rmsd": [0.0, 0.1234567891, pd.NA],
            "qc_pass": [True, False, pd.NA],
            "my_column": [1.5, 2.5, 3.5],  # not registered: read back by inference
        },
        index=pd.Index(["ms-1", "ms-2", "ms-3"], name="id"),
    )


def test_round_trip_keeps_values_types_and_missing_cells(tmp_path):
    path = write_table(_table(), tmp_path / "work" / "t.csv")
    back = read_table(path)
    expected = normalise(_table())
    pd.testing.assert_frame_equal(back, expected)
    assert str(back["n_atoms"].dtype) == "Int64" and str(back["qc_pass"].dtype) == "boolean"
    assert back.loc["ms-2", "name"] == "nan" and back.loc["ms-2", "topology"] == "NA"
    assert pd.isna(back.loc["ms-3", "topology"]) and pd.isna(back.loc["ms-2", "n_atoms"])


def test_file_is_deterministic_with_unix_line_endings(tmp_path):
    a = write_table(_table(), tmp_path / "a.csv")
    b = write_table(_table(), tmp_path / "b.csv")
    assert sha256_file(a) == sha256_file(b)
    raw = a.read_bytes()
    assert b"\r" not in raw and raw.startswith(b"id,name,")
    assert not list(tmp_path.glob("*.tmp"))


def test_ids_must_be_present_and_unique(tmp_path):
    dup = _table()
    dup.index = pd.Index(["ms-1", "ms-1", "ms-3"], name="id")
    with pytest.raises(TableError, match="duplicate id values: ms-1"):
        write_table(dup, tmp_path / "t.csv")
    (tmp_path / "noid.csv").write_text("name\nx\n", encoding="utf-8")
    with pytest.raises(TableError, match="no 'id' column"):
        read_table(tmp_path / "noid.csv")
    with pytest.raises(TableError, match="table not found"):
        read_table(tmp_path / "missing.csv")


def test_a_cell_of_the_wrong_type_is_reported(tmp_path):
    (tmp_path / "bad.csv").write_text("id,n_atoms\nms-1,many\n", encoding="utf-8")
    with pytest.raises(TableError, match="cannot read the table"):
        read_table(tmp_path / "bad.csv")


def test_register_columns_rejects_conflicts_and_unknown_types():
    register_columns({"n_atoms": "Int64"})  # same type again is fine
    with pytest.raises(ValueError, match="already registered"):
        register_columns({"n_atoms": "Float64"})
    with pytest.raises(ValueError, match="unsupported dtype"):
        register_columns({"x_new": "float32"})


def test_stage_table_paths(tmp_path):
    assert stage_table_path(tmp_path, "generate").name == "01_generate.csv"
    assert stage_table_path(tmp_path, "qc").name == "02_qc.csv"
    with pytest.raises(TableError):
        stage_table_path(tmp_path, "nope")
