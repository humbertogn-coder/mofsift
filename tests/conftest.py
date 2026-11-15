from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
import yaml

from mofsift.templates import EXAMPLE_CONFIG


@pytest.fixture
def example_dict() -> dict:
    """The example config as a fresh, mutable mapping."""
    return copy.deepcopy(yaml.safe_load(EXAMPLE_CONFIG))


@pytest.fixture
def minimal_dict() -> dict:
    return {
        "project": "t",
        "generate": {"source": "pormake", "topologies": ["pcu"], "nodes": ["N16"], "edges": ["E14"]},
    }


# --------------------------------------------------------------- structures
def methane(center=(5.0, 5.0, 5.0), cell=10.0):
    """A methane molecule in a periodic box: the smallest structure that passes every check."""
    from ase import Atoms

    d = 0.63
    offsets = [(0, 0, 0), (d, d, d), (-d, -d, d), (-d, d, -d), (d, -d, -d)]
    positions = [[center[k] + o[k] for k in range(3)] for o in offsets]
    return Atoms("CH4", positions=positions, cell=[cell] * 3, pbc=True)


METHANE_BONDS = [[0, 1], [0, 2], [0, 3], [0, 4]]


def write_cif(path, atoms):
    from ase.io import write

    path.parent.mkdir(parents=True, exist_ok=True)
    write(str(path), atoms, format="cif")
    return path


@pytest.fixture
def cif_dir(tmp_path):
    """A folder with three readable structures (one good, one with an overlap, one in two pieces)."""
    folder = tmp_path / "cifs"
    write_cif(folder / "good.cif", methane())
    clash = methane()
    clash.append("C")
    clash.positions[-1] = [5.3, 5.0, 5.0]
    write_cif(folder / "clash.cif", clash)
    two = methane() + methane(center=(2.0, 2.0, 2.0))
    write_cif(folder / "sub" / "two_pieces.cif", two)
    return folder


# ------------------------------------------------------------- fake PORMAKE
class FakeAdapter:
    """Stands in for the PORMAKE adapter: same interface, no PORMAKE.

    Topologies: ``one`` (one 4-c node type), ``two`` (3-c and 4-c node types).
    Blocks: nodes A4, B4 (4-c), T3 (3-c); edges L, M (2-c). ``B4`` cannot be built.
    """

    def __init__(self):
        from mofsift.generate.enumerate import BlockInfo, TopologyInfo

        self.topologies = {
            "one": TopologyInfo("one", node_cn=(4,), n_nodes=(1,), n_edges=(2,)),
            "two": TopologyInfo("two", node_cn=(3, 4), n_nodes=(4, 3), n_edges=(12,)),
        }
        self.blocks = {
            "A4": BlockInfo("A4", 4, {"C": 1}),
            "B4": BlockInfo("B4", 4, {"C": 1}),
            "T3": BlockInfo("T3", 3, {"C": 1}),
            "L": BlockInfo("L", 2, {"H": 2}),
            "M": BlockInfo("M", 2, {"H": 2}),
        }
        self.built: list[str] = []

    def topology_info(self, name):
        from mofsift.generate.pormake_adapter import UnknownNameError

        if name not in self.topologies:
            raise UnknownNameError(f"unknown topology {name!r}")
        return self.topologies[name]

    def block_info(self, name):
        from mofsift.generate.pormake_adapter import UnknownNameError

        if name not in self.blocks:
            raise UnknownNameError(f"unknown building block {name!r}")
        return self.blocks[name]

    def build(self, combo):
        if "B4" in combo.nodes:
            raise RuntimeError("cannot place\nthe node")
        self.built.append(combo.record_id)
        atoms = methane()
        return SimpleNamespace(
            n_atoms=len(atoms),
            counts={"C": 1, "H": 4},
            formula="CH4",
            bonds=[list(b) for b in METHANE_BONDS],
            max_rmsd=0.0123456789,
            mean_rmsd=0.01,
            write_cif=lambda path: write_cif(path, atoms),
        )


@pytest.fixture
def fake_adapter():
    return FakeAdapter()
