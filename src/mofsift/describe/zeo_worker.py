"""Worker process for the pyzeo backend: ``python -m mofsift.describe.zeo_worker in.json out.jsonl``."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path


def main(argv: list[str]) -> int:
    from ase.io import write

    from mofsift.describe.geometry import parse_res, parse_surface, parse_volume
    from mofsift.describe.isolate import worker_io
    from mofsift.io.structures import read_structure

    payload, emit = worker_io(argv)
    try:
        from pyzeo.area_volume import surface_area, volume
        from pyzeo.high_accuracy import high_accuracy_atomnet
        from pyzeo.netstorage import AtomNetwork
    except ImportError as err:
        emit({"error": f"pyzeo cannot be imported: {err}"})
        return 0

    probe = float(payload["probe_radius"])
    with tempfile.TemporaryDirectory(prefix="mofsift-zeo-") as tmp:
        cif = Path(tmp) / "s.cif"
        write(str(cif), read_structure(payload["cif"]), format="cif")

        def network():
            net = AtomNetwork.read_from_CIF(str(cif))
            if payload["high_accuracy"]:
                high_accuracy_atomnet(net, str(payload["high_accuracy"]))
            return net

        # A fresh network per quantity: Zeo++ modifies the network while it works on it.
        res_file = Path(tmp) / "s.res"
        network().calculate_free_sphere_parameters(str(res_file))
        emit(parse_res(res_file.read_text(encoding="utf-8", errors="replace")))
        out = surface_area(network(), probe, probe, int(payload["surface_samples"]))
        emit(parse_surface(out.decode("utf-8", errors="replace")))
        out = volume(network(), probe, probe, int(payload["volume_samples"]))
        emit(parse_volume(out.decode("utf-8", errors="replace")))
    emit({"done": True})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
