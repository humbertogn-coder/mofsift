from __future__ import annotations

from mofsift.config import parse_config
from mofsift.plan import plan_stages

ALL = {"pormake": True, "pyzeo": True, "tblite": True, "zeo++ (network)": True}
NONE = dict.fromkeys(ALL, False)


def _plan(data, available):
    return {p.stage: p for p in plan_stages(parse_config(data), available)}


def test_everything_runs_when_all_tools_are_present(example_dict):
    plan = _plan(example_dict, ALL)
    assert list(plan) == [
        "generate",
        "qc",
        "describe.composition",
        "describe.geometry",
        "describe.xtb",
        "score",
        "filter",
        "export",
    ]
    assert all(p.action == "run" for p in plan.values())


def test_missing_optional_tools_are_skipped_with_a_reason(example_dict):
    plan = _plan(example_dict, {**NONE, "pormake": True})
    assert (
        plan["describe.geometry"].action == "skip"
        and "pyzeo is not installed" in plan["describe.geometry"].reason
    )
    assert plan["describe.xtb"].action == "skip" and "tblite is not installed" in plan["describe.xtb"].reason
    assert plan["qc"].action == plan["describe.composition"].action == plan["export"].action == "run"


def test_generate_without_pormake_cannot_run_and_says_what_to_do(example_dict):
    plan = _plan(example_dict, NONE)
    assert plan["generate"].action == "error"
    assert "mofsift[generate]" in plan["generate"].reason and "source: cifs" in plan["generate"].reason


def test_cif_source_does_not_need_pormake():
    plan = _plan({"project": "t", "generate": {"source": "cifs", "cif_dir": "structures"}}, NONE)
    assert plan["generate"].action == "run"


def test_enabled_true_obeys_on_missing(minimal_dict):
    minimal_dict["describe"] = {"xtb": {"enabled": True, "on_missing": "skip"}}
    assert _plan(minimal_dict, {**NONE, "pormake": True})["describe.xtb"].action == "skip"
    minimal_dict["describe"] = {"xtb": {"enabled": True, "on_missing": "error"}}
    p = _plan(minimal_dict, {**NONE, "pormake": True})["describe.xtb"]
    assert p.action == "error" and "on_missing: error" in p.reason
    assert _plan(minimal_dict, ALL)["describe.xtb"].action == "run"


def test_auto_never_errors_even_with_on_missing_error(minimal_dict):
    minimal_dict["describe"] = {"geometry": {"enabled": "auto", "on_missing": "error"}}
    assert _plan(minimal_dict, {**NONE, "pormake": True})["describe.geometry"].action == "skip"


def test_disabled_stages_are_skipped_even_when_the_tool_exists(minimal_dict):
    minimal_dict["describe"] = {
        "composition": False,
        "geometry": {"enabled": False},
        "xtb": {"enabled": False},
    }
    minimal_dict["score"] = {"components": []}
    plan = _plan(minimal_dict, ALL)
    for name in ("describe.composition", "describe.geometry", "describe.xtb"):
        assert plan[name].action == "skip" and plan[name].reason == "disabled in the config"
    assert plan["score"].action == "skip" and plan["filter"].action == "skip"


def test_geometry_tool_choice_selects_the_adapter(minimal_dict):
    minimal_dict["describe"] = {"geometry": {"tool": "zeopp"}}
    avail = {"pormake": True, "pyzeo": True, "tblite": False, "zeo++ (network)": False}
    p = _plan(minimal_dict, avail)["describe.geometry"]
    assert p.action == "skip" and "zeo++ (network) is not installed" in p.reason
    assert _plan(minimal_dict, {**avail, "zeo++ (network)": True})["describe.geometry"].action == "run"
