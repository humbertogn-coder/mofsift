from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from mofsift.generate.linkers import (
    LinkerError,
    build_linker,
    build_linkers,
    collect_smiles,
    read_smiles_file,
)

BDC = "OC(=O)c1ccc(cc1)C(=O)O"
BTC = "OC(=O)c1cc(cc(c1)C(=O)O)C(=O)O"


def _parse_xyz(text):
    lines = text.splitlines()
    n = int(lines[0])
    atoms = [ln.split() for ln in lines[2 : 2 + n]]
    bonds = [ln.split() for ln in lines[2 + n :]]
    return n, [int(t) for t in lines[1].split()], atoms, bonds


def test_a_dicarboxylic_acid_becomes_an_edge_without_its_carboxylates():
    block = build_linker("bdc", BDC)
    assert (block.mode, block.role, block.n_connection_points) == ("carboxylate", "edge", 2)
    assert block.formula == "C6H4" and block.smiles == "O=C(O)c1ccc(C(=O)O)cc1"  # what PORMAKE's E14 holds
    assert block.symbols == ("C",) * 6 + ("H",) * 4 + ("X", "X")
    x = block.connection_points()
    pos = np.array(block.positions)
    carbons = pos[:6]
    # each connection point sits 0.75 A from its anchor carbon, pointing away from the ring
    for point in x:
        d = np.linalg.norm(carbons - point, axis=1)
        assert d.min() == pytest.approx(0.75, abs=1e-3)
    assert np.linalg.norm(x[0] - x[1]) == pytest.approx(2.8 + 1.5, abs=0.1)  # para positions, linear
    assert np.allclose(pos[:10].mean(axis=0), 0.0, atol=1e-3)  # centred on the atoms
    assert block.notes == ()


def test_deprotonated_acids_and_explicit_points_give_the_same_block_contents():
    acid = build_linker("a", BDC)
    anion = build_linker("b", "[O-]C(=O)c1ccc(cc1)C([O-])=O")
    explicit = build_linker("c", "[*]c1ccc([*])cc1")
    assert acid.counts == anion.counts == explicit.counts == {"C": 6, "H": 4}
    assert explicit.mode == "explicit" and explicit.smiles == "*c1ccc(*)cc1"
    assert np.linalg.norm(np.subtract(*explicit.connection_points())) == pytest.approx(
        np.linalg.norm(np.subtract(*acid.connection_points())), abs=0.05
    )
    assert len({acid.content_hash, anion.content_hash, explicit.content_hash}) == 3


def test_three_connection_points_make_an_organic_node():
    block = build_linker("btc", BTC)
    assert (block.role, block.n_connection_points, block.formula) == ("node", 3, "C6H3")
    x = block.connection_points()
    sides = sorted(np.linalg.norm(x[i] - x[j]) for i, j in [(0, 1), (0, 2), (1, 2)])
    assert sides[2] - sides[0] < 0.05  # an equilateral triangle


def test_xyz_text_is_what_pormake_reads():
    block = build_linker("bdc", BDC)
    n, x_indices, atoms, bonds = _parse_xyz(block.to_xyz())
    assert n == 12 and x_indices == [10, 11] and [a[0] for a in atoms] == list(block.symbols)
    assert all(len(a) == 4 for a in atoms)
    codes = [b[2] for b in bonds]
    assert codes.count("A") == 6 and codes.count("S") == 6 and len(bonds) == 12
    # every X is bonded to exactly one atom
    for x in x_indices:
        assert sum(str(x) in b[:2] for b in bonds) == 1
    assert block.to_xyz().endswith("\n") and "\r" not in block.to_xyz()


def test_bond_orders_are_kept():
    block = build_linker("fum", "OC(=O)/C=C/C(=O)O")  # fumaric acid
    assert block.formula == "C2H2" and [b[2] for b in block.bonds].count("D") == 1
    alkyne = build_linker("adc", "[*]C#C[*]")
    assert alkyne.formula == "C2" and [b[2] for b in alkyne.bonds].count("T") == 1


def test_same_input_gives_the_same_block_and_the_seed_is_part_of_the_identity():
    a, b = build_linker("bdc", BDC, seed=7), build_linker("bdc", BDC, seed=7)
    assert a == b and a.to_xyz() == b.to_xyz()
    other_seed = build_linker("bdc", BDC, seed=8)
    assert other_seed.content_hash != a.content_hash
    # the name is a label, not part of the content
    assert build_linker("other", BDC, seed=7).content_hash == a.content_hash
    # the way the SMILES is written does not matter
    assert build_linker("bdc", "O=C(O)c1ccc(C(=O)O)cc1", seed=7).content_hash == a.content_hash
    assert build_linker("bdc", BDC, seed=7, connection_distance=0.7).content_hash != a.content_hash


def test_connection_distance_is_configurable():
    block = build_linker("bdc", BDC, connection_distance=1.0)
    carbons = np.array(block.positions[:6])
    assert min(np.linalg.norm(carbons - block.connection_points()[0], axis=1)) == pytest.approx(1.0, abs=1e-3)


def test_a_bent_linker_is_built_and_flagged():
    block = build_linker("ipa", "OC(=O)c1cccc(c1)C(=O)O")  # isophthalic acid, 120 degrees
    assert block.role == "edge" and len(block.notes) == 1
    assert "bent linker" in block.notes[0] and "120 degrees" in block.notes[0]


@pytest.mark.parametrize(
    ("smiles", "message"),
    [
        ("c1ccccc1(", "is not a valid SMILES"),
        ("", "is not a valid SMILES"),
        ("c1ccccc1", "no connection point found"),
        ("OC(=O)c1ccccc1", "only one connection point found"),
        ("COC(=O)c1ccc(cc1)C(=O)OC", "no connection point found"),  # esters are not binding groups
        ("OC(=O)C(=O)O", "bonded to each other"),
        ("[*]c1ccc(cc1)C(=O)O", "only one connection point found"),  # [*] present: acids are not cut
        ("[*]=C1C=CC(=[*])C=C1", "by a single bond"),
        ("[*][*]", "bonded to each other"),
        ("[1*]c1ccc([2*])cc1", "without isotope labels"),
    ],
)
def test_unusable_input_is_explained(smiles, message):
    with pytest.raises(LinkerError, match=message):
        build_linker("x", smiles)


@pytest.mark.parametrize("name", ["", "1abc", "a b", "a/b", "é"])
def test_names_must_be_simple(name):
    with pytest.raises(LinkerError, match="linker name"):
        build_linker(name, BDC)


def test_smiles_file(tmp_path):
    path = tmp_path / "linkers.smi"
    path.write_text(f"# SMILES name\n\n{BDC} bdc\n  [*]C#C[*]\tadc  \n", encoding="utf-8")
    assert read_smiles_file(path) == [("bdc", BDC), ("adc", "[*]C#C[*]")]  # '#' in a SMILES is kept
    path.write_text(f"{BDC}\n", encoding="utf-8")
    with pytest.raises(LinkerError, match="line 1: expected 'SMILES name'"):
        read_smiles_file(path)
    with pytest.raises(LinkerError, match="cannot read the SMILES file"):
        read_smiles_file(tmp_path / "missing.smi")


def test_collect_and_build_many(tmp_path):
    (tmp_path / "l.smi").write_text(f"{BDC} bdc\n", encoding="utf-8")
    pairs = collect_smiles(tmp_path / "l.smi", {"btc": BTC}, tmp_path)
    assert pairs == [("bdc", BDC), ("btc", BTC)]
    assert collect_smiles(None, {"btc": BTC}, tmp_path) == [("btc", BTC)]
    assert collect_smiles(Path("l.smi"), {}, tmp_path) == [("bdc", BDC)]  # relative
    with pytest.raises(LinkerError, match="used more than once: bdc"):
        collect_smiles(tmp_path / "l.smi", {"bdc": BTC}, tmp_path)
    blocks, problems = build_linkers(
        [*pairs, ("bad", "c1ccccc1")], seed=0, connection_distance=0.75, conformers=3
    )
    assert [b.name for b in blocks] == ["bdc", "btc"]
    assert len(problems) == 1 and problems[0].startswith("bad: no connection point found")
