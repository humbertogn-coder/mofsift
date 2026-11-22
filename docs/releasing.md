# Releasing mofsift

For maintainers. The version on `main` is a development version (`X.Y.Z.dev0`) between releases.

## Before the first release (0.1.0)

These have not been done yet and should be, because the tests cannot cover them:

- [ ] One real run on a cluster, with the job array of [describe.md](describe.md#on-a-cluster),
      and one under WSL.
- [ ] The `zeopp` backend of the geometry stage run against the real Zeo++ binary. Until then
      keep the sentence in [install.md](install.md) that says it was tested against a stand-in.
- [ ] PORMAKE installed and run natively on Windows, or the sentence in [install.md](install.md)
      that says it was not tested there kept as it is.

## Steps

1. **Choose the version.** Semantic versioning. Before 1.0 a minor version (0.2.0) may change
   the config, the columns or the bundle; say so in the changelog. A change of the bundle that an
   older reader cannot ignore needs a new major `schema_version` in
   `src/mofsift/export/bundle.py`, because viewers check it.

2. **Start from a clean `main` with green CI.**

   ```bash
   git status                      # nothing to commit
   pytest -m "not external"        # core tier
   pytest -m external              # needs PORMAKE, pyzeo and tblite; runs the tutorial
   ruff check . && ruff format --check .
   ```

3. **Regenerate what is generated and confirm that nothing changes.**

   ```bash
   python -m mofsift.config > src/mofsift/schemas/config.schema.json
   python -m mofsift.export.schemas src/mofsift/schemas
   python -m mofsift.docgen docs
   git status                      # still nothing to commit
   ```

4. **Set the version in the two places that hold it** (a test checks that they agree):

   - `src/mofsift/__init__.py`: `__version__ = "0.1.0"`
   - `CITATION.cff`: `version: 0.1.0`, and add `date-released: YYYY-MM-DD`

   In `CHANGELOG.md`, rename `## [Unreleased]` to `## [0.1.0] - YYYY-MM-DD` and put a new, empty
   `## [Unreleased]` above it. In `README.md`, update the status note.

5. **Build and check the packages.**

   ```bash
   pip install build twine
   rm -rf dist && python -m build          # dist/mofsift-0.1.0.tar.gz and a .whl
   python -m twine check dist/*
   python -m venv /tmp/release-env
   /tmp/release-env/bin/pip install dist/*.whl
   cd /tmp && /tmp/release-env/bin/mofsift --version && /tmp/release-env/bin/mofsift doctor
   ```

   The version printed must be the new one, and `doctor` must find the core tools.

6. **Commit, tag and push.**

   ```bash
   git commit -am "Release 0.1.0"
   git tag -a v0.1.0 -m "mofsift 0.1.0"
   git push && git push origin v0.1.0
   ```

7. **Create the release on GitHub** from the tag (Releases, "Draft a new release"). Paste the
   section of the changelog and attach the two files of `dist/`.

8. **Go back to a development version**: `0.2.0.dev0` in the two places of step 4, commit, push.

## Optional, each to be decided once

**A DOI from Zenodo.** Connect the repository in Zenodo *before* creating the GitHub release;
Zenodo then archives every release and gives it a DOI. Afterwards add the DOI to `CITATION.cff`
(`doi:`) and to the README. Never write a DOI that does not exist yet.

**PyPI**, so that `pip install mofsift` works. A file uploaded to PyPI can never be replaced: a
mistake needs a new version number. The first time you need an account with two-factor
authentication and an API token; then

```bash
python -m twine upload dist/*
```

After that, change `pip install -e ".[all]"` in the README and in [install.md](install.md) to
`pip install "mofsift[all]"` for users, and keep the editable install for developers.

## What a release must not contain

Checked by `mofsift validate` for bundles and by `.gitignore` for the repository; look anyway.

- Unpublished results, structures or predictions of a study.
- `qmof.csv` or any other third-party dataset. The package ships counts derived from QMOF, with
  its licence and citation, not the data.
- VASP files (`POTCAR`, `WAVECAR`, `CHGCAR`), trained models, credentials, account names of a
  cluster.
