"""Precedent reference: how often topologies, node metals, linkers and metal-linker pairs occur
among experimentally synthesised MOFs.

The reference shipped with mofsift is built from the QMOF database (A. S. Rosen et al., Matter
2021, 4, 1578; npj Comput. Mater. 2022, 8, 195; data under CC BY 4.0), using its MOFid
decomposition of every structure flagged as synthesised. ``scripts/build_precedent_reference.py``
rebuilds it from ``qmof.csv``. Counts are over distinct MOFs (same nodes, linkers and topology
counted once), so that the many entries of one framework in the CSD do not inflate them.
"""

from __future__ import annotations

import ast
import gzip
import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from typing import Any

from mofsift.chem import is_metal, parse_formula
from mofsift.describe.composition import primary_metal

REFERENCE_FILE = "qmof_precedent.json.gz"
PAIR_SEPARATOR = "|"


@cache
def _uncharger():
    from rdkit.Chem.MolStandardize import rdMolStandardize

    return rdMolStandardize.Uncharger()


def normalise_linker(smiles: str) -> str | None:
    """Canonical SMILES of the neutral molecule, so that an acid and its anion compare equal.

    Returns None for SMILES that RDKit cannot read. Connection points written as ``[*]`` are
    kept: such a linker only matches itself.
    """
    from rdkit import Chem, RDLogger

    if not isinstance(smiles, str) or not smiles.strip():
        return None
    RDLogger.DisableLog("rdApp.*")
    try:
        mol = Chem.MolFromSmiles(smiles.strip())
        if mol is None or mol.GetNumAtoms() == 0:
            return None
        try:
            mol = _uncharger().uncharge(mol)
        except Exception:
            return None
        return Chem.MolToSmiles(mol)
    finally:
        RDLogger.EnableLog("rdApp.*")


@dataclass(frozen=True)
class MofRecord:
    """What the reference needs to know about one MOF."""

    metal: str | None
    linkers: frozenset[str]
    topology: str | None

    @property
    def identity(self) -> tuple[Any, ...]:
        return (self.metal, tuple(sorted(self.linkers)), self.topology)


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            parsed = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return []
        return [str(v) for v in parsed] if isinstance(parsed, list) else []
    return []


def qmof_record(row: Mapping[str, Any]) -> MofRecord | None:
    """Record of one QMOF row, or None when QMOF has no MOFid decomposition for it."""
    linkers = {n for n in (normalise_linker(s) for s in _as_list(row.get("info.mofid.smiles_linkers"))) if n}
    nodes = _as_list(row.get("info.mofid.smiles_nodes"))
    if not linkers or not nodes:
        return None
    formula = row.get("info.formula")
    try:
        metal = primary_metal(parse_formula(formula)) if isinstance(formula, str) else None
    except ValueError:
        metal = None
    topology = row.get("info.mofid.topology")
    topology = topology.strip() if isinstance(topology, str) and topology.strip() else None
    return MofRecord(metal, frozenset(linkers), topology)


@dataclass
class Reference:
    """Counts over distinct synthesised MOFs."""

    n_mofs: int = 0
    n_with_topology: int = 0
    topology: Counter = field(default_factory=Counter)
    metal: Counter = field(default_factory=Counter)
    linker: Counter = field(default_factory=Counter)
    pair: Counter = field(default_factory=Counter)
    info: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_records(cls, records: Iterable[MofRecord], info: dict[str, Any] | None = None) -> Reference:
        ref = cls(info=dict(info or {}))
        for rec in {r.identity: r for r in records}.values():
            ref.n_mofs += 1
            if rec.topology:
                ref.n_with_topology += 1
                ref.topology[rec.topology] += 1
            if rec.metal:
                ref.metal[rec.metal] += 1
            for linker in rec.linkers:
                ref.linker[linker] += 1
                if rec.metal:
                    ref.pair[f"{rec.metal}{PAIR_SEPARATOR}{linker}"] += 1
        return ref

    # ------------------------------------------------------------- lookups
    def topology_count(self, topology: str | None) -> int | None:
        return None if not topology else int(self.topology.get(topology, 0))

    def metal_count(self, metal: str | None) -> int | None:
        return None if not metal or not is_metal(metal) else int(self.metal.get(metal, 0))

    def linker_count(self, linkers: Iterable[str]) -> int | None:
        """Count of the least common linker (None when there is no linker to look up)."""
        counts = [int(self.linker.get(s, 0)) for s in linkers]
        return min(counts) if counts else None

    def pair_count(self, metal: str | None, linkers: Iterable[str]) -> int | None:
        """Count of the least common (metal, linker) pair."""
        if not metal or not is_metal(metal):
            return None
        counts = [int(self.pair.get(f"{metal}{PAIR_SEPARATOR}{s}", 0)) for s in linkers]
        return min(counts) if counts else None

    # ----------------------------------------------------------------- I/O
    def to_json(self) -> dict[str, Any]:
        return {
            "info": self.info,
            "n_mofs": self.n_mofs,
            "n_with_topology": self.n_with_topology,
            "topology": dict(sorted(self.topology.items())),
            "metal": dict(sorted(self.metal.items())),
            "linker": dict(sorted(self.linker.items())),
            "pair": dict(sorted(self.pair.items())),
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Reference:
        return cls(
            n_mofs=int(data["n_mofs"]),
            n_with_topology=int(data["n_with_topology"]),
            topology=Counter(data["topology"]),
            metal=Counter(data["metal"]),
            linker=Counter(data["linker"]),
            pair=Counter(data["pair"]),
            info=dict(data.get("info", {})),
        )

    def dumps(self) -> bytes:
        text = json.dumps(self.to_json(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return gzip.compress(text.encode("utf-8"), mtime=0)


@cache
def load_reference() -> Reference:
    """The reference shipped with mofsift."""
    raw = resources.files("mofsift.score").joinpath("data", REFERENCE_FILE).read_bytes()
    ref = Reference.from_json(json.loads(gzip.decompress(raw).decode("utf-8")))
    # Canonical SMILES can change between RDKit versions. The keys are written again with the
    # RDKit that is installed, so that a candidate's linker and the reference always agree.
    same = {s: normalise_linker(s) or s for s in ref.linker}
    linker: Counter = Counter()
    for smiles, n in ref.linker.items():
        linker[same[smiles]] += n
    pair: Counter = Counter()
    for key, n in ref.pair.items():
        metal, smiles = key.split(PAIR_SEPARATOR, 1)
        pair[f"{metal}{PAIR_SEPARATOR}{same.get(smiles, smiles)}"] += n
    ref.linker, ref.pair = linker, pair
    return ref
