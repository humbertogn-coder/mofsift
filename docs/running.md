# Running mofsift: the commands

## Windows (Anaconda Prompt)

Once:

```
conda create -n mofsift python=3.12
conda activate mofsift
cd "path\to\mofsift"
pip install -e ".[generate,dev]"
mofsift doctor
```

PORMAKE on Windows has not been tested by us. If it does not install, use `pip install -e .`
and build the structures on the cluster.

Each time:

```
conda activate mofsift
cd "path\to\my_project"
mofsift init                      # first time only: writes mofsift.yaml
mofsift doctor mofsift.yaml
mofsift run mofsift.yaml
```

On Windows the pore-geometry and xTB stages are skipped (with a warning) unless their results are
already in the `work\` folder. To get them, run those stages on a cluster or under WSL and copy
the whole `work\` folder back; then `mofsift run mofsift.yaml` only assembles the tables.

## A Slurm cluster (commands for Grace, TAMU HPRC)

Once, on a login node:

```bash
module purge
module load GCCcore/13.2.0
module load Python/3.11.5
python -m venv /scratch/user/$USER/mofsift_env
source /scratch/user/$USER/mofsift_env/bin/activate
pip install --upgrade pip
cd /scratch/user/$USER
git clone https://github.com/humbertogn-coder/mofsift.git
pip install -e "mofsift[all]"
mofsift doctor
```

Each time (in the folder that holds the config):

```bash
module purge; module load GCCcore/13.2.0 Python/3.11.5
source /scratch/user/$USER/mofsift_env/bin/activate
mofsift generate mofsift.yaml && mofsift qc mofsift.yaml
sbatch --account=YOUR_ACCOUNT --export=ALL,CONFIG=mofsift.yaml --array=1-20 \
    /scratch/user/$USER/mofsift/examples/slurm/grace_describe_array.slurm
squeue -u $USER                   # wait until the array has finished
mofsift describe mofsift.yaml     # assembles the table from the stored results
mofsift score mofsift.yaml
```

To update the installation later: `cd /scratch/user/$USER/mofsift && git pull`.

Generate the structures in one place only (see the note on coordinates in
[generate-and-qc.md](generate-and-qc.md#reruns)). If you generate on the cluster, copy the whole
`work/` folder to the laptop afterwards, not the other way round.

## Sending changes to GitHub

```
cd "path\to\mofsift"
git status
git add -A
git commit -m "Describe what changed"
git push
```

`git status` should not list data files (`qmof.csv`, `work/`); they are ignored on purpose.
