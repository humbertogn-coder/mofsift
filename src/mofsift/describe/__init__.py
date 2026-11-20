"""Describe stage: composition, pore geometry (Zeo++) and electronic proxies (xTB)."""

from mofsift.describe.composition import composition_features, primary_metal
from mofsift.describe.isolate import WorkerError
from mofsift.describe.stage import (
    STAGE,
    SUBSTAGES,
    VALUE_COLUMNS,
    DescribeResult,
    SubstageReport,
    parse_block,
    parse_only,
    run,
)

__all__ = [
    "STAGE",
    "SUBSTAGES",
    "VALUE_COLUMNS",
    "DescribeResult",
    "SubstageReport",
    "WorkerError",
    "composition_features",
    "parse_block",
    "parse_only",
    "primary_metal",
    "run",
]
