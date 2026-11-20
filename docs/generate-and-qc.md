# The generate and qc stages

## generate

Creates the candidate table and one CIF per structure. Two sources.

### `source: pormake`

```yaml
generate:
  source: pormake
  topologies: [pcu, nbo]     # PORMAKE topology names (RCSR nets)
  nodes: [N16, N409]         # PORMAKE node blocks
  edges: [E14, E34]          # PORMAKE edge blocks; [] = connect the nodes directly
  max_atoms: 400             # optional
  building_block_dir: blocks/   # optional: your own blocks in PORMAKE .xyz format
```

For each topology, every node type receives every listed node that has the same number of
connection points as the vertex, and all edge types receive the same edge. With the config
above: `pcu` has one 6-connected vertex, so it takes N16; `nbo` has one 4-connected vertex, so
it takes N409; each is combined with both edges, giving four candidates.

- A topology for which no listed node fits, and a node that fits no topology, are reported as
  notes (`mofsift doctor config.yaml` shows them before anything is built).
- An unknown topology or block name is an error, with the closest known names.
- `max_atoms` skips a candidate before it is built, from the sum of its building blocks.
- A build that fails is recorded (`generate_status: failed`, with the reason); the run goes on.
- A block in `building_block_dir` takes precedence over a bundled block of the same name, and
  its content is part of the record ID, so editing the file gives a new candidate.

### Linkers from SMILES

Linkers can be written as SMILES, alone or next to PORMAKE blocks:

```yaml
generate:
  source: pormake
  topologies: [pcu, tbo]
  nodes: [N16, N409]
  edges: [E14]                 # PORMAKE's own blocks still work
  linkers:
    smiles_file: linkers.smi   # one "SMILES name" per line; lines starting with # are ignored
    smiles:                    # and/or inline
      bdc: "OC(=O)c1ccc(cc1)C(=O)O"
      core: "[*]c1ccc([*])cc1"
```

Where a linker connects is decided in one of two ways.

- **Carboxylic acids.** Every `C(=O)OH` or `C(=O)[O-]` group is a connection point, and the
  group is removed: in PORMAKE's carboxylate nodes (N16, N409 and similar) the carboxylate
  carbon and oxygens belong to the node. Terephthalic acid therefore becomes a C6H4 block, the
  same content as PORMAKE's E14, and with N16 on `pcu` it gives the same C24H12O13Zn4 framework.
- **`[*]` atoms.** Each `[*]` marks a connection point. Use this for any other binding group or
  node type. When a SMILES contains `[*]`, acid groups are left alone.

The number of connection points decides the role: two make an **edge**, three or more an
**organic node** (trimesic acid on the 3-connected vertices of `tbo`, for example).

How the block is made: RDKit embeds the molecule in 3D (ETKDG with the config `seed`), MMFF94
optimises it (UFF when MMFF has no parameters), and the lowest-energy conformer of
`conformers` (default 10) is cut. Each connection point is placed `connection_distance`
(default 0.75 A, half a C-C bond, PORMAKE's own convention) from its anchor atom. The block is
written to `work/blocks/<name>.xyz` in PORMAKE's format, so you can inspect or reuse it.

- A linker name must not be the name of an existing block.
- A SMILES that cannot be used (invalid, fewer than two connection points, binding groups
  bonded to each other) is an error that names the linker; `mofsift doctor` shows it.
- A two-point linker whose connection directions are less than 150 degrees apart is built and
  reported as bent.
- The SMILES, the seed and the conversion settings are part of the record ID.

### `source: cifs`

```yaml
generate:
  source: cifs
  cif_dir: my_cifs/          # searched recursively
  max_atoms: 400             # optional
```

Every `.cif` file becomes one row. The file is copied unchanged to `work/structures/<id>.cif`.
A file that cannot be read is recorded as `failed`. Two files with the same content (line endings
aside) are kept once, and the duplicate is reported as a note.

### Record IDs

- Assembled candidates: `ms-` + 16 hex characters, a hash of the topology, the nodes, the edges
  and the content of any user-supplied block. It does not depend on the order of the config or on
  the machine.
- Structures from files: `msf-` + 16 hex characters, a hash of the file content.

### Columns written by generate

| Column | Meaning |
|---|---|
| `id` | Record ID (the key of the table) |
| `name` | Readable label: `pcu__N16__E14`, or the file name without extension |
| `source` | `pormake` or `cifs` |
| `topology`, `nodes`, `edges` | Building blocks (`+` between several); empty for `cifs` |
| `source_file` | Path of the original file inside `cif_dir`; empty for `pormake` |
| `n_atoms`, `formula` | Of the structure that was built or read (Hill order) |
| `expected_formula` | Sum of the building blocks; empty for `cifs` |
| `linker_smiles` | Canonical SMILES of the linkers (`;` between several) when they are known: given as SMILES, or the acid a metal-free PORMAKE block stands for. Empty when a metal-free node is joined to an edge, and for `cifs` |
| `node_rmsd` | Largest node-to-vertex misfit reported by PORMAKE, in angstrom; empty for `cifs` |
| `cif_path` | Path of the CIF relative to the work folder; empty when there is no structure |
| `generate_status` | `ok`, `skipped` (too large) or `failed` (build failed, file unreadable) |
| `generate_skip_reason` | Why, when the status is not `ok` |

## qc

Runs the structural checks on every row that has a structure. It never removes a row.

| Check | Fails when | Config key | Needs |
|---|---|---|---|
| Close pairs | any two atoms are closer than 0.70 A | `min_distance` | |
| Overlaps | two heavy atoms, or two H atoms, are closer than 1.00 A | `min_distance_heavy` | |
| Non-bonded contacts | two atoms that are not bonded in the building blocks are closer than 0.80 x the sum of their covalent radii | `nonbonded_factor` | bond list (PORMAKE) |
| Single network | bonds (covalent radii x 1.20) leave more than one connected component, or an isolated atom | `require_single_framework`, `bond_factor` | |
| Valence | an H atom does not have exactly one neighbour, or a C atom has fewer than 2 or more than 4 non-metal neighbours | `check_valence` | |
| Composition | the formula differs from the sum of the building blocks | `check_composition` | expected formula (PORMAKE) |
| Node misfit | `node_rmsd` is above 0.30 A | `max_node_rmsd` | PORMAKE |

A check whose input does not exist is not run and its column is empty. For structures read from
your own CIFs that is the case for the three checks marked PORMAKE.

Bonds much shorter than usual (for example C-C below 1.30 A) are counted in `qc_n_short_bonds`
and do not fail a structure.

### Columns written by qc

| Column | Meaning |
|---|---|
| `density` | g/cm3, from the cell and the atomic masses |
| `volume`, `cell_a` ... `cell_gamma` | Cell volume (A3), lengths (A) and angles (degrees) |
| `qc_min_distance` | Shortest interatomic distance, in angstrom |
| `qc_n_close_pairs`, `qc_n_overlaps`, `qc_n_nonbonded_contacts` | Counts for the three distance checks |
| `qc_n_components`, `qc_n_isolated_atoms` | Connectivity |
| `qc_n_bad_h`, `qc_n_bad_c` | Atoms that fail the valence check |
| `qc_n_short_bonds` | Compressed covalent bonds (reported only) |
| `qc_composition_ok`, `qc_node_rmsd_ok` | Result of the two PORMAKE-only checks |
| `qc_pass` | True when no enabled check failed; empty when the checks did not run |
| `qc_reasons` | The failed checks, separated by `; ` |
| `qc_status` | `ok` (the checks ran), `skipped` (no structure) or `failed` (structure unreadable) |
| `qc_skip_reason` | Why, when the status is not `ok` |

`qc_status` and `qc_pass` answer different questions: the first says whether the checks ran,
the second what they found.

## Entering at qc with your own table

`qc` needs only a table with an `id` column and a `cif_path` column (paths relative to the work
folder). Save it as `work/01_generate.csv` and run `mofsift qc config.yaml`.

## Reruns

`mofsift generate` reuses a structure when both its CIF and its `.build.json` exist in the work
folder; `--force` builds everything again. `mofsift qc` always recomputes (it takes milliseconds
per structure). In one software environment the same config gives byte-identical tables and CIFs
on every run.

Between environments the record IDs are always the same, but the coordinates need not be. The
topology does not fix how a linear linker is turned about its own axis, and PORMAKE's choice can
change with the versions of the numerical libraries it uses (we have seen a phenyl ring of the
same candidate turned differently under two sets of library versions, which changed the cavity
diameter by 0.5 A). So build the structures once and carry the `work/` folder to wherever the
later stages run, rather than generating again on each machine. `work/01_generate.meta.json`
records the versions of PORMAKE, jax, SciPy and NumPy that were used.

The checks and their default thresholds derive from `mofal.qc` in
[MOF_3D_Generator](https://github.com/humbertogn-coder/MOF_3D_Generator) (MIT).
