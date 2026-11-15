"""Decide, before anything runs, which stages will run, be skipped, or cannot run.

The same decision is used by ``mofsift doctor`` (to show it) and by the stage runner (to
act on it), so what the user is told and what happens cannot drift apart.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Literal

from mofsift.config import Config, GeometryConfig, XtbConfig

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
    if cfg.filter:
        plans.append(StagePlan("filter", "run", f"{len(cfg.filter)} expression(s)"))
    else:
        plans.append(StagePlan("filter", "skip", "no filters in the config"))
    plans.append(StagePlan("export", "run"))
    return plans
