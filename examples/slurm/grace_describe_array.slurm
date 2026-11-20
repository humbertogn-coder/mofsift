#!/bin/bash
# Pore geometry and xTB on Grace (TAMU HPRC), one block of the table per array task.
#
#   mofsift generate config.yaml && mofsift qc config.yaml                 # first, once (login node)
#   sbatch --account=YOUR_ACCOUNT --array=1-20 examples/slurm/grace_describe_array.slurm
#   mofsift describe config.yaml                                           # last, once: writes the table
#
# The account is given on the command line so that it is not stored in the repository.
# Written from the cluster's documented conventions; not yet run on Grace by the authors.

#SBATCH --job-name=mofsift-describe
#SBATCH --partition=medium            # up to 24 h
#SBATCH --time=24:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8             # threads for xTB; Zeo++ uses one
#SBATCH --mem=32G
#SBATCH --output=mofsift-describe-%A_%a.out

set -euo pipefail

CONFIG=${CONFIG:-config.yaml}                              # sbatch --export=ALL,CONFIG=other.yaml
MOFSIFT_ENV=${MOFSIFT_ENV:-/scratch/user/$USER/mofsift_env}

module purge
module load GCCcore/13.2.0
module load Python/3.11.5
source "$MOFSIFT_ENV/bin/activate"

export OMP_NUM_THREADS=${SLURM_CPUS_PER_TASK:-1}
export OMP_STACKSIZE=4G               # a large thread stack, as the xtb documentation advises
ulimit -s unlimited || true

cd "$SLURM_SUBMIT_DIR"
mofsift describe "$CONFIG" --only geometry,xtb \
    --block "${SLURM_ARRAY_TASK_ID}/${SLURM_ARRAY_TASK_COUNT}"
