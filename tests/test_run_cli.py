"""End-to-end runs through the command line, with core dependencies only (existing CIFs)."""

from __future__ import annotations

import json

import pytest
import yaml
from typer.testing import CliRunner

import mofsift.cli as cli
from mofsift.io.tables import read_table, sha256_file

runner = CliRunner()


@pytest.fixture(autouse=True)
def _without_geometry_and_xtb_tools(monkeypatch):
    """These runs must not depend on whether pyzeo and tblite happen to be installed."""
    real = cli.default_tools

    def tools(cfg=None):
        found = real(cfg)
        for tool in found:
            if tool.name in ("pyzeo", "tblite", "zeo++ (network)"):
                tool.check = lambda: (False, "not installed")
        return found

    monkeypatch.setattr(cli, "default_tools", tools)


@pytest.fixture
def project(tmp_path, cif_dir):
    """A config next to a folder of CIFs; returns the config path."""
    config = tmp_path / "run.yaml"
    config.write_text(
        yaml.safe_dump({"project": "e2e", "generate": {"source": "cifs", "cif_dir": "cifs"}}),
        encoding="utf-8",
    )
    return config


def test_run_goes_from_cifs_to_a_checked_table(project):
    res = runner.invoke(cli.app, ["run", str(project)])
    assert res.exit_code == 0, res.output
    assert "[3/3] two_pieces: ok" in res.output
    assert "generate: 3 row(s): 3 ok, 0 skipped, 0 failed" in res.output
    assert "qc: 3 row(s): 3 ok, 0 skipped, 0 failed; 1 passed the checks, 2 did not" in res.output
    assert "describe.composition: 3 row(s): 3 ok, 0 skipped, 0 failed" in res.output
    assert "score: 3 row(s): 0 ok, 3 skipped, 0 failed" in res.output  # methane has no linker or metal
    assert "filter: no filters in the config; all 3 row(s) are kept" in res.output
    assert "export: 3 of 3 row(s), 3 structure file(s)" in res.output
    # the tools for the main descriptors are missing: the run goes on, and says so loudly
    assert "WARNING: describe.geometry did not run: pyzeo is not installed" in res.output
    assert "WARNING: describe.xtb did not run: tblite is not installed" in res.output
    work = project.parent / "work"
    table = read_table(work / "02_qc.csv")
    assert table.set_index("name")["qc_pass"].to_dict() == {"clash": False, "good": True, "two_pieces": False}
    assert all((work / p).is_file() for p in table["cif_path"])
    meta = json.loads((work / "02_qc.meta.json").read_text(encoding="utf-8"))
    assert meta["stage"] == "qc" and meta["n_rows"] == 3 and meta["details"]["passed"] == 1
    assert meta["table_sha256"] == sha256_file(work / "02_qc.csv")
    assert meta["details"]["input_sha256"] == sha256_file(work / "01_generate.csv")
    assert (
        json.loads((work / "01_generate.meta.json").read_text(encoding="utf-8"))["details"]["source"]
        == "cifs"
    )


def test_tables_are_identical_on_a_second_run(project):
    work = project.parent / "work"
    assert runner.invoke(cli.app, ["run", str(project), "--quiet"]).exit_code == 0
    names = ("01_generate.csv", "02_qc.csv", "03_describe.csv", "04_score.csv", "05_filter.csv")
    first = {f: sha256_file(work / f) for f in names}
    quiet = runner.invoke(cli.app, ["run", str(project), "-q"])
    assert quiet.exit_code == 0 and "[1/3]" not in quiet.output
    assert {f: sha256_file(work / f) for f in first} == first


def test_stages_can_be_run_one_at_a_time(project):
    work = project.parent / "work"
    early = runner.invoke(cli.app, ["qc", str(project)])
    assert early.exit_code == 1 and "run 'mofsift generate" in early.output
    assert not (work / "02_qc.csv").exists()
    gen = runner.invoke(cli.app, ["generate", str(project)])
    assert gen.exit_code == 0 and (work / "01_generate.csv").exists() and not (work / "02_qc.csv").exists()
    checked = runner.invoke(cli.app, ["qc", str(project)])
    assert checked.exit_code == 0 and "1 passed the checks" in checked.output
    assert (work / "02_qc.csv").exists()


def test_workdir_follows_the_config(project):
    data = yaml.safe_load(project.read_text(encoding="utf-8"))
    data["workdir"] = "elsewhere/out"
    project.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert runner.invoke(cli.app, ["run", str(project), "-q"]).exit_code == 0
    assert (project.parent / "elsewhere" / "out" / "02_qc.csv").exists()
    assert not (project.parent / "work").exists()


def test_invalid_config_and_impossible_generate_exit_2(project, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("project: x\ngenerate: {source: cifs}\n", encoding="utf-8")
    res = runner.invoke(cli.app, ["run", str(bad)])
    assert res.exit_code == 2 and "needs cif_dir" in res.output
    nowhere = tmp_path / "nowhere.yaml"
    nowhere.write_text("project: x\ngenerate: {source: cifs, cif_dir: not_here}\n", encoding="utf-8")
    res = runner.invoke(cli.app, ["generate", str(nowhere)])
    assert res.exit_code == 2 and "generate.cif_dir: folder not found" in res.output
    doctor = runner.invoke(cli.app, ["doctor", str(nowhere), "--json"])
    stages = {s["stage"]: s for s in json.loads(doctor.stdout)["config"]["stages"]}
    assert doctor.exit_code == 1 and stages["generate"]["action"] == "error"


def test_generate_without_pormake_says_what_to_do(tmp_path, monkeypatch):
    class Missing:
        name, hint = "pormake", "pip install 'mofsift[generate]'"

        def check(self):
            return False, "not installed"

    monkeypatch.setattr(cli, "default_tools", lambda cfg=None: [Missing()])
    config = tmp_path / "c.yaml"
    runner.invoke(cli.app, ["init", str(config)])
    res = runner.invoke(cli.app, ["run", str(config)])
    assert res.exit_code == 1
    assert "pormake is not installed" in res.output and "generate.source: cifs" in res.output
    assert not (tmp_path / "work").exists()


def test_doctor_counts_the_cif_files(project):
    res = runner.invoke(cli.app, ["doctor", str(project)])
    assert "3 CIF file(s)" in res.output


def test_qc_accepts_a_hand_made_table(project):
    # entering the pipeline at qc: a table with only an id and the path of each CIF
    work = project.parent / "work"
    (work / "mine").mkdir(parents=True)
    (work / "mine" / "a.cif").write_bytes((project.parent / "cifs" / "good.cif").read_bytes())
    (work / "01_generate.csv").write_text("id,cif_path\nx1,mine/a.cif\nx2,\n", encoding="utf-8")
    res = runner.invoke(cli.app, ["qc", str(project)])
    assert res.exit_code == 0, res.output
    assert "qc: 2 row(s): 1 ok, 1 skipped, 0 failed; 1 passed the checks, 0 did not" in res.output
    table = read_table(work / "02_qc.csv")
    assert list(table.index) == ["x1", "x2"] and table.loc["x2", "qc_skip_reason"] == "no structure"


def test_qc_explains_a_table_it_cannot_use(project):
    work = project.parent / "work"
    work.mkdir()
    (work / "01_generate.csv").write_text("id,path\nx1,a.cif\n", encoding="utf-8")
    res = runner.invoke(cli.app, ["qc", str(project)])
    assert res.exit_code == 1 and "needs a 'cif_path' column" in res.output
    assert not (work / "02_qc.csv").exists()


def test_describe_command(project):
    work = project.parent / "work"
    early = runner.invoke(cli.app, ["describe", str(project)])
    assert early.exit_code == 1 and "run 'mofsift qc" in early.output
    assert runner.invoke(cli.app, ["generate", str(project), "-q"]).exit_code == 0
    assert runner.invoke(cli.app, ["qc", str(project)]).exit_code == 0
    res = runner.invoke(cli.app, ["describe", str(project)])
    assert res.exit_code == 0, res.output  # missing optional tools are not an error
    assert "describe.xtb: 3 row(s): 0 ok, 3 skipped, 0 failed" in res.output
    assert "2 x did not pass qc" in res.output and "1 x tblite is not installed" in res.output
    table = read_table(work / "03_describe.csv").set_index("name")
    assert table.loc["good", "c_fraction"] == 0.2 and table["xtb_gap"].isna().all()
    assert table.loc["good", "describe_xtb_skip_reason"].startswith("tblite is not installed")
    meta = json.loads((work / "03_describe.meta.json").read_text(encoding="utf-8"))
    assert meta["details"]["substages"]["geometry"]["not_run_because"].startswith("pyzeo is not installed")
    log = (work / "run.log").read_text(encoding="utf-8")
    assert "describe.xtb: 0 ok, 3 skipped, 0 failed" in log and "2 x did not pass qc" in log
    assert "generate: 3 rows, 3 ok" in log and "qc: 3 rows" in log


def test_describe_blocks_and_bad_options(project):
    work = project.parent / "work"
    runner.invoke(cli.app, ["generate", str(project), "-q"])
    runner.invoke(cli.app, ["qc", str(project)])
    block = runner.invoke(cli.app, ["describe", str(project), "--block", "1/2", "--only", "xtb"])
    assert block.exit_code == 0 and "block 1/2 stored" in block.output
    assert not (work / "03_describe.csv").exists()  # a block never writes the table
    for options in (["--block", "3/2"], ["--only", "zeo"]):
        res = runner.invoke(cli.app, ["describe", str(project), *options])
        assert res.exit_code == 2


def test_a_tool_declared_mandatory_stops_describe(project):
    data = yaml.safe_load(project.read_text(encoding="utf-8"))
    data["describe"] = {"xtb": {"enabled": True, "on_missing": "error"}}
    project.write_text(yaml.safe_dump(data), encoding="utf-8")
    runner.invoke(cli.app, ["generate", str(project), "-q"])
    runner.invoke(cli.app, ["qc", str(project)])
    res = runner.invoke(cli.app, ["describe", str(project)])
    assert res.exit_code == 1 and "describe.xtb cannot run: tblite is not installed" in res.output
    assert not (project.parent / "work" / "03_describe.csv").exists()


def test_score_command_needs_the_describe_table(project):
    res = runner.invoke(cli.app, ["score", str(project)])
    assert res.exit_code == 1 and "run 'mofsift describe" in res.output
    assert runner.invoke(cli.app, ["run", str(project), "-q"]).exit_code == 0
    table = read_table(project.parent / "work" / "04_score.csv")
    assert (table["score_status"] == "skipped").all() and table["linker_sa"].isna().all()
    meta = json.loads((project.parent / "work" / "04_score.meta.json").read_text(encoding="utf-8"))
    assert meta["details"]["reference"]["n_mofs"] > 10000 and "QMOF" in meta["details"]["reference"]["source"]


# ------------------------------------------------------ filter, export, bundles
def _with(config, **sections):
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    data.update(sections)
    config.write_text(yaml.safe_dump(data), encoding="utf-8")


def _manifest(folder):
    return json.loads((folder / "manifest.json").read_text(encoding="utf-8"))


def test_run_ends_with_a_valid_bundle_of_the_rows_that_passed(project):
    _with(project, filter=["qc_pass == True"])
    res = runner.invoke(cli.app, ["run", str(project), "-q"])
    assert res.exit_code == 0, res.output
    assert "filter: 3 row(s): 1 satisfy every filter, 2 do not" in res.output
    assert "    qc_pass == True: 1 true, 2 false" in res.output
    assert "export: 1 of 3 row(s), 1 structure file(s)" in res.output
    work, bundle = project.parent / "work", project.parent / "export" / "e2e"
    # the rejected rows are still in the work table, with the filter that rejected them
    filtered = read_table(work / "05_filter.csv")
    assert len(filtered) == 3 and filtered["filter_pass"].sum() == 1
    assert set(filtered["filter_rejected_by"].dropna()) == {"qc_pass == True"}
    # the bundle holds the one that passed
    manifest = _manifest(bundle)
    assert manifest["counts"] == {"enumerated": 3, "selected": 1, "structures": 1}
    assert manifest["filters"] == [
        {"expression": "qc_pass == True", "passed": 1, "rejected": 2, "undecided": 0}
    ]
    assert manifest["config"]["filter"] == ["qc_pass == True"] and manifest["warnings"] == []
    assert [s["stage"] for s in manifest["stages"]] == ["generate", "qc", "describe", "score", "filter"]
    assert manifest["config_hash"] == manifest["stages"][0]["config_hash"]
    records = json.loads((bundle / "candidates.json").read_text(encoding="utf-8"))["records"]
    assert [r["name"] for r in records] == ["good"] and records[0][
        "cif_path"
    ] == f"structures/{records[0]['id']}.cif"
    check = runner.invoke(cli.app, ["validate", str(bundle)])
    assert (
        check.exit_code == 0
        and "valid bundle of project e2e (1 record(s), 1 structure file(s))" in check.output
    )
    as_json = json.loads(runner.invoke(cli.app, ["validate", str(bundle), "--json"]).stdout)
    assert as_json["valid"] is True and as_json["records"] == 1
    log = (work / "run.log").read_text(encoding="utf-8")
    assert "filter:   qc_pass == True: 1 true, 2 false, 0 undecided" in log and "export: 1 of 3 rows" in log


def test_exporting_twice_gives_the_same_bundle(project):
    assert runner.invoke(cli.app, ["run", str(project), "-q"]).exit_code == 0
    bundle = project.parent / "export" / "e2e"
    first = _manifest(bundle)
    assert runner.invoke(cli.app, ["export", str(project)]).exit_code == 0
    second = _manifest(bundle)
    first.pop("created_utc"), second.pop("created_utc")
    assert first == second


def test_export_goes_where_it_is_told(project, tmp_path):
    _with(project, export={"out_dir": "shared/bundle", "include_cifs": False})
    assert runner.invoke(cli.app, ["run", str(project), "-q"]).exit_code == 0
    assert _manifest(project.parent / "shared" / "bundle")["structures"] is None
    elsewhere = tmp_path / "elsewhere"
    res = runner.invoke(cli.app, ["export", str(project), "--out", str(elsewhere)])
    assert res.exit_code == 0 and (elsewhere / "candidates.csv").is_file()
    # a folder with someone's files is left alone
    mine = tmp_path / "mine"
    mine.mkdir()
    (mine / "notes.txt").write_text("x", encoding="utf-8")
    refused = runner.invoke(cli.app, ["export", str(project), "--out", str(mine)])
    assert refused.exit_code == 1 and "does not hold a mofsift bundle" in refused.output
    assert [p.name for p in mine.iterdir()] == ["notes.txt"]


def test_filter_and_export_need_the_table_before_them(project):
    early = runner.invoke(cli.app, ["filter", str(project)])
    assert early.exit_code == 1 and "run 'mofsift score" in early.output
    early = runner.invoke(cli.app, ["export", str(project)])
    assert early.exit_code == 1 and "run 'mofsift filter" in early.output


def test_a_filter_on_an_unknown_column_stops_the_run_before_anything_is_built(project):
    _with(project, filter=["xtb_gapp < 1"])
    res = runner.invoke(cli.app, ["run", str(project)])
    assert res.exit_code == 2
    assert "no stage writes the column 'xtb_gapp' (did you mean 'xtb_gap'?)" in res.output
    assert not (project.parent / "work").exists()


def test_a_filter_that_cannot_be_applied_to_the_table_exits_2(project):
    assert runner.invoke(cli.app, ["run", str(project), "-q"]).exit_code == 0
    _with(project, filter=["topology > 3"])  # a known column, compared with the wrong kind of value
    res = runner.invoke(cli.app, ["filter", str(project)])
    assert res.exit_code == 2 and "cannot compare text with number" in res.output


def test_export_warns_when_the_tables_are_older_than_the_config(project):
    assert runner.invoke(cli.app, ["run", str(project), "-q"]).exit_code == 0
    _with(project, filter=["qc_pass == True"])
    # only the filter changed: running the filter again is enough, and nothing is out of date
    assert runner.invoke(cli.app, ["filter", str(project)]).exit_code == 0
    clean = runner.invoke(cli.app, ["export", str(project)])
    assert clean.exit_code == 0 and "WARNING" not in clean.output
    # the qc thresholds changed, but qc was not run again
    _with(project, qc={"min_distance": 0.5})
    stale = runner.invoke(cli.app, ["export", str(project)])
    assert stale.exit_code == 0
    assert (
        "WARNING: 02_qc.csv was made before 'qc' in the config was changed; run 'mofsift qc'" in stale.output
    )
    assert "01_generate.csv" not in stale.output and "03_describe.csv" not in stale.output
    bundle = project.parent / "export" / "e2e"
    assert any("02_qc.csv" in w for w in _manifest(bundle)["warnings"])
    # a table edited by hand, and a table older than its input
    work = project.parent / "work"
    table = work / "04_score.csv"
    table.write_text(table.read_text(encoding="utf-8").replace("good", "better"), encoding="utf-8")
    edited = runner.invoke(cli.app, ["export", str(project)])
    assert "04_score.csv was changed after 'mofsift score' wrote it" in edited.output
    assert "05_filter.csv was made from an older 04_score.csv" in edited.output


def test_two_runs_can_be_merged_from_the_command_line(project, tmp_path):
    assert runner.invoke(cli.app, ["run", str(project), "-q"]).exit_code == 0
    first = project.parent / "export" / "e2e"
    _with(project, project="second", filter=["qc_pass == True"], workdir="work2")
    assert runner.invoke(cli.app, ["run", str(project), "-q"]).exit_code == 0
    second = project.parent / "export" / "second"
    out = tmp_path / "merged"
    res = runner.invoke(cli.app, ["merge", str(first), str(second), "--out", str(out), "--project", "both"])
    assert res.exit_code == 0, res.output
    assert "merge: 3 record(s) from 2 bundles, 3 structure file(s)" in res.output
    manifest = _manifest(out)
    assert manifest["project"] == "both" and [s["project"] for s in manifest["sources"]] == ["e2e", "second"]
    assert runner.invoke(cli.app, ["validate", str(out)]).exit_code == 0
    # the same record, selected by a filter in one run and not filtered in the other, is no conflict
    merged = read_table(out / "candidates.csv")
    assert merged["filter_pass"].sum() == 1 and merged["filter_pass"].isna().sum() == 2


def test_merge_and_validate_report_bad_input(project, tmp_path):
    assert runner.invoke(cli.app, ["run", str(project), "-q"]).exit_code == 0
    bundle = project.parent / "export" / "e2e"
    missing = runner.invoke(
        cli.app, ["merge", str(bundle), str(tmp_path / "nope"), "--out", str(tmp_path / "o")]
    )
    assert missing.exit_code == 2 and "not a folder" in missing.output
    bad_name = runner.invoke(
        cli.app, ["merge", str(bundle), str(bundle), "--out", str(tmp_path / "o"), "--project", "a b"]
    )
    assert bad_name.exit_code == 2 and "--project" in bad_name.output
    bad_choice = runner.invoke(
        cli.app, ["merge", str(bundle), str(bundle), "--out", str(tmp_path / "o"), "--on-conflict", "maybe"]
    )
    assert bad_choice.exit_code == 2
    twice = runner.invoke(cli.app, ["merge", str(bundle), str(bundle), "--out", str(tmp_path / "o")])
    assert twice.exit_code == 1 and "listed twice" in twice.output
    assert runner.invoke(cli.app, ["validate", str(tmp_path / "nope")]).exit_code == 2
    (bundle / "candidates.csv").write_text("id\n", encoding="utf-8")
    broken = runner.invoke(cli.app, ["validate", str(bundle)])
    assert broken.exit_code == 1 and "NOT a valid bundle" in broken.output
    assert "candidates.csv: the file differs from the one the manifest describes" in broken.output


def test_a_filter_nothing_passes_gives_an_empty_but_valid_bundle(project):
    _with(project, filter=["n_atoms > 1000"])
    res = runner.invoke(cli.app, ["run", str(project), "-q"])
    assert res.exit_code == 0 and "export: 0 of 3 row(s), 0 structure file(s)" in res.output
    bundle = project.parent / "export" / "e2e"
    assert json.loads((bundle / "candidates.json").read_text(encoding="utf-8"))["records"] == []
    assert _manifest(bundle)["structures"] is None and not (bundle / "structures").exists()
    assert runner.invoke(cli.app, ["validate", str(bundle)]).exit_code == 0


def test_a_hand_made_table_with_its_own_columns_can_be_filtered_and_exported(project):
    work = project.parent / "work"
    (work / "mine").mkdir(parents=True)
    (work / "mine" / "a.cif").write_bytes((project.parent / "cifs" / "good.cif").read_bytes())
    (work / "01_generate.csv").write_text(
        "id,cif_path,my_gap\nx1,mine/a.cif,0.4\nx2,mine/a.cif,2.5\nx3,../outside.cif,0.1\n", encoding="utf-8"
    )
    _with(project, filter=["my_gap < 1"])
    for command in ("qc", "describe", "score", "filter"):
        assert (
            runner.invoke(
                cli.app, [command, str(project), *(["-q"] if command == "describe" else [])]
            ).exit_code
            == 0
        )
    # x3 passes the filter but its path leaves the work folder: nothing is exported
    refused = runner.invoke(cli.app, ["export", str(project)])
    assert refused.exit_code == 1 and "cif_path of x3 is not a path inside the work folder" in refused.output
    _with(project, filter=["my_gap < 0.3 or my_gap > 2"])
    assert runner.invoke(cli.app, ["filter", str(project)]).exit_code == 0
    refused = runner.invoke(cli.app, ["export", str(project)])
    assert refused.exit_code == 1  # x3 still selected
    _with(project, filter=["my_gap > 0.3"])
    assert runner.invoke(cli.app, ["filter", str(project)]).exit_code == 0
    done = runner.invoke(cli.app, ["export", str(project)])
    assert done.exit_code == 0 and "export: 2 of 3 row(s), 2 structure file(s)" in done.output
    bundle = project.parent / "export" / "e2e"
    records = json.loads((bundle / "candidates.json").read_text(encoding="utf-8"))["records"]
    assert [(r["id"], r["my_gap"], r["cif_path"]) for r in records] == [
        ("x1", 0.4, "structures/x1.cif"),
        ("x2", 2.5, "structures/x2.cif"),
    ]
    column = next(c for c in _manifest(bundle)["columns"] if c["name"] == "my_gap")
    assert column == {"name": "my_gap", "type": "number", "group": None, "unit": None, "description": None}
    assert runner.invoke(cli.app, ["validate", str(bundle)]).exit_code == 0
    # run would build the table again without that column, so it refuses before starting
    assert runner.invoke(cli.app, ["run", str(project)]).exit_code == 2


def test_parquet_without_pyarrow_stops_the_run_before_it_starts(project, monkeypatch):
    real = cli.default_tools

    def tools(cfg=None):
        found = real(cfg)
        for tool in found:
            if tool.name == "pyarrow":
                tool.check = lambda: (False, "not installed")
        return found

    monkeypatch.setattr(cli, "default_tools", tools)
    _with(project, export={"table_format": "parquet"})
    res = runner.invoke(cli.app, ["run", str(project)])
    assert res.exit_code == 1 and "export cannot run: table_format: parquet needs pyarrow" in res.output
    assert not (project.parent / "work").exists()


def test_the_bundle_records_the_filters_that_were_applied_not_the_ones_in_the_config(project):
    _with(project, filter=["n_atoms > 2"])
    assert runner.invoke(cli.app, ["run", str(project), "-q"]).exit_code == 0
    _with(project, filter=["n_atoms > 1000"])  # changed, but the filter step is not run again
    res = runner.invoke(cli.app, ["export", str(project)])
    assert res.exit_code == 0
    assert "WARNING: 05_filter.csv was made before 'filter' in the config was changed" in res.output
    manifest = _manifest(project.parent / "export" / "e2e")
    assert [f["expression"] for f in manifest["filters"]] == ["n_atoms > 2"]
    assert manifest["counts"]["selected"] == 3 and manifest["config"]["filter"] == ["n_atoms > 1000"]
    assert any("05_filter.csv" in w for w in manifest["warnings"])


def test_settings_a_stage_does_not_read_do_not_make_its_table_out_of_date(project):
    assert runner.invoke(cli.app, ["run", str(project), "-q"]).exit_code == 0
    _with(project, tools={"xtb": "/opt/xtb/bin/xtb"}, export={"viewer_bundle": False}, workdir="work")
    res = runner.invoke(cli.app, ["export", str(project)])
    assert res.exit_code == 0 and "WARNING" not in res.output
    _with(project, tools={"zeopp": "/opt/zeo/network"})
    res = runner.invoke(cli.app, ["export", str(project)])
    assert (
        "03_describe.csv was made before 'describe' (or tools.zeopp) in the config was changed" in res.output
    )
    assert "01_generate.csv" not in res.output


def test_an_infinite_threshold_in_the_config_does_not_break_the_export(project):
    _with(project, qc={"max_node_rmsd": float("inf")})
    res = runner.invoke(cli.app, ["run", str(project), "-q"])
    assert res.exit_code == 0, res.output
    bundle = project.parent / "export" / "e2e"
    assert _manifest(bundle)["config"]["qc"]["max_node_rmsd"] == "Infinity"
    assert runner.invoke(cli.app, ["validate", str(bundle)]).exit_code == 0
