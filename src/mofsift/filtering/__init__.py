"""Filter step: mark which rows satisfy the filters of the config. No row is removed."""

from mofsift.filtering.stage import COLUMNS, STAGE, FilterReport, FilterResult, run, selected

__all__ = ["COLUMNS", "STAGE", "FilterReport", "FilterResult", "run", "selected"]
