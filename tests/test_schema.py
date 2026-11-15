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
