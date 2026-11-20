from __future__ import annotations

import hashlib
from importlib import resources

import pytest

from mofsift.score import COLUMNS, LinkerScoreError, _scscore, linker_scores
from mofsift.score.linker import scoring_molecule

# SCScore values from the original code (connorcoley/scscore, commit 37090a6) with its 1024-bit
# boolean model, run under current NumPy with compatibility shims only.
ORIGINAL_SCSCORE = {
    "CCCOCCC": 1.432261,
    "CCCNc1ccccc1": 1.891306,
    "OC(=O)c1ccc(cc1)C(=O)O": 1.887001,
    "Nc1cc(C(=O)O)ccc1C(=O)O": 2.054769,
    "OC(=O)c1ccc(cc1)-c1ccc(cc1)C(=O)O": 2.062599,
    "OC(=O)c1cc(cc(c1)C(=O)O)C(=O)O": 1.761005,
    "C[C@H](N)C(=O)O": 1.370809,
    "c1ccccc1": 1.191512,
    "OC(=O)c1ccc2cc(ccc2c1)C(=O)O": 1.876066,
}


@pytest.mark.parametrize(("smiles", "expected"), ORIGINAL_SCSCORE.items())
def test_scscore_port_reproduces_the_original_code(smiles, expected):
    assert _scscore.score_mol(scoring_molecule(smiles)) == pytest.approx(expected, abs=2e-6)


def test_the_shipped_weights_are_the_original_file():
    data = resources.files("mofsift.score._scscore").joinpath(_scscore.WEIGHTS_FILE).read_bytes()
    assert hashlib.sha256(data).hexdigest() == _scscore.WEIGHTS_SHA256
    licence = resources.files("mofsift.score._scscore").joinpath("LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in licence and "Connor Coley" in licence
    shapes = [w.shape for w in _scscore._weights()]
    assert shapes[0][0] == 1024 and shapes[-1] == (1,) and len(shapes) % 2 == 0


def test_linker_components():
    bdc = linker_scores("OC(=O)c1ccc(cc1)C(=O)O")
    assert set(bdc) == set(COLUMNS)
    assert (bdc["linker_size"], bdc["linker_rotatable_bonds"]) == (12, 2)
    assert 1.0 <= bdc["linker_sa"] <= 2.0 and bdc["linker_sc"] == pytest.approx(1.887, abs=1e-3)
    # a larger, more decorated linker scores as harder on both scales
    hard = linker_scores("OC(=O)c1cc(C#Cc2ccc(cc2)C(F)(F)F)c(cc1N)C(=O)O")
    assert hard["linker_sa"] > bdc["linker_sa"] and hard["linker_sc"] > bdc["linker_sc"]
    assert hard["linker_size"] > bdc["linker_size"]
    # the same molecule written differently gives the same numbers
    assert linker_scores("O=C(O)c1ccc(C(=O)O)cc1") == bdc


def test_connection_points_are_scored_as_hydrogen():
    assert linker_scores("[*]c1ccc([*])cc1") == linker_scores("c1ccccc1")
    assert linker_scores("[*]C#C[*]")["linker_size"] == 2


@pytest.mark.parametrize("bad", ["", "c1ccccc1(", "[*][*]", None])
def test_unusable_smiles(bad):
    with pytest.raises(LinkerScoreError):
        linker_scores(bad)
