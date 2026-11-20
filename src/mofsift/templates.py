"""Text of the example config written by ``mofsift init``."""

EXAMPLE_CONFIG = """\
# mofsift example config. Every key is validated; unknown keys are an error.
# Paths are relative to the folder that holds this file.

project: demo_irmof          # short name: letters, digits, '_', '-' or '.'
seed: 42                     # same config + same seed = same table and same record IDs
workdir: work                # intermediate tables and structures

generate:
  source: pormake            # pormake = assemble structures | cifs = read existing CIF files
  topologies: [pcu]          # PORMAKE topology names
  nodes: [N16]               # PORMAKE node building blocks (N16 = Zn4O(CO2)6, the IRMOF node)
  edges: [E14, E34]          # PORMAKE edge building blocks (E14 -> BDC, E34 -> BPDC);
                             # [] = connect the nodes directly (for example tbo with N10 and N409)
  # linkers:                 # linkers given as SMILES, used next to the PORMAKE blocks above
  #   smiles:                # 2 connection points -> edge; 3 or more -> organic node
  #     bdc: "OC(=O)c1ccc(cc1)C(=O)O"     # carboxylic acids are found automatically
  #     core: "[*]c1ccc([*])cc1"          # or mark the connection points with [*]
  #   smiles_file: linkers.smi            # one "SMILES name" per line
  max_atoms: 400             # skip larger structures; null = no limit
  # building_block_dir: my_blocks/   # extra blocks in PORMAKE .xyz format
  # cif_dir: my_cifs/                # required when source: cifs

qc:                          # built-in structural checks; distances in angstrom
  min_distance: 0.70         # no pair of atoms closer than this
  min_distance_heavy: 1.00   # no heavy-heavy or H-H pair closer than this
  nonbonded_factor: 0.80     # non-bonded contacts > factor x (sum of covalent radii)
  bond_factor: 1.20          # multiplier on covalent radii to detect bonds
  require_single_framework: true
  check_valence: true
  check_composition: true    # formula = sum of the building blocks (PORMAKE structures only)
  max_node_rmsd: 0.30        # node-to-vertex misfit reported by PORMAKE; null = do not check

describe:
  only_qc_passed: true       # pore geometry and xTB only for structures that passed qc
  composition: true          # composition statistics (always available)
  geometry:                  # pore diameters, accessible surface and volume from Zeo++
    enabled: auto            # auto = run if the tool is installed, else skip and say so
    tool: pyzeo              # pyzeo (pip) | zeopp (the 'network' binary)
    probe_radius: 1.86       # angstrom
    high_accuracy: OCC       # Zeo++ -ha setting: OCC | DEF (much slower) | false
    timeout_minutes: null    # per structure
    on_missing: skip         # used when enabled: true and the tool is missing: skip | error
  xtb:                       # gap and frontier levels from periodic GFN-xTB (tblite)
    enabled: auto
    method: GFN1-xTB         # GFN1-xTB | GFN2-xTB
    periodic: true           # keep true for frameworks
    max_atoms: 300           # larger structures are skipped, not truncated
    spin: high_spin          # high_spin (rule of thumb for Mn-Cu) | low_spin
    electronic_temperature: 500.0   # kelvin
    max_iterations: 500
    timeout_minutes: null    # per structure
    on_missing: skip

score:                       # synthetic-accessibility components, reported separately
  components: [linker_sa, precedent_node, precedent_topology, precedent_pair]
  strain: {enabled: false}   # not available in this version
  aggregate:                 # optional rank-based summary; it is not a probability
    enabled: false
    weights: {}

filter:                      # explicit row filters; rejected rows stay visible in the full table
  - "qc_pass == True"

export:
  viewer_bundle: true
  include_cifs: true
  table_format: csv          # csv | parquet

# tools:                     # explicit paths to external binaries (default: look on PATH)
#   zeopp: /path/to/network
#   xtb: /path/to/xtb
"""
