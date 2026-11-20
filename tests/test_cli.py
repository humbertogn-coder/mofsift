from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

import mofsift.cli as cli
from mofsift import __version__
from mofsift.config import load_config
from mofsift.tools import PythonTool

runner = CliRunner()


def _fake_tools(present: set[str]):
    """Registry in which every tool's presence is decided by ``present`` (no real detection)."""

    class Fake(PythonTool):
        def available(self) -> bool:
            return self.name in present

        def check(self):
            return (True, None) if self.name in present else (False, "not installed")

        def version(self):
            return "1.2.3" if self.name in present else None

    spec = [
        ("numpy", "core"), ("pandas", "core"), ("ase", "core"), ("pymatgen", "core"), ("rdkit", "core"),
        ("pormake", "generate"), ("pyzeo", "zeo"), ("tblite", "xtb"), ("zeo++ (network)", "zeo"),
    ]  # fmt: skip
    return lambda cfg=None: [Fake(n, n, n, tier, "stage", f"install {n}") for n, tier in spec]


CORE = {"numpy", "pandas", "ase", "pymatgen", "rdkit"}


@pytest.fixture(autouse=True)
def _no_real_pormake(monkeypatch):
    """doctor counts the candidates through PORMAKE; these tests do not need the real one."""
    monkeypatch.setattr(cli, "preview_generate", lambda cfg, config_dir: (2, []))


def test_version():
    res = runner.invoke(cli.app, ["--version"])
    assert res.exit_code == 0 and res.stdout.strip() == f"mofsift {__version__}"


def test_init_writes_a_valid_config_and_refuses_to_overwrite(tmp_path):
    target = tmp_path / "sub" / "run.yaml"
    res = runner.invoke(cli.app, ["init", str(target)])
    assert res.exit_code == 0 and target.exists()
    assert load_config(target).project == "demo_irmof"
    target.write_text("project: mine\n", encoding="utf-8")
    again = runner.invoke(cli.app, ["init", str(target)])
    assert again.exit_code == 1 and "already exists" in again.output
    assert target.read_text(encoding="utf-8") == "project: mine\n"  # untouched
    forced = runner.invoke(cli.app, ["init", str(target), "--force"])
    assert forced.exit_code == 0 and load_config(target).project == "demo_irmof"


def test_doctor_without_config_lists_tools(monkeypatch):
    monkeypatch.setattr(cli, "default_tools", _fake_tools(CORE))
    res = runner.invoke(cli.app, ["doctor"])
    assert res.exit_code == 0, res.output
    assert "pormake" in res.stdout and "missing" in res.stdout and "install pormake" in res.stdout
    assert "Config" not in res.stdout  # no stage plan without a config


def test_doctor_with_config_all_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "default_tools", _fake_tools(CORE | {"pormake", "pyzeo", "tblite"}))
    cfg = tmp_path / "c.yaml"
    runner.invoke(cli.app, ["init", str(cfg)])
    res = runner.invoke(cli.app, ["doctor", str(cfg)])
    assert res.exit_code == 0, res.output
    assert "valid (project demo_irmof" in res.stdout
    assert "2 candidate(s)" in res.stdout
    assert "runs generate, qc, describe, score" in res.stdout  # no claim that the later stages exist


def test_doctor_reports_skips_as_success_and_blocked_generate_as_failure(tmp_path, monkeypatch):
    cfg = tmp_path / "c.yaml"
    runner.invoke(cli.app, ["init", str(cfg)])
    monkeypatch.setattr(cli, "default_tools", _fake_tools(CORE | {"pormake"}))
    res = runner.invoke(cli.app, ["doctor", str(cfg), "--json"])
    assert res.exit_code == 0, res.output  # optional tools missing -> skipped, not an error
    stages = {s["stage"]: s for s in json.loads(res.stdout)["config"]["stages"]}
    assert stages["describe.geometry"]["action"] == "skip" and stages["describe.xtb"]["action"] == "skip"
    monkeypatch.setattr(cli, "default_tools", _fake_tools(CORE))
    res = runner.invoke(cli.app, ["doctor", str(cfg), "--json"])
    assert res.exit_code == 1
    assert {s["stage"]: s for s in json.loads(res.stdout)["config"]["stages"]}["generate"][
        "action"
    ] == "error"


def test_doctor_invalid_config_exits_2_with_a_readable_message(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "default_tools", _fake_tools(CORE))
    cfg = tmp_path / "c.yaml"
    cfg.write_text("project: t\ngenerate: {source: pormake, topologys: [pcu]}\n", encoding="utf-8")
    res = runner.invoke(cli.app, ["doctor", str(cfg)])
    assert res.exit_code == 2
    assert "generate.topologys" in res.output and "did you mean 'topologies'" in res.output
    as_json = runner.invoke(cli.app, ["doctor", str(cfg), "--json"])
    payload = json.loads(as_json.stdout)
    assert (
        as_json.exit_code == 2 and payload["config"]["valid"] is False and payload["config"]["stages"] == []
    )


def test_doctor_fails_when_a_core_dependency_is_broken(monkeypatch):
    monkeypatch.setattr(cli, "default_tools", _fake_tools(CORE - {"rdkit"}))
    res = runner.invoke(cli.app, ["doctor"])
    assert res.exit_code == 1 and "core dependency" in res.output


def test_doctor_json_shape(monkeypatch):
    monkeypatch.setattr(cli, "default_tools", _fake_tools(CORE))
    payload = json.loads(runner.invoke(cli.app, ["doctor", "--json"]).stdout)
    assert payload["mofsift"] == __version__ and payload["config"] is None
    assert {"name", "tier", "available", "version", "hint"} <= set(payload["tools"][0])


@pytest.mark.parametrize("command", ["export", "merge", "validate"])
def test_unimplemented_stage_commands_are_not_exposed(command):
    res = runner.invoke(cli.app, [command])
    assert res.exit_code == 2 and "No such command" in res.output
