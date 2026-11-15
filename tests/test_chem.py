from __future__ import annotations

import pytest
from ase import Atoms

from mofsift.chem import count_symbols, hill_formula, is_metal, parse_formula


def test_parse_formula():
    # ported from MOF_3D_Generator/tests/test_core.py
    assert parse_formula("C24H12O13Zn4") == {"C": 24, "H": 12, "O": 13, "Zn": 4}
    assert parse_formula("CH4") == {"C": 1, "H": 4}
    assert parse_formula("CuOCu") == {"Cu": 2, "O": 1}


@pytest.mark.parametrize("bad", ["", "c24", "C24(OH)2", "C2H5+", "24C", "C 2"])
def test_parse_formula_rejects_what_it_cannot_read(bad):
    with pytest.raises(ValueError, match="cannot parse formula"):
        parse_formula(bad)


@pytest.mark.parametrize("symbols", ["C24H12O13Zn4", "H2O", "Zn4O", "CH4", "Cu2H8C4O8", "NaCl"])
def test_hill_formula_matches_ase_and_round_trips(symbols):
    atoms = Atoms(symbols)
    counts = count_symbols(atoms.get_chemical_symbols())
    assert hill_formula(counts) == atoms.get_chemical_formula()
    assert parse_formula(hill_formula(counts)) == counts


def test_hill_formula_ignores_zero_counts_and_rejects_negative():
    assert hill_formula({"C": 1, "H": 0, "O": 2}) == "CO2"
    with pytest.raises(ValueError):
        hill_formula({"C": -1})


def test_metal_split():
    assert all(is_metal(s) for s in ["Zn", "Cu", "Zr", "Mg", "Al", "Bi", "Ce"])
    assert not any(is_metal(s) for s in ["H", "C", "N", "O", "F", "S", "Cl", "Br", "I", "B", "Si", "P"])
