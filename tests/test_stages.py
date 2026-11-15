from __future__ import annotations

import pandas as pd
import pytest

from mofsift.stages import (
    STATUS_FAILED,
    STATUS_OK,
    STATUS_SKIPPED,
    StageContractError,
    check_contract,
    mark,
    skip_all,
    stage,
    status_columns,
)


@pytest.fixture
def table() -> pd.DataFrame:
    return pd.DataFrame({"id": ["a", "b", "c"], "n_atoms": [10, 20, 30]}).set_index("id")


def test_status_column_names():
    assert status_columns("qc") == ("qc_status", "qc_skip_reason")
    assert status_columns("describe.xtb") == ("describe_xtb_status", "describe_xtb_skip_reason")


def test_mark_does_not_modify_its_input(table):
    out = mark(table, "qc", STATUS_OK)
    assert "qc_status" not in table.columns
    assert list(out["qc_status"]) == ["ok"] * 3 and out["qc_skip_reason"].isna().all()


def test_mark_subset_and_reason_rules(table):
    out = mark(table, "xtb", STATUS_OK)
    out = mark(out, "xtb", STATUS_SKIPPED, "more than 15 atoms", rows=out["n_atoms"] > 15)
    assert list(out["xtb_status"]) == ["ok", "skipped", "skipped"]
    assert (
        pd.isna(out.loc["a", "xtb_skip_reason"]) and out.loc["c", "xtb_skip_reason"] == "more than 15 atoms"
    )
    with pytest.raises(ValueError, match="reason is required"):
        mark(table, "xtb", STATUS_FAILED)
    with pytest.raises(ValueError, match="status must be one of"):
        mark(table, "xtb", "done")


def test_skip_all_records_the_reason_on_every_row(table):
    out = skip_all(table, "describe.geometry", "pyzeo is not installed")
    assert (out["describe_geometry_status"] == "skipped").all()
    assert (out["describe_geometry_skip_reason"] == "pyzeo is not installed").all()
    check_contract(table, out, "describe.geometry")


def test_contract_accepts_added_columns(table):
    after = mark(table.assign(density=[0.5, None, 0.7]), "geo", STATUS_OK)
    check_contract(table, after, "geo")


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda t: t.iloc[:2], "dropped, added or reordered"),
        (lambda t: t.iloc[::-1], "dropped, added or reordered"),
        (lambda t: t.drop(columns="n_atoms"), "columns were removed: n_atoms"),
        (lambda t: t.assign(n_atoms=[10, 20, 31]), "existing columns were modified: n_atoms"),
    ],
)
def test_contract_rejects_dropping_or_changing_data(table, mutate, message):
    after = mark(mutate(table), "s", STATUS_OK)
    with pytest.raises(StageContractError, match=message):
        check_contract(table, after, "s")


def test_contract_requires_complete_status(table):
    with pytest.raises(StageContractError, match="missing column s_status"):
        check_contract(table, table.copy(), "s")
    partial = mark(table, "s", STATUS_OK, rows=table["n_atoms"] < 25)
    with pytest.raises(StageContractError, match=r"1 row\(s\) have no status"):
        check_contract(table, partial, "s")
    weird = mark(table, "s", STATUS_OK)
    weird.loc["a", "s_status"] = "maybe"
    with pytest.raises(StageContractError, match="unknown status"):
        check_contract(table, weird, "s")
    no_reason = mark(table, "s", STATUS_SKIPPED, "x")
    no_reason.loc["b", "s_skip_reason"] = pd.NA
    with pytest.raises(StageContractError, match="have no reason"):
        check_contract(table, no_reason, "s")
    with pytest.raises(StageContractError, match="must return a DataFrame"):
        check_contract(table, None, "s")  # type: ignore[arg-type]


def test_a_later_stage_may_not_rewrite_an_earlier_status(table):
    first = mark(table, "qc", STATUS_OK)
    tampered = mark(mark(first, "geo", STATUS_OK), "qc", STATUS_FAILED, "changed my mind")
    with pytest.raises(StageContractError, match="existing columns were modified: qc_status"):
        check_contract(first, tampered, "geo")


def test_stage_decorator_enforces_the_contract(table, tmp_path):
    @stage("good")
    def good(t, cfg, workdir):
        return mark(t.assign(x=1.0), "good", STATUS_OK)

    @stage("bad")
    def bad(t, cfg, workdir):
        return t[t["n_atoms"] > 15]

    assert good.stage_name == "good"
    assert list(good(table, None, tmp_path).columns) == ["n_atoms", "x", "good_status", "good_skip_reason"]
    with pytest.raises(StageContractError):
        bad(table, None, tmp_path)
