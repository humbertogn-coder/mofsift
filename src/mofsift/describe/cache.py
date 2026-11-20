"""Per-structure result files, so that slow calculations are never repeated.

Each geometry or xTB result is stored in ``<workdir>/describe/<substage>/<id>.json`` with a
key made from the settings and the content of the structure file. A result is reused only
when the key matches. Because the files are independent, blocks of a table can be computed
by separate jobs on a cluster and assembled later, on any machine, without the tool.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

CACHE_DIR = "describe"


def cache_path(workdir: Path, substage: str, record_id: str) -> Path:
    return Path(workdir) / CACHE_DIR / substage / f"{record_id}.json"


def cache_key(substage: str, settings: dict[str, Any], structure_sha256: str) -> str:
    text = json.dumps(
        {"substage": substage, "settings": settings, "structure": structure_sha256},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load(path: Path, key: str) -> dict[str, Any] | None:
    """The stored result when it exists and was made with the same settings and structure."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("key") != key or data.get("status") not in ("ok", "failed"):
        return None
    return data


def store(path: Path, key: str, status: str, values: dict[str, Any], **extra: Any) -> dict[str, Any]:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"key": key, "status": status, "values": values, **extra}
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, sort_keys=True, indent=1)
        fh.write("\n")
    os.replace(tmp, path)
    return data
