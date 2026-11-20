# The score stage

`mofsift score config.yaml` adds synthetic-accessibility **components** to the table. Each is a
separate, named column with its own meaning and units. There is no single score by default, and
none of the components is a probability that a MOF can be made.

| Column | What it is | Better when |
|---|---|---|
| `linker_sa` | SA score of the linker (Ertl and Schuffenhauer 2009, RDKit implementation), 1 to 10 | smaller |
| `linker_sc` | SCScore of the linker (Coley et al. 2018), 1 to 5 | smaller |
| `linker_size` | Heavy atoms of the linker | smaller |
| `linker_rotatable_bonds` | Rotatable bonds of the linker | smaller |
| `precedent_topology` | Distinct synthesised MOFs in the reference with this topology | larger |
| `precedent_node` | ... whose primary metal is this candidate's primary metal | (does not separate, see below) |
| `precedent_linker` | ... that contain this linker | larger |
| `precedent_pair` | ... that contain this linker together with this metal | larger |
| `score_notes` | Why a component is empty for this row | |
| `score_status`, `score_skip_reason` | `ok` when at least one component was computed | |

Choose the components in `score.components`; only those columns are written.

## Where the inputs come from

- **Linker**: the `linker_smiles` column written by `generate`. It holds the linker when it is
  known: a linker you gave as SMILES, or the acid that one of PORMAKE's metal-free blocks stands
  for (a connection point on a carbon atom is read as a carboxylic acid group, one on nitrogen as
  a donor atom). It is empty when a metal-free node is joined to an edge, because the two then
  form one larger linker that mofsift does not derive, and for structures read from CIF files.
  With several linkers the worst value counts: the largest SA, SC, size and rotatable-bond count,
  the smallest precedent count.
- **Metal**: `primary_metal` from the describe stage (or from the formula).
- **Topology**: the `topology` column (empty for structures read from CIF files).
- A linker written with `[*]` is scored with hydrogen in place of each `[*]`, and it has no
  precedent unless the reference contains exactly that SMILES.

A component that cannot be computed is empty, and `score_notes` says why.

## The precedent reference

Counts come from the QMOF database (Rosen et al., Matter 2021, 4, 1578; npj Comput. Mater. 2022,
8, 195; data under CC BY 4.0): the 16,881 structures flagged as synthesised, of which 13,683 have a
MOFid decomposition into nodes, linkers and (for 3,912 distinct MOFs) a topology. Identical MOFs
(same primary metal, linkers and topology) are counted once, which leaves 10,595 distinct MOFs,
4,115 linkers and 8,600 metal-linker pairs. Linkers are compared as neutral molecules, so an acid
and its anion are the same linker.

The reference is a small file in the package (`src/mofsift/score/data/qmof_precedent.json.gz`,
derived counts only). `scripts/build_precedent_reference.py qmof.csv` rebuilds it.

Read the counts with this in mind:

- A count of 0 means "not in this reference", not "never made". QMOF covers a part of the CSD,
  and MOFid assigns a topology to only about a third of it.
- `precedent_node` is evaluated at the level of the metal element, not of the whole cluster
  (Zn4O and a zinc paddlewheel both count as Zn).

## Validation

How well each component ranks synthesised MOFs above hypothetical ones is measured in
[score-validation.md](score-validation.md). In short: the linker-based components separate the
two sets to a moderate degree and `precedent_linker` separates them best; `precedent_node` does
not separate them and therefore **cannot be given a weight** in the aggregate.

## Optional aggregate

```yaml
score:
  aggregate:
    enabled: true
    weights: {linker_sc: 1, precedent_linker: 1}
```

`score_aggregate` is the weighted mean of each weighted component's rank within the table, from 0
(worst) to 1 (best). It is a **synthetic accessibility indicator** for ordering the candidates of
one table: it depends on which candidates are in the table, means nothing across tables, and is
not a probability. It is empty for a row that lacks any weighted component. The weights are
echoed in `work/04_score.meta.json`.
