from __future__ import annotations

from pathlib import Path

import pytest

from mofsift.config import Config, ConfigError, load_config, parse_config, resolve_path
from mofsift.templates import EXAMPLE_CONFIG


def test_example_config_is_valid(example_dict):
    cfg = parse_config(example_dict)
    assert cfg.project == "demo_irmof" and cfg.seed == 42
    assert cfg.generate.edges == ["E14", "E34"]
    assert cfg.describe.geometry.enabled == "auto"
    assert cfg.filter == ["qc_pass == True"]


def test_minimal_config_gets_documented_defaults(minimal_dict):
    cfg = parse_config(minimal_dict)
    assert cfg.seed == 0
    assert cfg.generate.max_atoms is None  # no limit unless the user sets one
    assert (cfg.qc.min_distance, cfg.qc.min_distance_heavy) == (0.70, 1.00)
    assert cfg.describe.xtb.enabled == "auto" and cfg.describe.xtb.periodic is True
    assert cfg.score.strain.enabled is False and cfg.score.aggregate.enabled is False
    assert cfg.filter == [] and cfg.export.table_format == "csv"


def test_unknown_key_is_rejected_with_suggestion(minimal_dict):
    minimal_dict["generate"]["topologys"] = ["dia"]
    with pytest.raises(ConfigError) as err:
        parse_config(minimal_dict, source="x.yaml")
    msg = str(err.value)
    assert "x.yaml" in msg and "generate.topologys" in msg and "did you mean 'topologies'" in msg


def test_missing_required_keys(minimal_dict):
    del minimal_dict["project"]
    with pytest.raises(ConfigError, match="project: required key is missing"):
        parse_config(minimal_dict)
    with pytest.raises(ConfigError, match="generate: required key is missing"):
        parse_config({"project": "t"})


@pytest.mark.parametrize("missing", ["topologies", "nodes"])
def test_pormake_source_needs_blocks(minimal_dict, missing):
    minimal_dict["generate"][missing] = []
    with pytest.raises(ConfigError, match=f"needs non-empty: {missing}"):
        parse_config(minimal_dict)


def test_edges_may_be_empty_for_directly_connected_nodes(minimal_dict):
    minimal_dict["generate"]["edges"] = []
    assert parse_config(minimal_dict).generate.edges == []


def test_cif_source_needs_folder():
    with pytest.raises(ConfigError, match="needs cif_dir"):
        parse_config({"project": "t", "generate": {"source": "cifs"}})
    cfg = parse_config({"project": "t", "generate": {"source": "cifs", "cif_dir": "structures"}})
    assert cfg.generate.cif_dir == Path("structures")


def test_duplicates_are_rejected(minimal_dict):
    minimal_dict["generate"]["edges"] = ["E14", "E34", "E14"]
    with pytest.raises(ConfigError, match="duplicate edges: E14"):
        parse_config(minimal_dict)


@pytest.mark.parametrize("value, ok", [("auto", True), (True, True), (False, True), ("yes please", False)])
def test_enabled_accepts_auto_or_bool(minimal_dict, value, ok):
    minimal_dict["describe"] = {"xtb": {"enabled": value}}
    if ok:
        assert parse_config(minimal_dict).describe.xtb.enabled == value
    else:
        with pytest.raises(ConfigError, match=r"describe\.xtb\.enabled"):
            parse_config(minimal_dict)


@pytest.mark.parametrize(
    "path, value",
    [
        (("qc", "min_distance"), -1),
        (("generate", "max_atoms"), 0),
        (("describe", "xtb", "method"), "GFN3"),
        (("export", "table_format"), "xlsx"),
        (("seed",), -5),
        (("project",), "has spaces"),
        (("score", "components"), ["linker_sa", "magic"]),
    ],
)
def test_invalid_values_are_rejected(minimal_dict, path, value):
    node = minimal_dict
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value
    with pytest.raises(ConfigError, match=path[0]):
        parse_config(minimal_dict)


def test_strain_cannot_be_enabled_yet(minimal_dict):
    minimal_dict["score"] = {"strain": {"enabled": True}}
    with pytest.raises(ConfigError, match="not available in this version"):
        parse_config(minimal_dict)


def test_aggregate_rules(minimal_dict):
    minimal_dict["score"] = {"components": ["linker_sa"], "aggregate": {"enabled": True}}
    with pytest.raises(ConfigError, match=r"needs aggregate\.weights"):
        parse_config(minimal_dict)
    minimal_dict["score"]["aggregate"] = {"enabled": True, "weights": {"precedent_node": 1.0}}
    with pytest.raises(ConfigError, match="not listed: precedent_node"):
        parse_config(minimal_dict)
    minimal_dict["score"]["aggregate"] = {"enabled": True, "weights": {"linker_sa": 0}}
    with pytest.raises(ConfigError, match="must be positive"):
        parse_config(minimal_dict)
    minimal_dict["score"]["aggregate"] = {"enabled": True, "weights": {"linker_sa": 2.0}}
    assert parse_config(minimal_dict).score.aggregate.weights == {"linker_sa": 2.0}


def test_bad_filter_is_rejected_at_load(minimal_dict):
    minimal_dict["filter"] = ["__import__('os').system('echo hi')"]
    with pytest.raises(ConfigError, match="not allowed"):
        parse_config(minimal_dict)


def test_config_hash_is_deterministic_and_sensitive(minimal_dict, example_dict):
    a, b = parse_config(minimal_dict), parse_config(dict(reversed(list(minimal_dict.items()))))
    assert a.config_hash() == b.config_hash()  # key order does not matter
    explicit = parse_config({**minimal_dict, "seed": 0})
    assert explicit.config_hash() == a.config_hash()  # writing a default explicitly does not matter
    changed = parse_config({**minimal_dict, "seed": 1})
    assert changed.config_hash() != a.config_hash()
    assert len(a.config_hash()) == 64
    assert parse_config(example_dict).config_hash() == Config.model_validate(example_dict).config_hash()


def test_load_config_from_file(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    assert load_config(f).project == "demo_irmof"


def test_load_config_errors(tmp_path):
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "missing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("project: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(bad)
    lst = tmp_path / "list.yaml"
    lst.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="must be a YAML mapping"):
        load_config(lst)
    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ConfigError, match="must be a YAML mapping"):
        load_config(empty)


def test_resolve_path_is_relative_to_the_config_file(tmp_path):
    cfg_file = tmp_path / "runs" / "c.yaml"
    assert resolve_path(cfg_file, "work") == (tmp_path / "runs" / "work").resolve()
    assert resolve_path(cfg_file, tmp_path / "abs") == tmp_path / "abs"


@pytest.mark.parametrize("name", ["pormake_small.yaml", "existing_cifs.yaml", "linkers_from_smiles.yaml"])
def test_shipped_examples_are_valid(name):
    cfg = load_config(Path(__file__).resolve().parents[1] / "examples" / name)
    assert cfg.project == name.removesuffix(".yaml")


def test_linkers_section(minimal_dict):
    minimal_dict["generate"]["linkers"] = {"smiles": {"bdc": "OC(=O)c1ccc(cc1)C(=O)O"}}
    linkers = parse_config(minimal_dict).generate.linkers
    assert linkers.connection_distance == 0.75 and linkers.conformers == 10 and linkers.smiles_file is None
    minimal_dict["generate"]["linkers"] = {"smiles_file": "linkers.smi", "conformers": 3}
    assert parse_config(minimal_dict).generate.linkers.smiles_file == Path("linkers.smi")
    for bad, message in [
        ({}, "give smiles_file, smiles, or both"),
        ({"smiles": {"a": "CC"}, "conformers": 0}, "generate.linkers.conformers"),
        ({"smiles": {"a": "CC"}, "connection_distance": 2.0}, "generate.linkers.connection_distance"),
        ({"smile": {"a": "CC"}}, "did you mean 'smiles'"),
    ]:
        minimal_dict["generate"]["linkers"] = bad
        with pytest.raises(ConfigError, match=message):
            parse_config(minimal_dict)
    assert parse_config({**minimal_dict, "generate": {**minimal_dict["generate"], "linkers": None}})


def test_geometry_fallback_settings(minimal_dict):
    geo = parse_config(minimal_dict).describe.geometry
    assert geo.high_accuracy == "OCC" and geo.fallback == ["DEF", False]
    minimal_dict["describe"] = {"geometry": {"high_accuracy": False, "fallback": []}}
    geo = parse_config(minimal_dict).describe.geometry
    assert geo.high_accuracy is False and geo.fallback == []
    for bad in ({"fallback": ["DEF", "DEF"]}, {"fallback": ["fast"]}, {"high_accuracy": True}):
        minimal_dict["describe"] = {"geometry": bad}
        with pytest.raises(ConfigError, match=r"describe\.geometry"):
            parse_config(minimal_dict)
