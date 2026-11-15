from __future__ import annotations

import json

import pandas as pd
import pytest

from conftest import methane, write_cif
from mofsift import generate, qc
from mofsift.config import parse_config
from mofsift.generate import GenerateError, build_record_path, structure_path
from mofsift.ids import file_record_id, record_id
from mofsift.io.tables import read_table, write_table


def _cfg(**gen):
    base = {"source": "pormake", "topologies": ["one"], "nodes": ["A4"], "edges": ["L"]}
    return parse_config({"project": "t", "generate": {**base, **gen}})


# ------------------------------------------------------------------ pormake
def test_table_from_assembled_candidates(tmp_path, fake_adapter):
    cfg = _cfg(topologies=["one", "two"], nodes=["A4", "T3"], edges=["L", "M"])
    seen = []
    res = generate.run(cfg, tmp_path, adapter=fake_adapter, progress=lambda *a: seen.append(a))
    t = res.table
    assert list(t["name"]) == ["one__A4__L", "one__A4__M", "two__T3+A4__L", "two__T3+A4__M"]
    assert t.index.name == "id" and t.index[0] == record_id("one", ["A4"], ["L"])
    assert (t["generate_status"] == "ok").all() and t["generate_skip_reason"].isna().all()
    assert (t["source"] == "pormake").all() and t["source_file"].isna().all()
    row = t.iloc[2]
    assert (row["topology"], row["nodes"], row["edges"]) == ("two", "T3+A4", "L")
    assert row["expected_formula"] == "C7H24" and row["formula"] == "CH4" and row["n_atoms"] == 5
    assert row["node_rmsd"] == pytest.approx(0.012346)
    assert row["cif_path"] == f"structures/{t.index[2]}.cif"  # relative to the work folder, forward slashes
    assert all((tmp_path / p).is_file() for p in t["cif_path"])
    assert [s[:3] for s in seen] == [(i, 4, n) for i, n in enumerate(t["name"], start=1)]
    record = json.loads(build_record_path(tmp_path, t.index[0]).read_text(encoding="utf-8"))
    assert record["bonds"] == [[0, 1], [0, 2], [0, 3], [0, 4]] and record["nodes"] == ["A4"]


def test_the_table_survives_a_round_trip_through_disk(tmp_path, fake_adapter):
    table = generate.run(_cfg(edges=[]), tmp_path, adapter=fake_adapter).table
    assert pd.isna(table["edges"].iloc[0]) and table["name"].iloc[0] == "one__A4"
    pd.testing.assert_frame_equal(read_table(write_table(table, tmp_path / "t.csv")), table)


def test_failed_builds_and_oversized_candidates_stay_in_the_table(tmp_path, fake_adapter):
    cfg = _cfg(topologies=["one", "two"], nodes=["A4", "B4", "T3"], max_atoms=10)
    t = generate.run(cfg, tmp_path, adapter=fake_adapter).table
    by_name = t.set_index("name")
    assert by_name.loc["one__A4__L", "generate_status"] == "ok"
    failed = by_name.loc["one__B4__L"]
    assert failed["generate_status"] == "failed"
    assert failed["generate_skip_reason"] == "build failed: RuntimeError: cannot place the node"
    skipped = by_name.loc["two__T3+A4__L"]
    assert skipped["generate_status"] == "skipped"
    assert skipped["generate_skip_reason"] == "31 atoms expected; generate.max_atoms is 10"
    for row in (failed, skipped):  # nothing invented for structures that do not exist
        assert pd.isna(row["cif_path"]) and pd.isna(row["n_atoms"]) and pd.isna(row["formula"])
        assert pd.isna(row["node_rmsd"])
    assert len(list((tmp_path / "structures").glob("*.cif"))) == 1


def test_existing_structures_are_reused_unless_forced(tmp_path, fake_adapter):
    cfg = _cfg(edges=["L", "M"])
    first = generate.run(cfg, tmp_path, adapter=fake_adapter)
    assert first.reused == 0 and len(fake_adapter.built) == 2
    second = generate.run(cfg, tmp_path, adapter=fake_adapter)
    assert second.reused == 2 and len(fake_adapter.built) == 2  # nothing was built again
    pd.testing.assert_frame_equal(second.table, first.table)
    # a missing or unreadable build record means "build again"
    build_record_path(tmp_path, first.table.index[0]).write_text("{not json", encoding="utf-8")
    structure_path(tmp_path, first.table.index[1]).unlink()
    third = generate.run(cfg, tmp_path, adapter=fake_adapter)
    assert third.reused == 0 and len(fake_adapter.built) == 4
    pd.testing.assert_frame_equal(third.table, first.table)
    forced = generate.run(cfg, tmp_path, adapter=fake_adapter, force=True)
    assert forced.reused == 0 and len(fake_adapter.built) == 6


def test_unknown_names_and_wrong_edges_are_config_errors(tmp_path, fake_adapter):
    cfg = _cfg(topologies=["one", "nope"], nodes=["A4", "Q9"], edges=["T3"])
    with pytest.raises(GenerateError) as err:
        generate.run(cfg, tmp_path, adapter=fake_adapter)
    text = str(err.value)
    assert "generate.topologies: unknown topology 'nope'" in text
    assert "generate.nodes: unknown building block 'Q9'" in text
    assert "generate.edges: T3 has 3 connection points" in text
    assert not (tmp_path / "structures").exists()  # nothing was started


def test_no_candidate_at_all_is_an_error_and_partial_misfits_are_notes(tmp_path, fake_adapter):
    with pytest.raises(GenerateError, match="does not define any candidate"):
        generate.run(_cfg(nodes=["T3"]), tmp_path, adapter=fake_adapter)
    res = generate.run(_cfg(topologies=["one", "two"]), tmp_path, adapter=fake_adapter)
    assert len(res.table) == 1 and any("topology two" in n for n in res.notes)


def test_preview_counts_without_building(tmp_path, fake_adapter, cif_dir):
    cfg = _cfg(topologies=["one", "two"], nodes=["A4", "T3"], edges=["L", "M"])
    assert generate.preview(cfg, tmp_path, adapter=fake_adapter) == (4, [])
    assert fake_adapter.built == []
    cifs = parse_config({"project": "t", "generate": {"source": "cifs", "cif_dir": str(cif_dir)}})
    assert generate.preview(cifs, tmp_path) == (3, [])


# --------------------------------------------------------------------- cifs
def _cif_cfg(folder, **gen):
    return parse_config({"project": "t", "generate": {"source": "cifs", "cif_dir": str(folder), **gen}})


def test_table_from_existing_cifs(tmp_path, cif_dir):
    work = tmp_path / "work"
    t = generate.run(_cif_cfg(cif_dir), work).table
    assert list(t["name"]) == ["clash", "good", "two_pieces"]  # sorted by relative path
    assert list(t["source_file"]) == ["clash.cif", "good.cif", "sub/two_pieces.cif"]
    assert (t["source"] == "cifs").all() and (t["generate_status"] == "ok").all()
    assert list(t["n_atoms"]) == [6, 5, 10] and t.loc[t["name"] == "good", "formula"].item() == "CH4"
    for col in ("topology", "nodes", "edges", "expected_formula", "node_rmsd"):
        assert t[col].isna().all()
    good = t.index[t["name"] == "good"][0]
    original = (cif_dir / "good.cif").read_bytes()
    assert good == file_record_id(original) and good.startswith("msf-")
    assert structure_path(work, good).read_bytes() == original  # copied as it is
    assert not build_record_path(work, good).exists()


def test_relative_cif_dir_is_resolved_against_the_config_folder(tmp_path, cif_dir):
    cfg = parse_config({"project": "t", "generate": {"source": "cifs", "cif_dir": "cifs"}})
    assert len(generate.run(cfg, tmp_path / "work", config_dir=tmp_path).table) == 3


def test_unreadable_duplicate_and_oversized_cifs(tmp_path, cif_dir):
    (cif_dir / "broken.cif").write_text("data_x\n_cell_length_a oops\n", encoding="utf-8")
    (cif_dir / "empty.cif").write_text("", encoding="utf-8")
    unix = (cif_dir / "good.cif").read_bytes().replace(b"\r\n", b"\n")
    (cif_dir / "good.cif").write_bytes(unix)
    (cif_dir / "zz_copy_of_good.cif").write_bytes(unix.replace(b"\n", b"\r\n"))  # Windows line endings
    big = methane() + methane(center=(2.0, 2.0, 2.0)) + methane(center=(8.0, 8.0, 8.0))
    write_cif(cif_dir / "big.cif", big)
    res = generate.run(_cif_cfg(cif_dir, max_atoms=12), tmp_path / "work")
    t = res.table.set_index("name")
    assert set(t.index) == {"big", "broken", "clash", "empty", "good", "two_pieces"}
    assert t.loc["broken", "generate_status"] == "failed"
    assert t.loc["broken", "generate_skip_reason"].startswith("cannot read the CIF: ")
    assert t.loc["empty", "generate_status"] == "failed"
    assert t.loc["big", "generate_status"] == "skipped"
    assert t.loc["big", "generate_skip_reason"] == "15 atoms; generate.max_atoms is 12"
    assert t.loc["big", "n_atoms"] == 15 and pd.isna(t.loc["big", "cif_path"])
    assert res.notes == ["zz_copy_of_good.cif: same content as good.cif; kept once"]
    assert res.table.index.is_unique


def test_missing_or_empty_cif_folder_is_a_config_error(tmp_path):
    with pytest.raises(GenerateError, match="folder not found"):
        generate.run(_cif_cfg(tmp_path / "nowhere"), tmp_path / "work")
    (tmp_path / "empty").mkdir()
    with pytest.raises(GenerateError, match=r"no \.cif files"):
        generate.run(_cif_cfg(tmp_path / "empty"), tmp_path / "work")


ROCK_SALT = """\
data_NaCl
_symmetry_space_group_name_H-M 'F m -3 m'
_symmetry_Int_Tables_number 225
_cell_length_a 5.64
_cell_length_b 5.64
_cell_length_c 5.64
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
Na1 Na 0.0 0.0 0.0
Cl1 Cl 0.5 0.5 0.5
"""


def test_a_cif_with_symmetry_is_expanded_to_the_full_cell(tmp_path):
    folder = tmp_path / "cifs"
    folder.mkdir()
    (folder / "nacl.cif").write_text(ROCK_SALT, encoding="utf-8")
    cfg = _cif_cfg(folder)
    table = generate.run(cfg, tmp_path / "work").table
    assert table["n_atoms"].item() == 8 and table["formula"].item() == "Cl4Na4"
    row = qc.run(table, cfg, tmp_path / "work").iloc[0]
    assert row["qc_pass"] and row["qc_n_components"] == 1
    assert row["qc_min_distance"] == pytest.approx(2.82) and row["density"] == pytest.approx(2.16, abs=0.01)
