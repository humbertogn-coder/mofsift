"""Run one calculation in a separate Python process.

Zeo++ can end the whole process on a structure it cannot handle, and a large xTB
calculation can be killed for lack of memory. Running each structure in its own process
means such a failure costs one row, with a recorded reason, not the run.

A worker is a module run as ``python -m <module> <input.json> <output.jsonl>``. It writes one
JSON object per line as results become available and a final ``{"done": true}``. Whatever was
written before a crash is still returned.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


class WorkerError(RuntimeError):
    """The worker did not finish. ``partial`` holds the results it wrote before stopping."""

    def __init__(self, reason: str, partial: dict[str, Any] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.partial = partial or {}


_PROBLEM_RE = re.compile(r"(?i)\b(error|unable|cannot|could not|failed|exception|abort)")


def _last_line(*texts: str) -> str | None:
    """The line that best explains a crash: the last one that names a problem, else the last one."""
    lines = [" ".join(ln.split()) for text in texts for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return None
    telling = [ln for ln in lines if _PROBLEM_RE.search(ln)]
    return (telling[-1] if telling else lines[-1])[:200]


def _read_results(path: Path) -> tuple[dict[str, Any], bool]:
    merged: dict[str, Any] = {}
    done = False
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue  # a line cut short by a crash
            if item.get("done") is True:
                done = True
            else:
                merged.update(item)
    return merged, done


def run_worker(module: str, payload: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
    """Run ``module`` on ``payload`` in a fresh process and return the merged results.

    Raises :class:`WorkerError` when the process crashes, reports an error or runs longer
    than ``timeout`` seconds.
    """
    with tempfile.TemporaryDirectory(prefix="mofsift-worker-", ignore_cleanup_errors=True) as tmp:
        source, target = Path(tmp) / "input.json", Path(tmp) / "output.jsonl"
        source.write_text(json.dumps(payload), encoding="utf-8")
        # Unbuffered output: a worker that dies with os._exit or a signal still leaves
        # its last messages in the pipe, so the crash reason can be reported.
        env = {**os.environ, "PYTHONWARNINGS": "ignore", "PYTHONUNBUFFERED": "1"}
        try:
            proc = subprocess.run(
                [sys.executable, "-m", module, str(source), str(target)],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                cwd=tmp,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as err:
            partial, _ = _read_results(target)
            raise WorkerError(f"stopped after {timeout / 60:g} min (time limit)", partial) from err
        results, done = _read_results(target)
        error = results.pop("error", None)
        if error:
            raise WorkerError(str(error), results)
        if proc.returncode != 0 or not done:
            if proc.returncode < 0:
                try:
                    what = f"killed by signal {signal.Signals(-proc.returncode).name}"
                except ValueError:
                    what = f"killed by signal {-proc.returncode}"
            else:
                what = f"exit code {proc.returncode}"
            detail = _last_line(proc.stdout, proc.stderr)
            raise WorkerError(
                f"the calculation stopped ({what})" + (f": {detail}" if detail else ""), results
            )
        return results


def worker_io(argv: list[str]) -> tuple[dict[str, Any], Any]:
    """For worker modules: read the input and return (payload, emit)."""
    payload = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    target = Path(argv[2])

    def emit(item: dict[str, Any]) -> None:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(item) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    return payload, emit
