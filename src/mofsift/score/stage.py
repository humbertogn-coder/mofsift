"""The score stage: synthetic-accessibility components, each reported on its own.

Linker components (from the linker SMILES; the worst linker counts when there are several):
``linker_sa``, ``linker_sc``, ``linker_size``, ``linker_rotatable_bonds``.

Precedent components (counts of distinct synthesised MOFs in the reference, see
``reference``): ``precedent_topology``, ``precedent_node`` (the primary metal),
``precedent_linker`` and ``precedent_pair`` (the least common linker, or pair, counts).

None of these is a probability. ``docs/score-validation.md`` shows how well each one ranks
synthesised MOFs above hypothetical ones; a component that does not is still reported but
cannot be part of the optional aggregate.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from mofsift.chem import parse_formula
from mofsift.config import Config
from mofsift.describe.composition import primary_metal
from mofsift.io.tables import register_columns
from mofsift.score.linker import COLUMNS as LINKER_COLUMNS
from mofsift.score.linker import LinkerScoreError, linker_scores
from mofsift.score.reference import Reference, load_reference, normalise_linker
from mofsift.stages import STATUS_OK, STATUS_SKIPPED, check_contract

STAGE = "score"
PRECEDENT_COLUMNS = {
    "precedent_topology": "Int64",
    "precedent_node": "Int64",
    "precedent_linker": "Int64",
    "precedent_pair": "Int64",
}
COMPONENT_COLUMNS = {**LINKER_COLUMNS, **PRECEDENT_COLUMNS}
SMALLER_IS_BETTER = frozenset(LINKER_COLUMNS)
OTHER_COLUMNS = {
    "score_aggregate": "Float64",
    "score_notes": "string",
    "score_status": "string",
    "score_skip_reason": "string",
}
register_columns({**COMPONENT_COLUMNS, **OTHER_COLUMNS})


@dataclass
class ScoreResult:
    table: pd.DataFrame
    reference: dict[str, Any]
    available: dict[str, int]  # rows with a value, per component


def _value(row: pd.Series, column: str) -> Any:
    value = row.get(column, pd.NA)
    return None if pd.isna(value) else value


def _metal(row: pd.Series) -> str | None:
    metal = _value(row, "primary_metal")
    if metal is None and _value(row, "formula") is not None and "primary_metal" not in row.index:
        try:
            metal = primary_metal(parse_formula(row["formula"]))
        except ValueError:
            metal = None
    return metal


def score_row(
    row: pd.Series, components: list[str], reference: Reference
) -> tuple[dict[str, Any], list[str]]:
    """Component values of one candidate, and notes on what could not be computed and why."""
    values: dict[str, Any] = dict.fromkeys(components)
    notes: list[str] = []
    raw = _value(row, "linker_smiles")
    smiles = [s for s in raw.split(";") if s] if isinstance(raw, str) else []
    linker_wanted = [c for c in components if c in LINKER_COLUMNS]
    if linker_wanted or "precedent_linker" in components or "precedent_pair" in components:
        if not smiles:
            notes.append("linker components: no linker SMILES for this candidate")
        else:
            try:
                scored = [linker_scores(s) for s in smiles]
                for name in linker_wanted:
                    per_linker = [s[name] for s in scored]
                    values[name] = None if any(v is None for v in per_linker) else max(per_linker)
            except LinkerScoreError as err:
                notes.append(f"linker components: {err}")
            normalised = [normalise_linker(s) for s in smiles]
            if any(n is None for n in normalised):
                notes.append("precedent of the linker: a linker SMILES cannot be read")
            else:
                if "precedent_linker" in components:
                    values["precedent_linker"] = reference.linker_count(normalised)
                if "precedent_pair" in components:
                    values["precedent_pair"] = reference.pair_count(_metal(row), normalised)
                    if values["precedent_pair"] is None:
                        notes.append("precedent_pair: no metal")
    if "precedent_topology" in components:
        values["precedent_topology"] = reference.topology_count(_value(row, "topology"))
        if values["precedent_topology"] is None:
            notes.append("precedent_topology: the topology of this structure is not known")
    if "precedent_node" in components:
        values["precedent_node"] = reference.metal_count(_metal(row))
        if values["precedent_node"] is None:
            notes.append("precedent_node: no metal")
    return values, notes


def aggregate(table: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """Weighted mean of the rank of each component within this table: 0 (worst) to 1 (best).

    A rank-based indicator: it orders the candidates of one table and means nothing across
    tables. It is missing for a row that lacks any of the weighted components.
    """
    total = float(sum(weights.values()))
    out = pd.Series(0.0, index=table.index, dtype="Float64")
    for name, weight in weights.items():
        column = table[name].astype("Float64")
        ranks = (-column if name in SMALLER_IS_BETTER else column).rank(pct=True, method="average")
        out = out + ranks * (weight / total)
    return out.round(4)


def run(
    table: pd.DataFrame, cfg: Config, workdir: str | Path | None = None, *, reference: Reference | None = None
) -> ScoreResult:
    """Add the components listed in ``score.components`` (and the aggregate, when enabled)."""
    reference = reference or load_reference()
    components = list(cfg.score.components)
    agg = cfg.score.aggregate
    types = {c: COMPONENT_COLUMNS[c] for c in components}
    if agg.enabled:
        types["score_aggregate"] = OTHER_COLUMNS["score_aggregate"]
    types.update({k: v for k, v in OTHER_COLUMNS.items() if k != "score_aggregate"})
    clash = [c for c in types if c in table.columns]
    if clash:
        raise ValueError(
            f"the input table already has columns written by the score stage: {', '.join(clash)}"
        )

    rows: list[dict[str, Any]] = []
    for _, row in table.iterrows():
        out: dict[str, Any] = dict.fromkeys(types, pd.NA)
        if not components:
            out["score_status"], out["score_skip_reason"] = (
                STATUS_SKIPPED,
                "no components listed in the config",
            )
        else:
            values, notes = score_row(row, components, reference)
            out.update({k: (pd.NA if v is None else v) for k, v in values.items()})
            if all(v is None for v in values.values()):
                out["score_status"], out["score_skip_reason"] = STATUS_SKIPPED, "; ".join(notes)
            else:
                out["score_status"] = STATUS_OK
                out["score_notes"] = "; ".join(notes) if notes else pd.NA
        rows.append(out)
    added = pd.DataFrame(rows, columns=list(types), index=table.index).astype(types)
    if agg.enabled:
        added["score_aggregate"] = aggregate(added, dict(agg.weights))
    out_table = pd.concat([table, added], axis=1)
    check_contract(table, out_table, STAGE)
    return ScoreResult(
        out_table,
        {"n_mofs": reference.n_mofs, "n_with_topology": reference.n_with_topology, **reference.info},
        {c: int(added[c].notna().sum()) for c in components},
    )
