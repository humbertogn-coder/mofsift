# Changelog

All notable changes are listed here. The format follows Keep a Changelog; versions follow SemVer.

## [Unreleased]

### Added
- `describe` stage with three sub-stages, each with its own status columns: composition
  descriptors (no extra tool), pore geometry from Zeo++ (pyzeo, or the `network` binary) and
  periodic GFN-xTB gap and frontier levels (tblite).
- Geometry and xTB results are stored per structure and reused; failures are stored with their
  reason. Each structure is computed in its own process, so a tool that ends the process costs
  one row.
- `mofsift describe CONFIG [--only ...] [--block I/N] [--force]`; `--block` is for cluster job
  arrays, and the table can be assembled later on a machine without the tools.
- By default geometry and xTB run only on structures that passed qc (`describe.only_qc_passed`).
- A visible warning when geometry or xTB cannot run because the tool is missing.
- `work/run.log`: what ran, what was skipped and why.
- New config keys: `describe.only_qc_passed`; `describe.geometry.timeout_minutes`;
  `describe.xtb.spin`, `electronic_temperature`, `max_iterations`, `timeout_minutes`.
- Docs: `docs/describe.md`, `docs/install.md` (Linux, Windows with WSL, Slurm clusters); a Slurm
  job-array template in `examples/slurm/`.
- `generate` stage. `source: pormake` enumerates topology x node x edge combinations (nodes are
  matched to vertices by coordination number; `edges: []` connects nodes directly), builds them
  with PORMAKE and records failures and oversized candidates instead of stopping. `source: cifs`
  takes existing CIF files. Structures already in the work folder are reused; `--force` rebuilds.
- `qc` stage: distance, connectivity, valence, composition and node-misfit checks with thresholds
  from the config; `qc_pass` and the reasons per structure. New config key `qc.max_node_rmsd`.
- Working tables (`work/01_generate.csv`, `work/02_qc.csv`) with typed columns, written
  deterministically, plus a `.meta.json` per stage.
- CLI: `mofsift generate`, `mofsift qc`, `mofsift run`. `mofsift doctor CONFIG` now checks the
  topology and block names and reports the number of candidates before anything is built.
- User building blocks (`generate.building_block_dir`); their content is part of the record ID.
- Linkers from SMILES (`generate.linkers`, inline or from a `.smi` file), next to PORMAKE's
  bundled blocks: carboxylic acids are found automatically, `[*]` marks explicit connection
  points; two connection points give an edge, three or more an organic node. The block is
  embedded and optimised with RDKit and written in PORMAKE's format. New column `linker_smiles`.
- Docs: `docs/generate-and-qc.md`; three example configs under `examples/`.
- CI: a job that installs PORMAKE and runs the tests marked `external`.

### Changed
- `describe.geometry.high_accuracy` takes `OCC` (default), `DEF` or `false` instead of true/false.
- `generate.edges` may be empty.

### Fixed
- `mofsift doctor` left a `runtime.log` file in the current folder when PORMAKE was installed
  (PORMAKE creates it on import). PORMAKE is now imported without that side effect and without
  its console output.

### Added in the first skeleton
- Package skeleton with `src/` layout, MIT licence and three-author metadata.
- Config model (pydantic) with YAML loading, strict validation (unknown keys are errors, with
  suggestions), deterministic config hash and a published JSON Schema.
- Safe filter-expression validation (no function calls, attributes or subscripts).
- Stable record IDs for assembled candidates and for structures read from files.
- Stage contract helpers: status and skip-reason columns, and a checker that a stage only adds columns.
- Tool detection adapters for PORMAKE, pyzeo, tblite, scikit-learn and the Zeo++ and xtb binaries.
- Stage planning shared by `doctor` and the future runner.
- CLI: `mofsift init`, `mofsift doctor [CONFIG] [--json]`, `mofsift --version`.
- CI workflow: ruff, tests on Python 3.11–3.13 (Linux) and the core tier on Windows, wheel build and clean-install smoke test.
