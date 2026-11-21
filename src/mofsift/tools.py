"""Detection of the optional tools behind each stage.

Every external dependency sits behind a small adapter with ``available()`` and
``version()``. Stage-specific adapters (added with each stage) subclass these and
implement ``run``. A missing optional tool is never an error here: it is reported, and
the stage that needs it is skipped with a recorded reason.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from mofsift.config import ToolsConfig

_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?(?:[\w.\-]*)?)")


@dataclass(frozen=True)
class ToolStatus:
    name: str
    kind: str  # "python" or "binary"
    tier: str  # "core" or the name of the extra
    used_by: str
    available: bool
    version: str | None
    location: str | None
    detail: str | None
    hint: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class Tool:
    """Base adapter. Subclasses implement detection; stage adapters add ``run``."""

    kind = "tool"

    def __init__(self, name: str, tier: str, used_by: str, hint: str):
        self.name, self.tier, self.used_by, self.hint = name, tier, used_by, hint

    def available(self) -> bool:
        raise NotImplementedError

    def version(self) -> str | None:
        raise NotImplementedError

    def location(self) -> str | None:
        return None

    def check(self) -> tuple[bool, str | None]:
        """Thorough check used by ``mofsift doctor``. Returns (works, detail)."""
        return self.available(), None

    def run(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(f"{self.name}: no run() on the detection adapter")

    def status(self) -> ToolStatus:
        ok, detail = self.check()
        return ToolStatus(
            name=self.name,
            kind=self.kind,
            tier=self.tier,
            used_by=self.used_by,
            available=ok,
            version=self.version() if ok else None,
            location=self.location() if ok else None,
            detail=detail,
            hint=self.hint,
        )


class PythonTool(Tool):
    """A Python package, detected without importing it; ``check`` does import it."""

    kind = "python"

    def __init__(self, name: str, module: str, dist: str, tier: str, used_by: str, hint: str):
        super().__init__(name, tier, used_by, hint)
        self.module, self.dist = module, dist

    def available(self) -> bool:
        try:
            return importlib.util.find_spec(self.module) is not None
        except (ImportError, ValueError):
            return False

    def version(self) -> str | None:
        try:
            return importlib.metadata.version(self.dist)
        except importlib.metadata.PackageNotFoundError:
            return None

    def check(self) -> tuple[bool, str | None]:
        if not self.available():
            return False, "not installed"
        try:
            importlib.import_module(self.module)
        except Exception as err:  # a broken compiled extension can raise anything
            return False, f"installed but import failed: {type(err).__name__}: {err}"
        return True, None


class PormakeTool(PythonTool):
    """PORMAKE, imported through the adapter so that the check leaves no ``runtime.log`` behind."""

    def check(self) -> tuple[bool, str | None]:
        if not self.available():
            return False, "not installed"
        from mofsift.generate.pormake_adapter import load_pormake

        try:
            load_pormake()
        except Exception as err:
            return False, f"installed but import failed: {type(err).__name__}: {err}"
        return True, None


class BinaryTool(Tool):
    """An executable, looked up at a configured path first and then on PATH."""

    kind = "binary"

    def __init__(
        self,
        name: str,
        executables: tuple[str, ...],
        tier: str,
        used_by: str,
        hint: str,
        configured: Path | None = None,
        version_args: tuple[str, ...] | None = ("--version",),
    ):
        super().__init__(name, tier, used_by, hint)
        self.executables, self.configured, self.version_args = executables, configured, version_args

    def location(self) -> str | None:
        if self.configured is not None:
            found = shutil.which(str(self.configured))
            return found
        for exe in self.executables:
            found = shutil.which(exe)
            if found:
                return found
        return None

    def available(self) -> bool:
        return self.location() is not None

    def check(self) -> tuple[bool, str | None]:
        if self.available():
            return True, None
        if self.configured is not None:
            return False, f"configured path is not an executable: {self.configured}"
        return False, "not found on PATH"

    def version(self) -> str | None:
        exe = self.location()
        if exe is None or self.version_args is None:
            return None
        try:
            out = subprocess.run(
                [exe, *self.version_args], capture_output=True, text=True, timeout=15, check=False
            )
        except (OSError, subprocess.SubprocessError):
            return None
        m = _VERSION_RE.search(out.stdout + "\n" + out.stderr)
        return m.group(1) if m else None


def default_tools(cfg: ToolsConfig | None = None) -> list[Tool]:
    """The tools mofsift knows about, core first."""
    cfg = cfg or ToolsConfig()
    core = "pip install mofsift"
    return [
        PythonTool("numpy", "numpy", "numpy", "core", "all stages", core),
        PythonTool("pandas", "pandas", "pandas", "core", "all stages", core),
        PythonTool("ase", "ase", "ase", "core", "structure I/O, qc", core),
        PythonTool("pymatgen", "pymatgen", "pymatgen", "core", "structure I/O", core),
        PythonTool("rdkit", "rdkit", "rdkit", "core", "score (linker)", core),
        PormakeTool(
            "pormake", "pormake", "pormake", "generate", "generate", "pip install 'mofsift[generate]'"
        ),
        PythonTool(
            "pyzeo",
            "pyzeo",
            "pyzeo",
            "zeo",
            "describe.geometry",
            "pip install 'mofsift[zeo]' (Linux/macOS; on Windows use WSL or a cluster)",
        ),
        PythonTool(
            "tblite",
            "tblite",
            "tblite",
            "xtb",
            "describe.xtb",
            "pip install 'mofsift[xtb]' (Linux/macOS; on Windows use WSL or a cluster)",
        ),
        PythonTool(
            "scikit-learn",
            "sklearn",
            "scikit-learn",
            "ml",
            "surrogate (deferred)",
            "pip install 'mofsift[ml]'",
        ),
        PythonTool(
            "pyarrow",
            "pyarrow",
            "pyarrow",
            "parquet",
            "export (table_format: parquet)",
            "pip install 'mofsift[parquet]'",
        ),
        BinaryTool(
            "zeo++ (network)",
            ("network",),
            "zeo",
            "describe.geometry (tool: zeopp)",
            "conda install -c conda-forge zeopp-lsmo",
            configured=cfg.zeopp,
            version_args=None,  # the binary has no version flag
        ),
        BinaryTool(
            "xtb (binary)",
            ("xtb",),
            "xtb",
            "reserved (not used yet)",
            "conda install -c conda-forge xtb",
            configured=cfg.xtb,
        ),
    ]


def collect_status(tools: list[Tool] | None = None) -> list[ToolStatus]:
    return [t.status() for t in (tools if tools is not None else default_tools())]
