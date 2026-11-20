"""Worker process for tblite: ``python -m mofsift.describe.xtb_worker in.json out.jsonl``."""

from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    import numpy as np

    from mofsift.describe.isolate import worker_io
    from mofsift.describe.xtb import (
        ANGSTROM_TO_BOHR,
        KELVIN_TO_HARTREE,
        frontier_levels,
        unpaired_electrons,
    )
    from mofsift.io.structures import read_structure

    payload, emit = worker_io(argv)
    try:
        from tblite.interface import Calculator
    except ImportError as err:
        emit({"error": f"tblite cannot be imported: {err}"})
        return 0

    atoms = read_structure(payload["cif"])
    uhf = unpaired_electrons(atoms.get_chemical_symbols(), atoms.numbers.tolist(), payload["spin"])
    keywords = {"uhf": uhf}
    if payload["periodic"]:
        keywords.update(lattice=atoms.cell.array * ANGSTROM_TO_BOHR, periodic=np.array([True, True, True]))
    try:
        calc = Calculator(payload["method"], atoms.numbers, atoms.positions * ANGSTROM_TO_BOHR, **keywords)
        calc.set("temperature", float(payload["electronic_temperature"]) * KELVIN_TO_HARTREE)
        calc.set("max-iter", int(payload["max_iterations"]))
        calc.set("verbosity", 0)
        result = calc.singlepoint()
        gap, homo, lumo = frontier_levels(result.get("orbital-energies"), result.get("orbital-occupations"))
    except Exception as err:  # tblite reports SCF and parameter problems as RuntimeError
        text = " ".join(str(err).split())
        emit({"error": f"xTB failed: {text[:200] or type(err).__name__}"})
        return 0
    emit(
        {
            "xtb_gap": round(gap, 4),
            "xtb_homo": round(homo, 4),
            "xtb_lumo": round(lumo, 4),
            "xtb_unpaired_electrons": int(uhf),
        }
    )
    emit({"done": True})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
