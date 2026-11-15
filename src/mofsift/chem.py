"""Small chemistry helpers shared by the stages: formulas and the metal / non-metal split."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

_NONMETAL_SYMBOLS = "H He B C N O F Ne Si P S Cl Ar As Se Br Kr Te I Xe At Rn"
NONMETALS: frozenset[str] = frozenset(_NONMETAL_SYMBOLS.split())
"""Elements treated as non-metals. Every other element (Ge, Sb and Po included) counts as a metal."""

_TOKEN_RE = re.compile(r"([A-Z][a-z]?)(\d*)")


def is_metal(symbol: str) -> bool:
    return symbol not in NONMETALS


def count_symbols(symbols: Iterable[str]) -> dict[str, int]:
    """Element counts of a list of chemical symbols."""
    counts: dict[str, int] = {}
    for s in symbols:
        counts[s] = counts.get(s, 0) + 1
    return counts


def hill_formula(counts: Mapping[str, int]) -> str:
    """Formula in Hill order: C, then H, then the rest alphabetically (all alphabetical without C).

    This is the order ASE uses for ``Atoms.get_chemical_formula()``.
    """
    present = {k: int(v) for k, v in counts.items() if v}
    if any(v < 0 for v in present.values()):
        raise ValueError("element counts cannot be negative")
    if "C" in present:
        order = ["C"] + (["H"] if "H" in present else []) + sorted(k for k in present if k not in ("C", "H"))
    else:
        order = sorted(present)
    return "".join(f"{k}{present[k] if present[k] != 1 else ''}" for k in order)


def parse_formula(formula: str) -> dict[str, int]:
    """Element counts of a plain formula such as ``C24H12O13Zn4`` (no brackets, no charges)."""
    text = formula.strip()
    counts: dict[str, int] = {}
    pos = 0
    for m in _TOKEN_RE.finditer(text):
        if m.start() != pos:
            break
        counts[m.group(1)] = counts.get(m.group(1), 0) + int(m.group(2) or 1)
        pos = m.end()
    if not text or pos != len(text):
        raise ValueError(f"cannot parse formula {formula!r}")
    return counts
