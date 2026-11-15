"""The qc stage: structural checks on every structure of the table.

``qc_status`` says whether the checks ran; ``qc_pass`` says whether the structure passed
them. A row without a structure (generation failed or was skipped) is ``skipped`` and its
``qc_pass`` is missing, so a filter such as ``qc_pass == True`` leaves it out.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from mofsift.chem import parse_formula
from mofsift.config import Config
from mofsift.io.structures import StructureError, read_structure
from mofsift.io.tables import register_columns
from mofsift.qc.checks import QCResult, check_atoms
from mofsift.stages import STATUS_FAILED, STATUS_OK, STATUS_SKIPPED, stage

STAGE = "qc"

COLUMNS = {
    "density": "Float64",
    "volume": "Float64",
    "cell_a": "Float64",
    "cell_b": "Float64",
    "cell_c": "Float64",
    "cell_alpha": "Float64",
    "cell_beta": "Float64",
    "cell_gamma": "Float64",
    "qc_min_distance": "Float64",
    "qc_n_close_pairs": "Int64",
    "qc_n_overlaps": "Int64",
    "qc_n_nonbonded_contacts": "Int64",
    "qc_n_components": "Int64",
    "qc_n_isolated_atoms": "Int64",
    "qc_n_bad_h": "Int64",
    "qc_n_bad_c": "Int64",
    "qc_n_short_bonds": "Int64",
    "qc_composition_ok": "boolean",
    "qc_node_rmsd_ok": "boolean",
    "qc_pass": "boolean",
    "qc_reasons": "string",
    "qc_status": "string",
    "qc_skip_reason": "string",
}
register_columns(COLUMNS)


def _none_to_na(value: Any) -> Any:
    return pd.NA if value is None else value


def _result_row(res: QCResult) -> dict[str, Any]:
    a, b, c, alpha, beta, gamma = res.cell
    return {
        "density": res.density,
        "volume": res.volume,
        "cell_a": a,
        "cell_b": b,
        "cell_c": c,
        "cell_alpha": alpha,
        "cell_beta": beta,
        "cell_gamma": gamma,
        "qc_min_distance": _none_to_na(res.min_distance),
        "qc_n_close_pairs": res.n_close_pairs,
        "qc_n_overlaps": res.n_overlaps,
        "qc_n_nonbonded_contacts": _none_to_na(res.n_nonbonded_contacts),
        "qc_n_components": res.n_components,
        "qc_n_isolated_atoms": res.n_isolated_atoms,
        "qc_n_bad_h": res.n_bad_h,
        "qc_n_bad_c": res.n_bad_c,
        "qc_n_short_bonds": res.n_short_bonds,
        "qc_composition_ok": _none_to_na(res.composition_ok),
        "qc_node_rmsd_ok": _none_to_na(res.node_rmsd_ok),
        "qc_pass": res.passed,
        "qc_reasons": "; ".join(res.reasons) if res.reasons else pd.NA,
    }


def _bonds(workdir: Path, cif_path: str) -> list[list[int]] | None:
    """Bond list written by the generate stage next to the CIF, if there is one."""
    record = (workdir / cif_path).with_suffix(".build.json")
    if not record.is_file():
        return None
    try:
        return json.loads(record.read_text(encoding="utf-8"))["bonds"]
    except (OSError, ValueError, KeyError):
        return None


def _value(row: pd.Series, column: str) -> Any:
    value = row.get(column, pd.NA)
    return None if pd.isna(value) else value


@stage(STAGE)
def run(table: pd.DataFrame, cfg: Config, workdir: Path) -> pd.DataFrame:
    """Check every structure listed in the ``cif_path`` column."""
    workdir = Path(workdir)
    if "cif_path" not in table.columns:
        raise ValueError(
            "the qc stage needs a 'cif_path' column (path of each CIF relative to the work folder)"
        )
    clash = [c for c in COLUMNS if c in table.columns]
    if clash:
        raise ValueError(f"the input table already has columns written by the qc stage: {', '.join(clash)}")
    rows: list[dict[str, Any]] = []
    for _, row in table.iterrows():
        out: dict[str, Any] = dict.fromkeys(COLUMNS, pd.NA)
        upstream = _value(row, "generate_status")
        cif_path = _value(row, "cif_path")
        if cif_path is None or upstream not in (None, STATUS_OK):
            out["qc_status"] = STATUS_SKIPPED
            out["qc_skip_reason"] = (
                f"no structure (generate: {upstream})"
                if upstream not in (None, STATUS_OK)
                else "no structure"
            )
            rows.append(out)
            continue
        try:
            atoms = read_structure(workdir / cif_path)
            expected = _value(row, "expected_formula")
            result = check_atoms(
                atoms,
                cfg.qc,
                bonds=_bonds(workdir, cif_path),
                expected_counts=parse_formula(expected) if expected is not None else None,
                node_rmsd=_value(row, "node_rmsd"),
            )
        except (StructureError, ValueError) as err:
            out["qc_status"] = STATUS_FAILED
            out["qc_skip_reason"] = f"cannot check the structure: {err}"
            rows.append(out)
            continue
        out.update(_result_row(result))
        out["qc_status"] = STATUS_OK
        rows.append(out)
    added = pd.DataFrame(rows, columns=list(COLUMNS), index=table.index).astype(COLUMNS)
    return pd.concat([table, added], axis=1)
