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

> **Status: pre-alpha, private repository.** This version implements the package skeleton only:
> config validation, `mofsift init` and `mofsift doctor`. The stages are not implemented yet
> (see [Roadmap](#roadmap)).

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

Python 3.11–3.13.

```bash
pip install -e .                 # core: config, structure I/O, built-in QC, linker scores
pip install -e ".[generate]"     # + PORMAKE (structure assembly)
pip install -e ".[zeo,xtb]"      # + pyzeo (Zeo++) and tblite (xTB); Linux and macOS only
pip install -e ".[all,dev]"      # everything, plus pytest and ruff
```

`pyzeo` and `tblite` have no Windows wheels. On Windows the core tier works natively; run the
geometry and xTB stages under WSL or on a cluster. Conda users: `environment.yml` (everything,
Linux) and `environment-min.yml` (core only, any OS).

## Quickstart (what works today)

```bash
mofsift init                  # writes a commented example config: mofsift.yaml
mofsift doctor                # which tools are installed, with versions
mofsift doctor mofsift.yaml   # also validates the config and shows the stage plan
mofsift doctor --json         # the same, machine-readable
```

`doctor` exits with code 0 when everything needed is in place, 1 when a core dependency is
broken or a stage that the config requires cannot run, and 2 when the config is invalid.

A missing optional tool is not an error: the stage that needs it is skipped and the reason is
recorded. Set `enabled: true` together with `on_missing: error` to make a tool mandatory.

## Design rules

- **Every stage only adds columns** and writes `<stage>_status` and `<stage>_skip_reason`.
  Stages never drop rows; filtering is a separate, explicit step.
- **No silent fallbacks.** A value that was not computed is missing, never a default number.
- **Deterministic.** The same config and seed give the same table and the same record IDs.
- **Unknown config keys are errors**, with a suggestion when the key looks like a typo.
- **Filters cannot run code.** Only comparisons, `and`/`or`/`not`, arithmetic and `in` are allowed.

## Roadmap

| # | Milestone | Status |
|---|---|---|
| 1 | Skeleton: install, `init`, `doctor`, config validation, CI | done locally; CI to be confirmed on the first push |
| 2 | Generate (PORMAKE, or existing CIFs) + QC | next |
| 3 | Describe: composition, pore geometry (pyzeo), xTB (tblite) | planned |
| 4 | Score: linker and precedent components, with a published validation benchmark | planned |
| 5 | Export: table + viewer bundle + manifest; `merge`; `validate` | planned |
| 6 | Viewer support for generic bundles | planned |
| 7 | Docs, tutorial, release preparation | planned |

## Development

```bash
pip install -e ".[dev]"
pytest            # unit tests; tests that need an optional tool are marked "external"
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
