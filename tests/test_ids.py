from __future__ import annotations

import re

import pytest

from mofsift.ids import file_record_id, record_id, record_name


def test_record_id_is_stable_across_runs_and_releases():
    # Frozen value: changing the ID recipe breaks every published bundle, so it must be deliberate.
    assert record_id("pcu", ["N16"], ["E14"]) == "ms-678e931c571c8679"


def test_record_id_format_and_determinism():
    a = record_id("pcu", ["N16"], ["E14"])
    assert re.fullmatch(r"ms-[0-9a-f]{16}", a)
    assert a == record_id("pcu", ("N16",), ("E14",))  # list or tuple


def test_record_id_distinguishes_what_matters():
    base = record_id("pcu", ["N16"], ["E14"])
    assert record_id("dia", ["N16"], ["E14"]) != base
    assert record_id("pcu", ["N409"], ["E14"]) != base
    assert record_id("pcu", ["N16"], ["E34"]) != base
    assert record_id("pcu", ["N16"], ["E14"], {"functional_group": "NH2"}) != base
    # block order maps to node/edge types of the topology, so it matters
    assert record_id("x", ["N1", "N2"], ["E1"]) != record_id("x", ["N2", "N1"], ["E1"])


def test_option_key_order_does_not_matter():
    assert record_id("pcu", ["N16"], ["E14"], {"a": 1, "b": 2}) == record_id(
        "pcu", ["N16"], ["E14"], {"b": 2, "a": 1}
    )
    assert record_id("pcu", ["N16"], ["E14"], {}) == record_id("pcu", ["N16"], ["E14"])


@pytest.mark.parametrize("args", [("", ["N16"], ["E14"]), ("pcu", [], ["E14"]), ("pcu", ["N16"], [])])
def test_record_id_requires_all_parts(args):
    with pytest.raises(ValueError):
        record_id(*args)


def test_record_name():
    assert record_name("pcu", ["N16"], ["E14"]) == "pcu__N16__E14"
    assert record_name("x", ["N1", "N2"], ["E1", "E2"]) == "x__N1+N2__E1+E2"


def test_file_id_ignores_line_endings_and_trailing_whitespace():
    unix = "data_x\n_cell_length_a 10\n"
    windows = "data_x\r\n_cell_length_a 10  \r\n\r\n"
    assert file_record_id(unix) == file_record_id(windows) == file_record_id(unix.encode())
    assert re.fullmatch(r"msf-[0-9a-f]{16}", file_record_id(unix))
    assert file_record_id("data_x\n_cell_length_a 11\n") != file_record_id(unix)
    with pytest.raises(ValueError):
        file_record_id("  \n\n")
