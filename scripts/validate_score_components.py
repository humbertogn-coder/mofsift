#!/usr/bin/env python3
"""Check each synthetic-accessibility component against experiment, using the QMOF database.

    python scripts/validate_score_components.py path/to/qmof.csv [output.json]

Question: does a component rank MOFs that were synthesised above hypothetical ones?

* Positive set: QMOF structures flagged ``info.synthesized`` (from the CSD and CoRE MOF).
* Unlabelled set: QMOF's hypothetical structures. They are not negatives: nobody showed that
  they cannot be made, and several of these sets were built from known building blocks.
* A MOF is counted once (same primary metal, linkers and topology). Hypothetical MOFs that
  coincide with a synthesised one are left out of the unlabelled set.
* Precedent components are computed by 5-fold cross-validation: a synthesised MOF is never
  looked up in a reference that contains it.
* Reported: ROC-AUC, the probability that a synthesised MOF gets the better value than a
  hypothetical one (0.5 = no separation), as mean and standard deviation over the folds.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu

from mofsift.score.linker import LinkerScoreError, linker_scores
from mofsift.score.reference import Reference, qmof_record

FOLDS, SEED = 5, 0
# +1: a larger value should mean "more accessible"; -1: a smaller one
ORIENTATION = {
    "linker_sa": -1,
    "linker_sc": -1,
    "linker_size": -1,
    "linker_rotatable_bonds": -1,
    "precedent_topology": 1,
    "precedent_node": 1,
    "precedent_linker": 1,
    "precedent_pair": 1,
}


def auc(positive: list[float], unlabelled: list[float]) -> float | None:
    if len(positive) < 10 or len(unlabelled) < 10:
        return None
    u = mannwhitneyu(positive, unlabelled, alternative="two-sided").statistic
    return float(u / (len(positive) * len(unlabelled)))


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__)
        return 2
    table = pd.read_csv(argv[1], low_memory=False)
    made, hypothetical, sources = {}, {}, {}
    for row in table.to_dict("records"):
        rec = qmof_record(row)
        if rec is None:
            continue
        if row["info.synthesized"] is True:
            made[rec.identity] = rec
        else:
            hypothetical[rec.identity] = rec
            sources[rec.identity] = row["info.source"]
    overlap = [k for k in hypothetical if k in made]
    for k in overlap:
        del hypothetical[k]

    cache: dict[str, dict | None] = {}

    def linker_part(rec) -> dict[str, float | None]:
        rows = []
        for smiles in rec.linkers:
            if smiles not in cache:
                try:
                    cache[smiles] = linker_scores(smiles)
                except LinkerScoreError:
                    cache[smiles] = None
            if cache[smiles] is None:
                return dict.fromkeys(("linker_sa", "linker_sc", "linker_size", "linker_rotatable_bonds"))
            rows.append(cache[smiles])
        return {k: max(r[k] for r in rows) if all(r[k] is not None for r in rows) else None for k in rows[0]}

    def precedent_part(rec, ref: Reference) -> dict[str, float | None]:
        return {
            "precedent_topology": ref.topology_count(rec.topology),
            "precedent_node": ref.metal_count(rec.metal),
            "precedent_linker": ref.linker_count(rec.linkers),
            "precedent_pair": ref.pair_count(rec.metal, rec.linkers),
        }

    made_keys = sorted(made, key=repr)
    order = np.random.default_rng(SEED).permutation(len(made_keys))
    fold_of = {made_keys[i]: n % FOLDS for n, i in enumerate(order)}
    linker_values = {k: linker_part(r) for k, r in {**made, **hypothetical}.items()}

    per_fold: dict[str, list[float]] = defaultdict(list)
    per_source: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    counts: dict[str, tuple[int, int]] = {}
    for fold in range(FOLDS):
        reference = Reference.from_records(r for k, r in made.items() if fold_of[k] != fold)
        held_out = [k for k in made_keys if fold_of[k] == fold]
        values = {
            k: {**linker_values[k], **precedent_part(rec, reference)}
            for k, rec in ((k, {**made, **hypothetical}[k]) for k in [*held_out, *hypothetical])
        }
        for name, sign in ORIENTATION.items():
            pos = [sign * values[k][name] for k in held_out if values[k][name] is not None]
            unl = [sign * values[k][name] for k in hypothetical if values[k][name] is not None]
            result = auc(pos, unl)
            if result is not None:
                per_fold[name].append(result)
                counts[name] = (len(pos) * FOLDS, len(unl))
            for source in sorted(set(sources.values())):
                sub = [
                    sign * values[k][name]
                    for k in hypothetical
                    if sources[k] == source and values[k][name] is not None
                ]
                result = auc(pos, sub)
                if result is not None:
                    per_source[source][name].append(result)

    summary = {
        "qmof_rows": len(table),
        "distinct_synthesised": len(made),
        "distinct_hypothetical": len(hypothetical),
        "hypothetical_identical_to_synthesised_left_out": len(overlap),
        "hypothetical_by_source": dict(Counter(sources[k] for k in hypothetical)),
        "folds": FOLDS,
        "components": {
            name: {
                "auc_mean": round(float(np.mean(v)), 3),
                "auc_sd": round(float(np.std(v)), 3),
                "n_synthesised": counts[name][0],
                "n_hypothetical": counts[name][1],
                "better_when": "smaller" if ORIENTATION[name] < 0 else "larger",
            }
            for name, v in per_fold.items()
        },
        "auc_by_hypothetical_source": {
            source: {name: round(float(np.mean(v)), 3) for name, v in comps.items()}
            for source, comps in per_source.items()
        },
    }
    text = json.dumps(summary, indent=2)
    if len(argv) == 3:
        Path(argv[2]).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
