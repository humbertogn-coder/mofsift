"""Structural checks on one periodic structure.

The checks are geometric and compositional, so they apply to any structure, with or
without the bond list of the building blocks:

1. close pairs       no pair of atoms closer than ``min_distance``;
2. overlaps          no heavy-heavy or H-H pair closer than ``min_distance_heavy``;
3. non-bonded        (needs the bond list) no pair that is not bonded in the building
                     blocks closer than ``nonbonded_factor`` x (sum of covalent radii);
4. single framework  every atom belongs to one connected network; no isolated atoms;
5. valence           every H has exactly one neighbour; every C has two to four
                     non-metal neighbours;
6. composition       (needs the expected formula) the formula equals the sum of the
                     building blocks;
7. node misfit       (needs PORMAKE's RMSD) the node fits its vertex in the topology.

Covalent bonds that are much shorter than usual are counted and reported, but they do not
fail a structure: a short pre-relaxation usually removes them.

Derived from ``mofal.qc`` in MOF_3D_Generator (MIT).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
from ase import Atoms
from ase.data import atomic_numbers, covalent_radii
from ase.neighborlist import natural_cutoffs, neighbor_list
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from mofsift.chem import count_symbols, is_metal
from mofsift.config import QCConfig

_PAIR_CUTOFF = 3.0  # angstrom; range searched for the shortest distance
_AVOGADRO = 6.02214076e23

MIN_BOND: dict[tuple[str, str], float] = {
    ("C", "C"): 1.30,
    ("C", "O"): 1.18,
    ("C", "N"): 1.25,
    ("C", "H"): 0.98,
    ("H", "N"): 0.95,
    ("C", "F"): 1.28,
    ("H", "O"): 0.90,
}
"""Shortest usual length (angstrom) of common covalent bonds, about 0.9 of the typical value."""


@dataclass
class QCResult:
    n_atoms: int
    formula: str
    volume: float
    density: float
    cell: tuple[float, float, float, float, float, float]
    min_distance: float | None
    n_close_pairs: int
    n_overlaps: int
    n_nonbonded_contacts: int | None
    n_components: int
    n_isolated_atoms: int
    n_bad_h: int
    n_bad_c: int
    n_short_bonds: int
    composition_ok: bool | None
    node_rmsd_ok: bool | None
    passed: bool = False
    reasons: list[str] = field(default_factory=list)


def short_bonds(atoms: Atoms, cutoff: float = 1.75) -> list[tuple[int, int, str, float]]:
    """Covalent bonds shorter than :data:`MIN_BOND`, as (i, j, pair, distance)."""
    symbols = atoms.get_chemical_symbols()
    i, j, d = neighbor_list("ijd", atoms, cutoff)
    out = []
    for a, b, dist in zip(i, j, d, strict=True):
        if a >= b:
            continue
        key = tuple(sorted((symbols[a], symbols[b])))
        low = MIN_BOND.get(key)  # type: ignore[arg-type]
        if low is not None and dist < low:
            out.append((int(a), int(b), f"{key[0]}-{key[1]}", round(float(dist), 3)))
    return out


def check_atoms(
    atoms: Atoms,
    cfg: QCConfig | None = None,
    bonds: Sequence[Sequence[int]] | None = None,
    expected_counts: Mapping[str, int] | None = None,
    node_rmsd: float | None = None,
) -> QCResult:
    """Run every check on a periodic ASE structure.

    ``bonds`` is the bond list of the assembled building blocks (pairs of atom indices),
    ``expected_counts`` the composition the structure should have and ``node_rmsd`` the
    misfit reported by PORMAKE. Checks whose input is missing are not run and their
    result is ``None``.
    """
    cfg = cfg or QCConfig()
    symbols = np.array(atoms.get_chemical_symbols())
    n = len(atoms)
    reasons: list[str] = []

    # 1-2: short distances
    i, j, d = neighbor_list("ijd", atoms, cutoff=_PAIR_CUTOFF)
    min_distance = float(d.min()) if len(d) else None
    pair = i < j
    i, j, d = i[pair], j[pair], d[pair]
    n_close = int((d < cfg.min_distance).sum())
    heavy = (symbols[i] != "H") & (symbols[j] != "H")
    both_h = (symbols[i] == "H") & (symbols[j] == "H")
    n_overlaps = int(((heavy | both_h) & (d < cfg.min_distance_heavy)).sum())

    # 4-5: connectivity from covalent radii
    bi, bj = neighbor_list("ij", atoms, cutoff=natural_cutoffs(atoms, mult=cfg.bond_factor))
    coordination = np.bincount(bi, minlength=n)
    graph = coo_matrix((np.ones(len(bi), dtype=np.int8), (bi, bj)), shape=(n, n))
    n_components = int(connected_components(graph, directed=False)[0]) if n else 0
    n_isolated = int((coordination == 0).sum())
    metal = np.array([is_metal(s) for s in symbols], dtype=bool)
    organic = np.bincount(bi[~metal[bj]], minlength=n)
    n_bad_h = int(((symbols == "H") & (coordination != 1)).sum())
    n_bad_c = int(((symbols == "C") & ((organic < 2) | (organic > 4))).sum())

    # 3: contacts between atoms that are not bonded in the building blocks
    n_nonbonded: int | None = None
    if bonds is not None:
        radii = np.array([covalent_radii[atomic_numbers[s]] for s in symbols])
        ci, cj = neighbor_list("ij", atoms, cutoff=list(cfg.nonbonded_factor * radii))
        keep = ci < cj
        bonded = {(min(a, b), max(a, b)) for a, b in ((int(x), int(y)) for x, y in bonds)}
        n_nonbonded = sum((int(a), int(b)) not in bonded for a, b in zip(ci[keep], cj[keep], strict=True))

    # 6-7
    composition_ok: bool | None = None
    if expected_counts is not None:
        expected = {k: int(v) for k, v in expected_counts.items() if v}
        composition_ok = count_symbols(symbols.tolist()) == expected
    node_rmsd_ok: bool | None = None
    if cfg.max_node_rmsd is not None and node_rmsd is not None:
        node_rmsd_ok = bool(node_rmsd <= cfg.max_node_rmsd)

    if n_close:
        reasons.append(f"{n_close} pair(s) closer than {cfg.min_distance:g} A")
    if n_overlaps:
        reasons.append(f"{n_overlaps} heavy-heavy or H-H pair(s) closer than {cfg.min_distance_heavy:g} A")
    if n_nonbonded:
        reasons.append(
            f"{n_nonbonded} non-bonded contact(s) below {cfg.nonbonded_factor:g} x sum of covalent radii"
        )
    if cfg.require_single_framework:
        if n_components != 1:
            reasons.append(f"{n_components} connected components")
        if n_isolated:
            reasons.append(f"{n_isolated} isolated atom(s)")
    if cfg.check_valence:
        if n_bad_h:
            reasons.append(f"{n_bad_h} H atom(s) without exactly one neighbour")
        if n_bad_c:
            reasons.append(f"{n_bad_c} C atom(s) with fewer than 2 or more than 4 non-metal neighbours")
    if cfg.check_composition and composition_ok is False:
        reasons.append("formula differs from the sum of the building blocks")
    if node_rmsd_ok is False:
        reasons.append(f"node misfit {node_rmsd:.3f} A above {cfg.max_node_rmsd:g} A")

    volume = float(atoms.cell.volume)
    density = float(atoms.get_masses().sum() / _AVOGADRO / (volume * 1e-24))
    lengths_angles = tuple(round(float(x), 4) for x in atoms.cell.cellpar())
    return QCResult(
        n_atoms=n,
        formula=atoms.get_chemical_formula(),
        volume=round(volume, 3),
        density=round(density, 5),
        cell=lengths_angles,  # type: ignore[arg-type]
        min_distance=round(min_distance, 4) if min_distance is not None else None,
        n_close_pairs=n_close,
        n_overlaps=n_overlaps,
        n_nonbonded_contacts=n_nonbonded,
        n_components=n_components,
        n_isolated_atoms=n_isolated,
        n_bad_h=n_bad_h,
        n_bad_c=n_bad_c,
        n_short_bonds=len(short_bonds(atoms)),
        composition_ok=composition_ok,
        node_rmsd_ok=node_rmsd_ok,
        passed=not reasons,
        reasons=reasons,
    )
