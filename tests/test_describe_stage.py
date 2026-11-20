"""The describe stage with stand-in backends: what runs, what is skipped, what is reused."""

from __future__ import annotations

import pandas as pd
import pytest

from conftest import methane, write_cif
from mofsift import describe, generate, qc
from mofsift.config import parse_config
from mofsift.describe import cache
from mofsift.describe.isolate import WorkerError
from mofsift.io.tables import read_table, write_table
from mofsift.plan import plan_stages

ALL = {"pyzeo": True, "tblite": True, "pormake": True}
NONE = {"pyzeo": False, "tblite": False, "pormake": True}
GEO = {
    "lcd": 5.0,
    "pld": 4.0,
    "lcd_free_path": 5.0,
    "accessible_surface_m2_g": 100.0,
    "accessible_surface_m2_cm3": 50.0,
    "accessible_volume_fraction": 0.5,
    "accessible_volume_cm3_g": 1.0,
}
XTB = {"xtb_gap": 2.5, "xtb_homo": -6.0, "xtb_lumo": -3.5, "xtb_unpaired_electrons": 0}


class Counting:
    """A backend that returns fixed values and remembers which structures it was asked for."""

    def __init__(self, values, fail_on=()):
        self.values, self.fail_on, self.calls = values, tuple(fail_on), []

    def __call__(self, cif, options, timeout):
        self.calls.append(cif.name)
        if any(tag in cif.read_text(encoding="utf-8") for tag in self.fail_on):
            raise WorkerError("the calculation stopped (exit code 1): Unable to add edge", {"lcd": 5.0})
        return dict(self.values)


@pytest.fixture
def project(tmp_path, cif_dir):
    """Four structures: good and big pass qc; clash and two_pieces do not."""
    big = methane()
    for dx in (1.6, 3.2, 4.8):  # a chain of carbons: one connected piece of 8 atoms
        big.append("C")
        big.positions[-1] = [5.0 + dx, 5.0, 5.0]
    write_cif(cif_dir / "big.cif", big)

    def make(available=ALL, **overrides):
        data = {"project": "t", "generate": {"source": "cifs", "cif_dir": str(cif_dir)}}
        data["qc"] = {"check_valence": False, "min_distance_heavy": 0.5}
        data["describe"] = overrides
        cfg = parse_config(data)
        work = tmp_path / "work"
        table = qc.run(generate.run(cfg, work).table, cfg, work)
        plans = {p.stage: p for p in plan_stages(cfg, available)}
        return cfg, work, table, plans

    return make


def _run(cfg, work, table, plans, geo=None, xt=None, **kwargs):
    geo, xt = geo or Counting(GEO), xt or Counting(XTB)
    result = describe.run(table, cfg, work, plans=plans, backends={"geometry": geo, "xtb": xt}, **kwargs)
    return result, geo, xt


def test_descriptors_are_added_for_structures_that_passed_qc(project):
    cfg, work, table, plans = project()
    assert table.set_index("name")["qc_pass"].to_dict() == {
        "big": True,
        "clash": False,
        "good": True,
        "two_pieces": False,
    }
    result, geo, xt = _run(cfg, work, table, plans)
    t = result.table.set_index("name")
    assert list(result.table.columns[: len(table.columns)]) == list(table.columns)  # only added columns
    # composition needs no tool and no qc: every row has it
    assert (t["describe_composition_status"] == "ok").all() and t["primary_metal"].isna().all()
    assert t.loc["good", "c_fraction"] == 0.2 and t.loc["good", "n_metal_atoms"] == 0
    for name in ("good", "big"):
        assert t.loc[name, "describe_geometry_status"] == t.loc[name, "describe_xtb_status"] == "ok"
        assert t.loc[name, "lcd"] == 5.0 and t.loc[name, "xtb_gap"] == 2.5
        assert t.loc[name, "xtb_unpaired_electrons"] == 0
    for name in ("clash", "two_pieces"):
        assert t.loc[name, "describe_geometry_status"] == "skipped"
        assert t.loc[name, "describe_xtb_skip_reason"] == "did not pass qc"
        assert pd.isna(t.loc[name, "lcd"]) and pd.isna(t.loc[name, "xtb_gap"])  # nothing invented
    assert len(geo.calls) == len(xt.calls) == 2
    rep = result.reports["xtb"]
    assert rep.counts == {"ok": 2, "skipped": 2, "failed": 0} and rep.reasons == {"did not pass qc": 2}
    assert (rep.computed, rep.from_cache) == (2, 0)
    pd.testing.assert_frame_equal(read_table(write_table(result.table, work / "t.csv")), result.table)


def test_qc_failures_can_be_included_and_large_structures_are_left_out_of_xtb(project):
    cfg, work, table, plans = project(only_qc_passed=False, xtb={"max_atoms": 7})
    result, geo, _ = _run(cfg, work, table, plans)
    t = result.table.set_index("name")
    assert (t["describe_geometry_status"] == "ok").all() and len(geo.calls) == 4
    assert t.loc["big", "describe_xtb_skip_reason"] == "8 atoms; describe.xtb.max_atoms is 7"
    assert t.loc["two_pieces", "describe_xtb_status"] == "skipped"  # 10 atoms
    assert sorted(t.index[t["describe_xtb_status"] == "ok"]) == ["clash", "good"]


def test_results_are_stored_and_never_computed_twice(project):
    cfg, work, table, plans = project()
    first, _, _ = _run(cfg, work, table, plans)
    again, geo2, xt2 = _run(cfg, work, table, plans)
    assert geo2.calls == [] and xt2.calls == []
    assert again.reports["geometry"].from_cache == 2 and again.reports["geometry"].computed == 0
    pd.testing.assert_frame_equal(again.table, first.table)
    # --force computes again; another setting or another structure file is another calculation
    _, geo3, _ = _run(cfg, work, table, plans, force=True)
    assert len(geo3.calls) == 2
    cfg2, _, _, plans2 = project(geometry={"probe_radius": 1.2})
    _, geo4, xt4 = _run(cfg2, work, table, plans2)
    assert len(geo4.calls) == 2 and xt4.calls == []
    good = table.index[table["name"] == "good"][0]
    with open(work / table.loc[good, "cif_path"], "a", encoding="utf-8") as fh:
        fh.write("\n# edited\n")
    _, geo5, _ = _run(cfg2, work, table, plans2)
    assert len(geo5.calls) == 1


def test_a_failed_calculation_is_recorded_once_with_what_it_produced(project):
    cfg, work, table, plans = project()
    marker = (work / table.loc[table.index[table["name"] == "big"][0], "cif_path"]).read_text(
        encoding="utf-8"
    )
    failing = Counting(GEO, fail_on=[marker])
    result, _, _ = _run(cfg, work, table, plans, geo=failing)
    t = result.table.set_index("name")
    assert t.loc["big", "describe_geometry_status"] == "failed"
    assert t.loc["big", "describe_geometry_skip_reason"] == (
        "the calculation stopped (exit code 1): Unable to add edge"
    )
    assert t.loc["big", "lcd"] == 5.0 and pd.isna(t.loc["big", "pld"])  # partial result kept, rest empty
    assert t.loc["good", "describe_geometry_status"] == "ok" and t.loc["big", "describe_xtb_status"] == "ok"
    # the failure is stored: it is not attempted again until forced
    again, geo2, _ = _run(cfg, work, table, plans, geo=Counting(GEO))
    assert (
        geo2.calls == [] and again.table.set_index("name").loc["big", "describe_geometry_status"] == "failed"
    )
    forced, geo3, _ = _run(cfg, work, table, plans, geo=Counting(GEO), force=True, only=("geometry",))
    assert len(geo3.calls) == 2 and (forced.table["describe_geometry_status"] != "failed").all()


def test_a_backend_that_returns_too_little_is_a_failure(project):
    cfg, work, table, plans = project()
    result, _, _ = _run(cfg, work, table, plans, xt=Counting({"xtb_gap": 1.0}))
    t = result.table.set_index("name")
    assert t.loc["good", "describe_xtb_status"] == "failed"
    assert t.loc["good", "describe_xtb_skip_reason"].startswith("no result for xtb_homo")
    assert t.loc["good", "xtb_gap"] == 1.0


def test_missing_tools_skip_with_a_reason_but_stored_results_are_still_used(project):
    cfg, work, table, plans = project(available=NONE)
    result, geo, xt = _run(cfg, work, table, plans)
    assert geo.calls == [] and xt.calls == []
    t = result.table.set_index("name")
    assert t.loc["good", "describe_geometry_skip_reason"].startswith("pyzeo is not installed")
    assert t.loc["good", "describe_xtb_skip_reason"].startswith("tblite is not installed")
    assert t.loc["clash", "describe_xtb_skip_reason"] == "did not pass qc"  # the more specific reason wins
    assert result.reports["xtb"].not_run_because.startswith("tblite is not installed")
    assert (t["describe_composition_status"] == "ok").all()
    # computed where the tools exist (a cluster), assembled where they do not (a laptop)
    _, work, table, with_tools = project(available=ALL)
    _run(cfg, work, table, with_tools)
    assembled, geo2, xt2 = _run(cfg, work, table, plans)
    assert geo2.calls == [] and xt2.calls == []
    t = assembled.table.set_index("name")
    assert t.loc["good", "describe_xtb_status"] == "ok" and t.loc["good", "xtb_gap"] == 2.5
    assert assembled.reports["xtb"].from_cache == 2


def test_disabled_substages(project):
    cfg, work, table, plans = project(composition=False, geometry={"enabled": False})
    result, geo, xt = _run(cfg, work, table, plans)
    t = result.table
    assert (t["describe_composition_status"] == "skipped").all() and t["c_fraction"].isna().all()
    assert (t["describe_geometry_skip_reason"] == "disabled in the config").all() and geo.calls == []
    assert len(xt.calls) == 2


def test_only_and_blocks(project):
    cfg, work, table, plans = project(only_qc_passed=False)
    partial, geo, xt = _run(cfg, work, table, plans, only=("xtb",))
    assert geo.calls == [] and len(xt.calls) == 4
    assert (partial.table["describe_geometry_skip_reason"] == "not requested in this call").all()
    # two cluster jobs, each with half of the table, then one call that assembles
    one, geo1, _ = _run(cfg, work, table, plans, block=(1, 2), only=("geometry",))
    assert len(geo1.calls) == 2
    assert list(one.table["describe_geometry_status"]) == ["ok", "skipped", "ok", "skipped"]
    assert one.table["describe_geometry_skip_reason"].iloc[1] == "outside the block of this call"
    _, geo2, _ = _run(cfg, work, table, plans, block=(2, 2), only=("geometry",))
    assert len(geo2.calls) == 2 and set(geo1.calls).isdisjoint(geo2.calls)
    final, geo3, xt3 = _run(cfg, work, table, plans)
    assert geo3.calls == [] and xt3.calls == []
    assert (final.table["describe_geometry_status"] == "ok").all()
    assert (final.table["describe_xtb_status"] == "ok").all()
    assert len(list((work / cache.CACHE_DIR / "geometry").glob("*.json"))) == 4


def test_option_parsers():
    assert describe.parse_only(None) == describe.SUBSTAGES and describe.parse_only(" ") == describe.SUBSTAGES
    assert describe.parse_only("xtb, geometry,xtb") == ("xtb", "geometry")
    with pytest.raises(ValueError, match="unknown sub-stage"):
        describe.parse_only("xtb,zeo")
    assert describe.parse_block(None) is None and describe.parse_block("3/20") == (3, 20)
    for bad in ("0/4", "5/4", "a/b", "3", "1/0"):
        with pytest.raises(ValueError):
            describe.parse_block(bad)


def test_a_table_without_qc_or_generate_columns(tmp_path):
    cfg = parse_config({"project": "t", "generate": {"source": "cifs", "cif_dir": "x"}})
    write_cif(tmp_path / "mine" / "a.cif", methane())
    table = pd.DataFrame(
        {"cif_path": ["mine/a.cif", pd.NA], "formula": [pd.NA, "C24H12O13Zn4"]},
        index=pd.Index(["x1", "x2"], name="id"),
    )
    plans = {p.stage: p for p in plan_stages(cfg, ALL)}
    result, geo, _ = _run(cfg, tmp_path, table, plans)
    t = result.table
    assert list(t["describe_composition_status"]) == ["ok", "ok"]  # from the file, and from the formula
    assert t.loc["x1", "c_fraction"] == 0.2 and t.loc["x2", "primary_metal"] == "Zn"
    assert list(t["describe_geometry_status"]) == ["ok", "skipped"] and len(geo.calls) == 1
    assert t.loc["x2", "describe_geometry_skip_reason"] == "no structure"
    with pytest.raises(ValueError, match="already has columns written by describe"):
        describe.run(t, cfg, tmp_path, plans=plans)
    with pytest.raises(ValueError, match="needs a 'cif_path' or a 'formula' column"):
        describe.run(pd.DataFrame({"x": [1]}, index=pd.Index(["a"], name="id")), cfg, tmp_path, plans=plans)
