# Installing mofsift and its tools

mofsift needs Python 3.11, 3.12 or 3.13.

| What | Package | Linux / macOS | Windows |
|---|---|---|---|
| Core (config, QC, SMILES linkers, composition) | `mofsift` | pip | pip |
| Structure assembly | PORMAKE (`mofsift[generate]`) | pip | pip (not tested by us on Windows) |
| Pore geometry | pyzeo (`mofsift[zeo]`) | pip | no Windows build: use WSL or a cluster |
| xTB | tblite (`mofsift[xtb]`) | pip | no Windows build on PyPI: use WSL or a cluster |

`mofsift doctor` shows what is installed and, with a config, which stages will run.

## Linux, macOS, WSL

```bash
python -m venv ~/venvs/mofsift && source ~/venvs/mofsift/bin/activate
pip install -e ".[all]"        # from the repository folder
mofsift doctor
```

With conda: `conda env create -f environment.yml`.

## Windows

Two ways, which can be combined.

**Natively**, for everything except pore geometry and xTB:

```
conda create -n mofsift python=3.12
conda activate mofsift
pip install -e ".[generate]"
mofsift doctor
```

Runs on Windows skip `describe.geometry` and `describe.xtb` and say so. They still use results
that were computed elsewhere: copy the `work/` folder from the cluster and run
`mofsift describe config.yaml`.

**Under WSL** (Windows Subsystem for Linux), for the complete pipeline on the same computer: in
an administrator PowerShell run `wsl --install`, restart, open "Ubuntu", then follow the Linux
steps above inside it (install Python first if needed: `sudo apt install python3-venv`). Your
Windows files are under `/mnt/c/...`. WSL is a regular Linux system; we have not run mofsift in
it ourselves.

## A Slurm cluster

1. Load a Python module (3.11 to 3.13) or use conda; `module spider Python` lists what the cluster
   has.
2. Create an environment in your home or scratch space and install as on Linux:
   `pip install -e ".[all]"`.
3. Run `mofsift doctor config.yaml` on a login node: all stages should say `run`.
4. Generate and check on the login node (seconds to minutes), then submit the slow part as a job
   array: see [describe.md](describe.md#on-a-cluster) and
   [`examples/slurm/describe_array.slurm`](../examples/slurm/describe_array.slurm).

tblite uses OpenMP threads: give each task a few cores and set `OMP_NUM_THREADS` to that number.
Zeo++ uses one core.

### Grace (TAMU HPRC)

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

`mofsift doctor` must list pormake, pyzeo and tblite as `ok`. The pyzeo and tblite wheels need
glibc 2.28 or newer; if pip reports that no matching distribution exists, the system is older
than that and the two tools have to come from conda instead.

Each time:

```bash
cd /scratch/user/$USER/my_project            # the folder with config.yaml
module purge; module load GCCcore/13.2.0 Python/3.11.5
source /scratch/user/$USER/mofsift_env/bin/activate
mofsift generate config.yaml && mofsift qc config.yaml
sbatch --account=YOUR_ACCOUNT --array=1-20 /scratch/user/$USER/mofsift/examples/slurm/grace_describe_array.slurm
# when the array has finished:
mofsift describe config.yaml
```

The template asks for the `medium` partition (24 h), one task with 8 cores and 32 GB. Choose the
number of blocks so that one block finishes well inside the time limit: the output of each task
shows how long each structure took. A task that is cut off loses nothing that it finished;
submit the same array again and only the missing structures are computed.

## The Zeo++ binary instead of pyzeo

Set `describe.geometry.tool: zeopp` to use the `network` executable
(`conda install -c conda-forge zeopp-lsmo`); give its path under `tools.zeopp` if it is not on
`PATH`. This backend is tested against a stand-in executable only, not yet against the real
binary.
