"""Thin adapter around PORMAKE: load it quietly, describe topologies and blocks, build structures.

PORMAKE is imported only here, so the rest of mofsift works without it.
"""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import importlib
import importlib.metadata
import logging
import os
import sys
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from mofsift.chem import count_symbols, is_metal
from mofsift.generate.enumerate import BlockInfo, Combination, TopologyInfo


class PormakeMissingError(RuntimeError):
    """PORMAKE is not installed (or cannot be imported)."""


class UnknownNameError(ValueError):
    """A topology or building block named in the config does not exist."""


def load_pormake() -> Any:
    """Import PORMAKE without its side effects.

    On import PORMAKE creates ``runtime.log`` in the current folder and attaches a console
    handler that prints every build step. Here the import runs inside a temporary folder,
    the file handler is removed, and the console handler is limited to errors.
    """
    if "pormake" in sys.modules:
        return sys.modules["pormake"]
    old_cwd = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="mofsift-pormake-", ignore_cleanup_errors=True) as tmp:
        os.chdir(tmp)
        try:
            pm = importlib.import_module("pormake")
        except ImportError as err:
            raise PormakeMissingError(
                f"PORMAKE cannot be imported ({err}); install it with: pip install 'mofsift[generate]'"
            ) from err
        finally:
            os.chdir(old_cwd)
        log = getattr(pm, "log", None)
        file_handler = getattr(log, "file_log_handler", None)
        if file_handler is not None:
            log.logger.removeHandler(file_handler)
            file_handler.close()
        console_handler = getattr(log, "console_log_handler", None)
        if console_handler is not None:
            console_handler.setLevel(logging.ERROR)
    return pm


def pormake_version() -> str | None:
    try:
        return importlib.metadata.version("pormake")
    except importlib.metadata.PackageNotFoundError:
        return None


@dataclass
class BuiltStructure:
    """A structure built by PORMAKE, with what mofsift needs from it."""

    framework: Any
    n_atoms: int
    counts: dict[str, int]
    formula: str
    bonds: list[list[int]]
    max_rmsd: float
    mean_rmsd: float

    def write_cif(self, path: Path) -> None:
        """Write the structure with PORMAKE's writer (it keeps the bond list in the CIF)."""
        with contextlib.redirect_stdout(None), warnings.catch_warnings():
            warnings.simplefilter("ignore")  # deprecation warnings raised inside PORMAKE
            self.framework.write_cif(str(path))


def _suggest(name: str, known: list[str]) -> str:
    close = difflib.get_close_matches(name, known, n=3)
    return f" (did you mean {', '.join(close)}?)" if close else ""


class PormakeAdapter:
    """Access to PORMAKE's topology and building-block database, plus an optional user folder."""

    def __init__(self, building_block_dir: Path | None = None):
        self.pm = load_pormake()
        self.db = self.pm.Database()
        self.builder = self.pm.Builder()
        self.block_dir = Path(building_block_dir) if building_block_dir is not None else None
        if self.block_dir is not None and not self.block_dir.is_dir():
            raise UnknownNameError(f"generate.building_block_dir: folder not found: {self.block_dir}")
        self._registered: dict[str, tuple[Path, str]] = {}
        self._topologies: dict[str, Any] = {}
        self._blocks: dict[str, Any] = {}
        self._block_hash: dict[str, str | None] = {}

    # ---------------------------------------------------------------- lookups
    def known_topologies(self) -> list[str]:
        return sorted(self.db.topo_list)

    def known_blocks(self) -> list[str]:
        names = set(self.db.bb_list)
        if self.block_dir is not None:
            names |= {p.stem for p in self.block_dir.glob("*.xyz")}
        return sorted(names)

    def register_block(self, name: str, path: Path, content_hash: str) -> None:
        """Make a generated building block (for example a linker built from SMILES) available by name."""
        self._registered[name] = (Path(path), content_hash)
        self._blocks.pop(name, None)

    def _topology(self, name: str) -> Any:
        if name not in self._topologies:
            if name not in self.db.topo_list:
                raise UnknownNameError(f"unknown topology {name!r}{_suggest(name, self.known_topologies())}")
            self._topologies[name] = self.db.get_topo(name)
        return self._topologies[name]

    def _block(self, name: str) -> Any:
        if name not in self._blocks:
            custom = self.block_dir / f"{name}.xyz" if self.block_dir is not None else None
            try:
                if name in self._registered:
                    path, digest = self._registered[name]
                    self._blocks[name] = self.pm.BuildingBlock(str(path))
                    self._block_hash[name] = digest
                elif custom is not None and custom.is_file():
                    text = custom.read_text(encoding="utf-8", errors="replace")
                    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]
                    digest = hashlib.sha256("\n".join(lines).strip("\n").encode("utf-8")).hexdigest()
                    self._blocks[name] = self.pm.BuildingBlock(str(custom))
                    self._block_hash[name] = digest[:16]
                elif name in self.db.bb_list:
                    self._blocks[name] = self.db.get_bb(name)
                    self._block_hash[name] = None
                else:
                    raise UnknownNameError(
                        f"unknown building block {name!r}{_suggest(name, self.known_blocks())}"
                    )
            except UnknownNameError:
                raise
            except Exception as err:  # PORMAKE raises a bare Exception for unreadable files
                raise UnknownNameError(f"building block {name!r} cannot be loaded: {err}") from err
        return self._blocks[name]

    def topology_info(self, name: str) -> TopologyInfo:
        topo = self._topology(name)
        node_types = np.asarray(topo.node_types)
        edge_types = np.asarray(topo.edge_types)
        n_nodes = tuple(int((node_types == t).sum()) for t in topo.unique_node_types)
        n_edges = tuple(
            int((edge_types == et).all(axis=1).sum()) for et in np.asarray(topo.unique_edge_types)
        )
        return TopologyInfo(
            name=name,
            node_cn=tuple(int(c) for c in topo.unique_cn),
            n_nodes=n_nodes,
            n_edges=n_edges,
        )

    def block_info(self, name: str) -> BlockInfo:
        block = self._block(name)
        symbols = [s for s in block.atoms.get_chemical_symbols() if s != "X"]
        return BlockInfo(
            name=name,
            n_connection_points=int(block.n_connection_points),
            counts=count_symbols(symbols),
            content_hash=self._block_hash[name],
            smiles=self.block_smiles(name),
        )

    def block_smiles(self, name: str) -> str | None:
        """SMILES of the linker that a metal-free block stands for, or None when it is not clear.

        A connection point on a carbon atom is read as a carboxylic acid group (the block is
        joined to a carboxylate node); one on a nitrogen atom as a donor atom that binds the
        metal directly. Anything else, and blocks that contain a metal, give None.
        """
        from rdkit import Chem, RDLogger

        from mofsift.score.reference import normalise_linker

        block = self._block(name)
        symbols = block.atoms.get_chemical_symbols()
        if any(s != "X" and is_metal(s) for s in symbols) or block.bonds is None:
            return None
        orders = {"S": Chem.BondType.SINGLE, "D": Chem.BondType.DOUBLE, "T": Chem.BondType.TRIPLE}
        mol = Chem.RWMol()
        index: dict[int, int] = {}
        for i, s in enumerate(symbols):
            if s != "X":
                atom = Chem.Atom(s)
                atom.SetNoImplicit(True)  # every hydrogen of a block is written out
                index[i] = mol.AddAtom(atom)
        anchors: list[int] = []
        for (a, b), kind in zip(block.bonds, block.bond_types, strict=True):
            a, b = int(a), int(b)
            if symbols[a] == "X" or symbols[b] == "X":
                anchors.append(b if symbols[a] == "X" else a)
            elif kind == "A":
                mol.AddBond(index[a], index[b], Chem.BondType.AROMATIC)
                for k in (a, b):
                    mol.GetAtomWithIdx(index[k]).SetIsAromatic(True)
            elif kind in orders:
                mol.AddBond(index[a], index[b], orders[kind])
            else:
                return None
        for anchor in anchors:
            if symbols[anchor] == "C":
                carbon, double, single = (mol.AddAtom(Chem.Atom(el)) for el in ("C", "O", "O"))
                mol.AddBond(index[anchor], carbon, Chem.BondType.SINGLE)
                mol.AddBond(carbon, double, Chem.BondType.DOUBLE)
                mol.AddBond(carbon, single, Chem.BondType.SINGLE)
            elif symbols[anchor] != "N":
                return None
        RDLogger.DisableLog("rdApp.*")
        try:
            Chem.SanitizeMol(mol)
            return normalise_linker(Chem.MolToSmiles(Chem.RemoveHs(mol)))
        except Exception:
            return None
        finally:
            RDLogger.EnableLog("rdApp.*")

    # ---------------------------------------------------------------- building
    def build(self, combo: Combination) -> BuiltStructure:
        topo = self._topology(combo.topology)
        node_bbs = {int(t): self._block(n) for t, n in zip(topo.unique_node_types, combo.nodes, strict=True)}
        edge_bbs = None
        if combo.edges:
            edge_bbs = {
                tuple(int(x) for x in et): self._block(e)
                for et, e in zip(np.asarray(topo.unique_edge_types), combo.edges, strict=True)
            }
        with contextlib.redirect_stdout(None), warnings.catch_warnings():
            warnings.simplefilter("ignore")  # SciPy optimiser warnings that PORMAKE triggers on every build
            framework = self.builder.build_by_type(topology=topo, node_bbs=node_bbs, edge_bbs=edge_bbs)
        atoms = framework.atoms
        bonds = np.sort(np.asarray(framework.bonds, dtype=int).reshape(-1, 2), axis=1)
        bonds = bonds[np.lexsort((bonds[:, 1], bonds[:, 0]))] if len(bonds) else bonds
        info = framework.info
        return BuiltStructure(
            framework=framework,
            n_atoms=len(atoms),
            counts=count_symbols(atoms.get_chemical_symbols()),
            formula=atoms.get_chemical_formula(),
            bonds=bonds.tolist(),
            max_rmsd=float(info["max_rmsd"]),
            mean_rmsd=float(info["mean_rmsd"]),
        )
