from __future__ import annotations

import gzip
import json

import pandas as pd
import pytest

from mofsift import score
from mofsift.config import ConfigError, parse_config
from mofsift.io.tables import normalise, read_table, write_table
from mofsift.score.reference import MofRecord, Reference, load_reference, normalise_linker, qmof_record

BDC = "O=C(O)c1ccc(C(=O)O)cc1"
BPDC = "O=C(O)c1ccc(-c2ccc(C(=O)O)cc2)cc1"


def _cfg(**score_keys):
    data = {"project": "t", "generate": {"source": "cifs", "cif_dir": "x"}, "score": score_keys}
    return parse_config(data)


def _table(**columns):
    n = len(next(iter(columns.values())))
    return pd.DataFrame(columns, index=pd.Index([f"ms-{i}" for i in range(n)], name="id"))


# ---------------------------------------------------------------- reference
def test_linker_normalisation_makes_acid_and_anion_equal():
    assert normalise_linker("OC(=O)c1ccc(cc1)C(=O)O") == BDC
    assert normalise_linker("[O-]C(=O)c1ccc(cc1)C(=O)[O-]") == BDC
    assert normalise_linker("[*]c1ccc([*])cc1") == "*c1ccc(*)cc1"
    assert (
        normalise_linker("c1ccccc1(") is None
        and normalise_linker("") is None
        and normalise_linker(None) is None
    )


def test_reference_counts_distinct_mofs():
    records = [
        MofRecord("Zn", frozenset({BDC}), "pcu"),
        MofRecord("Zn", frozenset({BDC}), "pcu"),  # the same MOF again: counted once
        MofRecord("Zn", frozenset({BDC}), None),
        MofRecord("Cu", frozenset({BDC, BPDC}), "nbo"),
        MofRecord(None, frozenset({BPDC}), None),
    ]
    ref = Reference.from_records(records, {"source": "test"})
    assert (ref.n_mofs, ref.n_with_topology) == (4, 2)
    assert (
        ref.topology_count("pcu") == 1 and ref.topology_count("dia") == 0 and ref.topology_count(None) is None
    )
    assert ref.metal_count("Zn") == 2 and ref.metal_count("Au") == 0
    assert ref.metal_count(None) is None and ref.metal_count("C") is None
    assert ref.linker_count([BDC]) == 3 and ref.linker_count([BDC, BPDC]) == 2  # the least common one
    assert ref.linker_count([]) is None
    assert ref.pair_count("Zn", [BDC]) == 2 and ref.pair_count("Zn", [BPDC]) == 0
    assert ref.pair_count("Cu", [BDC, BPDC]) == 1 and ref.pair_count(None, [BDC]) is None
    again = Reference.from_json(json.loads(gzip.decompress(ref.dumps())))
    assert again.to_json() == ref.to_json() and ref.dumps() == again.dumps()  # stable bytes


def test_qmof_rows_are_read_through_their_mofid_columns():
    row = {
        "info.formula": "Zn8C48H24O26",
        "info.mofid.smiles_nodes": "['[Zn][O]([Zn])([Zn])[Zn]']",
        "info.mofid.smiles_linkers": "['[O-]C(=O)c1ccc(cc1)C(=O)[O-]']",
        "info.mofid.topology": "pcu",
    }
    assert qmof_record(row) == MofRecord("Zn", frozenset({BDC}), "pcu")
    assert qmof_record({**row, "info.mofid.topology": float("nan")}).topology is None
    assert qmof_record({**row, "info.mofid.smiles_linkers": float("nan")}) is None
    assert qmof_record({**row, "info.mofid.smiles_nodes": "[]"}) is None


def test_the_shipped_reference():
    ref = load_reference()
    assert ref.n_mofs > 10000 and ref.n_with_topology > 3000
    assert "CC BY 4.0" in ref.info["licence"] and "QMOF" in ref.info["source"]
    # well-known chemistry is there: Zn with terephthalate, the pcu net, zinc and copper nodes
    assert ref.pair_count("Zn", [BDC]) > 50 and ref.linker_count([BDC]) > 200
    assert ref.topology_count("pcu") > 100 and ref.metal_count("Zn") > ref.metal_count("Au") > 0
    assert ref.linker_count(["O=C(O)C(F)(Cl)C(Br)(I)C(=O)O"]) == 0


# -------------------------------------------------------------------- stage
REF = Reference.from_records(
    [
        MofRecord("Zn", frozenset({BDC}), "pcu"),
        MofRecord("Zn", frozenset({BDC}), "dia"),
        MofRecord("Cu", frozenset({BPDC}), "pcu"),
    ]
)


def test_components_are_written_separately():
    table = _table(
        name=["a", "b", "c", "d"],
        topology=["pcu", "nbo", pd.NA, pd.NA],
        primary_metal=["Zn", "Cu", pd.NA, pd.NA],
        linker_smiles=[BDC, f"{BDC};{BPDC}", "[*]c1ccc([*])cc1", pd.NA],
    )
    result = score.run(table, _cfg(), reference=REF)
    t = result.table
    assert list(t.columns[:4]) == list(table.columns) and "score_aggregate" not in t.columns
    a = t.iloc[0]
    assert (a["linker_size"], a["linker_rotatable_bonds"]) == (12, 2) and a["linker_sc"] == pytest.approx(
        1.887
    )
    assert (a["precedent_topology"], a["precedent_node"], a["precedent_linker"], a["precedent_pair"]) == (
        2,
        2,
        2,
        2,
    )
    assert a["score_status"] == "ok" and pd.isna(a["score_notes"])
    b = t.iloc[1]  # two linkers: the harder and the rarer one count
    assert b["linker_size"] == 18 and b["linker_sc"] > a["linker_sc"]
    assert (b["precedent_topology"], b["precedent_node"], b["precedent_linker"], b["precedent_pair"]) == (
        0,
        1,
        1,
        0,
    )
    c = t.iloc[2]  # a linker with open connection points, no metal, no topology
    assert c["linker_size"] == 6 and c["precedent_linker"] == 0 and c["score_status"] == "ok"
    assert pd.isna(c["precedent_topology"]) and pd.isna(c["precedent_pair"]) and pd.isna(c["precedent_node"])
    assert "precedent_topology: the topology of this structure is not known" in c["score_notes"]
    d = t.iloc[3]  # nothing to score: skipped, with every reason, and no invented number
    assert d["score_status"] == "skipped" and "no linker SMILES" in d["score_skip_reason"]
    assert d[list(score.COMPONENT_COLUMNS)].isna().all()
    assert result.available["linker_sa"] == 3 and result.available["precedent_topology"] == 2
    pd.testing.assert_frame_equal(read_table(write_table(t, _tmp() / "t.csv")), normalise(t))


def _tmp():
    import pathlib
    import tempfile

    return pathlib.Path(tempfile.mkdtemp())


def test_only_listed_components_and_the_metal_from_the_formula():
    table = _table(formula=["C24H12O13Zn4"], linker_smiles=[BDC])
    t = score.run(table, _cfg(components=["precedent_node", "precedent_pair"]), reference=REF).table
    assert [c for c in t.columns if c in score.COMPONENT_COLUMNS] == ["precedent_node", "precedent_pair"]
    assert t["precedent_node"].item() == 2 and t["precedent_pair"].item() == 2
    skipped = score.run(table, _cfg(components=[]), reference=REF).table
    assert skipped["score_skip_reason"].item() == "no components listed in the config"
    with pytest.raises(ValueError, match="already has columns written by the score stage"):
        score.run(t, _cfg(), reference=REF)


def test_aggregate_is_a_rank_within_the_table_and_needs_every_weighted_component():
    table = _table(
        topology=["pcu", "pcu", "pcu", "pcu"],
        primary_metal=["Zn", "Zn", "Cu", "Zn"],
        linker_smiles=[BDC, BPDC, BPDC, pd.NA],
    )
    cfg = _cfg(aggregate={"enabled": True, "weights": {"linker_sc": 1, "precedent_pair": 3}})
    t = score.run(table, cfg, reference=REF).table
    agg = t["score_aggregate"]
    # row 0: simplest linker and the only pair with precedent -> best in both ranks
    assert agg.iloc[0] == 1.0 and agg.iloc[0] > agg.iloc[2] > 0 and agg.iloc[2] == agg.iloc[1] + 0.25
    assert pd.isna(agg.iloc[3]) and agg.dropna().between(0, 1).all()
    # lower SCScore must rank higher, higher precedent must rank higher
    assert (
        t["linker_sc"].iloc[0] < t["linker_sc"].iloc[1]
        and t["precedent_pair"].iloc[2] > t["precedent_pair"].iloc[1]
    )


def test_aggregate_rules_in_the_config():
    for bad, message in [
        ({"aggregate": {"enabled": True}}, "needs aggregate.weights"),
        (
            {"aggregate": {"enabled": True, "weights": {"precedent_node": 1}}},
            "precedent_node cannot be weighted",
        ),
        (
            {"components": ["linker_sa"], "aggregate": {"enabled": True, "weights": {"linker_sc": 1}}},
            "not listed",
        ),
        ({"components": ["linker_strain"]}, "score.components"),
    ]:
        with pytest.raises(ConfigError, match=message):
            _cfg(**bad)
    assert "precedent_linker" in _cfg().score.components and len(_cfg().score.components) == 8
