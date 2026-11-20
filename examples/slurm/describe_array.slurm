#!/bin/bash
# Pore geometry and xTB for one block of the table. Submit as a job array:
#
#   mofsift generate config.yaml && mofsift qc config.yaml      # first, once
#   sbatch --array=1-20 examples/slurm/describe_array.slurm     # 20 blocks
#   mofsift describe config.yaml                                # last, once: writes the table
#
# A generic Slurm template. It has not been run on a specific cluster: set the lines marked
# EDIT for yours (account, partition, how Python is loaded).

#SBATCH --job-name=mofsift-describe
#SBATCH --time=04:00:00
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --output=slurm-%A_%a.out
##SBATCH --account=EDIT
##SBATCH --partition=EDIT

set -euo pipefail

CONFIG=${CONFIG:-config.yaml}                 # sbatch --export=ALL,CONFIG=my.yaml to change it

# EDIT: make mofsift available, for example
# module load Python/3.12        # see: module spider Python
source "$HOME/venvs/mofsift/bin/activate"

export OMP_NUM_THREADS=${SLURM_CPUS_PER_TASK:-1}
export OMP_STACKSIZE=4G                        # a large thread stack, as the xtb documentation advises
ulimit -s unlimited || true

mofsift describe "$CONFIG" --only geometry,xtb \
    --block "${SLURM_ARRAY_TASK_ID}/${SLURM_ARRAY_TASK_COUNT}"
