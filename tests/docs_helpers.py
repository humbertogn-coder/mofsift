"""Reading the documentation the way the tests need it: code blocks, links and tutorial steps.

The tutorial (``docs/tutorial.md``) marks the parts a test acts on with HTML comments, which a
reader of the rendered page does not see::

    <!-- tutorial:file NAME -->            the next code block is the content of the file NAME
    <!-- tutorial:section FILE KEY -->     the next YAML block replaces the top-level KEY of FILE
    <!-- tutorial:run -->                  the next code block holds commands to run, one per line
    <!-- tutorial:python -->               the next code block is Python to execute
    <!-- tutorial:output -->               the next block is what the step before printed
    <!-- tutorial:results TABLE -->        the next Markdown table holds values of the CSV file TABLE

In an output block a line that is only an ellipsis stands for any number of lines, and an
ellipsis inside a line for any text (timings, paths, versions).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
TUTORIAL = DOCS / "tutorial.md"
ELLIPSIS = "…"

_FENCE_RE = re.compile(r"^[ \t]*```([^\n`]*)\n(.*?)^[ \t]*```[ \t]*$", re.M | re.S)
_INLINE_RE = re.compile(r"`([^`\n]+)`")
_MARKER_RE = re.compile(r"^<!-- tutorial:(\w+)([^>\n]*?) -->\n", re.M)

STEP_KINDS = ("file", "section", "run", "python", "output", "results")


def markdown_files() -> list[Path]:
    """The pages a user reads: the top-level ones and everything in docs/."""
    top = [ROOT / name for name in ("README.md", "CONTRIBUTING.md", "CHANGELOG.md")]
    return [*top, *sorted(DOCS.glob("*.md"))]


def fenced_blocks(text: str) -> list[tuple[str, str]]:
    """(language, body) of every fenced code block."""
    return [(m.group(1).strip(), m.group(2)) for m in _FENCE_RE.finditer(text)]


def inline_code(text: str) -> list[str]:
    """Inline code spans outside fenced blocks."""
    return _INLINE_RE.findall(_FENCE_RE.sub("", text))


def prose(text: str) -> str:
    """The text without code blocks and inline code, for looking at links and headings."""
    return _INLINE_RE.sub("", _FENCE_RE.sub("", text))


def heading_anchors(text: str) -> set[str]:
    """Anchors of the headings of a page, the way GitHub derives them."""
    anchors = set()
    for line in _FENCE_RE.sub("", text).splitlines():
        if line.startswith("#"):
            title = line.lstrip("#").strip().lower()
            anchors.add(re.sub(r"[^\w\- ]", "", title).replace(" ", "-"))
    return anchors


@dataclass(frozen=True)
class Step:
    kind: str
    arg: str
    body: str
    line: int  # line of the marker in the tutorial, for messages


def parse_tutorial(text: str | None = None) -> list[Step]:
    """The marked steps of the tutorial, in the order of the page."""
    text = TUTORIAL.read_text(encoding="utf-8") if text is None else text
    steps = []
    for marker in _MARKER_RE.finditer(text):
        kind, arg = marker.group(1), marker.group(2).strip()
        rest = text[marker.end() :]
        line = text.count("\n", 0, marker.start()) + 1
        if kind == "results":
            rows = []
            for row in rest.splitlines():
                if not row.startswith("|"):
                    break
                rows.append(row)
            body = "\n".join(rows)
        else:
            fence = _FENCE_RE.match(rest)
            if fence is None:
                raise ValueError(f"tutorial line {line}: a code block must follow the marker")
            body = fence.group(2).rstrip("\n")
        steps.append(Step(kind, arg, body, line))
    return steps


def table_cells(markdown_table: str) -> tuple[list[str], list[list[str]]]:
    """Header and rows of a Markdown table, cells stripped."""
    lines = [ln for ln in markdown_table.splitlines() if ln.strip()]
    split = lambda ln: [c.strip() for c in ln.strip().strip("|").split("|")]  # noqa: E731
    return split(lines[0]), [split(ln) for ln in lines[2:]]


def commands(block: str) -> list[list[str]]:
    """The commands of a shell block as token lists; comments and blank lines are dropped."""
    import shlex

    found = []
    for line in block.splitlines():
        tokens = shlex.split(line, comments=True)
        if tokens:
            found.append(tokens)
    return found


def missing_output_line(expected: str, actual: str) -> str | None:
    """The first line of ``expected`` that ``actual`` does not contain, in order; None if all are there.

    Runs of spaces are compared as one space. See the module docstring for the ellipsis.
    """
    squeeze = lambda s: " ".join(s.split())  # noqa: E731
    have = [squeeze(ln) for ln in actual.splitlines()]
    position = 0
    for line in expected.splitlines():
        want = squeeze(line)
        if not want or want == ELLIPSIS:
            continue
        pattern = re.compile(".*".join(re.escape(part) for part in want.split(ELLIPSIS)) + r"\Z")
        for index in range(position, len(have)):
            if pattern.match(have[index]):
                position = index + 1
                break
        else:
            return line
    return None
