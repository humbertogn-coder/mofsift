# mofsift documentation

New here? Do the [tutorial](tutorial.md): nine candidates from config to bundle in about two
minutes of computing, with every output shown.

## Learn

| Page | What it covers |
|---|---|
| [tutorial.md](tutorial.md) | A first screening from start to finish, and the variation that starts from your own CIF files |
| [install.md](install.md) | Installing mofsift and its tools on Linux, macOS, Windows, WSL and a Slurm cluster |
| [running.md](running.md) | The commands for a Windows laptop and for a cluster, in order |

## The stages

```
config.yaml -> generate -> qc -> describe -> score -> filter -> export
```

| Page | Stage |
|---|---|
| [generate-and-qc.md](generate-and-qc.md) | Building structures from PORMAKE blocks or SMILES linkers, or reading your own CIFs; the structural checks |
| [describe.md](describe.md) | Composition, pore geometry (Zeo++) and xTB proxies; stored results and cluster job arrays |
| [score.md](score.md) | The synthetic-accessibility components and the precedent reference |
| [export.md](export.md) | Filters, the bundle, `validate` and `merge`, and the JSON Schemas |

## Reference

| Page | Content |
|---|---|
| [config.md](config.md) | Every key of the config file, with type and default (generated from the code) |
| [columns.md](columns.md) | Every column of the tables, with unit and meaning (generated from the code) |
| `mofsift COMMAND --help` | The options of each command |
| `src/mofsift/schemas/` | JSON Schemas of the config, of the manifest and of a record |

## Judge the numbers

| Page | Content |
|---|---|
| [limitations.md](limitations.md) | What the structures, the descriptors and the scores do not tell you. Read it before using a number |
| [score-validation.md](score-validation.md) | How well each score component separates synthesised from hypothetical MOFs, and how much that depends on the comparison set |

## Help and project

| Page | Content |
|---|---|
| [troubleshooting.md](troubleshooting.md) | Common messages and what to do about them |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | Reporting a problem, setting up for development, the rules of the code |
| [releasing.md](releasing.md) | The steps of a release, for maintainers |
| [../CHANGELOG.md](../CHANGELOG.md) | What changed |

## Looking at results

A bundle is plain files (CSV, JSON, CIF) and can be read with any tool. Two ready-made ways:

- **In a browser:** the Bundles page of
  [MOF Discovery Studio](https://github.com/humbertogn-coder/MOF_Discovery_Studio) opens the
  folder of a bundle, with a table, the meaning of every column and the structures in 3D
  ([tutorial, step 6](tutorial.md#6-look-at-it-in-a-browser)).
- **From Python:** `mofsift.export.read_bundle` returns the manifest and the table as a pandas
  DataFrame ([tutorial, step 8](tutorial.md#8-use-the-result-from-python)).
