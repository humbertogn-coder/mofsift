"""The generate stage: create the candidate table and the structure files.

Two sources:

* ``pormake``: assemble every allowed (topology, nodes, edge) combination.
* ``cifs``: take existing CIF files as they are.

Either way the result is one row per candidate, a CIF in ``<workdir>/structures/<id>.cif``
for every row whose status is ``ok``, and the same columns, so the later stages do not
care where a structure came from.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from mofsift.chem import hill_formula
from mofsift.config import Config
from mofsift.generate.enumerate import BlockInfo, Combination, enumerate_combinations
from mofsift.generate.linkers import LinkerBlock, LinkerError, build_linkers, collect_smiles
from mofsift.ids import file_record_id
from mofsift.io.structures import StructureError, read_structure
from mofsift.io.tables import ID_COLUMN, normalise, register_columns
from mofsift.stages import STATUS_FAILED, STATUS_OK, STATUS_SKIPPED, check_new_table

STAGE = "generate"
STRUCTURE_DIR = "structures"
BLOCK_DIR = "blocks"

COLUMNS = {
    "name": "string",
    "source": "string",
    "topology": "string",
    "nodes": "string",
    "edges": "string",
    "source_file": "string",
    "n_atoms": "Int64",
    "formula": "string",
    "expected_formula": "string",
    "linker_smiles": "string",
    "node_rmsd": "Float64",
    "cif_path": "string",
    "generate_status": "string",
    "generate_skip_reason": "string",
}
register_columns(COLUMNS)

Progress = Callable[[int, int, str, str, str | None], None]
"""Called after each candidate with (position, total, name, status, reason)."""


class GenerateError(ValueError):
    """The generate section of the config cannot be carried out (unknown names, nothing to do)."""


@dataclass
class GenerateResult:
    table: pd.DataFrame
    notes: list[str] = field(default_factory=list)
    reused: int = 0
    tools: dict[str, str | None] = field(default_factory=dict)
    linkers: list[dict[str, Any]] = field(default_factory=list)


def structure_path(workdir: Path, record_id: str) -> Path:
    return Path(workdir) / STRUCTURE_DIR / f"{record_id}.cif"


def build_record_path(workdir: Path, record_id: str) -> Path:
    """Side file written after a successful PORMAKE build (bond list, RMSD, provenance)."""
    return Path(workdir) / STRUCTURE_DIR / f"{record_id}.build.json"


def _rel(workdir: Path, path: Path) -> str:
    return path.relative_to(workdir).as_posix()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, sort_keys=True, separators=(",", ":"))
        fh.write("\n")
    os.replace(tmp, path)


def _empty_row() -> dict[str, Any]:
    return dict.fromkeys(COLUMNS, pd.NA)


def _finish(rows: list[dict[str, Any]], ids: list[str]) -> pd.DataFrame:
    table = pd.DataFrame(rows, columns=list(COLUMNS), index=pd.Index(ids, name=ID_COLUMN))
    table = normalise(table)
    check_new_table(table, STAGE)
    return table


# -------------------------------------------------------------------- pormake
def _reuse(workdir: Path, combo: Combination) -> dict[str, Any] | None:
    """Row of a candidate that was already built in this work folder, or None."""
    cif, record = structure_path(workdir, combo.record_id), build_record_path(workdir, combo.record_id)
    if not (cif.is_file() and record.is_file()):
        return None
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
        return {
            "n_atoms": int(data["n_atoms"]),
            "formula": str(data["formula"]),
            "node_rmsd": float(data["max_rmsd"]),
        }
    except (OSError, ValueError, KeyError, TypeError):
        return None  # unreadable record: build again


def _cif_files(cfg: Config, config_dir: Path) -> tuple[Path, list[Path]]:
    gen = cfg.generate
    assert gen.cif_dir is not None  # guaranteed by the config model
    cif_dir = gen.cif_dir if gen.cif_dir.is_absolute() else config_dir / gen.cif_dir
    if not cif_dir.is_dir():
        raise GenerateError(f"generate.cif_dir: folder not found: {cif_dir}")
    files = sorted(
        (p for p in cif_dir.rglob("*") if p.is_file() and p.suffix.lower() == ".cif"),
        key=lambda p: p.relative_to(cif_dir).as_posix(),
    )
    if not files:
        raise GenerateError(f"generate.cif_dir: no .cif files in {cif_dir}")
    return cif_dir, files


def _resolve(
    cfg: Config, config_dir: Path, adapter: Any
) -> tuple[Any, list[Combination], list[str], list[LinkerBlock]]:
    """Look up every name of the generate section and enumerate the candidates."""
    from mofsift.generate import pormake_adapter as pa

    gen = cfg.generate
    if adapter is None:
        block_dir = None
        if gen.building_block_dir is not None:
            block_dir = (
                gen.building_block_dir
                if gen.building_block_dir.is_absolute()
                else config_dir / gen.building_block_dir
            )
        try:
            adapter = pa.PormakeAdapter(block_dir)
        except pa.UnknownNameError as err:
            raise GenerateError(str(err)) from err

    problems: list[str] = []
    topologies, nodes, edges = [], [], []
    for kind, names, getter, sink in (
        ("topologies", gen.topologies, adapter.topology_info, topologies),
        ("nodes", gen.nodes, adapter.block_info, nodes),
        ("edges", gen.edges, adapter.block_info, edges),
    ):
        for name in names:
            try:
                sink.append(getter(name))
            except pa.UnknownNameError as err:
                problems.append(f"generate.{kind}: {err}")
    for block in edges:
        if block.n_connection_points != 2:
            problems.append(
                f"generate.edges: {block.name} has {block.n_connection_points} connection points; "
                "an edge needs exactly 2 (list it under nodes instead)"
            )

    # Linkers given as SMILES join the edges (2 connection points) or the nodes (3 or more).
    linkers: list[LinkerBlock] = []
    linker_notes: list[str] = []
    if gen.linkers is not None:
        try:
            pairs = collect_smiles(gen.linkers.smiles_file, gen.linkers.smiles, config_dir)
        except LinkerError as err:
            pairs = []
            problems.append(f"generate.linkers: {err}")
        taken = set(adapter.known_blocks())
        clashes = [name for name, _ in pairs if name in taken]
        if clashes:
            problems.append(
                f"generate.linkers: name(s) already used by a building block: {', '.join(clashes)}; "
                "choose other names"
            )
        linkers, failed = build_linkers(
            [pair for pair in pairs if pair[0] not in taken],
            seed=cfg.seed,
            connection_distance=gen.linkers.connection_distance,
            conformers=gen.linkers.conformers,
        )
        problems += [f"generate.linkers: {message}" for message in failed]
        for linker in linkers:
            info = BlockInfo(linker.name, linker.n_connection_points, linker.counts, linker.content_hash)
            (edges if linker.role == "edge" else nodes).append(info)
            linker_notes += [f"linker {linker.name}: {note}" for note in linker.notes]
    if problems:
        raise GenerateError("\n".join(problems))

    combos, notes = enumerate_combinations(topologies, nodes, edges)
    if not combos:
        raise GenerateError("\n".join(["the config does not define any candidate", *notes]))
    return adapter, combos, [*linker_notes, *notes], linkers


def preview(cfg: Config, config_dir: str | Path = ".", adapter: Any = None) -> tuple[int, list[str]]:
    """Number of candidates the generate section defines, plus notes, without building anything.

    Raises :class:`GenerateError` when a name is unknown or nothing would be generated.
    """
    config_dir = Path(config_dir)
    if cfg.generate.source == "cifs":
        return len(_cif_files(cfg, config_dir)[1]), []
    _, combos, notes, _ = _resolve(cfg, config_dir, adapter)
    return len(combos), notes


def _from_pormake(
    cfg: Config, workdir: Path, config_dir: Path, force: bool, adapter: Any, progress: Progress | None
) -> GenerateResult:
    from mofsift.generate import pormake_adapter as pa

    gen = cfg.generate
    adapter, combos, notes, linkers = _resolve(cfg, config_dir, adapter)

    (workdir / STRUCTURE_DIR).mkdir(parents=True, exist_ok=True)
    smiles_of = {linker.name: linker.smiles for linker in linkers}
    for linker in linkers:
        path = linker.write_xyz(workdir / BLOCK_DIR / f"{linker.name}.xyz")
        adapter.register_block(linker.name, path, linker.content_hash)
    rows: list[dict[str, Any]] = []
    reused = 0
    for pos, combo in enumerate(combos, start=1):
        row = _empty_row()
        row.update(
            name=combo.name,
            source="pormake",
            topology=combo.topology,
            nodes="+".join(combo.nodes),
            edges="+".join(combo.edges) if combo.edges else pd.NA,
            expected_formula=hill_formula(combo.expected_counts),
        )
        used = [smiles_of[n] for n in dict.fromkeys([*combo.nodes, *combo.edges]) if n in smiles_of]
        if used:
            row["linker_smiles"] = ";".join(used)
        status, reason = STATUS_OK, None
        cif = structure_path(workdir, combo.record_id)
        record = build_record_path(workdir, combo.record_id)
        done = None if force else _reuse(workdir, combo)
        if gen.max_atoms is not None and combo.expected_n_atoms > gen.max_atoms:
            status = STATUS_SKIPPED
            reason = f"{combo.expected_n_atoms} atoms expected; generate.max_atoms is {gen.max_atoms}"
        elif done is not None:
            row.update(done, cif_path=_rel(workdir, cif))
            reused += 1
        else:
            try:
                built = adapter.build(combo)
                built.write_cif(cif)
                _write_json(
                    record,
                    {
                        "id": combo.record_id,
                        "topology": combo.topology,
                        "nodes": list(combo.nodes),
                        "edges": list(combo.edges),
                        "linker_smiles": used,
                        "n_atoms": built.n_atoms,
                        "formula": built.formula,
                        "max_rmsd": round(built.max_rmsd, 6),
                        "mean_rmsd": round(built.mean_rmsd, 6),
                        "bonds": built.bonds,
                        "pormake_version": pa.pormake_version(),
                    },
                )
                row.update(
                    n_atoms=built.n_atoms,
                    formula=built.formula,
                    node_rmsd=round(built.max_rmsd, 6),
                    cif_path=_rel(workdir, cif),
                )
            except Exception as err:  # PORMAKE can fail in many ways; the run goes on
                status = STATUS_FAILED
                text = " ".join(str(err).split())
                reason = f"build failed: {type(err).__name__}" + (f": {text[:160]}" if text else "")
                for leftover in (cif, record):
                    leftover.unlink(missing_ok=True)
        row["generate_status"] = status
        row["generate_skip_reason"] = reason if reason is not None else pd.NA
        rows.append(row)
        if progress is not None:
            progress(pos, len(combos), combo.name, status, reason)

    table = _finish(rows, [c.record_id for c in combos])
    described = [
        {
            "name": linker.name,
            "smiles": linker.smiles,
            "found_by": linker.mode,
            "used_as": linker.role,
            "connection_points": linker.n_connection_points,
            "formula": linker.formula,
            "block": _rel(workdir, workdir / BLOCK_DIR / f"{linker.name}.xyz"),
        }
        for linker in linkers
    ]
    tools = {"pormake": pa.pormake_version()}
    if linkers:
        from rdkit import rdBase

        tools["rdkit"] = rdBase.rdkitVersion
    return GenerateResult(table, notes, reused, tools, described)


# ----------------------------------------------------------------------- cifs
def _from_cifs(cfg: Config, workdir: Path, config_dir: Path, progress: Progress | None) -> GenerateResult:
    gen = cfg.generate
    cif_dir, files = _cif_files(cfg, config_dir)

    (workdir / STRUCTURE_DIR).mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    ids: list[str] = []
    first_seen: dict[str, str] = {}
    notes: list[str] = []
    for pos, path in enumerate(files, start=1):
        rel = path.relative_to(cif_dir).as_posix()
        content = path.read_bytes()
        try:
            rid = file_record_id(content)
        except ValueError:
            rid = file_record_id(f"empty file: {rel}")
        if rid in first_seen:
            notes.append(f"{rel}: same content as {first_seen[rid]}; kept once")
            continue
        first_seen[rid] = rel
        row = _empty_row()
        row.update(name=path.stem, source="cifs", source_file=rel)
        status, reason = STATUS_OK, None
        try:
            atoms = read_structure(path)
            row.update(n_atoms=len(atoms), formula=atoms.get_chemical_formula())
            if gen.max_atoms is not None and len(atoms) > gen.max_atoms:
                status = STATUS_SKIPPED
                reason = f"{len(atoms)} atoms; generate.max_atoms is {gen.max_atoms}"
            else:
                target = structure_path(workdir, rid)
                target.write_bytes(content)
                row["cif_path"] = _rel(workdir, target)
        except StructureError as err:
            status, reason = STATUS_FAILED, f"cannot read the CIF: {err}"
        row["generate_status"] = status
        row["generate_skip_reason"] = reason if reason is not None else pd.NA
        rows.append(row)
        ids.append(rid)
        if progress is not None:
            progress(pos, len(files), path.stem, status, reason)
    return GenerateResult(_finish(rows, ids), notes)


def run(
    cfg: Config,
    workdir: str | Path,
    config_dir: str | Path = ".",
    *,
    force: bool = False,
    adapter: Any = None,
    progress: Progress | None = None,
) -> GenerateResult:
    """Create the candidate table.

    ``config_dir`` is the folder that relative paths in the config refer to. With
    ``force`` every structure is built again; otherwise structures already present in the
    work folder are reused. ``adapter`` replaces the PORMAKE adapter (used by the tests).
    """
    workdir, config_dir = Path(workdir), Path(config_dir)
    if cfg.generate.source == "cifs":
        return _from_cifs(cfg, workdir, config_dir, progress)
    return _from_pormake(cfg, workdir, config_dir, force, adapter, progress)
