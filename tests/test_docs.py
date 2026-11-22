"""The documentation must agree with the program.

These tests need no optional tool. They check that what the pages show exists: commands and
their options, config snippets, links, quoted messages, the generated reference pages and the
structure of the tutorial. The tutorial is run for real in ``test_tutorial_external.py``.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest
import typer
import yaml

import mofsift
from docs_helpers import (
    DOCS,
    ROOT,
    STEP_KINDS,
    commands,
    fenced_blocks,
    heading_anchors,
    inline_code,
    markdown_files,
    missing_output_line,
    parse_tutorial,
    prose,
    table_cells,
)
from mofsift import cli, docgen
from mofsift.columns import COLUMN_INFO
from mofsift.config import Config, parse_config

PAGES = markdown_files()
IDS = [str(p.relative_to(ROOT)) for p in PAGES]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------- generated pages
@pytest.mark.parametrize("name", sorted(docgen.PAGES))
def test_generated_reference_pages_are_current(name):
    """After changing the config model or a column: python -m mofsift.docgen docs"""
    assert _read(DOCS / name) == docgen.PAGES[name]()


def test_every_config_key_has_a_description():
    assert docgen.undocumented_config_keys() == []


def test_reference_pages_cover_every_key_and_column():
    config_page = _read(DOCS / docgen.CONFIG_PAGE)
    for key in ("project", "generate.linkers.smiles", "describe.xtb.max_atoms", "score.aggregate.weights"):
        assert f"| `{key}` |" in config_page
    columns_page = _read(DOCS / docgen.COLUMNS_PAGE)
    for name in ("id", *COLUMN_INFO):
        assert f"| `{name}` |" in columns_page, name


# -------------------------------------------------------------------- links
_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


@pytest.mark.parametrize("page", PAGES, ids=IDS)
def test_links_point_at_something(page):
    text = _read(page)
    for target in _LINK_RE.findall(prose(text)):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        path, _, anchor = target.partition("#")
        file = (page.parent / path).resolve() if path else page
        assert file.exists(), f"{page.name}: link to {target}, which does not exist"
        if anchor and file.suffix == ".md":
            assert anchor in heading_anchors(_read(file)), f"{page.name}: no heading for {target}"


def test_the_index_lists_every_page():
    index = _read(DOCS / "README.md")
    for page in DOCS.glob("*.md"):
        if page.name != "README.md":
            assert f"({page.name})" in index, f"docs/README.md does not link to {page.name}"


# --------------------------------------------------------- commands, options
_CLICK = typer.main.get_command(cli.app)
_OPTIONS = {
    name: {opt for param in command.params for opt in param.opts} | {"--help"}
    for name, command in _CLICK.commands.items()
}
_PLACEHOLDER = re.compile(r"^[A-Z_.<\[]")  # COMMAND, <stage>, [--only ...]


def _mofsift_calls(text: str) -> list[list[str]]:
    """Token lists that follow the program name in the code of a page."""
    snippets = [body for _, body in fenced_blocks(text)] + inline_code(text)
    calls = []
    for snippet in snippets:
        for line in snippet.splitlines():
            for part in re.split(r"&&|\|\||;|\|", line.split("#")[0]):
                # the program at the start of a command, possibly with a path: not "conda activate mofsift"
                found = re.match(r"\s*(?:\$\s+)?(?:srun\s+)?(?:\S*/)?mofsift\s+(\S.*)$", part.rstrip())
                if found:
                    calls.append(found.group(1).split())
    return calls


def _slurm_templates() -> list[Path]:
    return sorted((ROOT / "examples" / "slurm").glob("*.slurm"))


@pytest.mark.parametrize("page", [*PAGES, *_slurm_templates()], ids=lambda p: str(p.relative_to(ROOT)))
def test_commands_and_options_shown_exist(page):
    text = _read(page) if page.suffix == ".md" else f"```\n{_read(page)}\n```\n"
    for tokens in _mofsift_calls(text):
        first = tokens[0]
        if first in ("--version", "--help") or _PLACEHOLDER.match(first):
            continue
        assert first in _OPTIONS, f"{page.name}: 'mofsift {first}' is not a command"
        for token in tokens[1:]:
            option = token.split("=")[0].rstrip(",.)`")
            if option.startswith("--") and not _PLACEHOLDER.match(option.lstrip("-")):
                assert option in _OPTIONS[first], f"{page.name}: 'mofsift {first}' has no option {option}"


def test_the_command_check_sees_the_commands():
    """Guard for the test above: it must find real calls, and would notice an invented one."""
    calls = _mofsift_calls(_read(ROOT / "README.md"))
    assert ["run", "mofsift.yaml"] in calls
    assert {c[0] for c in calls} >= {"init", "doctor", "run", "validate"}
    invented = "```bash\nconda activate mofsift\ncd x && /opt/bin/mofsift frobnicate x.yaml --fast\n```\n"
    assert _mofsift_calls(invented) == [["frobnicate", "x.yaml", "--fast"]]
    assert "--out" in _OPTIONS["export"] and "--fast" not in _OPTIONS["run"]


# ----------------------------------------------------------- config snippets
_BASE = {
    "project": "docs",
    "generate": {"source": "pormake", "topologies": ["pcu"], "nodes": ["N16"], "edges": ["E14"]},
}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in extra.items():
        out[key] = (
            _merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
        )
    return out


def _config_snippets(text: str) -> list[dict]:
    found = []
    for language, body in fenced_blocks(text):
        if language != "yaml":
            continue
        data = yaml.safe_load(body)
        if isinstance(data, dict) and data and set(data) <= set(Config.model_fields):
            found.append(data)
    return found


@pytest.mark.parametrize("page", PAGES, ids=IDS)
def test_config_snippets_are_valid(page):
    for snippet in _config_snippets(_read(page)):
        parse_config(_merge(_BASE, snippet), source=f"{page.name}: {next(iter(snippet))}")


def test_the_snippet_check_sees_the_snippets():
    total = sum(len(_config_snippets(_read(page))) for page in PAGES)
    assert total >= 12
    with pytest.raises(ValueError, match="unknown key"):
        parse_config(_merge(_BASE, {"generate": {"topologys": ["pcu"]}}))


# ------------------------------------------------------------------ version
def test_the_version_is_the_same_everywhere():
    citation = yaml.safe_load(_read(ROOT / "CITATION.cff"))
    assert citation["version"] == mofsift.__version__
    assert f"`{mofsift.__version__}`" in _read(ROOT / "README.md"), "update the status note of the README"


# ------------------------------------------------------- quoted messages
_QUOTED_MESSAGES = (
    "A core dependency is missing or broken; reinstall mofsift.",
    "unknown key",
    "did you mean",
    "unknown building block",
    "no stage writes the column",
    "is not installed",
    "the config sets enabled: true",
    "does not exist; run 'mofsift",
    "is not empty and does not hold a mofsift bundle; choose another",
    "the file differs from the one the manifest describes",
    "is not a valid bundle; nothing was merged",
    "did not pass qc",
    "no structure",
    "NOT a valid bundle",
)


def test_troubleshooting_quotes_messages_the_program_prints():
    page = " ".join(_read(DOCS / "troubleshooting.md").split())
    source = " ".join(
        " ".join(path.read_text(encoding="utf-8").split())
        for path in (ROOT / "src" / "mofsift").rglob("*.py")
    )
    for message in _QUOTED_MESSAGES:
        assert message in page, f"troubleshooting.md no longer mentions: {message}"
        assert message in source, f"the program no longer prints: {message}"


# ----------------------------------------------------------------- tutorial
def test_the_tutorial_is_made_of_known_steps():
    steps = parse_tutorial()
    assert {s.kind for s in steps} == set(STEP_KINDS)
    for before, step in zip([None, *steps], steps, strict=False):
        assert step.kind in STEP_KINDS, f"tutorial line {step.line}: unknown step {step.kind}"
        if step.kind == "output":
            assert before is not None and before.kind in ("run", "python"), f"tutorial line {step.line}"
        if step.kind == "run":
            for tokens in commands(step.body):
                assert tokens[0] in ("mofsift", "mkdir", "cp"), f"tutorial line {step.line}: {tokens[0]}"


def test_the_configs_of_the_tutorial_are_valid():
    files: dict[str, dict] = {}
    for step in parse_tutorial():
        if step.kind == "file":
            files[step.arg] = yaml.safe_load(step.body)
        elif step.kind == "section":
            name, key = step.arg.split()
            replacement = yaml.safe_load(step.body)
            assert list(replacement) == [key], f"tutorial line {step.line}"
            files[name] = {**files[name], **replacement}
        else:
            continue
        parse_config(files[step.arg.split()[0]], source=f"tutorial line {step.line}")
    assert set(files) == {"mofsift.yaml", "own_cifs.yaml"}


def test_the_table_of_the_tutorial_names_real_columns():
    (step,) = [s for s in parse_tutorial() if s.kind == "results"]
    header, rows = table_cells(step.body)
    assert header[0] == "name" and set(header[1:]) <= set(COLUMN_INFO)
    assert len(rows) == 9 and all(len(row) == len(header) for row in rows)


def test_output_blocks_are_compared_line_by_line_in_order():
    printed = (
        "  [1/2] a: ok (2.9 s)\n  [2/2] b: ok (0.1 s)\n"
        "generate: 2 row(s): 2 ok\n  table: /tmp/x/work/01.csv\n"
    )
    shown = "  [1/2] a: ok (… s)\n…\ngenerate: 2 row(s): 2 ok\n  table: …/work/01.csv"
    assert missing_output_line(shown, printed) is None
    assert missing_output_line("generate:   2 row(s): 2 ok", printed) is None  # runs of spaces are one space
    assert missing_output_line("generate: 2 row(s): 3 ok", printed) == "generate: 2 row(s): 3 ok"
    assert (
        missing_output_line("generate: 2 row(s)", printed) == "generate: 2 row(s)"
    )  # a whole line, not a part
    out_of_order = "generate: 2 row(s): 2 ok\n  [1/2] a: ok (… s)"
    assert missing_output_line(out_of_order, printed) == "  [1/2] a: ok (… s)"
