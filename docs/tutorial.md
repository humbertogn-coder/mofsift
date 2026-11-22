# Tutorial: a first screening, start to finish

You will declare nine hypothetical MOFs in a config file, let mofsift build, check, describe and
score them, read the result, narrow it with a filter, and open it in a browser. The computing
takes about two minutes on a laptop.

Everything shown here was produced by the commands on this page. A test of the repository runs
them again and compares what they print, and the table of step 4, with this page, so the page
cannot quietly drift from the program. Your numbers can differ in the last digits (see
[Reruns](generate-and-qc.md#reruns)).

**What you need.** mofsift with every optional tool, on Linux, macOS or WSL:

```bash
pip install -e ".[all]"       # from the repository folder; see install.md for other ways
mofsift doctor                # lists what is installed
```

On Windows without WSL the pore-geometry and xTB stages are skipped, because their tools are not
available there. The tutorial still runs; those columns stay empty and the program says why.
[install.md](install.md) has the details.

## 1. Write the config

Make an empty folder, go into it, and save this as `mofsift.yaml`:

<!-- tutorial:file mofsift.yaml -->
```yaml
project: first_screening
seed: 0

generate:
  source: pormake
  topologies: [pcu, nbo, dia]
  nodes: [N16, N409]           # Zn4O(CO2)6 (6 connections) and the Cu2(CO2)4 paddlewheel (4)
  edges: [E14, E34]            # PORMAKE's blocks for the BDC and BPDC linkers
  linkers:
    smiles:
      bdc_nh2: "Nc1cc(C(=O)O)ccc1C(=O)O"   # 2-aminoterephthalic acid, written as SMILES
  max_atoms: 400

filter:
  - "qc_pass == True"
```

That is the whole run. A net receives every listed node with the right number of connections:
`pcu` has 6-connected vertices and takes the zinc node; `nbo` and `dia` have 4-connected vertices
and take the copper paddlewheel. Each of the three is combined with the three linkers, two from
PORMAKE's library and one that you wrote as SMILES. Three nets times three linkers is nine
candidates.

Every other setting has a default. `mofsift init` writes a commented config with all of them,
and [config.md](config.md) lists every key.

## 2. Ask what would happen

<!-- tutorial:run -->
```bash
mofsift doctor mofsift.yaml
```

`doctor` computes nothing. It checks the config and tells you what each stage would do:

<!-- tutorial:output -->
```text
…
Config mofsift.yaml: valid (project first_screening, hash …)
  stage                 plan  note
  generate              run   9 candidate(s)
  qc                    run
  describe.composition  run
  describe.geometry     run
  describe.xtb          run
  score                 run   components: …
  filter                run   1 expression(s)
  export                run   table (csv), records for a viewer, structure files
```

A misspelt key, an unknown building block or a filter on a column that does not exist would be
reported here, before any time is spent. A stage whose tool is missing says `skip` and why.

## 3. Run it

<!-- tutorial:run -->
```bash
mofsift run mofsift.yaml
```

<!-- tutorial:output -->
```text
  [1/9] pcu__N16__E14: ok
  [2/9] pcu__N16__E34: ok
  [3/9] pcu__N16__bdc_nh2: ok
  [4/9] nbo__N409__E14: ok
  [5/9] nbo__N409__E34: ok
  [6/9] nbo__N409__bdc_nh2: ok
  [7/9] dia__N409__E14: ok
  [8/9] dia__N409__E34: skipped (432 atoms expected; generate.max_atoms is 400)
  [9/9] dia__N409__bdc_nh2: ok
generate: 9 row(s): 8 ok, 1 skipped, 0 failed
…
qc: 9 row(s): 8 ok, 1 skipped, 0 failed; 6 passed the checks, 2 did not
…
describe.composition: 9 row(s): 8 ok, 1 skipped, 0 failed
    1 x no structure
describe.geometry: 9 row(s): 6 ok, 3 skipped, 0 failed (computed 6, reused 0)
    2 x did not pass qc
    1 x no structure
describe.xtb: 9 row(s): 5 ok, 4 skipped, 0 failed (computed 5, reused 0)
    1 x 324 atoms; describe.xtb.max_atoms is 300
    2 x did not pass qc
    1 x no structure
…
score: 9 row(s): 9 ok, 0 skipped, 0 failed
…
filter: 9 row(s): 6 satisfy every filter, 3 do not
    qc_pass == True: 6 true, 2 false, 1 undecided because a value is missing
…
export: 6 of 9 row(s), 6 structure file(s)
…
```

Nine candidates went in and six came out, and the output says what happened to the others:

- **One was never built.** `dia__N409__E34` would have 432 atoms and the config allows 400.
- **Two were built and failed the checks.** The square paddlewheel does not fit the tetrahedral
  vertex of `dia`. The table gives the reason for `dia__N409__E14`:
  `24 connected components; node misfit 0.527 A above 0.3 A`.
- **One passed but has no xTB result.** `nbo__N409__E34` has 324 atoms and the xTB stage stops
  at 300 by default. It keeps its other values and stays in the result.

No row is ever dropped on the way. The tables of every stage are in `work/`, with all nine rows
and a status and a reason for each stage; `work/run.log` keeps the summary you just saw.

## 4. Read the result

Open `work/05_filter.csv` in a spreadsheet. It has more than eighty columns
([columns.md](columns.md) explains each one). A few of them:

<!-- tutorial:results work/05_filter.csv -->
| name | qc_pass | lcd | pld | xtb_gap | xtb_unpaired_electrons | precedent_linker |
|---|---|---|---|---|---|---|
| pcu__N16__E14 | True | 13.26 | 9.94 | 2.86 | 0 | 355 |
| pcu__N16__E34 | True | 19.50 | 14.54 | 2.19 | 0 | 67 |
| pcu__N16__bdc_nh2 | True | 11.33 | 8.24 | 2.70 | 0 | 15 |
| nbo__N409__E14 | True | 16.66 | 10.35 | 0.02 | 12 | 355 |
| nbo__N409__E34 | True | 25.73 | 16.95 |  |  | 67 |
| nbo__N409__bdc_nh2 | True | 16.11 | 9.29 | 0.09 | 12 | 15 |
| dia__N409__E14 | False |  |  |  |  | 355 |
| dia__N409__E34 |  |  |  |  |  | 67 |
| dia__N409__bdc_nh2 | False |  |  |  |  | 15 |

`lcd` and `pld` are the largest cavity and the pore-limiting diameter in angstrom, `xtb_gap` is in
eV. An empty cell means "not computed", never zero.

How to read it:

- **The zinc frameworks are closed-shell** (`xtb_unpaired_electrons` is 0), so their `xtb_gap` is
  an ordinary HOMO-LUMO gap and the three can be compared with each other. In this table the
  longer linker and the amino group both give a smaller gap than BDC.
- **The copper frameworks are not comparable with them.** The calculation used 12 unpaired
  electrons (one per copper), and for such a structure `xtb_gap` is the distance from the doubly
  occupied to the singly occupied levels. It is small by construction and is not a band gap in
  the usual sense. Do not read 0.02 eV as "almost a metal".
- **`precedent_linker`** counts the distinct synthesised MOFs with that linker in a reference
  derived from the QMOF database: 355 with BDC, 67 with BPDC, 15 with the amino linker. It is an
  indication of how familiar the linker is, not a probability that the framework can be made.
- **xTB gaps are proxies** computed on geometries that were not relaxed. Use them to rank and to
  choose what to compute with a better method, not as results.

[describe.md](describe.md) and [limitations.md](limitations.md) say more about each number.

## 5. The bundle

The six rows that satisfied the filter were written to `export/first_screening/`:

```
export/first_screening/
  manifest.json          what is in the bundle and what produced it
  candidates.csv         the six rows, every column
  candidates.json        the same rows as JSON records, for a viewer
  structures/<id>.cif    one structure file per row
```

This folder is what you keep, share or publish. It does not need the `work/` folder, and its
manifest records the config, the filters with their counts, the versions of the tools, a checksum
of every file and the meaning of every column. Check a bundle at any time, anywhere:

<!-- tutorial:run -->
```bash
mofsift validate export/first_screening
```

<!-- tutorial:output -->
```text
export/first_screening: valid bundle of project first_screening (6 record(s), 6 structure file(s))
```

## 6. Look at it in a browser

[MOF Discovery Studio](https://github.com/humbertogn-coder/MOF_Discovery_Studio) reads bundles.
Open its **Bundles** page, choose "Choose a folder" and pick `export/first_screening`. You get the
table with search and sorting, the meaning of every column, and each structure in 3D. The folder
is read by your browser and is not uploaded.

If the menu of the public site has no Bundles entry yet, run the Studio on your computer:

```bash
git clone https://github.com/humbertogn-coder/MOF_Discovery_Studio
cd MOF_Discovery_Studio
npm ci
npm run dev                   # then open http://localhost:5173/bundles
```

## 7. Ask a narrower question

Say you want closed-shell frameworks with an xTB gap below 2.5 eV. Replace the `filter` section
of `mofsift.yaml`:

<!-- tutorial:section mofsift.yaml filter -->
```yaml
filter:
  - "qc_pass == True"
  - "xtb_gap < 2.5 and xtb_unpaired_electrons == 0"
```

Nothing has to be computed again. Run the last two stages, and write this bundle next to the
first one:

<!-- tutorial:run -->
```bash
mofsift filter mofsift.yaml
mofsift export mofsift.yaml --out export/low_gap
```

<!-- tutorial:output -->
```text
filter: 9 row(s): 1 satisfy every filter, 8 do not
    qc_pass == True: 6 true, 2 false, 1 undecided because a value is missing
    xtb_gap < 2.5 and xtb_unpaired_electrons == 0: 1 true, 4 false, 4 undecided because a value is missing
…
export: 1 of 9 row(s), 1 structure file(s)
…
```

One framework qualifies, `pcu__N16__E34`. Before you conclude that, look at the word
**undecided**. A filter that needs a value which was not computed has no answer, and mofsift does
not guess one: the row does not pass, and `filter_rejected_by` in `work/05_filter.csv` names the
missing columns. For `nbo__N409__E34` it says

```
xtb_gap < 2.5 and xtb_unpaired_electrons == 0 [missing: xtb_gap, xtb_unpaired_electrons]
```

That framework was not rejected for its gap. It was never computed, because of the 300-atom
limit. Raising `describe.xtb.max_atoms` and running `mofsift run` again would compute only what is
missing; every stored result is reused. [export.md](export.md#filters) lists what a filter can
contain.

## 8. Use the result from Python

A bundle is plain files, so any tool can read it. From Python:

<!-- tutorial:python -->
```python
from mofsift.export import read_bundle

bundle = read_bundle("export/first_screening")
table = bundle.table  # a pandas DataFrame, indexed by record ID
closed_shell = table[table["xtb_unpaired_electrons"] == 0]
print(closed_shell.sort_values("xtb_gap")[["name", "xtb_gap", "lcd", "precedent_linker"]].to_string())
print(bundle.manifest["counts"])
```

<!-- tutorial:output -->
```text
…
ms-a0678e0f108f6e4d      pcu__N16__E34 …
ms-e64845b2ad90daec  pcu__N16__bdc_nh2 …
ms-678e931c571c8679      pcu__N16__E14 …
{'enumerated': 9, 'selected': 6, 'structures': 6}
```

The record IDs are the same on every machine: they depend only on what defines the candidate.

## 9. Start from structures you already have

mofsift does not need to build the structures. Any folder of CIF files can go through the same
checks, descriptors and export, and PORMAKE is not needed for it. As a stand-in for your own
files, copy two of the structures made above:

<!-- tutorial:run -->
```bash
mkdir my_cifs
cp work/structures/ms-678e931c571c8679.cif my_cifs/zn_bdc.cif
cp work/structures/ms-a0678e0f108f6e4d.cif my_cifs/zn_bpdc.cif
```

(On Windows use `copy` in place of `cp`, with `\` in the paths.) Save this as `own_cifs.yaml`:

<!-- tutorial:file own_cifs.yaml -->
```yaml
project: own_cifs
workdir: work_cifs

generate:
  source: cifs
  cif_dir: my_cifs

filter:
  - "qc_pass == True"
```

<!-- tutorial:run -->
```bash
mofsift run own_cifs.yaml
```

<!-- tutorial:output -->
```text
  [1/2] zn_bdc: ok
  [2/2] zn_bpdc: ok
generate: 2 row(s): 2 ok, 0 skipped, 0 failed
…
qc: 2 row(s): 2 ok, 0 skipped, 0 failed; 2 passed the checks, 0 did not
…
describe.geometry: 2 row(s): 2 ok, 0 skipped, 0 failed (computed 2, reused 0)
describe.xtb: 2 row(s): 2 ok, 0 skipped, 0 failed (computed 2, reused 0)
…
score: 2 row(s): 2 ok, 0 skipped, 0 failed
    linker_sa: 0 row(s) with a value
…
    precedent_node: 2 row(s) with a value
…
export: 2 of 2 row(s), 2 structure file(s)
…
```

What is different when mofsift did not build the structure:

- The record IDs start with `msf-` and are a hash of the file content.
- The checks that need the building blocks (expected formula, node misfit, non-bonded contacts)
  are not run, and their columns are empty.
- The linker and the net are not known, so the components that need them are empty.
  `score_notes` says so for each row. The count for the metal is still there.
- The pore diameters and the xTB gap are the same as in step 4, because the geometry is the same.

## Where to go next

| You want to | Read |
|---|---|
| Run the slow stages on a cluster and continue on your laptop | [running.md](running.md), [describe.md](describe.md#on-a-cluster) |
| Join bundles from several configs or two machines | [export.md](export.md#mofsift-merge) |
| Use your own linkers or building blocks | [generate-and-qc.md](generate-and-qc.md#linkers-from-smiles) |
| Understand the synthetic-accessibility components | [score.md](score.md), [score-validation.md](score-validation.md) |
| Know what the numbers do not tell you | [limitations.md](limitations.md) |
| Find out why something did not run | [troubleshooting.md](troubleshooting.md) |
