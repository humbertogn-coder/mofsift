from __future__ import annotations

import pandas as pd
import pytest

from mofsift import generate, qc
from mofsift.config import parse_config
from mofsift.generate import structure_path
from mofsift.io.tables import read_table, write_table
from mofsift.stages import check_contract


def _cfg(folder, **qc_keys):
    return parse_config(
        {"project": "t", "generate": {"source": "cifs", "cif_dir": str(folder)}, "qc": qc_keys}
    )


def test_checks_every_structure_and_only_adds_columns(tmp_path, cif_dir):
    cfg = _cfg(cif_dir)
    before = generate.run(cfg, tmp_path).table
    after = qc.run(before, cfg, tmp_path)
    check_contract(before, after, "qc")
    assert list(after.columns[: len(before.columns)]) == list(before.columns)
    assert set(after.columns) - set(before.columns) == set(qc.COLUMNS)
    t = after.set_index("name")
    assert (t["qc_status"] == "ok").all() and t["qc_skip_reason"].isna().all()
    assert t["qc_pass"].to_dict() == {"clash": False, "good": True, "two_pieces": False}
    assert pd.isna(t.loc["good", "qc_reasons"])
    assert "closer than 0.7 A" in t.loc["clash", "qc_reasons"]
    assert t.loc["two_pieces", "qc_reasons"] == "2 connected components"
    assert t.loc["good", "density"] == pytest.approx(0.02664, abs=1e-4) and t.loc["good", "cell_a"] == 10.0
    # user-supplied CIFs carry no bond list, expected formula or node misfit: those checks did not run
    for col in ("qc_n_nonbonded_contacts", "qc_composition_ok", "qc_node_rmsd_ok"):
        assert t[col].isna().all()


def test_assembled_structures_get_the_extra_checks(tmp_path, fake_adapter):
    cfg = parse_config(
        {
            "project": "t",
            "generate": {"source": "pormake", "topologies": ["one"], "nodes": ["A4", "B4"], "edges": ["L"]},
        }
    )
    table = generate.run(cfg, tmp_path, adapter=fake_adapter).table
    t = qc.run(table, cfg, tmp_path).set_index("name")
    built = t.loc["one__A4__L"]
    assert built["qc_status"] == "ok" and built["qc_n_nonbonded_contacts"] == 0
    assert built["qc_node_rmsd_ok"] == True  # noqa: E712 (pandas boolean)
    # one A4 (C) plus two L (H2) add up to CH4, which is what the fake build returns
    assert built["expected_formula"] == "CH4" and built["qc_composition_ok"] == True  # noqa: E712
    failed = t.loc["one__B4__L"]
    assert failed["qc_status"] == "skipped" and failed["qc_skip_reason"] == "no structure (generate: failed)"
    assert pd.isna(failed["qc_pass"]) and pd.isna(failed["density"]) and pd.isna(failed["qc_n_overlaps"])


def test_composition_mismatch_is_caught_for_assembled_structures(tmp_path, fake_adapter):
    cfg = parse_config(
        {
            "project": "t",
            "generate": {"source": "pormake", "topologies": ["one"], "nodes": ["A4"], "edges": ["L"]},
        }
    )
    table = generate.run(cfg, tmp_path, adapter=fake_adapter).table
    table["expected_formula"] = "C2H4"  # as if a block had lost atoms during assembly
    row = qc.run(table, cfg, tmp_path).iloc[0]
    assert row["qc_composition_ok"] == False and row["qc_pass"] == False  # noqa: E712
    assert row["qc_reasons"] == "formula differs from the sum of the building blocks"


def test_thresholds_from_the_config_are_used(tmp_path, cif_dir):
    cfg = _cfg(cif_dir, require_single_framework=False)
    t = qc.run(generate.run(cfg, tmp_path).table, cfg, tmp_path).set_index("name")
    assert t.loc["two_pieces", "qc_pass"] == True and t.loc["two_pieces", "qc_n_components"] == 2  # noqa: E712


def test_a_structure_that_disappeared_is_reported_not_fatal(tmp_path, cif_dir):
    cfg = _cfg(cif_dir)
    table = generate.run(cfg, tmp_path).table
    structure_path(tmp_path, table.index[0]).unlink()
    t = qc.run(table, cfg, tmp_path)
    assert t["qc_status"].iloc[0] == "failed"
    assert t["qc_skip_reason"].iloc[0].startswith("cannot check the structure: file not found")
    assert pd.isna(t["qc_pass"].iloc[0]) and (t["qc_status"].iloc[1:] == "ok").all()


def test_result_is_the_same_after_a_round_trip_and_on_a_second_run(tmp_path, cif_dir):
    cfg = _cfg(cif_dir)
    table = generate.run(cfg, tmp_path).table
    first = qc.run(table, cfg, tmp_path)
    reread = read_table(write_table(first, tmp_path / "02.csv"))
    pd.testing.assert_frame_equal(reread, first)
    pd.testing.assert_frame_equal(
        qc.run(read_table(write_table(table, tmp_path / "01.csv")), cfg, tmp_path), first
    )


def test_own_table_with_only_id_and_cif_path(tmp_path, cif_dir):
    # entering the pipeline at qc with a hand-made table
    (tmp_path / "mine").mkdir()
    (tmp_path / "mine" / "a.cif").write_bytes((cif_dir / "good.cif").read_bytes())
    table = pd.DataFrame({"cif_path": ["mine/a.cif", pd.NA]}, index=pd.Index(["x1", "x2"], name="id"))
    t = qc.run(table, _cfg(cif_dir), tmp_path)
    assert list(t["qc_status"]) == ["ok", "skipped"] and t["qc_skip_reason"].iloc[1] == "no structure"
    assert t["qc_pass"].iloc[0] == True  # noqa: E712


def test_input_errors_are_explicit(tmp_path, cif_dir):
    cfg = _cfg(cif_dir)
    with pytest.raises(ValueError, match="needs a 'cif_path' column"):
        qc.run(pd.DataFrame({"x": [1]}, index=pd.Index(["a"], name="id")), cfg, tmp_path)
    done = qc.run(generate.run(cfg, tmp_path).table, cfg, tmp_path)
    with pytest.raises(ValueError, match="already has columns written by the qc stage"):
        qc.run(done, cfg, tmp_path)
