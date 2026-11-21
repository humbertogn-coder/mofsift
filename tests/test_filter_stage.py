"""The filter step marks rows; it never removes them."""

from __future__ import annotations

import pandas as pd
import pytest

from mofsift import filtering
from mofsift.config import parse_config
from mofsift.filters import FilterError


@pytest.fixture
def table():
    return pd.DataFrame(
        {
            "qc_pass": pd.array([True, False, pd.NA, True], dtype="boolean"),
            "xtb_gap": pd.array([0.5, 2.0, pd.NA, pd.NA], dtype="Float64"),
        },
        index=pd.Index(["ms-1", "ms-2", "ms-3", "ms-4"], name="id"),
    )


def _cfg(*filters):
    return parse_config(
        {"project": "t", "generate": {"source": "cifs", "cif_dir": "x"}, "filter": list(filters)}
    )


def test_rows_are_marked_with_the_filters_they_fail(table):
    result = filtering.run(table, _cfg("qc_pass == True", " xtb_gap < 1 "))
    out = result.table
    assert len(out) == 4 and list(out.columns[:2]) == ["qc_pass", "xtb_gap"]
    assert out["filter_pass"].tolist() == [True, False, False, False]
    assert out["filter_rejected_by"].tolist()[1:] == [
        "qc_pass == True; xtb_gap < 1",
        "qc_pass == True [missing: qc_pass]; xtb_gap < 1 [missing: xtb_gap]",
        "xtb_gap < 1 [missing: xtb_gap]",
    ]
    assert pd.isna(out["filter_rejected_by"].iloc[0])
    assert (out["filter_status"] == "ok").all() and out["filter_skip_reason"].isna().all()
    assert [r.as_dict() for r in result.reports] == [
        {"expression": "qc_pass == True", "passed": 2, "rejected": 1, "undecided": 1},
        {"expression": "xtb_gap < 1", "passed": 1, "rejected": 1, "undecided": 2},
    ]
    assert result.n_selected == 1 and filtering.selected(out).tolist() == [True, False, False, False]


def test_without_filters_nothing_is_decided_and_every_row_is_kept(table):
    result = filtering.run(table, _cfg())
    out = result.table
    assert out["filter_pass"].isna().all() and out["filter_rejected_by"].isna().all()
    assert (out["filter_status"] == "skipped").all()
    assert (out["filter_skip_reason"] == "no filters in the config").all()
    assert result.reports == [] and result.n_selected == 4
    assert filtering.selected(out).all()
    assert filtering.selected(table).all()  # a table that never went through the step


def test_column_types_survive_the_work_table(table, tmp_path):
    from mofsift.io.tables import normalise, read_table, write_table

    out = filtering.run(table, _cfg("xtb_gap < 1")).table
    back = read_table(write_table(out, tmp_path / "05_filter.csv"))
    pd.testing.assert_frame_equal(back, normalise(out))


def test_a_filter_on_an_unknown_column_is_an_error_not_an_empty_result(table):
    with pytest.raises(FilterError, match="the table has no column 'xtb_gab'; did you mean 'xtb_gap'"):
        filtering.run(table, _cfg("xtb_gab < 1"))


def test_the_step_cannot_run_twice_on_the_same_table(table):
    once = filtering.run(table, _cfg("xtb_gap < 1")).table
    with pytest.raises(ValueError, match="already has columns written by the filter step"):
        filtering.run(once, _cfg("xtb_gap < 1"))


def test_a_division_by_zero_is_named_as_the_reason(table):
    table = table.assign(vol=pd.array([2.0, 0.0, 1.0, 0.0], dtype="Float64"))
    out = filtering.run(table, _cfg("xtb_gap / vol < 1")).table
    assert out["filter_pass"].tolist() == [True, False, False, False]
    assert out["filter_rejected_by"].tolist()[1:] == [
        "xtb_gap / vol < 1 [division by zero or overflow]",
        "xtb_gap / vol < 1 [missing: xtb_gap]",
        "xtb_gap / vol < 1 [missing: xtb_gap]",
    ]
