from __future__ import annotations

from mofsift.generate.enumerate import BlockInfo, TopologyInfo, enumerate_combinations
from mofsift.ids import record_id

ONE = TopologyInfo("one", node_cn=(4,), n_nodes=(1,), n_edges=(2,))
TWO = TopologyInfo("two", node_cn=(3, 4), n_nodes=(4, 3), n_edges=(12,))
SIX = TopologyInfo("six", node_cn=(6,), n_nodes=(1,), n_edges=(3,))
A4, B4 = BlockInfo("A4", 4, {"Zn": 2, "O": 8, "C": 4}), BlockInfo("B4", 4, {"Cu": 2, "O": 8, "C": 4})
T3 = BlockInfo("T3", 3, {"C": 6, "H": 3})
L, M = BlockInfo("L", 2, {"C": 6, "H": 4}), BlockInfo("M", 2, {"C": 12, "H": 8})


def test_a_node_only_goes_on_a_vertex_with_its_coordination_number():
    combos, notes = enumerate_combinations([ONE, TWO], [A4, B4, T3], [L])
    assert [c.name for c in combos] == ["one__A4__L", "one__B4__L", "two__T3+A4__L", "two__T3+B4__L"]
    assert notes == []
    assert all(len(c.nodes) == len(t.node_cn) for c, t in zip(combos, [ONE, ONE, TWO, TWO], strict=True))


def test_order_follows_the_config_and_ids_do_not_depend_on_it():
    forward, _ = enumerate_combinations([ONE, TWO], [A4, B4, T3], [L, M])
    backward, _ = enumerate_combinations([TWO, ONE], [T3, B4, A4], [M, L])
    assert [c.name for c in forward][:4] == ["one__A4__L", "one__A4__M", "one__B4__L", "one__B4__M"]
    assert backward[0].name == "two__T3+B4__M"
    assert {c.name: c.record_id for c in forward} == {c.name: c.record_id for c in backward}
    assert len({c.record_id for c in forward}) == len(forward) == 8


def test_ids_match_the_public_id_function():
    combos, _ = enumerate_combinations([ONE], [A4], [L])
    assert combos[0].record_id == record_id("one", ["A4"], ["L"])


def test_expected_composition_is_the_sum_of_the_blocks():
    combos, _ = enumerate_combinations([TWO], [T3, A4], [L])
    c = combos[0]
    # 4 x T3 + 3 x A4 + 12 x L
    assert c.expected_counts == {"C": 4 * 6 + 3 * 4 + 12 * 6, "H": 4 * 3 + 12 * 4, "Zn": 6, "O": 24}
    assert c.expected_n_atoms == sum(c.expected_counts.values())


def test_without_edges_the_nodes_are_connected_directly():
    combos, _ = enumerate_combinations([TWO], [T3, A4], [])
    assert [c.name for c in combos] == ["two__T3+A4"] and combos[0].edges == ()
    assert combos[0].expected_counts == {"C": 36, "H": 12, "Zn": 6, "O": 24}
    with_edge, _ = enumerate_combinations([TWO], [T3, A4], [L])
    assert combos[0].record_id != with_edge[0].record_id


def test_topologies_and_nodes_that_cannot_be_used_are_reported():
    combos, notes = enumerate_combinations([ONE, SIX], [A4, T3], [L])
    assert [c.topology for c in combos] == ["one"]
    assert any("topology six: no configured node with 6 connection points" in n for n in notes)
    assert any(n == "node(s) not used by any topology: T3" for n in notes)


def test_a_user_supplied_block_changes_the_id_when_its_content_changes():
    v1 = BlockInfo("L", 2, {"C": 6, "H": 4}, content_hash="aaaa")
    v2 = BlockInfo("L", 2, {"C": 6, "H": 4}, content_hash="bbbb")
    ids = {enumerate_combinations([ONE], [A4], [e])[0][0].record_id for e in (L, v1, v2)}
    assert len(ids) == 3
