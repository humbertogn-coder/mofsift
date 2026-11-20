# Limitations

Read this before using any number produced with mofsift.

## Structures

- Every structure is hypothetical: assembled in silico from building blocks, or supplied by you.
- Passing QC means the geometry passed geometric and compositional checks (no overlapping atoms,
  one connected network, sensible H and C coordination, expected formula, node that fits its
  vertex). It does not establish thermodynamic or mechanical stability, or that the framework can
  be synthesised.
- The checks do not test that the network is periodic in three dimensions: a molecule in a box is
  one connected component and passes.
- Assembled geometries are not relaxed and can contain compressed bonds at node-linker junctions
  (counted in `qc_n_short_bonds`). Pore descriptors and xTB gaps computed on unrelaxed geometries
  change after relaxation.
- Bonds are detected from covalent radii. Unusual coordination (bridging hydrides, agostic
  contacts, disordered sites in experimental CIFs) can fail the valence check without being wrong;
  the thresholds and the individual checks can be switched in the `qc` section.

## Generation

- Building blocks are PORMAKE blocks (bundled, or your own in PORMAKE's `.xyz` format) and
  linkers converted from SMILES.
- A linker from SMILES is one conformer: the lowest in MMFF94 energy of a small set embedded by
  RDKit. For a rigid aromatic linker that is the geometry; for a flexible one it is one choice
  among many, and bent linkers are flagged but still built.
- Carboxylic acid groups are removed from a SMILES linker because PORMAKE's carboxylate nodes
  already contain the carboxylate. With a node that does not, the framework would lack those
  atoms: mark the connection points with `[*]` instead. mofsift cannot tell which kind of node a
  block is.
- The coordinates RDKit produces can differ in the last digits between RDKit versions and
  platforms. Record IDs do not: they depend on the SMILES, the seed and the conversion settings.
- `linker_smiles` is known only for linkers given as SMILES, not for PORMAKE's bundled edges.
- Every edge type of a topology receives the same edge block. Mixed-linker frameworks cannot be
  declared yet.
- The orientation of a linear linker about its own axis is not determined by the topology. PORMAKE
  picks one, and the pick can differ between versions of its numerical libraries, so the same
  record ID can have different coordinates on two machines. Generate once and reuse the files.
- A node is only placed on a vertex with the same number of connection points. The shape can
  still be wrong (a square node on a tetrahedral vertex); that is what `node_rmsd` and
  `qc.max_node_rmsd` are for.

## Pore geometry

- Zeo++ treats the framework as rigid hard spheres. The numbers describe the generated geometry,
  which is not relaxed.
- Accessible surface and volume depend on the probe radius; compare values only at the same one.
- With `high_accuracy: OCC` Zeo++ ends on some structures. They are computed with the next setting
  in `fallback`, so one table can contain rows from different accuracy settings; the column
  `zeo_high_accuracy` says which. The settings can differ by a few tenths of an angstrom.

## Electronic descriptors

- A band gap, from any method, is not electrical conductivity, carrier mobility or a transport
  mechanism.
- xTB gaps are proxies from a semi-empirical method. How well they follow DFT gaps depends on the
  chemistry. For open-shell transition-metal frameworks, which are often the low-gap candidates,
  treat them as unvalidated. Check the proxy against a reference method on your own chemical space
  before relying on it.
- The xTB calculation is spin-restricted with a fixed number of unpaired electrons, chosen by a
  rule of thumb for Mn, Fe, Co, Ni and Cu. For a structure with unpaired electrons `xtb_gap` is
  the distance from the doubly occupied to the singly occupied levels, not a band gap in the usual
  sense (see [describe.md](describe.md#xtb-tblite)).
- Only the Gamma point of the given cell is sampled. For small cells that is a coarse sampling.
- A finite cluster cut from a framework is a different system. Keep `periodic: true`.
- Semi-local DFT (PBE) underestimates band gaps. A low PBE-level gap is a screening descriptor,
  not evidence of a low experimental gap.

## Synthetic-accessibility components

- There is no accepted single metric of MOF synthesisability. mofsift reports separate, named
  components; none is a probability.
- The SA score and SCScore were developed for drug-like organic molecules and are applied here to
  linkers only. Nodes, the assembly of the framework and the synthesis conditions are not scored.
- Precedent counts come from one database (QMOF, a part of the CSD). Zero means "not in this
  reference". The node is compared at the level of the metal element only.
- The validation measures resemblance to MOFs that have been made, against hypothetical sets that
  are not known to be unmakeable. Its outcome changes with the hypothetical set
  ([score-validation.md](score-validation.md)).
- The optional aggregate is a rank within one table with weights you choose. It orders
  candidates; it is not validated and not comparable across tables.
- The linker of a candidate is known only when it is given as SMILES or is one of PORMAKE's
  metal-free blocks used on its own; otherwise the linker components are empty.

## Not included

- No DFT automation, no trained models, no generative models, no server-side computation.
