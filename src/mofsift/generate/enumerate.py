"""Enumeration of the candidates declared by a config. Pure Python: no PORMAKE needed.

For every topology, each node type receives every configured node that has the right
number of connection points, and every edge type receives the same edge. A node is never
placed on a vertex with a different coordination number: PORMAKE would build such a
structure without complaint, and it would be wrong.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from mofsift.chem import is_metal
from mofsift.ids import record_id, record_name


@dataclass(frozen=True)
class TopologyInfo:
    name: str
    node_cn: tuple[int, ...]  # coordination number of each node type
    n_nodes: tuple[int, ...]  # number of vertices of each node type in the cell
    n_edges: tuple[int, ...]  # number of edges of each edge type in the cell


@dataclass(frozen=True)
class BlockInfo:
    name: str
    n_connection_points: int
    counts: Mapping[str, int] = field(default_factory=dict)  # composition without connection points
    content_hash: str | None = None  # set only for user-supplied blocks
    smiles: str | None = None  # the linker this block stands for, when known
    metal: bool | None = None  # whether the block contains a metal; None = decide from counts

    @property
    def has_metal(self) -> bool:
        if self.metal is not None:
            return self.metal
        return any(is_metal(el) for el, n in self.counts.items() if n)


@dataclass(frozen=True)
class Combination:
    topology: str
    nodes: tuple[str, ...]  # one block per node type
    edges: tuple[str, ...]  # one block per edge type; empty = nodes connected directly
    record_id: str
    name: str
    expected_counts: Mapping[str, int]

    @property
    def expected_n_atoms(self) -> int:
        return sum(self.expected_counts.values())


def _expected_counts(
    topo: TopologyInfo, nodes: Sequence[BlockInfo], edge: BlockInfo | None
) -> dict[str, int]:
    total: dict[str, int] = {}
    for block, n in zip(nodes, topo.n_nodes, strict=True):
        for el, c in block.counts.items():
            total[el] = total.get(el, 0) + c * n
    if edge is not None:
        for el, c in edge.counts.items():
            total[el] = total.get(el, 0) + c * sum(topo.n_edges)
    return total


def _options(blocks: Sequence[BlockInfo]) -> dict[str, dict[str, str]] | None:
    custom = {b.name: b.content_hash for b in blocks if b.content_hash}
    return {"custom_blocks": custom} if custom else None


def enumerate_combinations(
    topologies: Sequence[TopologyInfo],
    nodes: Sequence[BlockInfo],
    edges: Sequence[BlockInfo],
) -> tuple[list[Combination], list[str]]:
    """Return the candidates in a deterministic order, plus notes about anything left unused.

    The order follows the config: topologies, then nodes (per node type), then edges.
    """
    combos: list[Combination] = []
    notes: list[str] = []
    used: set[str] = set()
    edge_choices: list[BlockInfo | None] = list(edges) or [None]
    for topo in topologies:
        per_type = [[b for b in nodes if b.n_connection_points == cn] for cn in topo.node_cn]
        missing = sorted({cn for cn, fit in zip(topo.node_cn, per_type, strict=True) if not fit})
        if missing:
            have = ", ".join(f"{b.name} has {b.n_connection_points}" for b in nodes)
            notes.append(
                f"topology {topo.name}: no configured node with {', '.join(map(str, missing))} "
                f"connection points ({have}); no candidates from this topology"
            )
            continue
        for assignment in itertools.product(*per_type):
            for edge in edge_choices:
                node_names = tuple(b.name for b in assignment)
                edge_names = (edge.name,) * len(topo.n_edges) if edge is not None else ()
                blocks = [*assignment, *([edge] if edge is not None else [])]
                combos.append(
                    Combination(
                        topology=topo.name,
                        nodes=node_names,
                        edges=edge_names,
                        record_id=record_id(topo.name, node_names, edge_names, _options(blocks)),
                        name=record_name(topo.name, node_names, tuple(dict.fromkeys(edge_names))),
                        expected_counts=_expected_counts(topo, assignment, edge),
                    )
                )
                used.update(node_names)
    unused = [b.name for b in nodes if b.name not in used]
    if unused:
        notes.append(f"node(s) not used by any topology: {', '.join(unused)}")
    return combos, notes
