"""End-to-end runs through the command line, with core dependencies only (existing CIFs)."""

from __future__ import annotations

import json

import pytest
import yaml
from typer.testing import CliRunner

import mofsift.cli as cli
from mofsift.io.tables import read_table, sha256_file

runner = CliRunner()


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
    assert "Not implemented in this version, so not run: describe.composition" in res.output
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
    first = {f: sha256_file(work / f) for f in ("01_generate.csv", "02_qc.csv")}
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
