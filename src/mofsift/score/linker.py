"""Scores of an organic linker given as SMILES. They need RDKit only.

Each function returns one named quantity; nothing is combined into a single number here.

* ``sa_score``   synthetic accessibility of Ertl and Schuffenhauer (J. Cheminform. 2009, 1, 8),
                 from RDKit's contributed implementation: 1 (easy) to 10 (hard).
* ``sc_score``   synthetic complexity of Coley et al. (J. Chem. Inf. Model. 2018, 58, 252):
                 1 (simple) to 5 (complex). Code and weights are in ``mofsift.score._scscore``.
* ``heavy_atoms``, ``rotatable_bonds``  size and flexibility.

Both scores were developed for drug-like organic molecules. For MOF linkers they are
indicators that still have to be validated, not probabilities of anything.
"""

from __future__ import annotations

import importlib.util
import os
from functools import cache
from typing import Any

COLUMNS = {
    "linker_sa": "Float64",
    "linker_sc": "Float64",
    "linker_size": "Int64",
    "linker_rotatable_bonds": "Int64",
}


class LinkerScoreError(ValueError):
    """The SMILES cannot be scored."""


@cache
def _sascorer():
    """RDKit's contributed SA score module (shipped with RDKit, outside its Python package)."""
    from rdkit.Chem import RDConfig

    path = os.path.join(RDConfig.RDContribDir, "SA_Score", "sascorer.py")
    spec = importlib.util.spec_from_file_location("mofsift_rdkit_sascorer", path)
    if spec is None or spec.loader is None or not os.path.exists(path):
        raise LinkerScoreError("RDKit's SA_Score contribution was not found in this RDKit installation")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def scoring_molecule(smiles: str):
    """The molecule that is scored: the SMILES as given, with any ``[*]`` replaced by hydrogen."""
    from rdkit import Chem, RDLogger

    RDLogger.DisableLog("rdApp.*")
    try:
        mol = Chem.MolFromSmiles(smiles) if isinstance(smiles, str) and smiles.strip() else None
        if mol is None:
            raise LinkerScoreError(f"{smiles!r} is not a valid SMILES")
        if any(a.GetAtomicNum() == 0 for a in mol.GetAtoms()):
            editable = Chem.RWMol(mol)
            for atom in editable.GetAtoms():
                if atom.GetAtomicNum() == 0:
                    atom.SetAtomicNum(1)
                    atom.SetIsotope(0)
            try:
                mol = Chem.RemoveHs(editable.GetMol())
            except Exception as err:
                raise LinkerScoreError(f"{smiles!r} is not valid once [*] is replaced by H") from err
    finally:
        RDLogger.EnableLog("rdApp.*")
    if mol.GetNumHeavyAtoms() == 0:
        raise LinkerScoreError(f"{smiles!r} has no heavy atoms")
    return mol


def linker_scores(smiles: str) -> dict[str, Any]:
    """All linker components for one SMILES."""
    from rdkit.Chem import rdMolDescriptors

    from mofsift.score import _scscore

    mol = scoring_molecule(smiles)
    sc = _scscore.score_mol(mol)
    return {
        "linker_sa": round(float(_sascorer().calculateScore(mol)), 4),
        "linker_sc": round(sc, 4) if sc is not None else None,
        "linker_size": int(mol.GetNumHeavyAtoms()),
        "linker_rotatable_bonds": int(rdMolDescriptors.CalcNumRotatableBonds(mol)),
    }
