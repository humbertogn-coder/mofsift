"""The column dictionary: what every column of the tables means.

Each column a stage writes has an entry here with the stage that writes it, its unit and
a one-line description. An export copies the entries of its columns into the manifest, so
a table or a viewer bundle can be read without the documentation at hand. A test keeps
this dictionary and the columns registered by the stages in step.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

ID_DESCRIPTION = "Record ID; the key of the table. It depends only on what defines the candidate."

JSON_TYPES = {"string": "string", "Int64": "integer", "Float64": "number", "boolean": "boolean"}
"""Type names used in manifests and JSON Schemas, by pandas dtype of the working tables."""
PANDAS_TYPES = {v: k for k, v in JSON_TYPES.items()}


@dataclass(frozen=True)
class ColumnInfo:
    group: str  # the stage (or sub-stage) that writes the column
    description: str
    unit: str | None = None


def _status(group: str, what: str) -> dict[str, ColumnInfo]:
    key = group.replace(".", "_")
    return {
        f"{key}_status": ColumnInfo(group, f"Whether {what}: ok, skipped or failed."),
        f"{key}_skip_reason": ColumnInfo(group, "Why, when the status is not ok."),
    }


_A = "angstrom"

COLUMN_INFO: dict[str, ColumnInfo] = {
    # ------------------------------------------------------------ generate
    "name": ColumnInfo("generate", "Readable label: topology__nodes__edges, or the file name."),
    "source": ColumnInfo("generate", "Where the structure comes from: pormake or cifs."),
    "topology": ColumnInfo("generate", "Net the structure was assembled on; empty for existing CIFs."),
    "nodes": ColumnInfo("generate", "Node building blocks, + between several."),
    "edges": ColumnInfo("generate", "Edge building blocks, + between several; empty without edges."),
    "source_file": ColumnInfo("generate", "Original file inside cif_dir; empty for assembled structures."),
    "n_atoms": ColumnInfo("generate", "Atoms in the unit cell."),
    "formula": ColumnInfo("generate", "Formula of the unit cell, Hill order."),
    "expected_formula": ColumnInfo("generate", "Sum of the building blocks; empty for existing CIFs."),
    "linker_smiles": ColumnInfo(
        "generate", "Canonical SMILES of the linkers when they are known, ; between several."
    ),
    "node_rmsd": ColumnInfo("generate", "Largest node-to-vertex misfit reported by PORMAKE.", _A),
    "cif_path": ColumnInfo(
        "generate", "Path of the structure file, relative to the folder that holds the table."
    ),
    **_status("generate", "the structure was built or read"),
    # ------------------------------------------------------------------ qc
    "density": ColumnInfo("qc", "Density from the cell and the atomic masses.", "g/cm3"),
    "volume": ColumnInfo("qc", "Cell volume.", "angstrom3"),
    "cell_a": ColumnInfo("qc", "Cell length a.", _A),
    "cell_b": ColumnInfo("qc", "Cell length b.", _A),
    "cell_c": ColumnInfo("qc", "Cell length c.", _A),
    "cell_alpha": ColumnInfo("qc", "Cell angle alpha.", "degree"),
    "cell_beta": ColumnInfo("qc", "Cell angle beta.", "degree"),
    "cell_gamma": ColumnInfo("qc", "Cell angle gamma.", "degree"),
    "qc_min_distance": ColumnInfo("qc", "Shortest interatomic distance.", _A),
    "qc_n_close_pairs": ColumnInfo("qc", "Pairs of atoms closer than qc.min_distance."),
    "qc_n_overlaps": ColumnInfo("qc", "Heavy-heavy or H-H pairs closer than qc.min_distance_heavy."),
    "qc_n_nonbonded_contacts": ColumnInfo("qc", "Non-bonded pairs closer than allowed."),
    "qc_n_components": ColumnInfo("qc", "Connected components of the bond network."),
    "qc_n_isolated_atoms": ColumnInfo("qc", "Atoms without any bond."),
    "qc_n_bad_h": ColumnInfo("qc", "H atoms that do not have exactly one neighbour."),
    "qc_n_bad_c": ColumnInfo("qc", "C atoms with fewer than 2 or more than 4 non-metal neighbours."),
    "qc_n_short_bonds": ColumnInfo("qc", "Compressed covalent bonds (reported, not a check)."),
    "qc_composition_ok": ColumnInfo("qc", "The formula equals the sum of the building blocks."),
    "qc_node_rmsd_ok": ColumnInfo("qc", "The node misfit is within qc.max_node_rmsd."),
    "qc_pass": ColumnInfo(
        "qc", "No enabled structural check failed. Not a statement about stability or synthesis."
    ),
    "qc_reasons": ColumnInfo("qc", "The failed checks, separated by ; ."),
    **_status("qc", "the checks ran"),
    # ------------------------------------------------- describe.composition
    "primary_metal": ColumnInfo("describe.composition", "Most abundant metal (ties: alphabetical)."),
    "metals": ColumnInfo("describe.composition", "All metals present, + between them."),
    "n_metal_atoms": ColumnInfo("describe.composition", "Metal atoms in the unit cell."),
    "metal_fraction": ColumnInfo("describe.composition", "Metal atoms as a fraction of all atoms."),
    "n_transition_metal_atoms": ColumnInfo("describe.composition", "d-block atoms in the unit cell."),
    "has_open_shell_3d_metal": ColumnInfo(
        "describe.composition",
        "The primary metal is Ti-Cu. A flag on the composition, not a computed spin state.",
    ),
    "c_fraction": ColumnInfo("describe.composition", "C atoms as a fraction of all atoms."),
    "h_fraction": ColumnInfo("describe.composition", "H atoms as a fraction of all atoms."),
    "n_fraction": ColumnInfo("describe.composition", "N atoms as a fraction of all atoms."),
    "o_fraction": ColumnInfo("describe.composition", "O atoms as a fraction of all atoms."),
    "heteroatom_fraction": ColumnInfo(
        "describe.composition", "N, O, S, F, Cl, Br and I atoms as a fraction of all atoms."
    ),
    "mean_atomic_number": ColumnInfo("describe.composition", "Mean atomic number over all atoms."),
    "std_atomic_number": ColumnInfo("describe.composition", "Spread of the atomic number over all atoms."),
    "mean_electronegativity": ColumnInfo(
        "describe.composition", "Mean Pauling electronegativity over all atoms."
    ),
    "std_electronegativity": ColumnInfo(
        "describe.composition", "Spread of the Pauling electronegativity over all atoms."
    ),
    **_status("describe.composition", "the composition descriptors were computed"),
    # ---------------------------------------------------- describe.geometry
    "zeo_high_accuracy": ColumnInfo(
        "describe.geometry", "Zeo++ accuracy setting that produced this row: OCC, DEF or off."
    ),
    "lcd": ColumnInfo("describe.geometry", "Largest cavity diameter (largest included sphere).", _A),
    "pld": ColumnInfo("describe.geometry", "Pore limiting diameter (largest free sphere).", _A),
    "lcd_free_path": ColumnInfo(
        "describe.geometry", "Largest included sphere along the free-sphere path.", _A
    ),
    "accessible_surface_m2_g": ColumnInfo("describe.geometry", "Surface accessible to the probe.", "m2/g"),
    "accessible_surface_m2_cm3": ColumnInfo(
        "describe.geometry", "Surface accessible to the probe.", "m2/cm3"
    ),
    "accessible_volume_fraction": ColumnInfo(
        "describe.geometry", "Fraction of the cell volume accessible to the centre of the probe."
    ),
    "accessible_volume_cm3_g": ColumnInfo(
        "describe.geometry", "Volume accessible to the centre of the probe.", "cm3/g"
    ),
    **_status("describe.geometry", "the pore geometry was computed"),
    # --------------------------------------------------------- describe.xtb
    "xtb_gap": ColumnInfo(
        "describe.xtb",
        "xtb_lumo minus xtb_homo from periodic GFN-xTB. A proxy, not a DFT or measured band gap; "
        "comparable between rows only when xtb_unpaired_electrons is 0.",
        "eV",
    ),
    "xtb_homo": ColumnInfo("describe.xtb", "Highest doubly occupied level.", "eV"),
    "xtb_lumo": ColumnInfo("describe.xtb", "Lowest level that is not doubly occupied.", "eV"),
    "xtb_unpaired_electrons": ColumnInfo(
        "describe.xtb", "Unpaired electrons used in the calculation (a rule of thumb, not computed)."
    ),
    **_status("describe.xtb", "the xTB calculation ran"),
    # --------------------------------------------------------------- score
    "linker_sa": ColumnInfo("score", "SA score of the linker, 1 (easy) to 10 (hard). Smaller is better."),
    "linker_sc": ColumnInfo("score", "SCScore of the linker, 1 (simple) to 5 (complex). Smaller is better."),
    "linker_size": ColumnInfo("score", "Heavy atoms of the linker. Smaller is better."),
    "linker_rotatable_bonds": ColumnInfo("score", "Rotatable bonds of the linker. Smaller is better."),
    "precedent_topology": ColumnInfo(
        "score", "Distinct synthesised MOFs in the reference with this topology. Larger is better."
    ),
    "precedent_node": ColumnInfo(
        "score",
        "Distinct synthesised MOFs in the reference with this primary metal. Information only: "
        "it does not separate synthesised from hypothetical MOFs.",
    ),
    "precedent_linker": ColumnInfo(
        "score", "Distinct synthesised MOFs in the reference with this linker. Larger is better."
    ),
    "precedent_pair": ColumnInfo(
        "score", "Distinct synthesised MOFs in the reference with this linker and metal. Larger is better."
    ),
    "score_aggregate": ColumnInfo(
        "score",
        "Weighted mean rank of the chosen components within one table, 0 (worst) to 1 (best). "
        "Not a probability, and not comparable between tables.",
    ),
    "score_notes": ColumnInfo("score", "Why a component is empty for this row."),
    **_status("score", "at least one component was computed"),
    # -------------------------------------------------------------- filter
    "filter_pass": ColumnInfo(
        "filter", "The row satisfies every filter of the config. Empty when there are no filters."
    ),
    "filter_rejected_by": ColumnInfo(
        "filter", "The filters the row did not satisfy, with the missing columns when undecided."
    ),
    **_status("filter", "the filters were applied"),
}

STATUS_COLUMNS = frozenset(c for c in COLUMN_INFO if c.endswith("_status"))


def json_type(dtype: Any) -> str:
    """Manifest type name of a pandas dtype."""
    name = str(dtype)
    if name in JSON_TYPES:
        return JSON_TYPES[name]
    if pd.api.types.is_bool_dtype(dtype):
        return "boolean"
    if pd.api.types.is_integer_dtype(dtype):
        return "integer"
    return "number" if pd.api.types.is_numeric_dtype(dtype) else "string"


def describe_columns(table: pd.DataFrame) -> list[dict[str, Any]]:
    """Dictionary entries for the ID and every column of ``table``, in table order.

    A column that no stage of this version writes gets its type and nothing else.
    """
    entries: list[dict[str, Any]] = [
        {"name": "id", "type": "string", "group": "generate", "unit": None, "description": ID_DESCRIPTION}
    ]
    for name in table.columns:
        info = COLUMN_INFO.get(str(name))
        entries.append(
            {
                "name": str(name),
                "type": json_type(table[name].dtype),
                "group": info.group if info else None,
                "unit": info.unit if info else None,
                "description": info.description if info else None,
            }
        )
    return entries
