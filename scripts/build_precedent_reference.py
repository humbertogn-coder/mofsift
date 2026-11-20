#!/usr/bin/env python3
"""Build the precedent reference that ships with mofsift from the QMOF database.

    python scripts/build_precedent_reference.py path/to/qmof.csv

``qmof.csv`` is part of ``qmof_database.zip`` at https://doi.org/10.6084/m9.figshare.13147324
(CC BY 4.0). Only rows flagged ``info.synthesized`` are used. The output replaces
``src/mofsift/score/data/qmof_precedent.json.gz``.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pandas as pd

from mofsift.score.reference import REFERENCE_FILE, Reference, qmof_record


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    source = Path(argv[1])
    table = pd.read_csv(source, low_memory=False)
    made = table[table["info.synthesized"] == True]  # noqa: E712
    records = [r for r in (qmof_record(row) for row in made.to_dict("records")) if r is not None]
    info = {
        "source": "QMOF database, qmof.csv (https://doi.org/10.6084/m9.figshare.13147324)",
        "licence": "CC BY 4.0; derived counts, not the original data",
        "cite": "Rosen et al., Matter 2021, 4, 1578; Rosen et al., npj Comput. Mater. 2022, 8, 195",
        "qmof_csv_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "rows_synthesized": len(made),
        "rows_with_mofid_decomposition": len(records),
    }
    reference = Reference.from_records(records, info)
    target = Path(__file__).resolve().parents[1] / "src" / "mofsift" / "score" / "data" / REFERENCE_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(reference.dumps())
    print(f"{len(made)} synthesised rows, {len(records)} with a MOFid decomposition")
    print(
        f"{reference.n_mofs} distinct MOFs, {reference.n_with_topology} with a topology; "
        f"{len(reference.topology)} topologies, {len(reference.metal)} metals, "
        f"{len(reference.linker)} linkers, {len(reference.pair)} metal-linker pairs"
    )
    print(f"wrote {target} ({target.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
