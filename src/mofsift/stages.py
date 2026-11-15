"""The stage contract.

Every stage is a function ``run(table, cfg, workdir) -> table`` that

* only adds columns (it never drops, reorders or relabels rows, and never changes a
  value written by an earlier stage), and
* writes ``<stage>_status`` (``ok``, ``skipped`` or ``failed``) and
  ``<stage>_skip_reason`` for every row.

Filtering is a separate, explicit step, so a user can always see what was rejected and
why. Wrap a stage with :func:`stage` to have the contract checked on every call.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

STATUS_OK = "ok"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"
STATUSES = (STATUS_OK, STATUS_SKIPPED, STATUS_FAILED)


class StageContractError(RuntimeError):
    """A stage broke the contract (dropped rows, changed existing data, no status)."""


def status_columns(stage_name: str) -> tuple[str, str]:
    key = stage_name.replace(".", "_")
    return f"{key}_status", f"{key}_skip_reason"


def mark(
    table: pd.DataFrame,
    stage_name: str,
    status: str,
    reason: str | None = None,
    rows: pd.Series | None = None,
) -> pd.DataFrame:
    """Return a copy of ``table`` with the stage status set for all rows or for ``rows`` (a mask).

    A reason is required for ``skipped`` and ``failed`` and is stored as missing for ``ok``.
    """
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
    if status != STATUS_OK and not reason:
        raise ValueError(f"a reason is required when status is {status!r}")
    s_col, r_col = status_columns(stage_name)
    out = table.copy()
    for col in (s_col, r_col):
        if col not in out.columns:
            out[col] = pd.Series(pd.NA, index=out.index, dtype="object")
    mask = pd.Series(True, index=out.index) if rows is None else rows.reindex(out.index, fill_value=False)
    out.loc[mask, s_col] = status
    out.loc[mask, r_col] = pd.NA if status == STATUS_OK else reason
    return out


def skip_all(table: pd.DataFrame, stage_name: str, reason: str) -> pd.DataFrame:
    """Mark every row as skipped; used when a whole stage cannot run (tool missing, disabled)."""
    return mark(table, stage_name, STATUS_SKIPPED, reason)


def check_contract(before: pd.DataFrame, after: pd.DataFrame, stage_name: str) -> None:
    """Raise :class:`StageContractError` if ``after`` breaks the stage contract."""
    if not isinstance(after, pd.DataFrame):
        raise StageContractError(f"{stage_name}: a stage must return a DataFrame")
    if not before.index.equals(after.index):
        raise StageContractError(f"{stage_name}: rows were dropped, added or reordered")
    removed = [c for c in before.columns if c not in after.columns]
    if removed:
        raise StageContractError(f"{stage_name}: columns were removed: {', '.join(map(str, removed))}")
    s_col, r_col = status_columns(stage_name)
    own = {s_col, r_col}
    kept = [c for c in before.columns if c not in own]
    if kept and not before[kept].equals(after[kept]):
        changed = [c for c in kept if not before[c].equals(after[c])]
        raise StageContractError(
            f"{stage_name}: existing columns were modified: {', '.join(map(str, changed))}"
        )
    for col in (s_col, r_col):
        if col not in after.columns:
            raise StageContractError(f"{stage_name}: missing column {col}")
    status = after[s_col]
    if status.isna().any():
        raise StageContractError(f"{stage_name}: {int(status.isna().sum())} row(s) have no status")
    bad = sorted(set(status) - set(STATUSES))
    if bad:
        raise StageContractError(f"{stage_name}: unknown status value(s): {', '.join(map(str, bad))}")
    no_reason = (status != STATUS_OK) & after[r_col].isna()
    if no_reason.any():
        raise StageContractError(f"{stage_name}: {int(no_reason.sum())} skipped/failed row(s) have no reason")


StageFn = Callable[[pd.DataFrame, Any, Path], pd.DataFrame]


def stage(name: str) -> Callable[[StageFn], StageFn]:
    """Decorator that names a stage and enforces the contract on its output."""

    def decorator(fn: StageFn) -> StageFn:
        @functools.wraps(fn)
        def wrapper(table: pd.DataFrame, cfg: Any, workdir: Path) -> pd.DataFrame:
            after = fn(table, cfg, workdir)
            check_contract(table, after, name)
            return after

        wrapper.stage_name = name  # type: ignore[attr-defined]
        return wrapper

    return decorator
