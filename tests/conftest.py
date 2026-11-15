from __future__ import annotations

import copy

import pytest
import yaml

from mofsift.templates import EXAMPLE_CONFIG


@pytest.fixture
def example_dict() -> dict:
    """The example config as a fresh, mutable mapping."""
    return copy.deepcopy(yaml.safe_load(EXAMPLE_CONFIG))


@pytest.fixture
def minimal_dict() -> dict:
    return {
        "project": "t",
        "generate": {"source": "pormake", "topologies": ["pcu"], "nodes": ["N16"], "edges": ["E14"]},
    }
