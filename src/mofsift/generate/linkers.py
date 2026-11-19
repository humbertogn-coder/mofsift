"""Organic linkers given as SMILES, turned into PORMAKE building blocks.

Two ways to say where a linker connects:

* **carboxylic acids** (the default): ``OC(=O)c1ccc(cc1)C(=O)O``. Every ``C(=O)OH`` or
  ``C(=O)[O-]`` group is a connection point. The group itself is removed, because in
  PORMAKE's carboxylate nodes (N16, N409, ...) the carboxylate carbon and oxygens belong
  to the node.
* **explicit connection points**: ``[*]c1ccc([*])cc1``. Each ``[*]`` marks a connection
  point. Use this for any other binding group, or to control exactly where the cut is.

A real molecule is embedded in 3D with RDKit (ETKDG, fixed seed), optimised with MMFF94 (UFF
when MMFF has no parameters), and then cut. A linker with two connection points is an edge;
with three or more it is an organic node. This module needs RDKit only, not PORMAKE.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from mofsift.chem import count_symbols, hill_formula

_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_ACID_SMARTS = "[!#8:1]-[CX3:2](=[OX1:3])[OX2H1,OX1-:4]"
_BOND_CODES = {"SINGLE": "S", "DOUBLE": "D", "TRIPLE": "T", "AROMATIC": "A"}
_RECIPE_VERSION = 1  # part of the hash: bump when the conversion changes the geometry
_BENT_BELOW = 150.0  # degrees


class LinkerError(ValueError):
    """A linker SMILES cannot be turned into a building block."""


@dataclass(frozen=True)
class LinkerBlock:
    """A linker ready to be written as a PORMAKE ``.xyz`` building block."""

    name: str
    smiles: str  # canonical SMILES of the molecule as it was given
    mode: str  # "carboxylate" or "explicit"
    symbols: tuple[str, ...]  # atoms first, then one "X" per connection point
    positions: tuple[tuple[float, float, float], ...]
    bonds: tuple[tuple[int, int, str], ...]
    content_hash: str
    notes: tuple[str, ...] = ()

    @property
    def n_connection_points(self) -> int:
        return sum(s == "X" for s in self.symbols)

    @property
    def role(self) -> str:
        return "edge" if self.n_connection_points == 2 else "node"

    @property
    def counts(self) -> dict[str, int]:
        return count_symbols(s for s in self.symbols if s != "X")

    @property
    def formula(self) -> str:
        return hill_formula(self.counts)

    def connection_points(self) -> np.ndarray:
        return np.array([p for s, p in zip(self.symbols, self.positions, strict=True) if s == "X"])

    def to_xyz(self) -> str:
        """Text of the building block in PORMAKE's ``.xyz`` format (atoms, then bonds)."""
        x_indices = [i for i, s in enumerate(self.symbols) if s == "X"]
        lines = [str(len(self.symbols)), "".join(f"{i:5d}" for i in x_indices)]
        for s, (x, y, z) in zip(self.symbols, self.positions, strict=True):
            lines.append(f"{s:<2s} {x:10.4f} {y:10.4f} {z:10.4f}")
        lines += [f"{i:4d} {j:4d} {t}" for i, j, t in self.bonds]
        return "\n".join(lines) + "\n"

    def write_xyz(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(self.to_xyz())
        return path


def check_name(name: str) -> str:
    if not _NAME_RE.match(name):
        raise LinkerError(f"linker name {name!r}: use letters, digits, '_' or '-', starting with a letter")
    return name


def read_smiles_file(path: str | Path) -> list[tuple[str, str]]:
    """Read ``SMILES name`` pairs, one per line.

    Blank lines and lines that start with ``#`` are ignored. A ``#`` elsewhere is part of the
    SMILES (a triple bond), so comments cannot follow a linker on the same line.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        raise LinkerError(f"{path}: cannot read the SMILES file ({err.strerror or err})") from err
    pairs: list[tuple[str, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        if len(tokens) != 2:
            raise LinkerError(
                f"{path.name}, line {number}: expected 'SMILES name' (two columns), got {len(tokens)}"
            )
        pairs.append((check_name(tokens[1]), tokens[0]))
    return pairs


def _embed(mol, seed: int, n_conformers: int):
    """Embed and optimise; return (molecule with one conformer id chosen, conformer id, notes)."""
    from rdkit.Chem import AllChem

    notes: list[str] = []
    params = AllChem.ETKDGv3()
    params.randomSeed = int(seed)
    ids = list(AllChem.EmbedMultipleConfs(mol, n_conformers, params))
    if not ids:
        params.useRandomCoords = True
        ids = list(AllChem.EmbedMultipleConfs(mol, n_conformers, params))
    if not ids:
        raise LinkerError("RDKit could not build a 3D geometry for this molecule")
    if AllChem.MMFFHasAllMoleculeParams(mol):
        results = AllChem.MMFFOptimizeMoleculeConfs(mol, maxIters=2000)
    elif AllChem.UFFHasAllMoleculeParams(mol):
        results = AllChem.UFFOptimizeMoleculeConfs(mol, maxIters=2000)
        notes.append("no MMFF94 parameters; geometry optimised with UFF")
    else:
        notes.append("no force-field parameters; geometry is the unoptimised RDKit embedding")
        return ids[0], notes
    best = min(range(len(ids)), key=lambda k: (round(results[k][1], 6), k))
    if results[best][0] != 0:
        notes.append("force-field optimisation did not converge")
    return ids[best], notes


def build_linker(
    name: str,
    smiles: str,
    *,
    seed: int = 0,
    connection_distance: float = 0.75,
    conformers: int = 10,
) -> LinkerBlock:
    """Turn one SMILES into a building block. Raises :class:`LinkerError` with the reason."""
    from rdkit import Chem, RDLogger

    check_name(name)
    RDLogger.DisableLog("rdApp.*")  # parse errors are reported through LinkerError instead
    try:
        mol = Chem.MolFromSmiles(smiles.strip()) if isinstance(smiles, str) and smiles.strip() else None
    finally:
        RDLogger.EnableLog("rdApp.*")
    if mol is None:
        raise LinkerError(f"{smiles!r} is not a valid SMILES")
    canonical = Chem.MolToSmiles(mol)
    if any(a.GetIsotope() for a in mol.GetAtoms() if a.GetAtomicNum() == 0):
        raise LinkerError("write connection points as plain [*], without isotope labels")

    # Each connection point is (anchor atom, leaving atom, atoms removed with it), on a real molecule.
    dummies = [a.GetIdx() for a in mol.GetAtoms() if a.GetAtomicNum() == 0]
    cuts: list[tuple[int, int, set[int]]] = []
    if dummies:
        mode = "explicit"
        work = Chem.RWMol(mol)
        for idx in dummies:
            atom = work.GetAtomWithIdx(idx)
            if atom.GetDegree() != 1 or atom.GetBonds()[0].GetBondType().name != "SINGLE":
                raise LinkerError("every [*] must be attached to exactly one atom by a single bond")
            anchor = atom.GetNeighbors()[0].GetIdx()
            if anchor in dummies:
                raise LinkerError("two [*] are bonded to each other")
            atom.SetAtomicNum(1)  # a hydrogen stands in for the neighbouring block while embedding
            atom.SetFormalCharge(0)
            atom.SetNoImplicit(True)
            cuts.append((anchor, idx, {idx}))
        real = work.GetMol()
        try:
            Chem.SanitizeMol(real)
        except Exception as err:
            raise LinkerError(f"the molecule is not valid once [*] is replaced by H ({err})") from err
        real = Chem.AddHs(real, addCoords=False)
    else:
        mode = "carboxylate"
        matches = mol.GetSubstructMatches(Chem.MolFromSmarts(_ACID_SMARTS))
        real = Chem.AddHs(mol)
        for anchor, carbon, o_double, o_single in matches:
            removed = {carbon, o_double, o_single}
            removed |= {
                n.GetIdx() for n in real.GetAtomWithIdx(o_single).GetNeighbors() if n.GetAtomicNum() == 1
            }
            cuts.append((anchor, carbon, removed))
    if len(cuts) < 2:
        found = "no" if not cuts else "only one"
        raise LinkerError(
            f"{found} connection point found: a linker needs at least two carboxylic acid groups, "
            "or [*] atoms that mark where it connects"
        )
    removed_all = set().union(*(c[2] for c in cuts))
    if any(anchor in removed_all for anchor, _, _ in cuts):
        raise LinkerError(
            "two binding groups are bonded to each other, so nothing is left between them "
            "(for example oxalic acid); such a linker cannot be a separate building block"
        )

    conf_id, notes = _embed(real, seed, conformers)
    xyz = np.asarray(real.GetConformer(conf_id).GetPositions(), dtype=float)

    cuts.sort(key=lambda c: (c[0], c[1]))
    kept = [i for i in range(real.GetNumAtoms()) if i not in removed_all]
    new_index = {old: new for new, old in enumerate(kept)}
    symbols = [real.GetAtomWithIdx(i).GetSymbol() for i in kept]
    positions = [xyz[i] for i in kept]
    bonds: list[tuple[int, int, str]] = []
    for bond in real.GetBonds():
        a, b = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        if a in new_index and b in new_index:
            code = _BOND_CODES.get(bond.GetBondType().name)
            if code is None:
                raise LinkerError(f"bond type {bond.GetBondType().name} is not supported")
            i, j = sorted((new_index[a], new_index[b]))
            bonds.append((i, j, code))
    directions = []
    for anchor, leaving, _ in cuts:
        vector = xyz[leaving] - xyz[anchor]
        length = float(np.linalg.norm(vector))
        if length < 1e-6:
            raise LinkerError("a connection point coincides with its anchor atom")
        directions.append(vector / length)
        symbols.append("X")
        positions.append(xyz[anchor] + connection_distance * vector / length)
        bonds.append((new_index[anchor], len(symbols) - 1, "S"))
    bonds.sort()

    coords = np.array(positions)
    coords -= coords[: len(kept)].mean(axis=0)
    if len(cuts) == 2:
        angle = float(np.degrees(np.arccos(np.clip(np.dot(directions[0], directions[1]), -1.0, 1.0))))
        if angle < _BENT_BELOW:
            notes.append(
                f"bent linker: the two connection directions are {angle:.0f} degrees apart "
                "(180 = linear); check the structures built with it"
            )
    recipe = f"{canonical}|{mode}|{int(seed)}|{connection_distance:.4f}|{int(conformers)}|v{_RECIPE_VERSION}"
    return LinkerBlock(
        name=name,
        smiles=canonical,
        mode=mode,
        symbols=tuple(symbols),
        positions=tuple(tuple(round(float(v), 4) + 0.0 for v in row) for row in coords),
        bonds=tuple(bonds),
        content_hash=hashlib.sha256(recipe.encode("utf-8")).hexdigest()[:16],
        notes=tuple(notes),
    )


def collect_smiles(
    smiles_file: Path | None, inline: Mapping[str, str], base_dir: Path
) -> list[tuple[str, str]]:
    """(name, SMILES) pairs from the file and then from the inline mapping; names must be unique."""
    pairs: list[tuple[str, str]] = []
    if smiles_file is not None:
        path = smiles_file if smiles_file.is_absolute() else base_dir / smiles_file
        pairs += read_smiles_file(path)
    pairs += [(check_name(str(name)), str(smi)) for name, smi in inline.items()]
    seen: set[str] = set()
    repeated = sorted({name for name, _ in pairs if name in seen or seen.add(name)})
    if repeated:
        raise LinkerError(f"linker name(s) used more than once: {', '.join(repeated)}")
    return pairs


def build_linkers(
    pairs: Sequence[tuple[str, str]], *, seed: int, connection_distance: float, conformers: int
) -> tuple[list[LinkerBlock], list[str]]:
    """Build every linker; return the blocks and one message per linker that failed."""
    blocks: list[LinkerBlock] = []
    problems: list[str] = []
    for name, smiles in pairs:
        try:
            blocks.append(
                build_linker(
                    name, smiles, seed=seed, connection_distance=connection_distance, conformers=conformers
                )
            )
        except LinkerError as err:
            problems.append(f"{name}: {err}")
    return blocks, problems
