"""Structure files."""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
from ase import Atoms
from ase.io import read


class StructureError(ValueError):
    """A structure file cannot be read or is not a periodic crystal structure."""


def read_structure(path: str | Path) -> Atoms:
    """Read one periodic structure from a CIF file."""
    path = Path(path)
    if not path.exists():
        raise StructureError(f"file not found: {path.name}")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            atoms = read(str(path), format="cif")
    except Exception as err:  # ASE raises many different errors on malformed CIFs
        text = " ".join(str(err).split())
        raise StructureError(f"{type(err).__name__}: {text[:160]}" if text else type(err).__name__) from err
    if not isinstance(atoms, Atoms) or len(atoms) == 0:
        raise StructureError("the file contains no atoms")
    volume = float(abs(np.linalg.det(atoms.cell.array)))
    if not np.isfinite(volume) or volume < 1e-6:
        raise StructureError("the file has no unit cell")
    atoms.pbc = True
    return atoms
