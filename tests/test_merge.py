"""merge_bundles: union of records and columns, missing values filled, conflicts named."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from mofsift.export import MergeError, merge_bundles, read_bundle, validate_bundle, write_bundle


def _bundle(folder, table, files, project):
    write_bundle(table, folder, project=project, structure_files=files, enumerated=len(table))
    return folder


@pytest.fixture
def two(tmp_path, small_table):
    """Two bundles with one record in common (b2) and one of their own each."""
    table, files = small_table
    first = _bundle(tmp_path / "first", table.loc[["a1", "b2"]], files, "first")
    second = _bundle(tmp_path / "second", table.loc[["b2", "c3"]], files, "second")
    return first, second, table


def test_records_and_columns_are_united(tmp_path, two):
    first, second, table = two
    result = merge_bundles([first, second], tmp_path / "out", project="both")
    merged = read_bundle(tmp_path / "out")
    assert merged.table.index.tolist() == ["a1", "b2", "c3"]  # first bundle first, then the new records
    pd.testing.assert_frame_equal(merged.table, table)
    assert result.n_records == 3 and result.n_structures == 2
    assert sorted(p.name for p in (tmp_path / "out" / "structures").iterdir()) == ["a1.cif", "b2.cif"]
    assert validate_bundle(tmp_path / "out").ok


def test_the_manifest_says_where_the_records_come_from(tmp_path, two):
    first, second, _ = two
    manifest = merge_bundles([first, second], tmp_path / "out", project="both").manifest
    assert manifest["project"] == "both" and manifest["config"] is None and manifest["config_hash"] is None
    assert manifest["counts"] == {"enumerated": None, "selected": 3, "structures": 2}
    assert [s["project"] for s in manifest["sources"]] == ["first", "second"]
    assert [s["selected"] for s in manifest["sources"]] == [2, 2]
    for source, folder in zip(manifest["sources"], (first, second), strict=True):
        original = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        assert source["created_utc"] == original["created_utc"]
    # a merged bundle can be merged again; the history is kept
    third = merge_bundles([tmp_path / "out", first], tmp_path / "again", project="again").manifest
    assert [s["project"] for s in third["sources"]] == ["both", "first"]
    assert [s["project"] for s in third["sources"][0]["sources"]] == ["first", "second"]
    assert validate_bundle(tmp_path / "again").ok


def test_a_value_one_bundle_lacks_is_taken_from_the_other(tmp_path, small_table):
    table, files = small_table
    laptop = table.loc[["a1", "b2"]].copy()  # no xTB here: the stage was skipped
    laptop["xtb_gap"] = pd.array([pd.NA, pd.NA], dtype="Float64")
    laptop["describe_xtb_status"] = "skipped"
    laptop["describe_xtb_skip_reason"] = "tblite is not installed"
    cluster = table.loc[["a1", "b2"]].drop(columns=["qc_pass"])  # xTB ran; a column is absent
    first = _bundle(tmp_path / "laptop", laptop, files, "run")
    second = _bundle(tmp_path / "cluster", cluster, files, "run")
    merge_bundles([first, second], tmp_path / "out", project="run")
    merged = read_bundle(tmp_path / "out").table
    assert merged["xtb_gap"].tolist() == [2.8622, 0.1]
    assert merged["describe_xtb_status"].tolist() == ["ok", "ok"]  # the status of the run that computed it
    assert merged["describe_xtb_skip_reason"].isna().all()
    assert merged["qc_pass"].tolist() == [True, False]  # the column only the first bundle has
    assert validate_bundle(tmp_path / "out").ok
    # the order of the bundles does not change the result
    merge_bundles([second, first], tmp_path / "swapped", project="run")
    swapped = read_bundle(tmp_path / "swapped").table
    pd.testing.assert_frame_equal(swapped[merged.columns], merged)


def test_different_values_for_the_same_record_stop_the_merge(tmp_path, two, small_table):
    first, _, table = two
    _, files = small_table
    changed = table.loc[["b2", "c3"]].copy()
    changed.loc["b2", "xtb_gap"] = 0.9
    changed.loc["b2", "topology"] = "nbo"
    other = _bundle(tmp_path / "other", changed, files, "other")
    with pytest.raises(MergeError) as err:
        merge_bundles([first, other], tmp_path / "out")
    text = str(err.value)
    assert "2 value(s) differ between the bundles for the same record; nothing was written" in text
    assert "b2: xtb_gap is 0.1, but 0.9 in" in text and "b2: topology is 'dia', but 'nbo' in" in text
    assert "--on-conflict first" in text
    assert not (tmp_path / "out").exists()

    kept_first = merge_bundles([first, other], tmp_path / "f", on_conflict="first")
    assert read_bundle(tmp_path / "f").table.loc["b2", ["xtb_gap", "topology"]].tolist() == [0.1, "dia"]
    assert "2 value(s) differed" in kept_first.manifest["warnings"][0] and "first" in kept_first.warnings[0]
    merge_bundles([first, other], tmp_path / "l", on_conflict="last")
    assert read_bundle(tmp_path / "l").table.loc["b2", ["xtb_gap", "topology"]].tolist() == [0.9, "nbo"]
    assert validate_bundle(tmp_path / "f").ok and validate_bundle(tmp_path / "l").ok


def test_structures_come_from_the_bundle_that_has_them(tmp_path, small_table):
    table, files = small_table
    without = _bundle(tmp_path / "without", table.loc[["a1"]], {}, "one")
    with_file = _bundle(tmp_path / "with", table.loc[["a1"]], files, "two")
    result = merge_bundles([without, with_file], tmp_path / "out")
    assert result.n_structures == 1
    assert (tmp_path / "out" / "structures" / "a1.cif").read_bytes() == files["a1"].read_bytes()
    assert read_bundle(tmp_path / "out").table.loc["a1", "cif_path"] == "structures/a1.cif"


def test_bundles_that_cannot_be_merged(tmp_path, two):
    first, second, _ = two
    with pytest.raises(MergeError, match="at least two bundles"):
        merge_bundles([first], tmp_path / "out")
    with pytest.raises(MergeError, match="listed twice"):
        merge_bundles([first, first], tmp_path / "out")
    with pytest.raises(MergeError, match="output folder is one of the bundles"):
        merge_bundles([first, second], second)
    (second / "structures" / "b2.cif").write_text("changed", encoding="utf-8")
    with pytest.raises(MergeError, match="is not a valid bundle; nothing was merged") as err:
        merge_bundles([first, second], tmp_path / "out")
    assert "structures/b2.cif: the file differs" in str(err.value)
    assert not (tmp_path / "out").exists()


def _retype(folder, column, new_type):
    path = folder / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for entry in manifest["columns"]:
        if entry["name"] == column:
            entry["type"] = new_type
    path.write_text(json.dumps(manifest), encoding="utf-8")


def test_whole_numbers_and_decimals_become_one_numeric_column(tmp_path, small_table):
    table, files = small_table
    counts = table.loc[["a1"]].assign(my_value=pd.array([4], dtype="Int64"))
    decimals = table.loc[["b2"]].assign(my_value=pd.array([2.5], dtype="Float64"))
    first = _bundle(tmp_path / "first", counts, files, "first")
    second = _bundle(tmp_path / "second", decimals, files, "second")
    result = merge_bundles([first, second], tmp_path / "out")
    column = next(c for c in result.manifest["columns"] if c["name"] == "my_value")
    assert column["type"] == "number"
    assert read_bundle(tmp_path / "out").table["my_value"].tolist() == [4.0, 2.5]
    assert validate_bundle(tmp_path / "out").ok


def test_a_column_that_is_text_in_one_bundle_and_numbers_in_another_is_refused(tmp_path, two):
    first, second, _ = two
    _retype(second, "topology", "number")
    with pytest.raises(MergeError):
        merge_bundles([first, second], tmp_path / "out")  # the damaged bundle is found by validation
    words = two[2].loc[["a1"]].assign(my_value=pd.array(["many"], dtype="string"))
    numbers = two[2].loc[["b2"]].assign(my_value=pd.array([3], dtype="Int64"))
    one = _bundle(tmp_path / "one", words, {}, "one")
    other = _bundle(tmp_path / "other", numbers, {}, "other")
    with pytest.raises(
        MergeError, match="column my_value holds string values in one bundle and integer values"
    ):
        merge_bundles([one, other], tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_another_structure_file_for_the_same_record_is_a_conflict(tmp_path, small_table):
    from conftest import methane, write_cif

    table, files = small_table
    laptop = table.loc[["a1", "b2"]].copy()
    laptop["xtb_gap"] = pd.array([pd.NA, pd.NA], dtype="Float64")  # not computed here
    cluster = table.loc[["a1", "b2"]].copy()
    cluster["n_atoms"] = pd.array([pd.NA, 10], dtype="Int64")
    other_files = {**files, "a1": write_cif(tmp_path / "elsewhere" / "a1.cif", methane(cell=11.0))}
    first = _bundle(tmp_path / "laptop", laptop, files, "run")
    second = _bundle(tmp_path / "cluster", cluster, other_files, "run")
    with pytest.raises(MergeError) as err:
        merge_bundles([first, second], tmp_path / "out")
    assert "a1: structure file is" in str(err.value) and "taken whole from that bundle" in str(err.value)

    # with a side chosen, the record with two geometries comes whole from that side ...
    merge_bundles([first, second], tmp_path / "f", on_conflict="first")
    kept = read_bundle(tmp_path / "f").table
    assert pd.isna(kept.loc["a1", "xtb_gap"]) and kept.loc["a1", "n_atoms"] == 5
    assert (tmp_path / "f" / "structures" / "a1.cif").read_bytes() == files["a1"].read_bytes()
    merge_bundles([first, second], tmp_path / "l", on_conflict="last")
    kept = read_bundle(tmp_path / "l").table
    assert kept.loc["a1", "xtb_gap"] == 2.8622 and pd.isna(kept.loc["a1", "n_atoms"])
    assert (tmp_path / "l" / "structures" / "a1.cif").read_bytes() == other_files["a1"].read_bytes()
    assert kept.index.tolist() == ["a1", "b2"]  # in place, not moved to the end
    # ... while the record with one geometry is still completed from both
    assert kept.loc["b2", "xtb_gap"] == 0.1 and kept.loc["b2", "n_atoms"] == 10
    assert validate_bundle(tmp_path / "f").ok and validate_bundle(tmp_path / "l").ok
