# Filter, export, validate and merge

The last two stages turn the work tables into something you can hand to someone else: a
**bundle**. Two more commands work on bundles: `validate` checks one, `merge` joins several.

```bash
mofsift filter config.yaml       # marks the rows that satisfy the filters   -> work/05_filter.csv
mofsift export config.yaml       # writes the rows that passed as a bundle   -> export/<project>/
mofsift validate export/demo     # checks a bundle, wherever it is
mofsift merge export/a export/b --out export/both --project both
```

`mofsift run` runs every stage, these two included.

## Filters

```yaml
filter:
  - "qc_pass == True"
  - "xtb_gap < 1.5 and xtb_unpaired_electrons == 0"
  - "topology in ['pcu', 'dia']"
```

A row is exported when **every** filter is true for it. A filter is a comparison over columns
of the table (any column of any stage, and `id`):

| You can write | Example |
|---|---|
| comparisons | `density > 0.3`, `topology != 'pcu'`, `0.5 < xtb_gap < 2` |
| `and`, `or`, `not`, parentheses | `qc_pass and (lcd > 8 or pld > 6)` |
| arithmetic on numbers | `lcd - pld < 2`, `n_metal_atoms / n_atoms > 0.05` |
| membership in a list | `primary_metal in ['Zn', 'Cu']`, `topology not in ['dia']` |
| a test for "not computed" | `xtb_gap != None`, `lcd == None` |

Function calls, attributes and subscripts are refused, so a config file cannot run code. Values
are compared with values of the same kind: `topology > 3` or `qc_pass in [1]` is an error, not a
filter that silently matches nothing.

**No row is removed.** `work/05_filter.csv` has all the rows, with two more columns:

| Column | Meaning |
|---|---|
| `filter_pass` | True when the row satisfies every filter. Empty when the config has no filters |
| `filter_rejected_by` | The filters the row did not satisfy, separated by `; ` |
| `filter_status`, `filter_skip_reason` | `ok`, or `skipped` when the config has no filters |

### Missing values

A value that was not computed is missing, and a comparison with a missing value has no answer.
mofsift does not guess one:

- `xtb_gap < 1` is **undecided** for a row without an xTB result. The row does not pass, and
  `filter_rejected_by` says `xtb_gap < 1 [missing: xtb_gap]`.
- A missing value only matters when the answer depends on it. `xtb_gap < 1 or lcd > 10` is true
  for a row with `lcd = 12` and no `xtb_gap`.
- To keep the rows without a value, say so: `xtb_gap == None or xtb_gap < 1`.
- A division by zero has no answer either. The row is undecided and the reason says so:
  `lcd / pld > 2 [division by zero or overflow]`. Guard it if you want those rows decided:
  `pld != 0 and lcd / pld > 2`.

The command prints how many rows each filter found true, false and undecided; the same numbers
go to `work/run.log` and into the manifest of the bundle.

A filter that names a column no stage writes is an error, with a suggestion when it looks like
a typo. `mofsift doctor config.yaml` and `mofsift run` report it before anything is computed.
(`mofsift filter` itself looks at the table it is given, so if you entered the pipeline with your
own table, its extra columns can be used in filters.)

## The bundle

```
export/<project>/
  manifest.json          what is in the bundle and what produced it
  candidates.csv         the rows that passed, every column
  candidates.json        the same rows as JSON records, for a viewer
  structures/<id>.cif    the structure file of every record that has one
```

```yaml
export:
  out_dir: export/demo     # default: export/<project>, next to the config file
  viewer_bundle: true      # write candidates.json
  include_cifs: true       # copy the structure files
  table_format: csv        # csv | parquet  (parquet: pip install "mofsift[parquet]")
```

- **`candidates.csv`** is written like the work tables: UTF-8, an empty cell is a missing value.
  `cif_path` is relative to the bundle (`structures/<id>.cif`), and empty for a record without
  a file in the bundle, so a path never points at nothing.
- **`candidates.json`** holds one record per row, one per line:
  `{"id": "ms-…", "name": "pcu__N16__E14", …, "xtb_gap": 2.8622, …}`. A missing value is `null`.
  The keys are the columns of the table, in the same order.
- **`manifest.json`** is what makes the bundle readable without this documentation:

| Key | What it holds |
|---|---|
| `project`, `mofsift_version`, `created_utc` | Which run, written by which version, when |
| `config`, `config_hash` | The validated config of the run, defaults included, and its SHA-256 |
| `counts` | Rows before the filters (`enumerated`), rows in the bundle (`selected`), records with a structure file |
| `filters` | Each filter with the number of rows found true, false and undecided |
| `stages` | What every stage reported: status counts, tool versions, checksums of the work tables |
| `tools` | Versions of the programs that produced the numbers |
| `columns` | The column dictionary: name, type, stage, unit and a one-line description of every column |
| `table`, `records`, `structures` | The parts of the bundle |
| `files` | Every file with its size and SHA-256 |
| `notices` | What a reader must not miss (hypothetical structures, xTB gaps are proxies, scores are not probabilities) |
| `third_party` | Data from others that the numbers rest on, with licence and citation |
| `warnings` | What was out of step when the bundle was written (see below) |
| `sources` | For a merged bundle: the bundles it was made from |

Everything except `created_utc` follows from the work folder: two exports of the same work
folder differ in that one line.

The manifest contains the whole config of the run, defaults included, with the paths as you
wrote them. If a path in your config says something you would not publish (a user name in
`cif_dir`, for example), use a relative path. JSON has no infinite numbers: a value such as
`.inf` in the config appears in the manifest as the text `"Infinity"`.

Columns of your own (see "Entering at qc with your own table" in
[generate-and-qc.md](generate-and-qc.md)) are exported with the type their values have: whole
numbers, decimals, true/false or text. In such a column an empty text is stored as a missing
value, because a table file writes both the same way.

If you keep a bundle in a git repository, stop git from rewriting line endings in it (a
`.gitattributes` file with the line `* -text` in the bundle folder). Otherwise a checkout on
Windows changes the files, and `mofsift validate` reports that they differ from the manifest.

### Where the bundle goes, and what is overwritten

`mofsift export` replaces an older bundle **of the same project** in the output folder, removing
only the files that bundle listed. It refuses a folder that holds anything else, and it refuses
to replace the bundle of another project unless you pass `--force`. `--out DIR` (relative to the
current folder) overrides `export.out_dir` for one call.

If an export is interrupted, the folder is marked as unfinished: `mofsift validate` says so, and
running the export again replaces it.

### Out-of-date tables

Each stage records what it was made from. The export compares the records and warns when the
work folder is out of step, for example:

```
WARNING: 02_qc.csv was made before 'qc' in the config was changed; run 'mofsift qc' and the stages after it again
WARNING: 05_filter.csv was made from an older 04_score.csv; run 'mofsift filter' and the stages after it again
```

The bundle is still written, and the warnings are stored in its manifest. Changing only the
filters or the export settings does not make the earlier tables out of date. The `filters` of
the manifest are always the ones that were applied to the table, also when the config has
changed since.

## `mofsift validate`

```bash
mofsift validate export/demo          # exit code 0 = valid, 1 = problems found
mofsift validate export/demo --json
```

It needs only the folder. It checks that

- `manifest.json` follows its schema;
- every listed file is there, with the listed size and SHA-256;
- the table has the columns of the column dictionary, and unique record IDs;
- every record follows the candidate schema and says the same as its table row;
- every `cif_path` points at a listed file that has a unit cell and atom sites, and no structure
  file is left without a record;
- the counts add up;
- no file of a restricted kind is in the folder (`POTCAR`, `WAVECAR`, `CHGCAR`, pickled models).

Files in the folder that the manifest does not list are reported as notes, not as problems
(hidden files and `desktop.ini` are ignored).

## `mofsift merge`

```bash
mofsift merge export/zn export/cu --out export/zn_cu --project zn_cu
```

Two uses:

1. **Several configs, one set.** One config holds one list of edges for all nets; to screen
   different families, run one config each and merge the bundles.
2. **Two machines.** The same config run on a laptop (no xTB) and on a cluster (xTB): the merged
   bundle has the values of both. (Copying the `work/describe/` folder between machines does the
   same before the export; see [describe.md](describe.md).)

Rules:

- Rows are matched by record ID. The result has the records of the first bundle, then the new
  records of the second, and so on. Columns are the union.
- For a record that is in several bundles, a value one bundle lacks is taken from the bundle that
  has it. The status of a stage is taken from the bundle where the stage ran.
- **Different values for the same record and column are a conflict.** The merge stops, lists
  them and writes nothing. `--on-conflict first` or `--on-conflict last` keeps the value of the
  first or the last bundle instead; the manifest then says how many values were decided that way.
- **Different structure files for the same record are a conflict too.** The same ID can have
  different coordinates when the structures were generated on two machines (see "Reruns" in
  [generate-and-qc.md](generate-and-qc.md)). Descriptors computed on two geometries do not
  describe one structure, so with `--on-conflict first` or `last` such a record is taken whole
  from that bundle, structure file included, and nothing is filled in from the other. If only
  one of the bundles holds the structure file, the files cannot be compared and the values are
  merged as usual.
- A column of your own that holds whole numbers in one bundle and decimals in another becomes a
  column of decimals. Text in one and numbers in the other cannot be merged.
- Every source is validated first; an invalid bundle is not merged.
- The manifest of the result lists its `sources` (project, config, filters, tool versions and
  the SHA-256 of each source manifest). `config` is empty, because no single config produced it.

`score_aggregate` is a rank within one table. After a merge it still holds the ranks of the
source tables and must not be compared across them.

## Schemas

Two JSON Schemas (draft 2020-12) are part of the package, for programs that read bundles:

- `mofsift/schemas/manifest.schema.json`
- `mofsift/schemas/candidate.schema.json` (one record of `candidates.json`; `id` is required,
  every column of mofsift has its type, and other columns may be text, numbers or true/false)

```python
from mofsift.export import read_bundle, validate_bundle

bundle = read_bundle("export/demo")  # .manifest (dict) and .table (DataFrame, typed)
print(validate_bundle("export/demo").problems)
```
