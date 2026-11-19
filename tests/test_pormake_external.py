"""Tests that build real structures with PORMAKE. Skipped when PORMAKE is not installed."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

import mofsift.cli as cli
from mofsift import generate, qc
from mofsift.config import parse_config
from mofsift.generate import GenerateError
from mofsift.io.structures import read_structure
from mofsift.io.tables import read_table, sha256_file

pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(importlib.util.find_spec("pormake") is None, reason="PORMAKE is not installed"),
]


def _cfg(**gen):
    base = {"source": "pormake", "topologies": ["pcu"], "nodes": ["N16"], "edges": ["E14"]}
    return parse_config({"project": "t", "generate": {**base, **gen}})


def test_mof5_is_built_checked_and_reproducible(tmp_path):
    cfg = _cfg()
    table = generate.run(cfg, tmp_path / "a").table
    assert list(table.index) == ["ms-678e931c571c8679"]  # the ID promised by mofsift.ids
    row = table.iloc[0]
    assert (row["name"], row["formula"], row["n_atoms"]) == ("pcu__N16__E14", "C24H12O13Zn4", 53)
    assert row["expected_formula"] == row["formula"] and row["node_rmsd"] < 0.01
    atoms = read_structure(tmp_path / "a" / row["cif_path"])
    assert atoms.get_chemical_formula() == "C24H12O13Zn4" and atoms.cell.cellpar()[0] == pytest.approx(13.246)
    checked = qc.run(table, cfg, tmp_path / "a").iloc[0]
    assert checked["qc_pass"] and checked["qc_n_nonbonded_contacts"] == 0 and checked["qc_composition_ok"]
    assert checked["qc_n_components"] == 1 and checked["density"] == pytest.approx(0.55, abs=0.01)
    # a second, independent build gives the same files
    generate.run(cfg, tmp_path / "b")
    for name in ("ms-678e931c571c8679.cif", "ms-678e931c571c8679.build.json"):
        assert sha256_file(tmp_path / "a" / "structures" / name) == sha256_file(
            tmp_path / "b" / "structures" / name
        )


def test_a_node_that_does_not_fit_its_vertex_is_flagged(tmp_path):
    # a square paddlewheel on the tetrahedral vertex of dia: same coordination number, wrong shape
    cfg = _cfg(topologies=["dia"], nodes=["N409"])
    table = qc.run(generate.run(cfg, tmp_path).table, cfg, tmp_path)
    row = table.iloc[0]
    assert row["generate_status"] == "ok" and row["node_rmsd"] > 0.3
    assert not row["qc_pass"] and not row["qc_node_rmsd_ok"] and "node misfit" in row["qc_reasons"]


def test_nodes_are_matched_to_vertices_by_coordination_number(tmp_path):
    # N16 is 6-connected: it is used on pcu and never forced onto the 4-connected vertices of dia
    res = generate.run(_cfg(topologies=["pcu", "dia"], max_atoms=60), tmp_path)
    assert list(res.table["topology"]) == ["pcu"]
    assert any("topology dia: no configured node with 4 connection points" in n for n in res.notes)


def test_nodes_can_be_connected_directly(tmp_path):
    # tbo with a 3-connected organic node and the Cu paddlewheel, no edge block
    cfg = _cfg(topologies=["tbo"], nodes=["N10", "N409"], edges=[])
    row = qc.run(generate.run(cfg, tmp_path).table, cfg, tmp_path).iloc[0]
    assert row["name"] == "tbo__N10+N409" and row["formula"] == "C288H96Cu48O192"
    assert row["expected_formula"] == row["formula"] and row["qc_composition_ok"]


def test_unknown_names_get_suggestions(tmp_path):
    with pytest.raises(GenerateError) as err:
        generate.run(_cfg(topologies=["pcuu"], nodes=["N16", "N99999"], edges=["N16"]), tmp_path)
    text = str(err.value)
    assert "unknown topology 'pcuu' (did you mean" in text and "pcu" in text
    assert "unknown building block 'N99999'" in text
    assert "generate.edges: N16 has 6 connection points" in text


def test_user_building_blocks_take_precedence_and_change_the_id(tmp_path):
    import pormake

    blocks = tmp_path / "blocks"
    blocks.mkdir()
    bundled = Path(pormake.__file__).parent / "database" / "bbs" / "E14.xyz"
    (blocks / "MYEDGE.xyz").write_bytes(bundled.read_bytes())
    cfg = _cfg(edges=["MYEDGE", "E14"], building_block_dir="blocks")
    table = generate.run(cfg, tmp_path / "work", config_dir=tmp_path).table
    assert list(table["formula"]) == ["C24H12O13Zn4"] * 2 and (table["generate_status"] == "ok").all()
    assert table.index[1] == "ms-678e931c571c8679" and table.index[0] != table.index[1]
    # the same name with other content is another candidate
    (blocks / "MYEDGE.xyz").write_bytes(bundled.read_bytes() + b"\n")
    assert generate.run(cfg, tmp_path / "w2", config_dir=tmp_path).table.index[0] == table.index[0]
    other = Path(pormake.__file__).parent / "database" / "bbs" / "E34.xyz"
    (blocks / "MYEDGE.xyz").write_bytes(other.read_bytes())
    changed = generate.run(cfg, tmp_path / "w3", config_dir=tmp_path).table
    assert changed.index[0] != table.index[0] and changed["formula"].iloc[0] == "C42H24O13Zn4"


def test_linkers_from_smiles_are_built_like_the_bundled_blocks(tmp_path):
    cfg = _cfg(
        edges=["E14"],
        linkers={
            "smiles": {
                "bdc": "OC(=O)c1ccc(cc1)C(=O)O",
                "core": "[*]c1ccc([*])cc1",
                "bdc_nh2": "Nc1cc(C(=O)O)ccc1C(=O)O",
            }
        },
    )
    checked = qc.run(generate.run(cfg, tmp_path).table, cfg, tmp_path)
    assert checked.index.is_unique and len(checked) == 4
    table = checked.set_index("name")
    assert list(table.index) == ["pcu__N16__E14", "pcu__N16__bdc", "pcu__N16__core", "pcu__N16__bdc_nh2"]
    assert (table["generate_status"] == "ok").all() and table["qc_pass"].all()
    # terephthalic acid from SMILES gives the framework that PORMAKE's own block E14 gives (MOF-5)
    for name in ("pcu__N16__bdc", "pcu__N16__core"):
        assert table.loc[name, "formula"] == table.loc["pcu__N16__E14", "formula"] == "C24H12O13Zn4"
        assert table.loc[name, "qc_n_nonbonded_contacts"] == 0 and table.loc[name, "qc_composition_ok"]
        assert table.loc[name, "node_rmsd"] < 0.01
        # a real benzene ring is smaller than PORMAKE's idealised one, so the cell is a little smaller
        assert 12.8 < table.loc[name, "cell_a"] < table.loc["pcu__N16__E14", "cell_a"]
    assert table.loc["pcu__N16__bdc_nh2", "formula"] == "C24H15N3O13Zn4"
    # the block PORMAKE read is the one mofsift wrote
    assert (tmp_path / "blocks" / "bdc.xyz").read_text(encoding="utf-8").startswith("12\n")
    # same result on a second, independent run
    again = generate.run(cfg, tmp_path / "again").table
    for record_id in again.index:
        name = f"{record_id}.cif"
        assert sha256_file(tmp_path / "structures" / name) == sha256_file(
            tmp_path / "again" / "structures" / name
        )


def test_a_tritopic_linker_from_smiles_is_used_as_a_node(tmp_path):
    # trimesic acid on the 3-connected vertices of tbo, Cu paddlewheel on the 4-connected ones: HKUST-1
    cfg = _cfg(
        topologies=["tbo"],
        nodes=["N409"],
        edges=[],
        linkers={"smiles": {"btc": "OC(=O)c1cc(cc(c1)C(=O)O)C(=O)O"}},
    )
    row = qc.run(generate.run(cfg, tmp_path).table, cfg, tmp_path).iloc[0]
    assert row["name"] == "tbo__btc+N409" and row["formula"] == "C288H96Cu48O192"
    assert row["qc_pass"] and row["qc_n_components"] == 1 and row["node_rmsd"] < 0.3
    assert row["linker_smiles"] == "O=C(O)c1cc(C(=O)O)cc(C(=O)O)c1"


def test_linker_names_cannot_shadow_pormake_blocks(tmp_path):
    cfg = _cfg(linkers={"smiles": {"E34": "OC(=O)c1ccc(cc1)C(=O)O"}})
    with pytest.raises(GenerateError, match="already used by a building block: E34"):
        generate.run(cfg, tmp_path)


def test_command_line_run_is_quiet_and_leaves_no_log_file(tmp_path):
    # in a fresh process, so that PORMAKE is really imported for the first time
    env = {**os.environ, "PYTHONWARNINGS": "default"}
    init = subprocess.run(
        [sys.executable, "-m", "mofsift.cli", "init"], cwd=tmp_path, capture_output=True, text=True, env=env
    )
    assert init.returncode == 0, init.stderr
    for command in (["doctor", "mofsift.yaml"], ["run", "mofsift.yaml"]):
        res = subprocess.run(
            [sys.executable, "-m", "mofsift.cli", *command],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            env=env,
        )
        assert res.returncode == 0, res.stdout + res.stderr
        assert ">>>" not in res.stdout + res.stderr  # PORMAKE's build log
        assert "Warning" not in res.stderr
    assert (
        "2 candidate(s)"
        in subprocess.run(
            [sys.executable, "-m", "mofsift.cli", "doctor", "mofsift.yaml"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        ).stdout
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == ["mofsift.yaml", "work"]  # no runtime.log
    table = read_table(tmp_path / "work" / "02_qc.csv")
    assert list(table["name"]) == ["pcu__N16__E14", "pcu__N16__E34"] and table["qc_pass"].all()


def test_doctor_reports_bad_names_before_anything_is_built(tmp_path):
    config = tmp_path / "c.yaml"
    config.write_text(
        "project: t\ngenerate: {source: pormake, topologies: [pcu], nodes: [N16], edges: [E99999]}\n",
        encoding="utf-8",
    )
    res = CliRunner().invoke(cli.app, ["doctor", str(config)])
    assert res.exit_code == 1 and "unknown building block 'E99999'" in res.output
    assert not (tmp_path / "work").exists()
