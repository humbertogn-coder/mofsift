from __future__ import annotations

import json
from importlib import resources

import jsonschema
import pytest

from mofsift.config import config_json_schema


def _packaged() -> dict:
    return json.loads(resources.files("mofsift.schemas").joinpath("config.schema.json").read_text("utf-8"))


def _property_paths(schema: dict) -> set[str]:
    """Every property name with the definition it belongs to; robust to cosmetic schema changes."""
    out = {f"Config.{k}" for k in schema.get("properties", {})}
    for name, sub in schema.get("$defs", {}).items():
        out |= {f"{name}.{k}" for k in sub.get("properties", {})}
    return out


def test_packaged_schema_matches_the_model():
    packaged, generated = _packaged(), config_json_schema()
    assert _property_paths(packaged) == _property_paths(generated), (
        "run: python -m mofsift.config > src/mofsift/schemas/config.schema.json"
    )
    assert packaged["required"] == generated["required"] == ["project", "generate"]


def test_packaged_schema_is_a_valid_schema_and_accepts_the_example(example_dict):
    schema = _packaged()
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(example_dict, schema)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(colour="blue"),
        lambda d: d["generate"].update(source="magic"),
        lambda d: d["describe"]["xtb"].update(max_atoms="many"),
        lambda d: d.pop("project"),
    ],
)
def test_packaged_schema_rejects_bad_configs(example_dict, mutate):
    mutate(example_dict)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(example_dict, _packaged())


# ------------------------------------------------------- bundle schemas
@pytest.mark.parametrize("name", ["candidate", "manifest"])
def test_packaged_bundle_schemas_are_current_and_valid(name):
    from mofsift.export import schemas
    from mofsift.export.validate import packaged_schema

    packaged = packaged_schema(name)
    assert packaged == schemas.SCHEMAS[f"{name}.schema.json"](), (
        "run: python -m mofsift.export.schemas src/mofsift/schemas"
    )
    jsonschema.Draft202012Validator.check_schema(packaged)


def test_candidate_schema_types_and_patterns():
    from mofsift.export.validate import packaged_schema

    validator = jsonschema.Draft202012Validator(packaged_schema("candidate"))
    good = {"id": "ms-0123456789abcdef", "xtb_gap": 1.5, "n_atoms": 53, "qc_pass": None, "my_column": "x"}
    assert not list(validator.iter_errors(good))
    for bad in (
        {"xtb_gap": 1.5},  # no id
        {"id": "../x"},
        {"id": "a", "n_atoms": 5.5},
        {"id": "a", "qc_pass": "yes"},
        {"id": "a", "qc_status": "fine"},
        {"id": "a", "cif_path": "../../etc/passwd"},
        {"id": "a", "cif_path": "structures/sub/a.cif"},
        {"id": "a", "my_column": [1, 2]},
    ):
        assert list(validator.iter_errors(bad)), bad


def test_the_quick_record_test_decides_like_the_schema():
    from mofsift.export.validate import packaged_schema, quick_record_test

    schema = packaged_schema("candidate")
    validator = jsonschema.Draft202012Validator(schema)
    quick = quick_record_test(schema)
    assert quick is not None  # the packaged schema stays within what the quick test understands
    records = [
        {"id": "ms-0123456789abcdef", "xtb_gap": 1.5, "n_atoms": 53, "qc_pass": None, "my_column": "x"},
        {"id": "a", "n_atoms": 53.0, "xtb_gap": 2, "qc_pass": True, "qc_status": "ok", "extra": 1.5},
        {"id": "a", "cif_path": "structures/a.cif", "generate_status": None, "extra": False},
        {"xtb_gap": 1.5},
        {"id": "../x"},
        {"id": 5},
        {"id": "a", "n_atoms": 5.5},
        {"id": "a", "n_atoms": True},
        {"id": "a", "xtb_gap": "1.5"},
        {"id": "a", "qc_pass": 1},
        {"id": "a", "qc_pass": "yes"},
        {"id": "a", "qc_status": "fine"},
        {"id": "a", "qc_status": 0},
        {"id": "a", "cif_path": "../../etc/passwd"},
        {"id": "a", "cif_path": "structures/sub/a.cif"},
        {"id": "a", "my_column": [1, 2]},
        {"id": "a", "my_column": {"a": 1}},
        ["id", "a"],
        "a",
    ]
    for record in records:
        assert quick(record) == (not list(validator.iter_errors(record))), record
    # a schema with anything else is left to the full validator
    assert quick_record_test({**schema, "minProperties": 2}) is None
    assert quick_record_test({"type": "object", "properties": {"a": {"minimum": 0}}}) is None


def test_schemas_can_be_written_to_a_folder(tmp_path):
    from mofsift.export import schemas

    written = schemas.write_schemas(tmp_path)
    assert sorted(p.name for p in written) == ["candidate.schema.json", "manifest.schema.json"]
    assert json.loads(written[0].read_text(encoding="utf-8"))["title"] == "mofsift candidate record"
