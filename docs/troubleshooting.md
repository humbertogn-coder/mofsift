# When something does not work

Start with two commands. They compute nothing and answer most questions:

```bash
mofsift doctor                # which tools are installed, with versions
mofsift doctor config.yaml    # is the config valid, and what would each stage do
```

After a run, `work/run.log` has one line per stage with what ran, what was skipped and why, and
every table has a `<stage>_status` and a `<stage>_skip_reason` column for each row.

The messages below are quoted as the program prints them.

## Installing

**`pip` finds no version of pyzeo or tblite.** They have no Windows builds, and their Linux
builds need glibc 2.28 or newer. On Windows use WSL or run those two stages on a cluster
([install.md](install.md)). Everything else works without them: the stages are skipped and the
table says so.

**`mofsift doctor` lists a tool as `missing` although you installed it.** It was installed into
another Python environment. Activate the environment you installed into and check with
`python -m pip show pyzeo` (or the tool in question).

**`A core dependency is missing or broken; reinstall mofsift.`** One of numpy, pandas, ASE,
pymatgen or RDKit cannot be imported. The table above that line shows which one.

## The config

**`generate.topologys: unknown key; did you mean 'topologies'?`** A key is misspelt or in the
wrong section. Unknown keys are always an error, so that a typo cannot silently change a run.
[config.md](config.md) lists every key.

**`generate.nodes: unknown building block 'N999' (did you mean N99, N699, N599?)`** The name is
not one of PORMAKE's blocks and is not in your `building_block_dir`.

**`doctor` says `0 candidate(s)`, or fewer than you expected.** A node is only placed on a vertex
with the same number of connection points. `doctor` prints a note for every net that no listed
node fits and for every node that fits no net.

**`filter cannot run: no stage writes the column 'xtb_gapp' (did you mean 'xtb_gap'?)`** A filter
names a column that does not exist. [columns.md](columns.md) lists the columns.

**`describe.geometry  error  zeo++ (network) is not installed ...; the config sets enabled: true
and on_missing: error`** You asked for a tool to be mandatory and it is not there. Install it, or
set `enabled: auto` to skip the stage when the tool is missing.

## Running

**`qc cannot run: .../work/01_generate.csv does not exist; run 'mofsift generate config.yaml'
first (or 'mofsift run' for all stages)`** The stages build on each other.

**A stage says `skipped`.** That is not an error. The reason is in the output, in `work/run.log`
and in the `_skip_reason` column. The usual ones:

| Reason | What to do |
|---|---|
| `did not pass qc` | Nothing, or set `describe.only_qc_passed: false` to describe every structure |
| `324 atoms; describe.xtb.max_atoms is 300` | Raise `describe.xtb.max_atoms`; larger cells take longer |
| `432 atoms expected; generate.max_atoms is 400` | Raise `generate.max_atoms`, or leave it out for no limit |
| `no structure` | The structure was not built; look at `generate_skip_reason` |
| the tool is not installed | Install it, or compute that stage on another machine ([running.md](running.md)) |

**Few or no rows are exported.** Look at `filter_rejected_by` in `work/05_filter.csv` before you
conclude that nothing qualifies. A filter that needs a value which was not computed is
*undecided* and the row does not pass; the cell then ends with `[missing: ...]` and names the
columns. Often the stage that computes the value simply did not run. To keep such rows, say so in
the filter: `xtb_gap == None or xtb_gap < 1.5`.

**xTB is slow.** The time grows quickly with the size of the cell. In the run of the
[tutorial](tutorial.md), on one core, a calculation took 2 s for 53 to 83 atoms, about 15 s for
about 200 atoms and about 40 s for 324 atoms. tblite uses OpenMP threads: set `OMP_NUM_THREADS`
to the number of cores you give it. Results are stored per structure, so an interrupted run continues where it stopped, and a
table can be split over cluster jobs ([describe.md](describe.md#on-a-cluster)).

**Zeo++ stops on a structure.** mofsift tries the settings in `describe.geometry.fallback` in
turn, and the column `zeo_high_accuracy` records which one produced each row. Nothing to do
unless every setting fails; the row is then `failed` and the reason names what was tried.

**The same config gives slightly different numbers on two machines.** The record IDs are the
same everywhere. The coordinates need not be: PORMAKE's choice of how a linear linker is turned
can change with the versions of its numerical libraries. Build the structures once and carry the
`work/` folder to the other machine ([generate-and-qc.md](generate-and-qc.md#reruns)).

## Bundles

**`export cannot run: ... is not empty and does not hold a mofsift bundle; choose another
folder`** The export only replaces a bundle it wrote itself. Give an empty folder with
`export.out_dir` or `--out`.

**`NOT a valid bundle ... the file differs from the one the manifest describes`** A file was
changed after the export. The usual cause is a program that rewrote the line endings: git on
Windows does it to text files. If you keep a bundle in a git repository, add a `.gitattributes`
file with the line `* -text` to its folder ([export.md](export.md#the-bundle)).

**`merge cannot be done: ... is not a valid bundle; nothing was merged`** Every bundle is checked
before a merge. Run `mofsift validate` on the one that is named.

**A merge stops on conflicts.** Two bundles hold different values for the same record and
column. Nothing is written and the differences are listed. `--on-conflict first` or `last`
decides them ([export.md](export.md#mofsift-merge)).

**The Studio says a bundle "cannot be shown".** It needs `candidates.json`, so
`export.viewer_bundle` must be `true` (the default), and the files must be unchanged since the
export.

## Asking for help

Open an issue at <https://github.com/humbertogn-coder/mofsift/issues> with:

- the output of `mofsift doctor --json`;
- the config file, or the smallest one that shows the problem;
- the command and what it printed, and the lines of `work/run.log` for that run.

Do not attach unpublished structures or results; a public example that shows the same problem is
enough.
