# mofsift

**Config-driven screening of hypothetical metal–organic frameworks (MOFs) for electronic properties.**

One config file declares a set of building blocks; mofsift turns it into a filtered, described and
scored table of hypothetical MOFs, plus a bundle that a static viewer can display.

```
config.yaml -> generate -> qc -> describe -> score -> filter -> export -> viewer
               (PORMAKE)  (QC)  (Zeo++, xTB)  (components)        (JSON + CIF)
```

Most open MOF workflows target gas adsorption. mofsift targets **electronic properties**
(band gap and related proxies).

> **Status: pre-alpha.** This version runs `generate`, `qc`, `describe` (composition, pore
> geometry, xTB) and `score` (synthetic-accessibility components). Filtering and export are not
> implemented yet (see [Roadmap](#roadmap)).

## Read this first

- All structures are **hypothetical**. Passing the structural checks does not make a framework
  stable or synthesisable.
- A low band gap is **not** electrical conductivity.
- xTB band gaps are **proxies**. How well they follow DFT gaps depends on the chemistry; for
  open-shell transition-metal frameworks, which are often the low-gap candidates, treat them as
  unvalidated.
- The synthetic-accessibility components are reported separately. An optional rank-based
  summary exists; it is an indicator, never a probability.

More in [docs/limitations.md](docs/limitations.md).

## Install

Python 3.11–3.13. On Linux, macOS or WSL, install everything:

```bash
pip install -e ".[all]"          # PORMAKE, pyzeo (Zeo++), tblite (xTB), scikit-learn
```

Pore geometry (Zeo++) and the xTB electronic proxies are the descriptors this package exists
for. They are optional only in the sense that a run does not crash without them: the stage is
skipped and the table says why. Smaller installs:

```bash
pip install -e .                 # core: config, structure I/O, QC, SMILES linkers, linker scores
pip install -e ".[generate]"     # + PORMAKE (structure assembly)
pip install -e ".[zeo,xtb]"      # + pyzeo and tblite
pip install -e ".[all,dev]"      # everything, plus pytest and ruff
```

`pyzeo` and `tblite` have no Windows wheels. On Windows the core tier and PORMAKE work natively;
run the geometry and xTB stages under WSL or on a cluster. Conda users: `environment.yml`
(everything, Linux) and `environment-min.yml` (core only, any OS).

## Quickstart (what works today)

```bash
mofsift init                  # writes a commented example config: mofsift.yaml
mofsift doctor mofsift.yaml   # tools found, config valid, how many candidates, which stages run
mofsift run mofsift.yaml      # generate + qc + describe + score (the stages implemented so far)
```

The example config assembles two IRMOF-type frameworks with PORMAKE (Zn4O node on the `pcu` net
with BDC and BPDC) and checks them; it takes a few seconds. The result is in `work/`:

```
work/
  01_generate.csv        one row per candidate: id, blocks, formula, path of the CIF, status
  02_qc.csv              the same rows plus the check results and qc_pass
  03_describe.csv        the same rows plus composition, pore geometry and xTB columns
  04_score.csv           the same rows plus the synthetic-accessibility components
  describe/              one stored result per structure and sub-stage (reused on later runs)
  run.log                what ran, what was skipped, and why
  *.meta.json            what produced each table (versions, config hash, counts)
  structures/<id>.cif    one CIF per structure
  structures/<id>.build.json   bond list and node misfit of structures assembled by PORMAKE
  blocks/<name>.xyz      building blocks made from SMILES linkers, in PORMAKE's format
```

Stages can also be run one at a time (`mofsift generate`, `mofsift qc`, `mofsift describe`).
Structures and descriptor results that already exist in the work folder are reused; `--force`
computes them again.

### Descriptors

`mofsift describe` adds composition descriptors, pore geometry from Zeo++ (LCD, PLD, accessible
surface and volume) and periodic GFN-xTB levels and gap. By default only structures that passed
qc get the last two. Every result is stored per structure, so an interrupted run continues, and
a table can be split over cluster jobs (`--block 3/20`) and assembled afterwards, even on a
machine without the tools. If pyzeo or tblite is missing the stage is skipped with a warning.
Columns, settings and what the xTB numbers mean: [docs/describe.md](docs/describe.md).
Installing the tools (Linux, Windows with WSL, clusters): [docs/install.md](docs/install.md).

### Synthetic-accessibility components

`mofsift score` adds separate, named components: SA score, SCScore, size and rotatable bonds of
the linker, and how many distinct synthesised MOFs in a reference built from the QMOF database
share the candidate's topology, metal, linker, and metal-linker pair. None is a probability.
How well each one separates synthesised from hypothetical MOFs is measured and reported in
[docs/score-validation.md](docs/score-validation.md); details in [docs/score.md](docs/score.md).

The commands for Windows, a Slurm cluster and GitHub are collected in
[docs/running.md](docs/running.md).

### Linkers from SMILES

Linkers can be PORMAKE's bundled blocks, SMILES, or both in the same run:

```yaml
generate:
  source: pormake
  topologies: [pcu]
  nodes: [N16]
  edges: [E14]
  linkers:
    smiles:
      bdc_nh2: "Nc1cc(C(=O)O)ccc1C(=O)O"    # carboxylic acids are found automatically
      core: "[*]c1ccc([*])cc1"              # or mark the connection points with [*]
    smiles_file: linkers.smi                # one "SMILES name" per line
```

A linker with two connection points is used as an edge; with three or more, as an organic node.
Details and caveats: [docs/generate-and-qc.md](docs/generate-and-qc.md#linkers-from-smiles).

### Starting from your own CIFs

No PORMAKE needed:

```yaml
project: my_structures
generate:
  source: cifs
  cif_dir: my_cifs/          # searched recursively; relative to the config file
```

Each file gets an ID from its content, is copied unchanged to `work/structures/`, and goes
through the same checks. What the checks cover, and every column, is described in
[docs/generate-and-qc.md](docs/generate-and-qc.md).

### Exit codes

0 = done; 1 = a core dependency is broken or a stage cannot run (for example PORMAKE is needed
and missing, or `qc` was called before `generate`); 2 = the config is invalid or names
something that does not exist.

A missing optional tool is not an error: the stage that needs it is skipped and the reason is
recorded. Set `enabled: true` together with `on_missing: error` to make a tool mandatory.

## Design rules

- **Every stage only adds columns** and writes `<stage>_status` and `<stage>_skip_reason`.
  Stages never drop rows; filtering is a separate, explicit step.
- **No silent fallbacks.** A value that was not computed is missing, never a default number.
- **Deterministic.** The same config and seed give the same record IDs everywhere, and the same
  tables in the same software environment (see the note on coordinates in
  [docs/generate-and-qc.md](docs/generate-and-qc.md#reruns)).
- **Unknown config keys are errors**, with a suggestion when the key looks like a typo.
- **Filters cannot run code.** Only comparisons, `and`/`or`/`not`, arithmetic and `in` are allowed.

## Roadmap

| # | Milestone | Status |
|---|---|---|
| 1 | Skeleton: install, `init`, `doctor`, config validation, CI | done locally; CI to be confirmed on the first push |
| 2 | Generate (PORMAKE blocks, linkers from SMILES, or existing CIFs) + QC | done locally |
| 3 | Describe: composition, pore geometry (pyzeo), xTB (tblite) | done locally |
| 4 | Score: linker and precedent components, with a published validation benchmark | done locally |
| 5 | Export: table + viewer bundle + manifest; `merge`; `validate` | next |
| 6 | Viewer support for generic bundles | planned |
| 7 | Docs, tutorial, release preparation | planned |

## Development

```bash
pip install -e ".[dev]"
pytest            # tests that need an optional tool are marked "external" and skip without it
pip install -e ".[dev,generate]" && pytest -m external   # the tests that build with PORMAKE
ruff check .
ruff format --check .
python -m mofsift.config > src/mofsift/schemas/config.schema.json   # after changing the config model
```

## Origin, licence and citation

The general-purpose parts of this package derive from the research code
[MOF_3D_Generator](https://github.com/humbertogn-coder/MOF_3D_Generator)
(C. H. Guerrero Navarro, M. Ghotbi, P. B. Balbuena; MIT). No study-specific catalogue, DFT
result, prediction or trained model is included here.

MIT licence; see [LICENSE](LICENSE) and [CITATION.cff](CITATION.cff).

mofsift builds on PORMAKE, Zeo++ (via pyzeo), tblite, RDKit, ASE and pymatgen. Please cite
them when you use the corresponding stages.

Included third-party data: precedent counts derived from the QMOF database (A. S. Rosen et al.,
Matter 2021, 4, 1578; npj Comput. Mater. 2022, 8, 195), which is distributed under CC BY 4.0, in
`src/mofsift/score/data/`. The file holds counts computed by mofsift, not the original data.

Included third-party code: SCScore (C. W. Coley et al., MIT licence), code and model weights, in
`src/mofsift/score/_scscore/` with its licence and source.
