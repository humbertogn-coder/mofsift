# Changelog

All notable changes are listed here. The format follows Keep a Changelog; versions follow SemVer.

## [Unreleased]

### Added
- Package skeleton with `src/` layout, MIT licence and three-author metadata.
- Config model (pydantic) with YAML loading, strict validation (unknown keys are errors, with
  suggestions), deterministic config hash and a published JSON Schema.
- Safe filter-expression validation (no function calls, attributes or subscripts).
- Stable record IDs for assembled candidates and for structures read from files.
- Stage contract helpers: status and skip-reason columns, and a checker that a stage only adds columns.
- Tool detection adapters for PORMAKE, pyzeo, tblite, scikit-learn and the Zeo++ and xtb binaries.
- Stage planning shared by `doctor` and the future runner.
- CLI: `mofsift init`, `mofsift doctor [CONFIG] [--json]`, `mofsift --version`.
- CI workflow: ruff, tests on Python 3.11–3.13 (Linux) and the core tier on Windows, wheel build and clean-install smoke test.
