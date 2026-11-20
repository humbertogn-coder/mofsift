"""SCScore: a synthetic complexity score learned from reactions (1 = simple, 5 = complex).

Port of ``scscore/standalone_model_numpy.py`` from https://github.com/connorcoley/scscore
(commit 37090a6, 2018-02-05; MIT licence, Copyright (c) 2017 Connor Coley; see ``LICENSE`` in
this folder), with the weights of its ``full_reaxys_model_1024bool`` model, unmodified.

Reference: C. W. Coley, L. Rogers, W. H. Green, K. F. Jensen, "SCScore: Synthetic Complexity
Learned from a Reaction Corpus", J. Chem. Inf. Model. 2018, 58, 252-261.

Changes from the original file, none of which alters the numbers (checked against the
original code for the molecules in ``tests/test_linker_scores.py``):

* Python 3 and current NumPy and RDKit (the original uses ``cPickle``, ``six``, ``np.bool``
  and the Morgan fingerprint function that RDKit has since deprecated);
* only the 1024-bit boolean model; the weights are read from the JSON file, never from a pickle;
* no printing; a molecule that cannot be scored gives ``None`` instead of 0.
"""

from __future__ import annotations

import gzip
import json
from functools import cache
from importlib import resources

import numpy as np

WEIGHTS_FILE = "model.ckpt-10654.as_numpy.json.gz"
WEIGHTS_SHA256 = "fa90bd552307b2efd05eb1e53033bc9eadadfd5bdca682e541040982af99536b"
SCORE_SCALE = 5.0
FP_LENGTH = 1024
FP_RADIUS = 2


@cache
def _weights() -> tuple[np.ndarray, ...]:
    """Weight and bias arrays of the network, alternating."""
    raw = resources.files(__package__).joinpath(WEIGHTS_FILE).read_bytes()
    return tuple(np.asarray(x, dtype=np.float64) for x in json.loads(gzip.decompress(raw).decode("utf-8")))


@cache
def _fingerprints():
    from rdkit.Chem import rdFingerprintGenerator

    return rdFingerprintGenerator.GetMorganGenerator(
        radius=FP_RADIUS, fpSize=FP_LENGTH, includeChirality=True
    )


def score_mol(mol) -> float | None:
    """SCScore of an RDKit molecule; None when it has no fingerprint bits (for example no atoms)."""
    x = _fingerprints().GetFingerprintAsNumPy(mol).astype(np.float64)
    if not x.any():
        return None
    weights = _weights()
    for i in range(0, len(weights), 2):
        x = x @ weights[i] + weights[i + 1]
        if i != len(weights) - 2:
            x = np.maximum(x, 0.0)  # ReLU
    value = float(np.ravel(x)[0])
    return 1.0 + (SCORE_SCALE - 1.0) / (1.0 + float(np.exp(-value)))
