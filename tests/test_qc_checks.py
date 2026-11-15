from __future__ import annotations

import pytest
from ase import Atoms

from conftest import METHANE_BONDS, methane
from mofsift.config import QCConfig
from mofsift.qc import check_atoms, short_bonds


def test_qc_detects_overlap():
    # ported from MOF_3D_Generator/tests/test_core.py
    ok = methane()
    assert check_atoms(ok).n_overlaps == 0
    bad = ok.copy()
    bad.append("C")
    bad.positions[-1] = [5.3, 5.0, 5.0]
    r = check_atoms(bad)
    assert not r.passed and r.n_close_pairs >= 1


def test_a_clean_structure_passes_and_reports_its_facts():
    r = check_atoms(methane())
    assert r.passed and r.reasons == []
    assert (r.n_atoms, r.formula, r.n_components, r.n_isolated_atoms) == (5, "CH4", 1, 0)
    assert r.min_distance == pytest.approx(1.0912, abs=1e-3)  # the C-H bond
    assert r.volume == pytest.approx(1000.0) and r.cell == (10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    assert r.density == pytest.approx(16.043 / 6.02214076e23 / 1e-21, rel=1e-3)
    # checks without input are not run, and say so
    assert r.n_nonbonded_contacts is None and r.composition_ok is None and r.node_rmsd_ok is None


def test_fragments_and_isolated_atoms():
    two = methane() + methane(center=(2.0, 2.0, 2.0))
    r = check_atoms(two)
    assert not r.passed and r.n_components == 2 and "2 connected components" in r.reasons
    lone = methane()
    lone.append("O")
    lone.positions[-1] = [1.0, 1.0, 1.0]
    r = check_atoms(lone)
    assert r.n_isolated_atoms == 1 and "1 isolated atom(s)" in r.reasons
    # the same structures are accepted when a single framework is not required
    relaxed = QCConfig(require_single_framework=False)
    assert check_atoms(two, relaxed).passed and check_atoms(lone, relaxed).passed
    assert check_atoms(two, relaxed).n_components == 2  # still reported


def test_valence_of_h_and_c():
    missing_h = methane()
    del missing_h[4]  # CH3: carbon keeps 3 neighbours, still fine
    assert check_atoms(missing_h).passed
    del missing_h[3]
    del missing_h[2]  # CH: carbon with one neighbour
    r = check_atoms(missing_h)
    assert r.n_bad_c == 1 and not r.passed
    assert check_atoms(missing_h, QCConfig(check_valence=False)).passed
    bridging = Atoms("CHC", positions=[[4, 5, 5], [5, 5, 5], [6, 5, 5]], cell=[10, 10, 10], pbc=True)
    assert check_atoms(bridging).n_bad_h == 1


def test_metal_neighbours_do_not_count_towards_carbon_valence():
    # a carbon bonded to two metals only has no non-metal neighbour
    atoms = Atoms("ZnCZn", positions=[[3.1, 5, 5], [5, 5, 5], [6.9, 5, 5]], cell=[10, 10, 10], pbc=True)
    r = check_atoms(atoms)
    assert r.n_components == 1 and r.n_bad_c == 1


def test_non_bonded_contacts_need_the_bond_list():
    atoms = methane()
    assert check_atoms(atoms).n_nonbonded_contacts is None  # no bond list: the check is not run
    assert check_atoms(atoms, bonds=METHANE_BONDS).n_nonbonded_contacts == 0
    # an oxygen 1.05 A from the carbon: too close for two atoms that are not bonded
    crowded = atoms.copy()
    crowded.append("O")
    crowded.positions[-1] = [6.05, 5.0, 5.0]
    r = check_atoms(crowded, bonds=METHANE_BONDS)
    assert r.n_nonbonded_contacts == 1 and r.n_overlaps == 0 and not r.passed
    assert any("non-bonded contact" in reason for reason in r.reasons)
    # the same pair is accepted when the building blocks say it is a bond (in either order)
    assert check_atoms(crowded, bonds=[*METHANE_BONDS, [5, 0]]).n_nonbonded_contacts == 0
    # a looser factor accepts it as well
    assert check_atoms(crowded, QCConfig(nonbonded_factor=0.5), bonds=METHANE_BONDS).n_nonbonded_contacts == 0


def test_composition_and_node_misfit():
    atoms = methane()
    assert check_atoms(atoms, expected_counts={"C": 1, "H": 4}).composition_ok is True
    r = check_atoms(atoms, expected_counts={"C": 1, "H": 3})
    assert r.composition_ok is False and "formula differs" in r.reasons[0]
    assert check_atoms(atoms, QCConfig(check_composition=False), expected_counts={"C": 2}).passed
    assert check_atoms(atoms, node_rmsd=0.1).node_rmsd_ok is True
    r = check_atoms(atoms, node_rmsd=0.45)
    assert r.node_rmsd_ok is False and r.reasons == ["node misfit 0.450 A above 0.3 A"]
    assert check_atoms(atoms, QCConfig(max_node_rmsd=None), node_rmsd=0.45).node_rmsd_ok is None


def test_thresholds_come_from_the_config():
    pair = Atoms("C2", positions=[[5, 5, 5], [5.9, 5, 5]], cell=[10, 10, 10], pbc=True)
    default = check_atoms(pair, QCConfig(check_valence=False))
    assert default.n_close_pairs == 0 and default.n_overlaps == 1
    assert check_atoms(pair, QCConfig(check_valence=False, min_distance_heavy=0.8)).passed
    assert check_atoms(pair, QCConfig(check_valence=False, min_distance=0.95)).n_close_pairs == 1


def test_short_bonds_are_reported_but_do_not_fail_a_structure():
    squeezed = methane()
    squeezed.positions[1] = [5.55, 5.55, 5.55]  # C-H of 0.95 A
    assert [(i, j, pair) for i, j, pair, _ in short_bonds(squeezed)] == [(0, 1, "C-H")]
    r = check_atoms(squeezed)
    assert r.n_short_bonds == 1 and r.passed
    assert check_atoms(methane()).n_short_bonds == 0


def test_bonds_across_the_cell_boundary_are_found():
    wrapped = methane(center=(0.0, 0.0, 0.0))  # the molecule straddles the origin
    r = check_atoms(wrapped, bonds=METHANE_BONDS)
    assert r.passed and r.n_components == 1 and r.min_distance == pytest.approx(1.0912, abs=1e-3)
