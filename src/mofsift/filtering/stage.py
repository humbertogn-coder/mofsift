"""The filter step: evaluate the ``filter`` expressions of the config on every row.

Like every stage it only adds columns. ``filter_pass`` is true for the rows that satisfy
every expression; ``filter_rejected_by`` names the expressions a row did not satisfy. A
row for which an expression cannot be decided because a value is missing does not pass,
and the missing columns are recorded: ``xtb_gap < 1 [missing: xtb_gap]``. The same holds
for a division by zero: ``lcd / pld > 2 [division by zero or overflow]``.

Rejected rows stay in the work table (``05_filter.csv``). Only the export leaves them out.
Without filters the step is skipped: ``filter_pass`` stays empty and every row is exported.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from mofsift.config import Config
from mofsift.filters import evaluate, missing_columns
from mofsift.io.tables import register_columns
from mofsift.stages import STATUS_OK, check_contract, skip_all

STAGE = "filter"
COLUMNS = {
    "filter_pass": "boolean",
    "filter_rejected_by": "string",
    "filter_status": "string",
    "filter_skip_reason": "string",
}
register_columns(COLUMNS)
NO_FILTERS = "no filters in the config"


@dataclass(frozen=True)
class FilterReport:
    """What one expression did to the table."""

    expression: str
    passed: int
    rejected: int
    undecided: int  # rows for which a missing value left the answer open; they do not pass

    def as_dict(self) -> dict[str, Any]:
        return {
            "expression": self.expression,
            "passed": self.passed,
            "rejected": self.rejected,
            "undecided": self.undecided,
        }


@dataclass
class FilterResult:
    table: pd.DataFrame
    reports: list[FilterReport]
    n_selected: int


def selected(table: pd.DataFrame) -> pd.Series:
    """Mask of the rows that go into an export: every row that was not rejected by a filter."""
    if "filter_pass" not in table.columns:
        return pd.Series(True, index=table.index)
    return table["filter_pass"].astype("boolean").fillna(True).astype(bool)


def run(table: pd.DataFrame, cfg: Config, workdir: str | Path | None = None) -> FilterResult:
    """Add ``filter_pass`` and ``filter_rejected_by``. Raises ``FilterError`` for a filter that
    cannot be applied to this table (unknown column, values that cannot be compared)."""
    clash = [c for c in COLUMNS if c in table.columns]
    if clash:
        raise ValueError(
            f"the input table already has columns written by the filter step: {', '.join(clash)}"
        )
    added = pd.DataFrame(
        {name: pd.Series(pd.NA, index=table.index, dtype=dtype) for name, dtype in COLUMNS.items()}
    )
    out = pd.concat([table, added], axis=1)
    if not cfg.filter:
        out = skip_all(out, STAGE, NO_FILTERS).astype(COLUMNS)
        check_contract(table, out, STAGE)
        return FilterResult(out, [], len(out))

    passed = pd.Series(True, index=table.index, dtype="boolean")
    why = np.full(len(table), "", dtype=object)
    reports: list[FilterReport] = []
    for expression in cfg.filter:
        expression = expression.strip()
        answer = evaluate(expression, table)
        undecided = answer.isna()
        rejected = (~answer).fillna(False).astype(bool)
        tag = np.where(rejected.to_numpy(), expression, "").astype(object)
        if undecided.any():
            missing = missing_columns(expression, table, undecided)
            tag[undecided.to_numpy(dtype=bool)] = [
                f"{expression} [missing: {m}]" if m else f"{expression} [division by zero or overflow]"
                for m in missing
            ]
        both = (why != "") & (tag != "")
        why = np.where(both, why + "; " + tag, np.where(tag != "", tag, why))
        passed &= answer.fillna(False)
        reports.append(
            FilterReport(
                expression,
                passed=int(answer.fillna(False).sum()),
                rejected=int(rejected.sum()),
                undecided=int(undecided.sum()),
            )
        )
    out["filter_pass"] = passed
    out["filter_rejected_by"] = pd.Series(why, index=table.index).replace("", pd.NA).astype("string")
    out["filter_status"] = STATUS_OK
    out = out.astype(COLUMNS)
    check_contract(table, out, STAGE)
    return FilterResult(out, reports, int(passed.sum()))
