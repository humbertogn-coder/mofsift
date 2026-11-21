"""Decide, before anything runs, which stages will run, be skipped, or cannot run.

The same decision is used by ``mofsift doctor`` (to show it) and by the stage runner (to
act on it), so what the user is told and what happens cannot drift apart.
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Literal

from mofsift.columns import COLUMN_INFO
from mofsift.config import Config, GeometryConfig, XtbConfig
from mofsift.filters import ID_NAME, validate_expression

Action = Literal["run", "skip", "error"]


@dataclass(frozen=True)
class StagePlan:
    stage: str
    action: Action
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _optional(stage: str, block: GeometryConfig | XtbConfig, tool: str, found: bool, hint: str) -> StagePlan:
    if block.enabled is False:
        return StagePlan(stage, "skip", "disabled in the config")
    if found:
        return StagePlan(stage, "run")
    reason = f"{tool} is not installed ({hint})"
    if block.enabled is True and block.on_missing == "error":
        return StagePlan(stage, "error", reason + "; the config sets enabled: true and on_missing: error")
    return StagePlan(stage, "skip", reason)


def plan_stages(cfg: Config, available: Mapping[str, bool]) -> list[StagePlan]:
    """Plan every stage of ``cfg`` given which tools are available (by tool name)."""
    plans: list[StagePlan] = []
    gen = cfg.generate
    if gen.source == "cifs":
        plans.append(StagePlan("generate", "run", f"reads CIF files from {gen.cif_dir}"))
    elif available.get("pormake", False):
        plans.append(StagePlan("generate", "run"))
    else:
        plans.append(
            StagePlan(
                "generate",
                "error",
                "pormake is not installed (pip install 'mofsift[generate]'); "
                "alternatively set generate.source: cifs to start from existing structures",
            )
        )
    plans.append(StagePlan("qc", "run"))
    if cfg.describe.composition:
        plans.append(StagePlan("describe.composition", "run"))
    else:
        plans.append(StagePlan("describe.composition", "skip", "disabled in the config"))
    geo = cfg.describe.geometry
    if geo.tool == "pyzeo":
        geo_tool, geo_hint = "pyzeo", "pip install 'mofsift[zeo]'"
    else:
        geo_tool, geo_hint = "zeo++ (network)", "conda install -c conda-forge zeopp-lsmo"
    plans.append(_optional("describe.geometry", geo, geo_tool, available.get(geo_tool, False), geo_hint))
    plans.append(
        _optional(
            "describe.xtb",
            cfg.describe.xtb,
            "tblite",
            available.get("tblite", False),
            "pip install 'mofsift[xtb]'",
        )
    )
    if cfg.score.components:
        plans.append(StagePlan("score", "run", "components: " + ", ".join(cfg.score.components)))
    else:
        plans.append(StagePlan("score", "skip", "no components listed in the config"))
    plans.append(_filter_plan(cfg))
    if cfg.export.table_format == "parquet" and not available.get("pyarrow", False):
        plans.append(
            StagePlan(
                "export",
                "error",
                "table_format: parquet needs pyarrow, which is not installed "
                "(pip install 'mofsift[parquet]')",
            )
        )
    else:
        parts = [f"table ({cfg.export.table_format})"]
        parts += ["records for a viewer"] if cfg.export.viewer_bundle else []
        parts += ["structure files"] if cfg.export.include_cifs else []
        plans.append(StagePlan("export", "run", ", ".join(parts)))
    return plans


def _filter_plan(cfg: Config) -> StagePlan:
    """Filters can name any column a stage writes; a name that no stage writes is caught here."""
    if not cfg.filter:
        return StagePlan("filter", "skip", "no filters in the config")
    known = {ID_NAME, *COLUMN_INFO}
    unknown: list[str] = []
    for expression in cfg.filter:
        for name in sorted(validate_expression(expression) - known):
            close = difflib.get_close_matches(name, sorted(known), n=1)
            unknown.append(f"{name!r}" + (f" (did you mean {close[0]!r}?)" if close else ""))
    if unknown:
        return StagePlan("filter", "error", "no stage writes the column " + ", ".join(unknown))
    return StagePlan("filter", "run", f"{len(cfg.filter)} expression(s)")
