"""The describe stage with the real tools (pyzeo, tblite). Skipped when they are not installed."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys

import pytest

from mofsift import describe, generate, qc
from mofsift.config import parse_config
from mofsift.io.tables import read_table, sha256_file
from mofsift.plan import plan_stages

needed = ("pormake", "pyzeo", "tblite")
pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(
        any(importlib.util.find_spec(m) is None for m in needed), reason="needs PORMAKE, pyzeo and tblite"
    ),
]
AVAILABLE = {"pormake": True, "pyzeo": True, "tblite": True}


def _cfg(**generate_keys):
    base = {"source": "pormake", "topologies": ["pcu"], "nodes": ["N16"], "edges": ["E14"]}
    return parse_config({"project": "t", "generate": {**base, **generate_keys}})


def _describe(cfg, work, **kwargs):
    table = qc.run(generate.run(cfg, work).table, cfg, work)
    plans = {p.stage: p for p in plan_stages(cfg, AVAILABLE)}
    return describe.run(table, cfg, work, plans=plans, **kwargs)


def test_mof5_descriptors(tmp_path):
    result = _describe(_cfg(), tmp_path)
    row = result.table.iloc[0]
    assert row["describe_geometry_status"] == row["describe_xtb_status"] == "ok"
    # Zn4O(BDC)3 as assembled by PORMAKE (cell 13.246 A): a large open pore. The cavity size depends
    # on how PORMAKE turns the phenyl rings about their axis, which varies with its numerical libraries.
    assert 12.5 < row["lcd"] < 13.5 and row["pld"] == pytest.approx(9.94, abs=0.1)
    assert row["lcd"] >= row["lcd_free_path"] >= row["pld"]
    assert 3000 < row["accessible_surface_m2_g"] < 4500 and 0.3 < row["accessible_volume_fraction"] < 0.5
    # periodic GFN1-xTB, closed shell
    assert row["xtb_gap"] == pytest.approx(2.86, abs=0.05) and row["xtb_unpaired_electrons"] == 0
    assert row["xtb_lumo"] - row["xtb_homo"] == pytest.approx(row["xtb_gap"], abs=1e-3)
    assert row["primary_metal"] == "Zn"
    assert result.reports["xtb"].computed == 1
    # nothing is computed twice
    plans = {p.stage: p for p in plan_stages(_cfg(), AVAILABLE)}
    again = describe.run(read_table_for(tmp_path, result), _cfg(), tmp_path, plans=plans)
    assert again.reports["xtb"].computed == 0 and again.reports["geometry"].from_cache == 1
    assert again.table.iloc[0]["xtb_gap"] == row["xtb_gap"]


def read_table_for(work, result):
    """The qc part of a described table (what describe takes as input)."""
    own = [c for cols in describe.VALUE_COLUMNS.values() for c in cols]
    own += [c for c in result.table.columns if c.startswith("describe_")]
    return result.table.drop(columns=own)


def test_xtb_settings_reach_the_calculation(tmp_path):
    cfg = parse_config(
        {
            "project": "t",
            "generate": {"source": "pormake", "topologies": ["pcu"], "nodes": ["N16"], "edges": ["E14"]},
            "describe": {"geometry": {"enabled": False}, "xtb": {"max_iterations": 2}},
        }
    )
    row = _describe(cfg, tmp_path).table.iloc[0]
    assert row["describe_xtb_status"] == "failed"
    assert row["describe_xtb_skip_reason"] == "xTB failed: SCF not converged in 2 cycles"
    assert row["describe_geometry_skip_reason"] == "disabled in the config"


def test_a_zeopp_crash_costs_one_row_not_the_run(tmp_path):
    # With the OCC setting Zeo++ ends the process on this structure. It must be one failed row.
    cfg = parse_config(
        {
            "project": "t",
            "seed": 42,
            "generate": {
                "source": "pormake",
                "topologies": ["pcu"],
                "nodes": ["N16"],
                "edges": ["E14"],
                "linkers": {"smiles": {"ndc": "OC(=O)c1ccc2cc(ccc2c1)C(=O)O"}},
            },
            "describe": {"xtb": {"enabled": False}},
        }
    )
    table = _describe(cfg, tmp_path).table.set_index("name")
    assert table.loc["pcu__N16__E14", "describe_geometry_status"] == "ok"
    status = table.loc["pcu__N16__ndc", "describe_geometry_status"]
    assert status in ("ok", "failed")  # depends on the Zeo++ build; either way the run finished
    if status == "failed":
        assert table.loc["pcu__N16__ndc", "describe_geometry_skip_reason"].startswith(
            "the calculation stopped"
        )


def test_command_line_blocks_give_the_same_table_as_one_run(tmp_path):
    env = {**os.environ, "PYTHONWARNINGS": "default"}

    def mofsift(*args, cwd):
        res = subprocess.run(
            [sys.executable, "-m", "mofsift.cli", *args], cwd=cwd, capture_output=True, text=True, env=env
        )
        assert res.returncode == 0, res.stdout + res.stderr
        return res

    one, two = tmp_path / "one", tmp_path / "two"
    for folder in (one, two):
        folder.mkdir()
        mofsift("init", cwd=folder)
    direct = mofsift("run", "mofsift.yaml", cwd=one)
    assert "WARNING" not in direct.stderr and "describe.xtb: 2 row(s): 2 ok" in direct.stdout
    mofsift("generate", "mofsift.yaml", "-q", cwd=two)
    mofsift("qc", "mofsift.yaml", cwd=two)
    for block in ("1/2", "2/2"):
        res = mofsift("describe", "mofsift.yaml", "--block", block, cwd=two)
        assert "computed 1" in res.stdout and not (two / "work" / "03_describe.csv").exists()
    assembled = mofsift("describe", "mofsift.yaml", cwd=two)
    assert "computed 0, reused 2" in assembled.stdout
    assert sha256_file(one / "work" / "03_describe.csv") == sha256_file(two / "work" / "03_describe.csv")
    table = read_table(one / "work" / "03_describe.csv")
    assert table["xtb_gap"].notna().all() and table["lcd"].notna().all()
    assert sorted(p.name for p in one.iterdir()) == ["mofsift.yaml", "work"]
