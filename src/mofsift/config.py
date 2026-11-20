"""Run configuration: pydantic models, YAML loading and validation.

A config file declares the whole pipeline (generate -> qc -> describe -> score -> filter ->
export). Unknown keys are rejected so that a typo never silently changes a run. Paths are
stored as written and resolved against the folder of the config file when they are used,
so the config hash does not depend on the machine.

Regenerate the published JSON Schema after changing a model::

    python -m mofsift.config > src/mofsift/schemas/config.schema.json
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal, get_args

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from mofsift.filters import validate_expression

AutoBool = bool | Literal["auto"]
"""``auto`` runs a stage when its tool is installed and skips it (with a recorded reason) when not."""

ScoreComponent = Literal[
    "linker_sa",
    "linker_sc",
    "linker_size",
    "linker_rotatable_bonds",
    "precedent_node",
    "precedent_topology",
    "precedent_linker",
    "precedent_pair",
]
NOT_IN_AGGREGATE = {"precedent_node": "it does not separate synthesised from hypothetical MOFs"}
"""Components that may be reported but not weighted (see docs/score-validation.md)."""

_PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class ConfigError(ValueError):
    """Raised when a config file is missing, is not valid YAML, or fails validation."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)


def _no_duplicates(values: list[str], what: str) -> list[str]:
    seen: set[str] = set()
    dup = sorted({v for v in values if v in seen or seen.add(v)})
    if dup:
        raise ValueError(f"duplicate {what}: {', '.join(dup)}")
    return values


class LinkersConfig(_Model):
    """Organic linkers given as SMILES and converted to PORMAKE building blocks."""

    smiles_file: Path | None = Field(
        None, description="Text file with one 'SMILES name' pair per line; lines starting with # are ignored."
    )
    smiles: dict[str, str] = Field(
        default_factory=dict, description="Linkers written inline, as name: SMILES."
    )
    connection_distance: float = Field(
        0.75,
        gt=0,
        le=1.5,
        description="Distance (angstrom) from the anchor atom to the connection point; half a C-C bond.",
    )
    conformers: int = Field(
        10, ge=1, le=200, description="Conformers embedded per linker; the lowest in energy is kept."
    )

    @model_validator(mode="after")
    def _something_to_do(self) -> LinkersConfig:
        if self.smiles_file is None and not self.smiles:
            raise ValueError("give smiles_file, smiles, or both")
        return self


class GenerateConfig(_Model):
    """Where candidate structures come from."""

    source: Literal["pormake", "cifs"] = Field(
        "pormake", description="'pormake' assembles structures; 'cifs' reads existing CIF files."
    )
    topologies: list[str] = Field(default_factory=list, description="PORMAKE topology names, e.g. pcu.")
    nodes: list[str] = Field(default_factory=list, description="PORMAKE node building blocks, e.g. N16.")
    edges: list[str] = Field(
        default_factory=list,
        description="PORMAKE edge building blocks, e.g. E14. Leave empty to connect the nodes directly.",
    )
    linkers: LinkersConfig | None = Field(
        None,
        description="Linkers given as SMILES. With two connection points they are used as edges, "
        "with three or more as organic nodes, next to the blocks listed in nodes and edges.",
    )
    building_block_dir: Path | None = Field(
        None, description="Optional folder with extra building blocks in PORMAKE .xyz format."
    )
    cif_dir: Path | None = Field(None, description="Folder with CIF files (source: cifs).")
    max_atoms: int | None = Field(
        None, gt=0, description="Skip structures larger than this. null = no limit."
    )

    @field_validator("topologies", "nodes", "edges")
    @classmethod
    def _unique(cls, v: list[str], info) -> list[str]:
        return _no_duplicates(v, info.field_name)

    @model_validator(mode="after")
    def _source_requirements(self) -> GenerateConfig:
        if self.source == "pormake":
            missing = [k for k in ("topologies", "nodes") if not getattr(self, k)]
            if missing:
                raise ValueError(f"source 'pormake' needs non-empty: {', '.join(missing)}")
        elif self.cif_dir is None:
            raise ValueError("source 'cifs' needs cif_dir")
        return self


class QCConfig(_Model):
    """Thresholds of the built-in structural checks (distances in angstrom)."""

    min_distance: float = Field(0.70, gt=0, description="No pair of atoms may be closer than this.")
    min_distance_heavy: float = Field(
        1.00, gt=0, description="No heavy-heavy or H-H pair may be closer than this."
    )
    nonbonded_factor: float = Field(
        0.80, gt=0, description="Non-bonded contacts must exceed this fraction of the covalent-radius sum."
    )
    bond_factor: float = Field(1.20, gt=0, description="Multiplier on covalent radii used to detect bonds.")
    require_single_framework: bool = Field(True, description="Reject isolated atoms and extra fragments.")
    check_valence: bool = Field(True, description="H needs one neighbour; C needs two to four.")
    check_composition: bool = Field(
        True, description="Formula must equal the sum of the building blocks (when they are known)."
    )
    max_node_rmsd: float | None = Field(
        0.30,
        gt=0,
        description="Largest accepted misfit (angstrom) between a node and its vertex in the topology, "
        "as reported by PORMAKE. null = do not check.",
    )


class GeometryConfig(_Model):
    """Pore geometry from Zeo++."""

    enabled: AutoBool = "auto"
    tool: Literal["pyzeo", "zeopp"] = Field(
        "pyzeo", description="'pyzeo' = Python bindings (pip); 'zeopp' = the 'network' binary."
    )
    probe_radius: float = Field(1.86, gt=0, description="Probe radius in angstrom for accessible properties.")
    high_accuracy: Literal[False, "OCC", "DEF"] = Field(
        "OCC",
        description="Zeo++ high-accuracy Voronoi decomposition (-ha). OCC is the faster setting, DEF the "
        "most thorough and much slower on large cells, false turns it off.",
    )
    fallback: list[Literal[False, "OCC", "DEF"]] = Field(
        default_factory=lambda: ["DEF", False],
        description="Settings tried in this order when Zeo++ stops on a structure with high_accuracy. "
        "The column zeo_high_accuracy records which setting produced each row. [] = do not retry.",
    )
    timeout_minutes: float | None = Field(
        None, gt=0, description="Stop an attempt that takes longer than this; the next setting is then tried."
    )
    on_missing: Literal["skip", "error"] = Field(
        "skip", description="What to do when enabled is true and the tool is not installed."
    )

    @field_validator("fallback")
    @classmethod
    def _fallback_unique(cls, v: list[Any]) -> list[Any]:
        if len(set(map(str, v))) != len(v):
            raise ValueError("each setting may appear once")
        return v


class XtbConfig(_Model):
    """Semi-empirical electronic proxies (band gap, band edges) from tblite."""

    enabled: AutoBool = "auto"
    method: Literal["GFN1-xTB", "GFN2-xTB"] = "GFN1-xTB"
    periodic: bool = Field(
        True, description="Keep true for frameworks: a finite cluster is a different system."
    )
    max_atoms: int = Field(300, gt=0, description="Structures above this size are skipped, not truncated.")
    spin: Literal["high_spin", "low_spin"] = Field(
        "high_spin",
        description="Number of unpaired electrons. high_spin: rule of thumb (M(II) high-spin count for "
        "Mn, Fe, Co, Ni, Cu). low_spin: none, or one when the electron count is odd.",
    )
    electronic_temperature: float = Field(500.0, gt=0, description="Electronic temperature in kelvin.")
    max_iterations: int = Field(500, ge=1, description="Maximum number of SCF iterations.")
    timeout_minutes: float | None = Field(
        None, gt=0, description="Stop a structure that takes longer than this and record it as failed."
    )
    on_missing: Literal["skip", "error"] = "skip"


class DescribeConfig(_Model):
    only_qc_passed: bool = Field(
        True,
        description="Run pore geometry and xTB only on structures that passed qc. Set to false to run "
        "them on every structure.",
    )
    composition: bool = Field(True, description="Composition statistics (always available).")
    geometry: GeometryConfig = Field(default_factory=GeometryConfig)
    xtb: XtbConfig = Field(default_factory=XtbConfig)


class StrainConfig(_Model):
    enabled: bool = False

    @field_validator("enabled")
    @classmethod
    def _not_yet(cls, v: bool) -> bool:
        if v:
            raise ValueError("linker strain is not available in this version of mofsift; set it to false")
        return v


class AggregateConfig(_Model):
    """Optional rank-based summary of the score components. It is not a probability."""

    enabled: bool = False
    weights: dict[str, float] = Field(default_factory=dict)

    @field_validator("weights")
    @classmethod
    def _positive(cls, v: dict[str, float]) -> dict[str, float]:
        bad = sorted(k for k, w in v.items() if w <= 0)
        if bad:
            raise ValueError(f"weights must be positive: {', '.join(bad)}")
        return v


class ScoreConfig(_Model):
    """Synthetic-accessibility components, reported separately."""

    components: list[ScoreComponent] = Field(
        default_factory=lambda: [
            "linker_sa",
            "linker_sc",
            "linker_size",
            "linker_rotatable_bonds",
            "precedent_topology",
            "precedent_node",
            "precedent_linker",
            "precedent_pair",
        ]
    )
    strain: StrainConfig = Field(default_factory=StrainConfig)
    aggregate: AggregateConfig = Field(default_factory=AggregateConfig)

    @field_validator("components")
    @classmethod
    def _unique(cls, v: list[str]) -> list[str]:
        return _no_duplicates(v, "components")

    @model_validator(mode="after")
    def _aggregate_matches_components(self) -> ScoreConfig:
        agg = self.aggregate
        if agg.enabled and not agg.weights:
            raise ValueError("aggregate.enabled needs aggregate.weights")
        unknown = sorted(set(agg.weights) - set(self.components))
        if unknown:
            raise ValueError(f"aggregate.weights for components that are not listed: {', '.join(unknown)}")
        for name in sorted(set(agg.weights) & set(NOT_IN_AGGREGATE)):
            raise ValueError(f"aggregate.weights: {name} cannot be weighted: {NOT_IN_AGGREGATE[name]}")
        return self


class ExportConfig(_Model):
    viewer_bundle: bool = True
    include_cifs: bool = True
    table_format: Literal["csv", "parquet"] = "csv"


class ToolsConfig(_Model):
    """Optional explicit paths to external binaries (otherwise they are looked up on PATH)."""

    zeopp: Path | None = Field(None, description="Path to the Zeo++ 'network' binary.")
    xtb: Path | None = Field(None, description="Path to the xtb binary.")


class Config(_Model):
    """Top-level run configuration."""

    project: str = Field(description="Short name of the run; used in file names and the manifest.")
    seed: int = Field(0, ge=0)
    workdir: Path = Field(Path("work"), description="Folder for intermediate tables and structures.")
    generate: GenerateConfig
    qc: QCConfig = Field(default_factory=QCConfig)
    describe: DescribeConfig = Field(default_factory=DescribeConfig)
    score: ScoreConfig = Field(default_factory=ScoreConfig)
    filter: list[str] = Field(
        default_factory=list, description="Row filters applied as an explicit step, e.g. 'qc_pass == True'."
    )
    export: ExportConfig = Field(default_factory=ExportConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)

    @field_validator("project")
    @classmethod
    def _project_slug(cls, v: str) -> str:
        if not _PROJECT_RE.match(v):
            raise ValueError("use letters, digits, '_', '-' or '.', starting with a letter or digit")
        return v

    @field_validator("filter")
    @classmethod
    def _filters_parse(cls, v: list[str]) -> list[str]:
        for expr in v:
            validate_expression(expr)
        return v

    def canonical_json(self) -> str:
        """Deterministic JSON of the validated config (defaults included)."""
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    def config_hash(self) -> str:
        """SHA-256 of :meth:`canonical_json`; recorded in the export manifest."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def _all_field_names(model: type[BaseModel], seen: set[type] | None = None) -> set[str]:
    seen = seen if seen is not None else set()
    if model in seen:
        return set()
    seen.add(model)
    names = set(model.model_fields)
    for f in model.model_fields.values():
        for ann in (f.annotation, *get_args(f.annotation)):  # also models inside "Model | None"
            if isinstance(ann, type) and issubclass(ann, BaseModel):
                names |= _all_field_names(ann, seen)
    return names


def _format_errors(err: ValidationError, source: str) -> str:
    known = sorted(_all_field_names(Config))
    lines = [f"{source}: {err.error_count()} problem(s) in the config"]
    for e in err.errors():
        loc = ".".join(str(p) for p in e["loc"]) or "(top level)"
        msg = e["msg"].removeprefix("Value error, ")
        if e["type"] == "extra_forbidden":
            close = difflib.get_close_matches(str(e["loc"][-1]), known, n=1)
            msg = "unknown key" + (f"; did you mean '{close[0]}'?" if close else "")
        elif e["type"] == "missing":
            msg = "required key is missing"
        lines.append(f"  - {loc}: {msg}")
    return "\n".join(lines)


def parse_config(data: Any, source: str = "config") -> Config:
    """Validate an already-loaded mapping."""
    if not isinstance(data, dict):
        raise ConfigError(
            f"{source}: the config must be a YAML mapping (key: value), got {type(data).__name__}"
        )
    try:
        return Config.model_validate(data)
    except ValidationError as err:
        raise ConfigError(_format_errors(err, source)) from err


def load_config(path: str | Path) -> Config:
    """Read and validate a YAML config file."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        raise ConfigError(f"{path}: cannot read the config file ({err.strerror or err})") from err
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as err:
        raise ConfigError(f"{path}: not valid YAML ({err})") from err
    return parse_config(data, source=str(path))


def resolve_path(config_file: str | Path, value: str | Path) -> Path:
    """Resolve a path written in a config against the folder that holds the config file."""
    value = Path(value)
    return value if value.is_absolute() else Path(config_file).resolve().parent / value


def config_json_schema() -> dict[str, Any]:
    """JSON Schema (draft 2020-12) of the config file."""
    schema = Config.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://github.com/humbertogn-coder/mofsift/schemas/config.schema.json"
    return schema


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(config_json_schema(), indent=2, sort_keys=True))
