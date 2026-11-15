from __future__ import annotations

import os
import stat
import sys

import pytest

from mofsift.config import ToolsConfig
from mofsift.tools import BinaryTool, PythonTool, collect_status, default_tools


def test_python_tool_present():
    t = PythonTool("pyyaml", "yaml", "pyyaml", "core", "config", "hint")
    s = t.status()
    assert t.available() and s.available and s.version and s.detail is None and s.kind == "python"


def test_python_tool_absent_is_reported_not_raised():
    t = PythonTool(
        "nope", "definitely_not_a_module_xyz", "definitely-not-a-dist", "zeo", "x", "pip install nope"
    )
    s = t.status()
    assert not t.available() and not s.available
    assert s.version is None and s.detail == "not installed" and s.hint == "pip install nope"


def test_python_tool_broken_import_is_reported(tmp_path, monkeypatch):
    (tmp_path / "broken_mod_for_test.py").write_text("raise RuntimeError('bad extension')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    t = PythonTool("broken", "broken_mod_for_test", "broken", "xtb", "x", "hint")
    assert t.available()  # found on the path ...
    ok, detail = t.check()  # ... but the real check catches that it does not import
    assert not ok and "import failed" in detail and "bad extension" in detail
    assert not t.status().available


def _fake_binary(folder, name, output):
    if os.name == "nt":
        path = folder / f"{name}.bat"
        path.write_text(f"@echo {output}\n")
    else:
        path = folder / name
        path.write_text(f"#!/bin/sh\necho '{output}'\n")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def test_binary_tool_on_path_with_version(tmp_path, monkeypatch):
    _fake_binary(tmp_path, "fakextb", "* xtb version 6.7.1 (edcfbbe) compiled by conda")
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    t = BinaryTool("xtb", ("fakextb",), "xtb", "x", "hint")
    s = t.status()
    assert s.available and s.version == "6.7.1" and s.location and s.kind == "binary"


def test_binary_tool_missing(monkeypatch):
    monkeypatch.setenv("PATH", "")
    s = BinaryTool("zeo", ("network_not_here",), "zeo", "x", "conda install zeopp-lsmo").status()
    assert not s.available and s.version is None and s.detail == "not found on PATH"


def test_binary_tool_configured_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    exe = _fake_binary(tmp_path, "network", "no version here")
    good = BinaryTool("zeo", ("network",), "zeo", "x", "hint", configured=exe, version_args=None)
    assert good.available() and good.version() is None  # found; version unknown is not an error
    bad = BinaryTool("zeo", ("network",), "zeo", "x", "hint", configured=tmp_path / "missing")
    ok, detail = bad.check()
    assert not ok and "configured path is not an executable" in detail


def test_detection_adapter_has_no_run():
    with pytest.raises(NotImplementedError):
        PythonTool("pyyaml", "yaml", "pyyaml", "core", "config", "hint").run()


def test_default_registry_covers_every_tier():
    tools = default_tools()
    names = [t.name for t in tools]
    assert len(names) == len(set(names))
    assert {"pormake", "pyzeo", "tblite", "rdkit", "zeo++ (network)"} <= set(names)
    assert {t.tier for t in tools} == {"core", "generate", "zeo", "xtb", "ml"}
    core = [s for s in collect_status(tools) if s.tier == "core"]
    assert core and all(s.available and s.version for s in core), [s for s in core if not s.available]


def test_configured_paths_reach_the_binary_adapters(tmp_path):
    cfg = ToolsConfig(zeopp=tmp_path / "network", xtb=tmp_path / "xtb")
    by_name = {t.name: t for t in default_tools(cfg)}
    assert by_name["zeo++ (network)"].configured == tmp_path / "network"
    assert by_name["xtb (binary)"].configured == tmp_path / "xtb"


def test_status_is_json_serialisable():
    import json

    json.dumps([s.as_dict() for s in collect_status()])
    assert sys.version_info >= (3, 11)
