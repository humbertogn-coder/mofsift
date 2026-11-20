"""Pieces of the describe stage that need no optional tool: process isolation, cache, parsers."""

from __future__ import annotations

import os
import sys
import textwrap

import numpy as np
import pytest

from mofsift.describe import cache, geometry, xtb
from mofsift.describe.isolate import WorkerError, run_worker


# ---------------------------------------------------------------- isolation
@pytest.fixture
def worker(tmp_path, monkeypatch):
    """Write a worker module and make it importable by the child process."""

    def make(body: str) -> str:
        (tmp_path / "fake_worker.py").write_text(
            "import sys, os, time\nfrom mofsift.describe.isolate import worker_io\n"
            "payload, emit = worker_io(sys.argv)\n" + textwrap.dedent(body),
            encoding="utf-8",
        )
        return "fake_worker"

    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(tmp_path), os.environ.get("PYTHONPATH", "")]))
    return make


def test_worker_results_are_merged(worker):
    module = worker('emit({"a": payload["x"] + 1})\nemit({"b": 2})\nemit({"done": True})\n')
    assert run_worker(module, {"x": 1}) == {"a": 2, "b": 2}


def test_a_crash_costs_one_calculation_and_keeps_what_was_written(worker):
    module = worker('emit({"a": 1})\nprint("Unable to add edge")\nprint("Exiting...")\nos._exit(3)\n')
    with pytest.raises(WorkerError) as err:
        run_worker(module, {})
    assert err.value.partial == {"a": 1}
    assert err.value.reason == "the calculation stopped (exit code 3): Unable to add edge"


def test_worker_error_message_missing_done_and_timeout(worker):
    with pytest.raises(WorkerError, match=r"^SCF not converged$") as err:
        run_worker(worker('emit({"a": 1})\nemit({"error": "SCF not converged"})\n'), {})
    assert err.value.partial == {"a": 1}
    with pytest.raises(WorkerError, match="the calculation stopped"):
        run_worker(worker('emit({"a": 1})\n'), {})  # ended without saying it was done
    with pytest.raises(WorkerError, match="time limit") as err:
        run_worker(worker('emit({"a": 1})\ntime.sleep(30)\n'), {}, timeout=1.5)
    assert err.value.partial == {"a": 1}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_a_killed_worker_is_reported(worker):
    module = worker("import signal\nos.kill(os.getpid(), signal.SIGKILL)\n")
    with pytest.raises(WorkerError, match="killed by signal SIGKILL"):
        run_worker(module, {})


# -------------------------------------------------------------------- cache
def test_cache_round_trip_and_invalidation(tmp_path):
    path = cache.cache_path(tmp_path, "xtb", "ms-1")
    assert path == tmp_path / "describe" / "xtb" / "ms-1.json"
    key = cache.cache_key("xtb", {"method": "GFN1-xTB"}, "abc")
    assert cache.load(path, key) is None
    cache.store(path, key, "ok", {"xtb_gap": 1.5}, seconds=2.0)
    assert cache.load(path, key)["values"] == {"xtb_gap": 1.5}
    # other settings, another structure or another sub-stage: not the same result
    for other in (
        cache.cache_key("xtb", {"method": "GFN2-xTB"}, "abc"),
        cache.cache_key("xtb", {"method": "GFN1-xTB"}, "abd"),
        cache.cache_key("geometry", {"method": "GFN1-xTB"}, "abc"),
    ):
        assert other != key and cache.load(path, other) is None
    path.write_text("{broken", encoding="utf-8")
    assert cache.load(path, key) is None
    assert not list(path.parent.glob("*.tmp"))


# ------------------------------------------------------------ Zeo++ output
RES = "s.res    13.25160 9.95873  13.24943\n"
SA = (
    "@ s.sa Unitcell_volume: 2182.33   Density: 0.620099   ASA_A^2: 473.786 ASA_m^2/cm^3: 2171.01 "
    "ASA_m^2/g: 3501.07 NASA_A^2: 0 NASA_m^2/cm^3: 0 NASA_m^2/g: 0\nNumber_of_channels: 1\n"
)
VOL = (
    "@ s.vol Unitcell_volume: 2182.33   Density: 0.620099   AV_A^3: 744.284 AV_Volume_fraction: 0.34105 "
    "AV_cm^3/g: 0.549993 NAV_A^3: 0 NAV_Volume_fraction: 0 NAV_cm^3/g: 0\n"
)


def test_zeopp_output_parsers():
    assert geometry.parse_res(RES) == {"lcd": 13.2516, "pld": 9.9587, "lcd_free_path": 13.2494}
    assert geometry.parse_surface(SA) == {
        "accessible_surface_m2_g": 3501.07,
        "accessible_surface_m2_cm3": 2171.01,
    }
    assert geometry.parse_volume(VOL) == {
        "accessible_volume_fraction": 0.34105,
        "accessible_volume_cm3_g": 0.54999,
    }
    for parser in (geometry.parse_res, geometry.parse_surface, geometry.parse_volume):
        with pytest.raises(ValueError):
            parser("nothing useful")
    parsed = set(geometry.parse_res(RES) | geometry.parse_surface(SA) | geometry.parse_volume(VOL))
    assert parsed == set(geometry.COLUMNS) - {"zeo_high_accuracy"}


def test_fallback_settings_are_tried_in_order_and_the_one_that_worked_is_recorded():
    def backend(fails_with):
        seen = []

        def compute(cif, options, timeout):
            seen.append(options["high_accuracy"])
            if options["high_accuracy"] in fails_with:
                raise WorkerError(f"stopped with {options['high_accuracy']}", {"lcd": 1.0})
            return {"lcd": 5.0}

        return geometry.with_fallback(compute), seen

    options = geometry.settings(1.86, "OCC", ["DEF", False])
    assert options["fallback"] == ["DEF", False]
    compute, seen = backend(fails_with=())
    assert compute(None, options, None) == {"lcd": 5.0, "zeo_high_accuracy": "OCC"} and seen == ["OCC"]
    compute, seen = backend(fails_with=("OCC",))
    assert compute(None, options, None)["zeo_high_accuracy"] == "DEF" and seen == ["OCC", "DEF"]
    compute, seen = backend(fails_with=("OCC", "DEF"))
    assert compute(None, options, None)["zeo_high_accuracy"] == "off" and seen == ["OCC", "DEF", False]
    compute, seen = backend(fails_with=("OCC", "DEF", False))
    with pytest.raises(WorkerError) as err:
        compute(None, options, None)
    assert err.value.reason == "stopped with OCC; no result with DEF, off either"
    assert err.value.partial == {"lcd": 1.0, "zeo_high_accuracy": "OCC"}
    # no fallback configured: one attempt, the plain reason
    compute, seen = backend(fails_with=("OCC",))
    with pytest.raises(WorkerError, match=r"^stopped with OCC$"):
        compute(None, geometry.settings(1.86, "OCC", []), None)
    assert seen == ["OCC"]
    # the first choice is not tried twice, and the fallback is part of what identifies a result
    assert geometry.settings(1.86, "DEF", ["DEF", False])["fallback"] == [False]
    assert geometry.settings(1.86, False, [False, "OCC"])["fallback"] == ["OCC"]
    assert geometry.settings(1.86, "OCC", []) != geometry.settings(1.86, "OCC", ["DEF"])


def _fake_network(folder, body: str):
    script = folder / "network"
    script.write_text(
        f"#!{sys.executable}\nimport sys\nargs = sys.argv[1:]\n" + textwrap.dedent(body), encoding="utf-8"
    )
    script.chmod(0o755)
    return str(script)


@pytest.mark.skipif(sys.platform == "win32", reason="uses a POSIX executable script")
def test_zeopp_binary_backend_with_a_stand_in_executable(tmp_path):
    from conftest import methane, write_cif

    cif = write_cif(tmp_path / "s" / "m.cif", methane())
    ok = _fake_network(
        tmp_path,
        f"""
        open("args.txt", "w").write(" ".join(args))
        open("s.res", "w").write({RES!r}); open("s.sa", "w").write({SA!r}); open("s.vol", "w").write({VOL!r})
        import shutil; shutil.copy("args.txt", {str(tmp_path / "seen.txt")!r})
        """,
    )
    values = geometry.compute_zeopp(cif, geometry.settings(1.86, "DEF"), executable=ok)
    assert values["lcd"] == 13.2516 and values["accessible_volume_fraction"] == 0.34105
    seen = (tmp_path / "seen.txt").read_text(encoding="utf-8")
    assert seen == "-ha DEF -res s.res -sa 1.86 1.86 2000 s.sa -vol 1.86 1.86 50000 s.vol s.cif"
    geometry.compute_zeopp(cif, geometry.settings(1.2, False), executable=ok)
    assert (tmp_path / "seen.txt").read_text(encoding="utf-8").startswith("-res s.res -sa 1.2 1.2 2000")
    # a run that stops half-way keeps what it produced and says what is missing
    half = _fake_network(
        tmp_path, f'open("s.res", "w").write({RES!r})\nprint("Error: bad cell")\nsys.exit(1)\n'
    )
    with pytest.raises(WorkerError) as err:
        geometry.compute_zeopp(cif, geometry.settings(1.86, "OCC"), executable=half)
    assert err.value.partial == {"lcd": 13.2516, "pld": 9.9587, "lcd_free_path": 13.2494}
    assert "no result for sa, vol" in err.value.reason and "Error: bad cell" in err.value.reason
    with pytest.raises(WorkerError, match="cannot run"):
        geometry.compute_zeopp(cif, geometry.settings(1.86, "OCC"), executable=str(tmp_path / "nowhere"))


# ---------------------------------------------------------------------- xtb
def test_unpaired_electrons_rule():
    mof5 = ["Zn"] * 4 + ["O"] * 13 + ["C"] * 24 + ["H"] * 12
    z = {"Zn": 30, "O": 8, "C": 6, "H": 1, "Cu": 29, "Co": 27, "N": 7}
    assert xtb.unpaired_electrons(mof5, [z[s] for s in mof5], "high_spin") == 0
    paddlewheel = ["Cu"] * 2 + ["O"] * 8 + ["C"] * 4
    assert xtb.unpaired_electrons(paddlewheel, [z[s] for s in paddlewheel], "high_spin") == 2
    assert xtb.unpaired_electrons(paddlewheel, [z[s] for s in paddlewheel], "low_spin") == 0
    # the count is made consistent with the parity of the electron count
    assert xtb.unpaired_electrons(["Co", "H"], [27, 1], "high_spin") == 2  # 28 electrons: 3 -> 2
    assert xtb.unpaired_electrons(["N", "O"], [7, 8], "high_spin") == 1  # 15 electrons: 0 -> 1
    assert xtb.unpaired_electrons(["N", "O"], [7, 8], "low_spin") == 1
    with pytest.raises(ValueError):
        xtb.unpaired_electrons(["H"], [1], "sideways")


def test_frontier_levels():
    hartree = 1 / xtb.HARTREE_TO_EV
    energies = np.array([-12.0, -10.0, -7.0, -1.0]) * hartree
    gap, homo, lumo = xtb.frontier_levels(energies, [2, 2, 0, 0])
    assert (round(gap, 6), round(homo, 6), round(lumo, 6)) == (3.0, -10.0, -7.0)
    # with unpaired electrons the lowest level that is not doubly occupied is singly occupied
    gap, homo, lumo = xtb.frontier_levels(energies, [2, 1, 1, 0])
    assert (round(gap, 6), round(homo, 6), round(lumo, 6)) == (2.0, -12.0, -10.0)
    with pytest.raises(ValueError):
        xtb.frontier_levels(energies, [2, 2, 2, 2])
