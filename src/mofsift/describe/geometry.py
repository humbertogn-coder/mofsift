"""Pore geometry from Zeo++: pore diameters, accessible surface area and accessible volume.

Two backends give the same quantities:

* ``pyzeo``: the Python bindings of Zeo++ (``pip install 'mofsift[zeo]'``);
* ``zeopp``: the ``network`` executable (``conda install -c conda-forge zeopp-lsmo``).

Both read a P1 copy of the structure written by ASE, so Zeo++ sees exactly the atoms that
the other stages see. Each structure is processed in its own process (see ``isolate``).
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

from mofsift.describe.isolate import WorkerError, run_worker

COLUMNS = {
    "lcd": "Float64",
    "pld": "Float64",
    "lcd_free_path": "Float64",
    "accessible_surface_m2_g": "Float64",
    "accessible_surface_m2_cm3": "Float64",
    "accessible_volume_fraction": "Float64",
    "accessible_volume_cm3_g": "Float64",
}
SURFACE_SAMPLES = 2000  # Monte Carlo samples per atom
VOLUME_SAMPLES = 50000  # Monte Carlo samples per cell


def settings(probe_radius: float, high_accuracy: str | bool) -> dict[str, Any]:
    """Everything that changes the numbers; part of the cache key."""
    return {
        "probe_radius": float(probe_radius),
        "high_accuracy": high_accuracy if high_accuracy else False,
        "surface_samples": SURFACE_SAMPLES,
        "volume_samples": VOLUME_SAMPLES,
    }


def parse_res(text: str) -> dict[str, float]:
    """``<name> Di Df Dif``: largest included sphere, largest free sphere, included along the free path."""
    tokens = text.split()
    if len(tokens) < 4:
        raise ValueError("unexpected Zeo++ pore-diameter output")
    di, df, dif = (float(t) for t in tokens[-3:])
    return {"lcd": round(di, 4), "pld": round(df, 4), "lcd_free_path": round(dif, 4)}


def _after(tokens: list[str], label: str) -> float:
    try:
        return float(tokens[tokens.index(label) + 1])
    except (ValueError, IndexError):
        raise ValueError(f"'{label}' not found in the Zeo++ output") from None


def parse_surface(text: str) -> dict[str, float]:
    tokens = text.split()
    return {
        "accessible_surface_m2_g": round(_after(tokens, "ASA_m^2/g:"), 3),
        "accessible_surface_m2_cm3": round(_after(tokens, "ASA_m^2/cm^3:"), 3),
    }


def parse_volume(text: str) -> dict[str, float]:
    tokens = text.split()
    return {
        "accessible_volume_fraction": round(_after(tokens, "AV_Volume_fraction:"), 5),
        "accessible_volume_cm3_g": round(_after(tokens, "AV_cm^3/g:"), 5),
    }


def compute_pyzeo(cif: Path, options: dict[str, Any], timeout: float | None = None) -> dict[str, float]:
    """All quantities with the pyzeo bindings. Raises ``WorkerError`` (with partial results)."""
    return run_worker("mofsift.describe.zeo_worker", {"cif": str(cif), **options}, timeout=timeout)


def compute_zeopp(
    cif: Path, options: dict[str, Any], executable: str = "network", timeout: float | None = None
) -> dict[str, float]:
    """All quantities with the Zeo++ ``network`` executable."""
    from ase.io import write

    from mofsift.io.structures import read_structure

    probe = f"{options['probe_radius']:g}"
    with tempfile.TemporaryDirectory(prefix="mofsift-zeopp-", ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        write(str(work / "s.cif"), read_structure(cif), format="cif")
        command = [executable]
        if options["high_accuracy"]:
            command += ["-ha", str(options["high_accuracy"])]
        command += ["-res", "s.res"]
        command += ["-sa", probe, probe, str(options["surface_samples"]), "s.sa"]
        command += ["-vol", probe, probe, str(options["volume_samples"]), "s.vol"]
        command.append("s.cif")
        try:
            proc = subprocess.run(
                command,
                cwd=work,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as err:
            raise WorkerError(f"stopped after {timeout / 60:g} min (time limit)") from err
        except OSError as err:
            raise WorkerError(f"cannot run {executable}: {err.strerror or err}") from err
        values: dict[str, float] = {}
        problems: list[str] = []
        for name, parser in (("s.res", parse_res), ("s.sa", parse_surface), ("s.vol", parse_volume)):
            try:
                values.update(parser((work / name).read_text(encoding="utf-8", errors="replace")))
            except (OSError, ValueError):
                problems.append(name.split(".")[1])
        if problems or proc.returncode != 0:
            lines = [ln.strip() for ln in (proc.stderr + "\n" + proc.stdout).splitlines() if ln.strip()]
            detail = f": {lines[-1][:200]}" if lines else ""
            what = f"no result for {', '.join(problems)}" if problems else f"exit code {proc.returncode}"
            raise WorkerError(f"Zeo++ did not finish ({what}){detail}", values)
        return values
