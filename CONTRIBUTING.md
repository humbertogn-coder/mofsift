# Contributing to mofsift

Thank you for looking. Questions, bug reports and improvements are welcome, from a typo in the
documentation to a new descriptor.

## Asking a question or reporting a problem

Open an issue: <https://github.com/humbertogn-coder/mofsift/issues>. First look at
[docs/troubleshooting.md](docs/troubleshooting.md); most surprises are a stage that was skipped
for a recorded reason.

A report we can act on has:

- the output of `mofsift doctor --json` (versions of mofsift, Python and the tools);
- the config, or the smallest one that shows the problem;
- the command, what it printed, and the lines of `work/run.log` for that run;
- what you expected instead.

Do not attach unpublished structures or results. A public example that shows the same problem is
enough.

## Setting up for development

Python 3.11 to 3.13.

```bash
git clone https://github.com/humbertogn-coder/mofsift
cd mofsift
pip install -e ".[dev]"            # core tier and the test tools; works on Windows too
pip install -e ".[dev,all]"        # plus PORMAKE, pyzeo and tblite (Linux, macOS, WSL)
```

## Tests and style

```bash
pytest -m "not external"           # core tier: no optional tool needed, under a minute
pytest -m external                 # tests that need PORMAKE, pyzeo or tblite; a few minutes
ruff check .
ruff format --check .              # "ruff format ." rewrites the files
```

CI runs the core tier on Linux (Python 3.11, 3.12, 3.13) and on Windows, the external tier on
Linux, the style checks, and a build of the package. A change needs tests: a test that fails
without the change and passes with it.

A test that needs an optional tool is marked `@pytest.mark.external` and must skip, not fail, when
the tool is absent.

## The rules of the code

They are what makes the output trustworthy, and a change that breaks one will not be merged.

- **A stage only adds columns** and writes `<stage>_status` and `<stage>_skip_reason`. Stages
  never drop rows; selecting rows is the filter step.
- **No silent fallbacks.** A value that was not computed is missing, never a default number. When
  a fallback is used on purpose (the Zeo++ settings), a column records which one.
- **An optional tool never crashes a run.** If it is missing the stage is skipped and the reason
  is recorded, unless the config makes the tool mandatory.
- **Deterministic.** The same config and seed give the same record IDs everywhere, and the same
  tables in the same software environment. No timestamps or machine paths in tables.
- **Unknown config keys are errors.**
- **Honest wording.** Structures are hypothetical; an xTB gap is a proxy; a band gap is not
  conductivity; no score is a probability of synthesis. This applies to column descriptions,
  messages and documentation.
- **No data that is not ours to publish**: no unpublished results, no third-party datasets, no
  VASP pseudopotentials or outputs, no trained models.

## Changing a config key or a column

Three things are generated from the code, and tests fail until they are regenerated:

```bash
python -m mofsift.config > src/mofsift/schemas/config.schema.json   # after changing the config model
python -m mofsift.export.schemas src/mofsift/schemas                # after changing a column or the manifest
python -m mofsift.docgen docs                                       # the reference pages docs/config.md and docs/columns.md
```

- A new **config key** needs a `description` in its `Field(...)`, a line in the template of
  `mofsift init` (`src/mofsift/templates.py`), and a mention in the guide of its stage.
- A new **column** needs its type registered by the stage and an entry in
  `src/mofsift/columns.py` (stage, unit, one-line meaning).
- A change a user can notice goes into `CHANGELOG.md` under "Unreleased".

## Documentation

The guides are in `docs/` ([index](docs/README.md)). Tests check that the commands, options and
config snippets they show exist, that the links resolve, and that the tutorial still produces
what it says. If you change an output that the tutorial quotes, run the tutorial test
(`pytest -m external -k tutorial`); its message tells you what to update.

## Pull requests

- One topic per pull request, with a description of what changes for a user.
- Keep the tests, `ruff check` and `ruff format --check` green.
- By contributing you agree that your contribution is distributed under the MIT licence of the
  project ([LICENSE](LICENSE)).

## Scope

mofsift screens: it builds or reads structures, checks them, describes them with fast methods
and exports a table. DFT automation, trained property models, generative models and server-side
computation are outside its scope for now ([docs/limitations.md](docs/limitations.md)). Open an
issue before starting a large change, so that the work is not wasted.
