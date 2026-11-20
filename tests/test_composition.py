from __future__ import annotations

import pytest

from mofsift.chem import parse_formula
from mofsift.describe.composition import COLUMNS, composition_features, primary_metal


def test_open_shell_flag():
    # ported from MOF_3D_Generator/tests/test_core.py
    f = composition_features(parse_formula("C24H12O12Co3"))
    assert f["has_open_shell_3d_metal"] is True and f["primary_metal"] == "Co"
    assert composition_features(parse_formula("C24H12O13Zn4"))["has_open_shell_3d_metal"] is False


def test_mof5_composition():
    f = composition_features({"C": 24, "H": 12, "O": 13, "Zn": 4})
    assert set(f) == set(COLUMNS)
    assert (f["primary_metal"], f["metals"], f["n_metal_atoms"], f["n_transition_metal_atoms"]) == (
        "Zn",
        "Zn",
        4,
        4,
    )
    assert f["metal_fraction"] == pytest.approx(4 / 53, abs=1e-6)
    assert f["c_fraction"] + f["h_fraction"] + f["o_fraction"] + f["metal_fraction"] == pytest.approx(
        1, abs=1e-5
    )
    assert f["heteroatom_fraction"] == f["o_fraction"] and f["n_fraction"] == 0
    assert f["mean_atomic_number"] == pytest.approx((24 * 6 + 12 + 13 * 8 + 4 * 30) / 53, abs=1e-6)
    # Pauling: C 2.55, H 2.20, O 3.44, Zn 1.65
    assert f["mean_electronegativity"] == pytest.approx(
        (24 * 2.55 + 12 * 2.2 + 13 * 3.44 + 4 * 1.65) / 53, abs=1e-5
    )
    assert f["std_atomic_number"] > 0 and f["std_electronegativity"] > 0


def test_primary_metal_rules():
    assert primary_metal({"Cu": 2, "Zn": 4, "O": 9}) == "Zn"  # the most abundant
    assert primary_metal({"Zn": 2, "Cu": 2}) == "Cu"  # a tie: alphabetical
    assert primary_metal({"C": 6, "H": 6}) is None
    assert composition_features({"Cu": 2, "Zn": 4, "O": 9})["metals"] == "Cu+Zn"


def test_no_metal_and_elements_outside_the_usual_set():
    organic = composition_features({"C": 6, "H": 6})
    assert organic["primary_metal"] is None and organic["metals"] is None
    assert organic["n_metal_atoms"] == 0 and organic["has_open_shell_3d_metal"] is False
    # lanthanides count as metals but not as transition metals; every element has an atomic number
    f = composition_features({"Ce": 6, "O": 32, "C": 48, "H": 28, "Zr": 0})
    assert f["primary_metal"] == "Ce" and f["n_transition_metal_atoms"] == 0 and f["mean_atomic_number"] > 6
    # an element without a Pauling value: no average over the others only
    noble = composition_features({"C": 1, "Ne": 1})
    assert noble["mean_electronegativity"] is None and noble["std_electronegativity"] is None


def test_bad_input():
    with pytest.raises(ValueError, match="unknown element"):
        composition_features({"C": 1, "Xx": 2})
    with pytest.raises(ValueError, match="empty composition"):
        composition_features({})
