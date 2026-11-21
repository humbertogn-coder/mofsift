"""validate_bundle: a bundle is accepted as written, and every kind of damage is named."""

from __future__ import annotations

import json

import pytest

from mofsift.export import BundleError, validate_bundle, write_bundle
from mofsift.io.tables import sha256_file


@pytest.fixture
def bundle(tmp_path, small_table):
    table, files = small_table
    out = tmp_path / "bundle"
    write_bundle(
        table,
        out,
        project="demo",
        structure_files=files,
        enumerated=5,
        filters=[{"expression": "n_atoms > 1", "passed": 3, "rejected": 1, "undecided": 1}],
    )
    return out


def _edit_manifest(folder, change):
    path = folder / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    change(manifest)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _relist(folder, name):
    """Make the manifest describe the current content of ``name`` (as a careful forger would)."""

    def change(manifest):
        entry = {"bytes": (folder / name).stat().st_size, "sha256": sha256_file(folder / name)}
        manifest["files"][name] = entry
        for part in ("table", "records"):
            if manifest[part] and manifest[part]["path"] == name:
                manifest[part]["sha256"] = entry["sha256"]

    _edit_manifest(folder, change)


def test_a_fresh_bundle_is_valid(bundle):
    result = validate_bundle(bundle)
    assert result.ok and result.problems == [] and result.notes == []
    assert result.as_dict() == {
        "path": str(bundle),
        "valid": True,
        "problems": [],
        "notes": [],
        "project": "demo",
        "mofsift_version": result.summary["mofsift_version"],
        "records": 3,
        "structures": 2,
    }


def test_a_folder_that_is_not_a_bundle(tmp_path):
    with pytest.raises(BundleError, match="not a folder"):
        validate_bundle(tmp_path / "nowhere")
    result = validate_bundle(tmp_path)
    assert not result.ok and "no manifest.json; this folder is not a mofsift bundle" in result.problems[0]
    (tmp_path / "manifest.json").write_text('{"kind": "something else"}', encoding="utf-8")
    assert "not the manifest of a mofsift bundle" in validate_bundle(tmp_path).problems[0]


def test_a_changed_file_is_found_by_its_checksum(bundle):
    cif = bundle / "structures" / "a1.cif"
    cif.write_text(cif.read_text(encoding="utf-8").replace("C", "N", 1), encoding="utf-8")
    result = validate_bundle(bundle)
    assert result.problems == ["structures/a1.cif: the file differs from the one the manifest describes"]


def test_a_missing_file_is_found(bundle):
    (bundle / "structures" / "b2.cif").unlink()
    (bundle / "candidates.json").unlink()
    problems = validate_bundle(bundle).problems
    assert "structures/b2.cif: listed in the manifest but not in the bundle" in problems
    assert "candidates.json: listed in the manifest but not in the bundle" in problems


def test_a_table_edited_with_a_consistent_manifest_still_disagrees_with_the_records(bundle):
    table = bundle / "candidates.csv"
    table.write_text(table.read_text(encoding="utf-8").replace("2.8622", "0.5"), encoding="utf-8")
    _relist(bundle, "candidates.csv")
    problems = validate_bundle(bundle).problems
    assert problems == ["candidates.json: a1: xtb_gap is 2.8622 but the table has 0.5"]


def test_records_are_checked_against_the_candidate_schema(bundle):
    path = bundle / "candidates.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["records"][0]["n_atoms"] = "five"
    payload["records"][1]["generate_status"] = "fine"
    path.write_text(json.dumps(payload), encoding="utf-8")
    _relist(bundle, "candidates.json")
    problems = validate_bundle(bundle).problems
    assert any(p.startswith("candidates.json: a1: n_atoms:") for p in problems)
    assert any(p.startswith("candidates.json: b2: generate_status:") for p in problems)


def test_non_standard_json_numbers_are_refused(bundle):
    path = bundle / "candidates.json"
    path.write_text(path.read_text(encoding="utf-8").replace("2.8622", "NaN"), encoding="utf-8")
    _relist(bundle, "candidates.json")
    problems = validate_bundle(bundle).problems
    assert len(problems) == 1 and "non-standard JSON constant NaN" in problems[0]


def test_a_file_that_is_not_a_cif(bundle):
    cif = bundle / "structures" / "a1.cif"
    cif.write_text("data_x\n# no cell and no atoms\n", encoding="utf-8")
    _relist(bundle, "structures/a1.cif")
    assert validate_bundle(bundle).problems == [
        "structures/a1.cif: no unit cell or no atom sites; not a usable CIF"
    ]


@pytest.mark.parametrize(
    "change, expected",
    [
        (lambda m: m.pop("columns"), "manifest.json: (top level): 'columns' is a required property"),
        (lambda m: m.update(schema_version="2.0.0"), "manifest.json: schema_version:"),
        (lambda m: m["counts"].update(selected=2), "the number of rows differs from counts.selected"),
        (lambda m: m["counts"].update(structures=1), "counts.structures must say the same"),
        (lambda m: m["counts"].update(enumerated=2), "counts.selected is larger than counts.enumerated"),
        (lambda m: m["filters"][0].update(passed=4), "do not add up to counts.enumerated"),
        (lambda m: m["columns"].pop(3), "its columns are not the ones listed in the manifest"),
        (lambda m: m["table"].update(sha256="0" * 64), "table.path or table.sha256 does not match 'files'"),
        (
            lambda m: m["files"].update({"../outside.txt": {"bytes": 1, "sha256": "0" * 64}}),
            "manifest.json: files:",
        ),
        (
            lambda m: m["files"].update({"structures/zz.cif": m["files"]["structures/a1.cif"]}),
            "structures/zz.cif: listed in the manifest but not in the bundle",
        ),
    ],
)
def test_an_inconsistent_manifest_is_named(bundle, change, expected):
    _edit_manifest(bundle, change)
    problems = validate_bundle(bundle).problems
    assert any(expected in p for p in problems), problems


def test_a_listed_structure_nobody_points_at(bundle):
    extra = bundle / "structures" / "zz.cif"
    extra.write_bytes((bundle / "structures" / "a1.cif").read_bytes())

    def change(manifest):
        manifest["files"]["structures/zz.cif"] = manifest["files"]["structures/a1.cif"]

    _edit_manifest(bundle, change)
    assert validate_bundle(bundle).problems == [
        "structures/zz.cif: listed in the manifest but no record points at it"
    ]


def test_restricted_files_are_problems_and_stray_files_are_notes(bundle):
    (bundle / "POTCAR").write_text("x", encoding="utf-8")
    (bundle / "structures" / "model.pkl").write_text("x", encoding="utf-8")
    (bundle / "readme.txt").write_text("x", encoding="utf-8")
    (bundle / ".DS_Store").write_text("x", encoding="utf-8")
    result = validate_bundle(bundle)
    assert result.problems == [
        "POTCAR: restricted or unsafe file type in a bundle",
        "structures/model.pkl: restricted or unsafe file type in a bundle",
    ]
    assert result.notes == ["readme.txt: in the folder but not listed in the manifest"]


def test_a_long_list_of_problems_is_cut(bundle):
    def change(manifest):
        for i in range(60):
            manifest["files"][f"structures/x{i}.cif"] = {"bytes": 1, "sha256": "0" * 64}

    _edit_manifest(bundle, change)
    problems = validate_bundle(bundle).problems
    assert len(problems) == 41 and problems[-1].startswith("... and ")


@pytest.mark.parametrize("cell", ["5.5", "99999999999999999999999999", "five"])
def test_a_damaged_cell_is_a_problem_not_a_crash(bundle, cell):
    table = bundle / "candidates.csv"
    text = table.read_text(encoding="utf-8")
    assert ",pcu,5," in text
    table.write_text(text.replace(",pcu,5,", f",pcu,{cell},"), encoding="utf-8")
    _relist(bundle, "candidates.csv")
    problems = validate_bundle(bundle).problems
    assert len(problems) == 1 and "candidates.csv: cannot read the table" in problems[0]


@pytest.mark.parametrize("content", ["[]", "null", '"text"', "{", ""])
def test_a_manifest_that_is_not_one(bundle, content):
    (bundle / "manifest.json").write_text(content, encoding="utf-8")
    result = validate_bundle(bundle)
    assert not result.ok and len(result.problems) == 1
