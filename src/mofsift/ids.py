"""Stable record identifiers.

A record ID depends only on what defines the candidate (topology, building blocks and
any options that change the structure), never on the order in which candidates were
enumerated or on the machine. The same config therefore always gives the same IDs.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

ID_PREFIX = "ms"
FILE_ID_PREFIX = "msf"
_HEX = 16  # 64 bits: collision probability ~3e-8 for one million records


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()[:_HEX]


def record_id(
    topology: str,
    nodes: Sequence[str],
    edges: Sequence[str],
    options: Mapping[str, Any] | None = None,
) -> str:
    """ID of an assembled candidate.

    ``nodes`` and ``edges`` keep their order because it maps blocks to the node and edge
    types of the topology. ``edges`` is empty when the nodes are connected directly.
    ``options`` holds anything else that changes the structure; its key order does not
    matter.
    """
    if not topology or not nodes:
        raise ValueError("a topology and at least one node are required for a record ID")
    payload = {
        "topology": topology,
        "nodes": list(nodes),
        "edges": list(edges),
        "options": dict(options or {}),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"{ID_PREFIX}-{_digest(canonical.encode('utf-8'))}"


def record_name(topology: str, nodes: Sequence[str], edges: Sequence[str]) -> str:
    """Human-readable label, e.g. ``pcu__N16__E14``. Not guaranteed unique; use the ID as key."""
    label = f"{topology}__{'+'.join(nodes)}"
    return f"{label}__{'+'.join(edges)}" if edges else label


def file_record_id(content: bytes | str) -> str:
    """ID of a structure that entered the pipeline as a file (source: cifs).

    Line endings and trailing whitespace are normalised so that the same CIF gives the
    same ID on Windows and Linux.
    """
    text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    normalised = "\n".join(lines).strip("\n")
    if not normalised:
        raise ValueError("cannot build an ID from an empty file")
    return f"{FILE_ID_PREFIX}-{_digest(normalised.encode('utf-8'))}"
