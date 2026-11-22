"""Run docs/tutorial.md for real and compare the outcome with what the page says.

Needs PORMAKE, pyzeo and tblite; about two minutes. When this test fails after a deliberate
change of the program, the message says which line or value of the tutorial to update.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import shutil
from pathlib import Path

import pandas as pd
import pytest
import yaml
from typer.testing import CliRunner

from docs_helpers import commands, missing_output_line, parse_tutorial, table_cells
from mofsift import cli
from mofsift.columns import COLUMN_INFO
from mofsift.export import validate_bundle
from mofsift.io.tables import read_table

needed = ("pormake", "pyzeo", "tblite")
pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(
        any(importlib.util.find_spec(m) is None for m in needed), reason="needs PORMAKE, pyzeo and tblite"
    ),
]

# How far a number of the tutorial may be from a fresh run before the page counts as wrong.
# Generated coordinates can differ between software environments (docs/generate-and-qc.md,
# "Reruns"), and pore diameters and xTB levels follow them.
TOLERANCE = {"angstrom": 1.0, "eV": 0.3}
RELATIVE = 0.02


def _run_commands(block: str) -> str:
    printed = []
    for tokens in commands(block):
        program, args = tokens[0], tokens[1:]
        if program == "mofsift":
            result = CliRunner().invoke(cli.app, args)
            assert result.exit_code == 0, (
                f"'{' '.join(tokens)}' ended with {result.exit_code}:\n{result.output}"
            )
            printed.append(result.output)
        elif program == "mkdir":
            Path(args[0]).mkdir(parents=True)
        elif program == "cp":
            shutil.copyfile(args[0], args[1])
        else:  # pragma: no cover - the core-tier test of the tutorial refuses other programs
            raise AssertionError(f"the tutorial test cannot run {program!r}")
    return "".join(printed)


def _run_python(code: str) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(compile(code, "tutorial.md", "exec"), {"__name__": "__tutorial__"})
    return out.getvalue()


def _check_results(step) -> None:
    table = read_table(step.arg).set_index("name")
    header, rows = table_cells(step.body)
    assert sorted(row[0] for row in rows) == sorted(table.index), "the tutorial lists other candidates"
    for row in rows:
        name = row[0]
        for column, cell in zip(header[1:], row[1:], strict=True):
            value = table.loc[name, column]
            where = f"docs/tutorial.md (line {step.line}) says {column} of {name} is {cell or 'empty'}"
            fix = "if the program is right, update the table of the tutorial"
            if cell == "":
                assert pd.isna(value), f"{where}; this run gives {value}; {fix}"
            elif cell in ("True", "False"):
                assert not pd.isna(value) and bool(value) is (cell == "True"), (
                    f"{where}; this run gives {value}; {fix}"
                )
            else:
                assert not pd.isna(value), f"{where}; this run has no value; {fix}"
                unit = COLUMN_INFO[column].unit
                allowed = TOLERANCE.get(unit, RELATIVE * abs(float(cell)))
                if str(table[column].dtype) == "Int64":
                    allowed = 0
                assert abs(float(value) - float(cell)) <= allowed, f"{where}; this run gives {value}; {fix}"


def test_the_tutorial_does_what_it_says(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    steps = parse_tutorial()
    printed = ""
    for step in steps:
        if step.kind == "file":
            Path(step.arg).write_text(step.body + "\n", encoding="utf-8")
        elif step.kind == "section":
            name, key = step.arg.split()
            config = yaml.safe_load(Path(name).read_text(encoding="utf-8"))
            config[key] = yaml.safe_load(step.body)[key]
            Path(name).write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        elif step.kind == "run":
            printed = _run_commands(step.body)
        elif step.kind == "python":
            printed = _run_python(step.body)
        elif step.kind == "output":
            missing = missing_output_line(step.body, printed)
            assert missing is None, (
                f"docs/tutorial.md (line {step.line}) shows a line the program did not print:\n  {missing}\n"
                f"What it printed:\n{printed}"
            )
        elif step.kind == "results":
            _check_results(step)

    # what the page promises at its end: three bundles, each valid
    counts = {}
    for folder in ("export/first_screening", "export/low_gap", "export/own_cifs"):
        outcome = validate_bundle(folder)
        assert outcome.problems == [], f"{folder}: {outcome.problems}"
        counts[folder] = len(read_table(Path(folder) / "candidates.csv"))
    assert counts == {"export/first_screening": 6, "export/low_gap": 1, "export/own_cifs": 2}
