"""Generate stage: candidate table and structure files, from PORMAKE or from existing CIFs."""

from mofsift.generate.enumerate import BlockInfo, Combination, TopologyInfo, enumerate_combinations
from mofsift.generate.stage import (
    COLUMNS,
    STAGE,
    GenerateError,
    GenerateResult,
    build_record_path,
    preview,
    run,
    structure_path,
)

__all__ = [
    "COLUMNS",
    "STAGE",
    "BlockInfo",
    "Combination",
    "GenerateError",
    "GenerateResult",
    "TopologyInfo",
    "build_record_path",
    "enumerate_combinations",
    "preview",
    "run",
    "structure_path",
]
