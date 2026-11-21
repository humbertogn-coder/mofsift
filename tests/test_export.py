"""Writing a bundle: the files, the manifest, and what happens to an existing folder."""

from __future__ import annotations

import importlib.util
import json

import pandas as pd
import pytest

from mofsift.export import BundleError, read_bundle, validate_bundle, write_bundle
from mofsift.export.bundle import iter_records, prepare_out_dir
from mofsift.io.tables import sha256_file

HAS_PYARROW = importlib.util.find_spec("pyarrow") is not None


def _manifest(folder):
    return json.loads((folder / "manifest.json").read_text(encoding="utf-8"))


def test_bundle_has_a_table_records_structures_and_a_manifest(tmp_path, small_table):
    table, files = small_table
    out = tmp_path / "bundle"
    result = write_bundle(table, out, project="demo", structure_files=files, enumerated=7)
    assert sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()) == [
        "candidates.csv",
        "candidates.json",
        "manifest.json",
        "structures/a1.cif",
        "structures/b2.cif",
    ]
    manifest = _manifest(out)
    assert manifest == result.manifest
    assert manifest["kind"] == "mofsift-bundle" and manifest["project"] == "demo"
    assert manifest["counts"] == {"enumerated": 7, "selected": 3, "structures": 2}
    assert manifest["table"]["n_rows"] == 3 and manifest["records"]["n_records"] == 3
    assert manifest["structures"] == {"directory": "structures", "format": "cif", "n_files": 2}
    # every file but the manifest is listed with its size and checksum
    assert set(manifest["files"]) == {
        "candidates.csv",
        "candidates.json",
        "structures/a1.cif",
        "structures/b2.cif",
    }
    for name, entry in manifest["files"].items():
        assert entry == {"bytes": (out / name).stat().st_size, "sha256": sha256_file(out / name)}
    assert (out / "structures" / "a1.cif").read_bytes() == files["a1"].read_bytes()
    assert validate_bundle(out).ok


def test_the_manifest_explains_every_column(tmp_path, small_table):
    table, files = small_table
    manifest = write_bundle(table, tmp_path / "b", project="demo", structure_files=files).manifest
    columns = {c["name"]: c for c in manifest["columns"]}
    assert list(columns) == ["id", *table.columns]
    assert columns["xtb_gap"]["type"] == "number" and columns["xtb_gap"]["unit"] == "eV"
    assert columns["xtb_gap"]["group"] == "describe.xtb" and "proxy" in columns["xtb_gap"]["description"]
    assert columns["n_atoms"]["type"] == "integer" and columns["qc_pass"]["type"] == "boolean"
    # the statements a reader must not miss follow from what the table holds
    text = " ".join(manifest["notices"])
    assert "hypothetical" in text and "not electrical conductivity" in text and "qc_pass" in text


def test_records_are_the_table_rows_with_null_for_missing(tmp_path, small_table):
    table, files = small_table
    out = tmp_path / "b"
    write_bundle(table, out, project="demo", structure_files=files)
    text = (out / "candidates.json").read_text(encoding="utf-8")
    assert text.count("\n") == 5  # a head line, one line per record, the closing line
    payload = json.loads(text)
    assert payload["project"] == "demo" and payload["n_records"] == 3
    first, _, third = payload["records"]
    assert list(first) == ["id", *table.columns]
    assert first["id"] == "a1" and first["n_atoms"] == 5 and first["qc_pass"] is True
    assert first["xtb_gap"] == 2.8622 and first["cif_path"] == "structures/a1.cif"
    assert third["n_atoms"] is None and third["qc_pass"] is None and third["xtb_gap"] is None
    assert third["cif_path"] is None and third["generate_skip_reason"] == "cannot place the node"
    # the exported table says the same, with the same types
    back = read_bundle(out).table
    pd.testing.assert_frame_equal(back, table)


def test_cif_paths_in_a_bundle_never_point_at_nothing(tmp_path, small_table):
    table, files = small_table
    files["b2"].unlink()  # named in the table, gone from disk
    out = tmp_path / "b"
    result = write_bundle(table, out, project="demo", structure_files=files)
    back = read_bundle(out).table
    assert back["cif_path"].tolist()[0] == "structures/a1.cif" and back["cif_path"].isna().tolist()[1:] == [
        True,
        True,
    ]
    assert (
        result.n_structures == 1
        and "1 structure file(s) named in the table were not found" in result.warnings[0]
    )
    assert "b2" in result.manifest["warnings"][0]
    assert validate_bundle(out).ok


def test_structures_and_records_can_be_left_out(tmp_path, small_table):
    table, files = small_table
    out = tmp_path / "b"
    manifest = write_bundle(
        table, out, project="demo", structure_files=files, include_cifs=False, viewer_bundle=False
    ).manifest
    assert sorted(p.name for p in out.iterdir()) == ["candidates.csv", "manifest.json"]
    assert manifest["records"] is None and manifest["structures"] is None
    assert read_bundle(out).table["cif_path"].isna().all()
    assert validate_bundle(out).ok


def test_two_exports_differ_only_in_the_time_stamp(tmp_path, small_table):
    table, files = small_table
    first = write_bundle(table, tmp_path / "one", project="demo", structure_files=files).manifest
    second = write_bundle(table, tmp_path / "two", project="demo", structure_files=files).manifest
    first.pop("created_utc"), second.pop("created_utc")
    assert first == second


def test_an_older_bundle_of_the_same_project_is_replaced(tmp_path, small_table):
    table, files = small_table
    out = tmp_path / "b"
    write_bundle(table, out, project="demo", structure_files=files)
    (out / "notes.txt").write_text("mine", encoding="utf-8")
    write_bundle(table.iloc[:1], out, project="demo", structure_files=files)
    assert sorted(p.name for p in (out / "structures").iterdir()) == ["a1.cif"]  # b2.cif is gone
    assert (out / "notes.txt").read_text(encoding="utf-8") == "mine"  # a file it did not write stays
    check = validate_bundle(out)
    assert check.ok and check.notes == ["notes.txt: in the folder but not listed in the manifest"]


def test_other_folders_are_not_touched(tmp_path, small_table):
    table, files = small_table
    mine = tmp_path / "thesis"
    mine.mkdir()
    (mine / "chapter1.docx").write_text("words", encoding="utf-8")
    with pytest.raises(BundleError, match="is not empty and does not hold a mofsift bundle"):
        write_bundle(table, mine, project="demo", structure_files=files)
    assert sorted(p.name for p in mine.iterdir()) == ["chapter1.docx"]

    other = tmp_path / "other"
    write_bundle(table, other, project="first", structure_files=files)
    with pytest.raises(BundleError, match="holds the bundle of project 'first'"):
        write_bundle(table, other, project="second", structure_files=files)
    assert json.loads((other / "manifest.json").read_text(encoding="utf-8"))["project"] == "first"
    write_bundle(table, other, project="second", structure_files=files, force=True)
    assert json.loads((other / "manifest.json").read_text(encoding="utf-8"))["project"] == "second"

    a_file = tmp_path / "file.txt"
    a_file.write_text("x", encoding="utf-8")
    with pytest.raises(BundleError, match="is not a folder"):
        prepare_out_dir(a_file, "demo")


def test_ids_must_be_usable_as_file_names(tmp_path, small_table):
    table, files = small_table
    bad = table.rename(index={"a1": "../escape"})
    with pytest.raises(BundleError, match="cannot be used as file names"):
        write_bundle(bad, tmp_path / "b", project="demo", structure_files=files)
    assert not (tmp_path / "b").exists()


def test_a_number_json_cannot_hold_is_empty_in_the_records_and_reported(tmp_path, small_table):
    table, files = small_table
    table = table.copy()
    table.loc["b2", "xtb_gap"] = float("inf")
    out = tmp_path / "b"
    result = write_bundle(table, out, project="demo", structure_files=files)
    records = json.loads((out / "candidates.json").read_text(encoding="utf-8"))["records"]
    assert records[1]["xtb_gap"] is None
    assert any("non-finite" in w and "xtb_gap" in w for w in result.warnings)
    assert validate_bundle(out).ok


def test_records_are_produced_in_chunks(small_table):
    table, _ = small_table
    records = [r for r, _ in iter_records(table, chunk=2)]
    assert [r["id"] for r in records] == ["a1", "b2", "c3"] and records[2]["topology"] is None


@pytest.mark.skipif(not HAS_PYARROW, reason="pyarrow is not installed")
def test_parquet_table_keeps_values_and_types(tmp_path, small_table):
    table, files = small_table
    out = tmp_path / "b"
    manifest = write_bundle(
        table, out, project="demo", structure_files=files, table_format="parquet"
    ).manifest
    assert manifest["table"]["path"] == "candidates.parquet" and (out / "candidates.parquet").is_file()
    back = read_bundle(out).table
    expected = table.copy()
    pd.testing.assert_frame_equal(back, expected)
    assert validate_bundle(out).ok


def test_parquet_without_pyarrow_is_a_clear_error(tmp_path, small_table, monkeypatch):
    import mofsift.export.bundle as bundle

    monkeypatch.setattr(bundle, "parquet_available", lambda: False)
    table, files = small_table
    with pytest.raises(BundleError, match=r"needs pyarrow \(pip install 'mofsift\[parquet\]'\)"):
        write_bundle(table, tmp_path / "b", project="demo", structure_files=files, table_format="parquet")
    assert not (tmp_path / "b").exists()


@pytest.mark.parametrize(
    "name, safe",
    [
        ("candidates.csv", True),
        ("structures/ms-0123.cif", True),
        ("", False),
        ("/etc/passwd", False),
        ("../outside.txt", False),
        ("structures/../../x", False),
        ("structures//x.cif", False),
        ("./x", False),
        ("a\\b", False),
        ("C:/Windows/win.ini", False),  # would replace the folder it is joined to, on Windows
        ("C:x", False),
        ("a b.cif", False),
        (None, False),
    ],
)
def test_file_names_in_a_bundle_are_plain(name, safe):
    from mofsift.export import is_safe_relative

    assert is_safe_relative(name) is safe


def test_an_export_that_stops_half_way_can_be_run_again(tmp_path, small_table, monkeypatch):
    import mofsift.export.bundle as bundle

    table, files = small_table
    out = tmp_path / "b"
    write_bundle(table, out, project="demo", structure_files=files)

    def fails(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(bundle, "_write_records", fails)
    with pytest.raises(KeyboardInterrupt):
        write_bundle(table.iloc[:1], out, project="demo", structure_files=files)
    monkeypatch.undo()
    # the folder says what happened to it, and is not mistaken for a bundle
    check = validate_bundle(out)
    assert check.problems == ["manifest.json: the export that wrote this folder did not finish; run it again"]
    with pytest.raises(BundleError, match="holds the bundle of project 'demo'"):
        write_bundle(table, out, project="other", structure_files=files)
    # the next export of the project replaces it, leftovers included
    write_bundle(table.iloc[1:2], out, project="demo", structure_files=files)
    assert sorted(p.name for p in (out / "structures").iterdir()) == ["b2.cif"]
    check = validate_bundle(out)
    assert check.ok and check.notes == []


def test_nothing_is_removed_when_the_input_is_refused(tmp_path, small_table):
    table, files = small_table
    out = tmp_path / "b"
    write_bundle(table, out, project="demo", structure_files=files)
    before = sorted(p.name for p in out.rglob("*"))
    with pytest.raises(BundleError):
        write_bundle(table.rename(index={"a1": "a 1"}), out, project="demo", structure_files=files)
    with pytest.raises(BundleError):
        write_bundle(table, out, project="demo", structure_files=files, table_format="xlsx")
    assert sorted(p.name for p in out.rglob("*")) == before and validate_bundle(out).ok


def test_an_older_manifest_in_bad_shape_does_not_stop_the_export(tmp_path, small_table):
    table, files = small_table
    out = tmp_path / "b"
    write_bundle(table, out, project="demo", structure_files=files)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"] = None
    (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    write_bundle(table, out, project="demo", structure_files=files)
    assert validate_bundle(out).ok


def test_a_config_value_json_cannot_hold_is_written_as_text(tmp_path, small_table):
    table, files = small_table
    out = tmp_path / "b"
    manifest = write_bundle(
        table,
        out,
        project="demo",
        structure_files=files,
        config={"qc": {"max_node_rmsd": float("inf"), "low": float("-inf")}},
        stages=[{"stage": "qc", "details": {"mean": float("nan")}}],
    ).manifest
    assert manifest["config"]["qc"] == {"max_node_rmsd": "Infinity", "low": "-Infinity"}
    assert manifest["stages"][0]["details"]["mean"] == "NaN"
    assert validate_bundle(out).ok


def test_columns_of_a_hand_made_table_get_the_type_of_their_values(tmp_path, small_table):
    table, files = small_table
    table = table.copy()
    table["reports"] = [4.0, float("nan"), 2.0]  # a count with an empty cell, as a CSV reader returns it
    table["seen"] = pd.Series([True, None, False], index=table.index, dtype=object)
    table["note"] = pd.Series(["x", "", None], index=table.index, dtype=object)
    out = tmp_path / "b"
    manifest = write_bundle(table, out, project="demo", structure_files=files).manifest
    types = {c["name"]: c["type"] for c in manifest["columns"]}
    assert (types["reports"], types["seen"], types["note"]) == ("integer", "boolean", "string")
    records = json.loads((out / "candidates.json").read_text(encoding="utf-8"))["records"]
    assert [r["reports"] for r in records] == [4, None, 2]
    assert [r["seen"] for r in records] == [True, None, False]
    # empty text is what a table file writes for a missing value, so it is one in the records too
    assert [r["note"] for r in records] == ["x", None, None]
    assert validate_bundle(out).ok
