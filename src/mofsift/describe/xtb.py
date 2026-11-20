"""Electronic proxies from periodic GFN-xTB (tblite): gap and frontier levels.

Derived from ``mofal.xtb`` in MOF_3D_Generator (MIT). What the numbers are:

* One calculation at the Gamma point of the cell as given (tblite has no k-point sampling).
* Spin-restricted, with a fixed number of unpaired electrons. With ``spin: high_spin`` that
  number is a rule of thumb, the high-spin count of M(II) for Mn, Fe, Co, Ni and Cu, made
  consistent with the parity of the electron count. It is not a computed spin state.
* ``xtb_homo`` is the highest level that is doubly occupied and ``xtb_lumo`` the lowest level
  that is not. Without unpaired electrons their difference is the usual HOMO-LUMO gap. With
  unpaired electrons the lowest such level is singly occupied, so ``xtb_gap`` is the distance
  from the doubly occupied levels to the singly occupied ones and is not a band gap in the
  usual sense. ``xtb_unpaired_electrons`` is reported so that such rows can be told apart.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from mofsift.describe.isolate import run_worker

COLUMNS = {
    "xtb_gap": "Float64",
    "xtb_homo": "Float64",
    "xtb_lumo": "Float64",
    "xtb_unpaired_electrons": "Int64",
}

ANGSTROM_TO_BOHR = 1 / 0.52917721067
KELVIN_TO_HARTREE = 3.166811563e-6
HARTREE_TO_EV = 27.211386245988
HIGH_SPIN_UNPAIRED = {"Mn": 5, "Fe": 4, "Co": 3, "Ni": 2, "Cu": 1}


def unpaired_electrons(symbols: Sequence[str], numbers: Sequence[int], spin: str) -> int:
    """Number of unpaired electrons used for the calculation (see the module docstring)."""
    electrons = int(sum(numbers))
    if spin == "low_spin":
        return electrons % 2
    if spin != "high_spin":
        raise ValueError(f"unknown spin setting {spin!r}")
    u = sum(HIGH_SPIN_UNPAIRED.get(s, 0) for s in symbols)
    if (electrons - u) % 2:
        u = u + 1 if u == 0 else u - 1
    return u


def frontier_levels(energies: Any, occupations: Any) -> tuple[float, float, float]:
    """(gap, homo, lumo) in eV from orbital energies (hartree) and occupations."""
    import numpy as np

    e = np.atleast_2d(np.asarray(energies, dtype=float))
    occ = np.atleast_2d(np.asarray(occupations, dtype=float)).reshape(e.shape)
    threshold = 0.5 * occ.max()
    filled = occ > threshold
    if not filled.any() or filled.all():
        raise ValueError("cannot separate occupied from empty levels")
    homo = max(ei[fi].max() for ei, fi in zip(e, filled, strict=True) if fi.any())
    lumo = min(ei[~fi].min() for ei, fi in zip(e, filled, strict=True) if (~fi).any())
    return (
        float((lumo - homo) * HARTREE_TO_EV),
        float(homo * HARTREE_TO_EV),
        float(lumo * HARTREE_TO_EV),
    )


def settings(
    method: str, periodic: bool, spin: str, electronic_temperature: float, max_iterations: int
) -> dict[str, Any]:
    """Everything that changes the numbers; part of the cache key."""
    return {
        "method": method,
        "periodic": bool(periodic),
        "spin": spin,
        "electronic_temperature": float(electronic_temperature),
        "max_iterations": int(max_iterations),
    }


def compute_tblite(cif: Path, options: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
    """One single-point calculation in its own process. Raises ``WorkerError`` on failure."""
    return run_worker("mofsift.describe.xtb_worker", {"cif": str(cif), **options}, timeout=timeout)
