"""Composition descriptors: computed from the formula alone, so they need no optional tool.

Derived from ``mofal.features`` in MOF_3D_Generator (MIT), with two differences: atomic
numbers and Pauling electronegativities cover the whole periodic table instead of a fixed
list of elements, and a metal is any element that is not a non-metal (see ``mofsift.chem``).
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Mapping
from functools import cache
from typing import Any

from ase.data import atomic_numbers

from mofsift.chem import is_metal

COLUMNS = {
    "primary_metal": "string",
    "metals": "string",
    "n_metal_atoms": "Int64",
    "metal_fraction": "Float64",
    "n_transition_metal_atoms": "Int64",
    "has_open_shell_3d_metal": "boolean",
    "c_fraction": "Float64",
    "h_fraction": "Float64",
    "n_fraction": "Float64",
    "o_fraction": "Float64",
    "heteroatom_fraction": "Float64",
    "mean_atomic_number": "Float64",
    "std_atomic_number": "Float64",
    "mean_electronegativity": "Float64",
    "std_electronegativity": "Float64",
}

_D_BLOCK = "Sc Ti V Cr Mn Fe Co Ni Cu Zn Y Zr Nb Mo Tc Ru Rh Pd Ag Cd Hf Ta W Re Os Ir Pt Au Hg"
TRANSITION_METALS = frozenset(_D_BLOCK.split())
_OPEN_3D = "Ti V Cr Mn Fe Co Ni Cu"
OPEN_SHELL_3D = frozenset(_OPEN_3D.split())
"""3d metals from Ti to Cu. A flag on the composition, not a computed spin state."""
HETEROATOMS = ("N", "O", "S", "F", "Cl", "Br", "I")


@cache
def electronegativity(symbol: str) -> float | None:
    """Pauling electronegativity, or None when the element has none (for example Ne)."""
    from pymatgen.core import Element

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        value = Element(symbol).X
    return None if value is None or math.isnan(value) else float(value)


def primary_metal(counts: Mapping[str, int]) -> str | None:
    """The most abundant metal; ties go to the first in alphabetical order. None without metals."""
    metals = {el: n for el, n in counts.items() if n > 0 and is_metal(el)}
    if not metals:
        return None
    return min(metals, key=lambda el: (-metals[el], el))


def _mean_std(counts: Mapping[str, int], prop: Mapping[str, float]) -> tuple[float, float]:
    total = sum(counts.values())
    mean = sum(prop[el] * n for el, n in counts.items()) / total
    var = sum(n * (prop[el] - mean) ** 2 for el, n in counts.items()) / total
    return mean, math.sqrt(var)


def composition_features(counts: Mapping[str, int]) -> dict[str, Any]:
    """Descriptors of a composition given as element counts. Fractions are per atom."""
    counts = {el: int(n) for el, n in counts.items() if n}
    unknown = sorted(el for el in counts if el not in atomic_numbers)
    if unknown:
        raise ValueError(f"unknown element symbol(s): {', '.join(unknown)}")
    n_atoms = sum(counts.values())
    if n_atoms <= 0:
        raise ValueError("empty composition")
    metal_counts = {el: n for el, n in counts.items() if is_metal(el)}
    n_metal = sum(metal_counts.values())
    main = primary_metal(counts)
    z_mean, z_std = _mean_std(counts, {el: float(atomic_numbers[el]) for el in counts})
    en = {el: electronegativity(el) for el in counts}
    if any(v is None for v in en.values()):
        en_mean = en_std = None  # an element without a Pauling value: no partial average
    else:
        en_mean, en_std = _mean_std(counts, en)  # type: ignore[arg-type]

    def frac(n: int) -> float:
        return round(n / n_atoms, 6)

    return {
        "primary_metal": main,
        "metals": "+".join(sorted(metal_counts)) if metal_counts else None,
        "n_metal_atoms": n_metal,
        "metal_fraction": frac(n_metal),
        "n_transition_metal_atoms": sum(n for el, n in counts.items() if el in TRANSITION_METALS),
        "has_open_shell_3d_metal": main in OPEN_SHELL_3D if main is not None else False,
        "c_fraction": frac(counts.get("C", 0)),
        "h_fraction": frac(counts.get("H", 0)),
        "n_fraction": frac(counts.get("N", 0)),
        "o_fraction": frac(counts.get("O", 0)),
        "heteroatom_fraction": frac(sum(counts.get(el, 0) for el in HETEROATOMS)),
        "mean_atomic_number": round(z_mean, 6),
        "std_atomic_number": round(z_std, 6),
        "mean_electronegativity": round(en_mean, 6) if en_mean is not None else None,
        "std_electronegativity": round(en_std, 6) if en_std is not None else None,
    }
