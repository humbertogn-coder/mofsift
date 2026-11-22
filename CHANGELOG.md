# Changelog

All notable changes are listed here. The format follows Keep a Changelog; versions follow SemVer.

## [Unreleased]

### Added
- Tutorial, `docs/tutorial.md`: a first screening from the config to the bundle, with the real
  output of every command, a filter with undecided rows, reading a bundle from Python, opening
  it in MOF Discovery Studio, and the variation that starts from existing CIF files. A test in
  the external tier runs its commands and compares the outcome with the page.
- Reference pages generated from the code: `docs/config.md` (every key of the config, with type,
  limits and default) and `docs/columns.md` (every column, with type, unit and meaning).
  `python -m mofsift.docgen docs` writes them; a test keeps them in step.
- `docs/README.md` (index of the documentation), `docs/troubleshooting.md` (common messages and
  what to do), `docs/releasing.md` (release steps and what is still to be checked before 0.1.0).
- `CONTRIBUTING.md` and issue forms for GitHub.
- Tests of the documentation: commands, options and config snippets shown in the pages exist and
  validate, links resolve, messages quoted in the troubleshooting page are the ones in the code,
  and the version is the same in the package and in `CITATION.cff`.
- `filter` step (`mofsift filter`, table `work/05_filter.csv`): the filters of the config are
  evaluated on every row and recorded in `filter_pass` and `filter_rejected_by`. No row is
  removed. A filter whose answer depends on a missing value is undecided: the row does not pass
  and the missing columns are named; so is a division by zero. `column == None` and
  `column != None` test for a missing value. A filter on a column that no stage writes is reported by `doctor` and stops `run`
  before anything is computed.
- `export` stage (`mofsift export`): the rows that passed are written as a bundle in
  `export/<project>/`: `candidates.csv` (or parquet), `candidates.json` (one JSON record per
  row), `structures/<id>.cif` and `manifest.json`. The manifest holds the config, the filters
  with their counts, what every stage reported, tool versions, a SHA-256 of every file and a
  dictionary of the columns (type, stage, unit, description).
- The export warns, and records in the manifest, when a work table is older than its input or
  than the section of the config it depends on.
- `mofsift validate DIR`: checks a bundle from the folder alone (manifest schema, checksums,
  table, records against the candidate schema and against the table, structure files, counts).
- `mofsift merge A B ... --out DIR`: union of records and columns; missing values are filled
  from the other bundle; different values, or different structure files, for the same record
  stop the merge unless `--on-conflict first|last` is given. The manifest lists the sources.
- JSON Schemas for the manifest and for a candidate record, in `mofsift/schemas/`.
- `mofsift run` now ends with `filter` and `export`.
- New config key `export.out_dir`; new extra `parquet` (pyarrow), part of `all`.
- `docs/export.md`.
- `score` stage (`mofsift score`, table `work/04_score.csv`): linker components (`linker_sa`,
  `linker_sc`, `linker_size`, `linker_rotatable_bonds`) and precedent components
  (`precedent_topology`, `precedent_node`, `precedent_linker`, `precedent_pair`) as separate
  columns, with `score_notes` for what could not be computed. Optional rank-based
  `score_aggregate`.
- Precedent reference built from the synthesised structures of the QMOF database (CC BY 4.0):
  10,595 distinct MOFs. `scripts/build_precedent_reference.py` rebuilds it.
- Validation benchmark, `scripts/validate_score_components.py`, and its result in
  `docs/score-validation.md`: ROC-AUC of every component for synthesised against hypothetical
  MOFs of QMOF, with cross-validated precedent counts. `precedent_node` does not separate the
  sets and cannot be given a weight in the aggregate.
- `linker_smiles` is now also filled for PORMAKE's metal-free blocks (the acid they stand for).
- `docs/score.md`, `docs/running.md` (commands for Windows, a Slurm cluster and GitHub).
- `describe` stage with three sub-stages, each with its own status columns: composition
  descriptors (no extra tool), pore geometry from Zeo++ (pyzeo, or the `network` binary) and
  periodic GFN-xTB gap and frontier levels (tblite).
- Geometry and xTB results are stored per structure and reused; failures are stored with their
  reason. Each structure is computed in its own process, so a tool that ends the process costs
  one row.
- `mofsift describe CONFIG [--only ...] [--block I/N] [--force]`; `--block` is for cluster job
  arrays, and the table can be assembled later on a machine without the tools.
- When Zeo++ stops on a structure it is tried again with the settings in
  `describe.geometry.fallback` (default DEF, then off); the column `zeo_high_accuracy` records
  which setting produced each row.
- Linker scores as functions (`mofsift.score.linker_scores`): SA score (RDKit), SCScore, size and
  rotatable bonds. SCScore is not on PyPI, so its code (ported to current Python) and its original
  weights are included under `src/mofsift/score/_scscore/` with their MIT licence.
- `examples/slurm/grace_describe_array.slurm` and install steps for Grace (TAMU HPRC).
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
- Every key of the config has a description (also in `config.schema.json`). `tools.xtb` is
  described as reserved: this version runs xTB through tblite.
- Each `.meta.json` also records `stage_config_hash`, the hash of the part of the config its
  stage reads.
- `linker_smiles` is empty when a metal-free node is joined to an edge (the two form one larger
  linker, which is not derived); before, the SMILES of both blocks were listed.
- `score.components` lists all eight components by default; new component `precedent_linker`.
- `describe.geometry.high_accuracy` takes `OCC` (default), `DEF` or `false` instead of true/false.
- `generate.edges` may be empty.

### Fixed
- `score_aggregate` was wrong with pandas 2 when a weighted component that holds whole numbers
  (`precedent_*`, `linker_size`, `linker_rotatable_bonds`) had a missing value: pandas 2 gave the
  missing value a rank. The ranks are now computed the same way on pandas 2 and 3; results with
  pandas 3 are unchanged.
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
