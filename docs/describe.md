# The describe stage

Adds three groups of descriptors to the table. Each group has its own status columns, so you can
always tell whether a value is missing because it was skipped, because it failed, or because the
tool is not installed.

| Sub-stage | Needs | Status columns |
|---|---|---|
| `describe.composition` | nothing extra | `describe_composition_status`, `..._skip_reason` |
| `describe.geometry` | pyzeo, or the Zeo++ `network` binary | `describe_geometry_status`, `..._skip_reason` |
| `describe.xtb` | tblite | `describe_xtb_status`, `..._skip_reason` |

Pore geometry and xTB are the descriptors mofsift exists for. They are optional only in the sense
that a run without them does not crash: their columns stay empty, the reason is in the table and
in `work/run.log`, and the command prints a warning. See [install.md](install.md).

```bash
mofsift describe config.yaml                    # everything that can run
mofsift describe config.yaml --only xtb         # compute only this; stored results of the others are kept
mofsift describe config.yaml --block 3/20       # one block of twenty, for a cluster job array
mofsift describe config.yaml --force            # compute again
```

## Which structures are described

- Composition: every row with a formula or a structure.
- Geometry and xTB: by default only structures that **passed qc** (`describe.only_qc_passed: true`).
  The others are `skipped` with the reason `did not pass qc`. Set the key to `false` to run them
  anyway.
- xTB: structures with more than `describe.xtb.max_atoms` atoms (default 300) are skipped, never
  truncated.

## Results are stored per structure

Every geometry and xTB result is written to `work/describe/<sub-stage>/<id>.json` as soon as it
exists, together with the settings that produced it. A later call reuses it when the settings and
the structure file are unchanged. Consequences:

- An interrupted run continues where it stopped.
- A failure is stored too, with its reason, and is not retried until `--force`.
- Changing a setting (probe radius, method, ...) recomputes that sub-stage only.
- The table can be assembled on a machine that does not have the tools: compute on a cluster,
  copy the `work/` folder to a laptop, run `mofsift describe` there. Copy the whole folder,
  structures included: a result is tied to the exact structure file it was computed from.

Each structure is computed in its own process. If Zeo++ or xTB ends that process (Zeo++ does this
on structures it cannot decompose; a large xTB run can run out of memory), the row is `failed`
with the reason and the run goes on.

## On a cluster

```bash
mofsift generate config.yaml && mofsift qc config.yaml        # fast; on the login node or locally
sbatch --array=1-20 examples/slurm/describe_array.slurm       # each task: mofsift describe --block $i/20
mofsift describe config.yaml                                  # afterwards: writes work/03_describe.csv
```

`--block i/n` computes every n-th structure starting at the i-th and writes only the per-structure
files, never the table, so tasks cannot overwrite each other. See
[`examples/slurm/describe_array.slurm`](../examples/slurm/describe_array.slurm).

## Columns

### Composition

| Column | Meaning |
|---|---|
| `primary_metal` | Most abundant metal (ties: alphabetical); empty without metals |
| `metals` | All metals present, `+` between them |
| `n_metal_atoms`, `metal_fraction` | Count, and fraction of all atoms |
| `n_transition_metal_atoms` | d-block atoms (Sc-Zn, Y-Cd, Hf-Hg) |
| `has_open_shell_3d_metal` | True when the primary metal is Ti-Cu. A flag on the composition, not a computed spin state |
| `c_fraction`, `h_fraction`, `n_fraction`, `o_fraction` | Fraction of all atoms |
| `heteroatom_fraction` | N, O, S, F, Cl, Br, I together |
| `mean_atomic_number`, `std_atomic_number` | Over all atoms |
| `mean_electronegativity`, `std_electronegativity` | Pauling, over all atoms; empty if an element has no Pauling value |

A metal is any element except H, He, B, C, N, O, F, Ne, Si, P, S, Cl, Ar, As, Se, Br, Kr, Te, I, Xe,
At and Rn.

### Pore geometry (Zeo++)

| Column | Meaning |
|---|---|
| `zeo_high_accuracy` | Zeo++ accuracy setting that produced this row: `OCC`, `DEF` or `off` |
| `lcd` | Largest cavity diameter: largest included sphere, in angstrom |
| `pld` | Pore limiting diameter: largest free sphere, in angstrom |
| `lcd_free_path` | Largest included sphere along the free-sphere path, in angstrom |
| `accessible_surface_m2_g`, `accessible_surface_m2_cm3` | Surface accessible to the probe |
| `accessible_volume_fraction`, `accessible_volume_cm3_g` | Volume accessible to the centre of the probe |

Settings: `probe_radius` (default 1.86 A, used for the channel and the probe), `high_accuracy`
(`OCC`, `DEF` or `false`: the setting of Zeo++'s `-ha` option), 2000 Monte Carlo samples per atom
for the surface and 50000 per cell for the volume. Zeo++ reads a P1 copy of the structure written
by ASE, so it sees the same atoms as the other stages. The numbers are reproducible: Zeo++ uses a
fixed random sequence.

`high_accuracy: DEF` is the most thorough setting and can take minutes for a few hundred atoms;
`OCC` is roughly ten times faster. With `OCC` Zeo++ ends on some structures. Such a structure is
tried again with the settings listed in `fallback` (default `[DEF, false]`, in that order), and
`zeo_high_accuracy` records which setting produced the numbers of each row, so rows computed
differently can be told apart or filtered (`zeo_high_accuracy == "OCC"`). If every setting fails
the row is `failed` and the reason names what was tried. `fallback: []` turns the retry off.

### xTB (tblite)

| Column | Meaning |
|---|---|
| `xtb_gap` | `xtb_lumo` minus `xtb_homo`, in eV |
| `xtb_homo` | Highest doubly occupied level, in eV |
| `xtb_lumo` | Lowest level that is not doubly occupied, in eV |
| `xtb_unpaired_electrons` | Number of unpaired electrons used in the calculation |

What the calculation is: one periodic single point at the Gamma point of the cell as given
(tblite has no k-point sampling), `method` GFN1-xTB or GFN2-xTB, electronic temperature 500 K and
at most 500 SCF iterations by default, spin-restricted with a fixed number of unpaired electrons.

- `spin: high_spin` (default) sets that number by a rule of thumb: 5, 4, 3, 2, 1 per Mn, Fe, Co,
  Ni, Cu atom (the high-spin count of M(II)), adjusted by one if the parity of the electron count
  requires it. It is not a computed spin state. `spin: low_spin` uses 0, or 1 for an odd count.
- **When `xtb_unpaired_electrons` is 0**, `xtb_gap` is the usual HOMO-LUMO gap.
- **When it is not 0**, the lowest level that is not doubly occupied is a singly occupied one, so
  `xtb_gap` is the distance from the doubly occupied levels to the singly occupied levels. It is
  small by construction and is not a band gap in the usual sense. Filter on
  `xtb_unpaired_electrons == 0` if you need comparable gaps.
- The structure is used as generated, without relaxation.

See also [limitations.md](limitations.md).
