# Limitations

Read this before using any number produced with mofsift.

## Structures

- Every structure is hypothetical: assembled in silico from building blocks.
- Passing QC means the geometry passed geometric and compositional checks (no overlapping atoms,
  one connected framework, sensible H and C coordination, expected formula). It does not establish
  thermodynamic or mechanical stability, or that the framework can be synthesised.
- Assembled geometries can contain compressed bonds at node–linker junctions. Pore descriptors and
  xTB gaps computed on unrelaxed geometries change after relaxation.

## Electronic descriptors

- A band gap, from any method, is not electrical conductivity, carrier mobility or a transport
  mechanism.
- xTB gaps are proxies from a semi-empirical method. How well they follow DFT gaps depends on the
  chemistry. For open-shell transition-metal frameworks, which are often the low-gap candidates,
  treat them as unvalidated. Check the proxy against a reference method on your own chemical space
  before relying on it.
- A finite cluster cut from a framework is a different system. Keep `periodic: true`.
- Semi-local DFT (PBE) underestimates band gaps. A low PBE-level gap is a screening descriptor,
  not evidence of a low experimental gap.

## Synthetic-accessibility components

- There is no accepted single metric of MOF synthesisability. mofsift reports separate, named
  components (linker scores, precedent of the node, topology and metal–linker pair).
- The optional aggregate is a rank-based summary with weights declared in the config. It is an
  indicator, not a probability.
- Each component must be validated against experimentally reported MOFs before it is trusted; the
  validation benchmark ships with the scoring stage.

## Not included

- No DFT automation, no trained models, no generative models, no server-side computation.
