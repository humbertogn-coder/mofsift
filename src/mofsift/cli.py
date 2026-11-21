"""Command-line interface.

``init`` and ``doctor``; one command per stage (``generate``, ``qc``, ``describe``, ``score``,
``filter``, ``export``) and ``run`` for all of them; ``validate`` and ``merge`` for bundles.

Exit codes: 0 = done; 1 = a stage could not run, or a bundle is not valid; 2 = the config
is invalid or names something that does not exist.
"""

from __future__ import annotations

import json
import platform
import sys
from pathlib import Path

import typer

from mofsift import __version__
from mofsift.config import Config, ConfigError, load_config, valid_project_name
from mofsift.describe import SUBSTAGES, parse_block, parse_only
from mofsift.export import BundleError, MergeError, merge_bundles, validate_bundle
from mofsift.generate import GenerateError
from mofsift.generate import preview as preview_generate
from mofsift.generate.pormake_adapter import PormakeMissingError
from mofsift.plan import StagePlan, plan_stages
from mofsift.runner import (
    ExportOutcome,
    InvalidFilterError,
    Run,
    RunnerError,
    StageOutcome,
    run_describe,
    run_export,
    run_filter,
    run_generate,
    run_qc,
    run_score,
)
from mofsift.templates import EXAMPLE_CONFIG
from mofsift.tools import ToolStatus, default_tools

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Screen hypothetical MOFs for electronic properties: generate, check, describe, score, export.",
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"mofsift {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show the version and exit."
    ),
) -> None:
    """mofsift command-line interface."""


@app.command()
def init(
    path: Path = typer.Argument(Path("mofsift.yaml"), help="Where to write the example config."),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
) -> None:
    """Write a commented example config."""
    if path.exists() and not force:
        typer.echo(f"{path} already exists; use --force to overwrite it.", err=True)
        raise typer.Exit(code=1)
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True)
    path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    typer.echo(f"Wrote {path}")
    typer.echo(f"Next: edit it, then check it with:  mofsift doctor {path}")


def _table(rows: list[list[str]], header: list[str]) -> list[str]:
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]
    fmt = "  " + "  ".join(f"{{:<{w}}}" for w in widths)
    return [fmt.format(*header).rstrip(), *(fmt.format(*r).rstrip() for r in rows)]


def _tool_lines(statuses: list[ToolStatus]) -> list[str]:
    rows = [
        [s.name, s.tier, "ok" if s.available else "missing", s.version or "-", s.used_by] for s in statuses
    ]
    lines = _table(rows, ["tool", "tier", "status", "version", "used by"])
    notes = []
    for s in statuses:
        if not s.available:
            why = (
                f" [{s.detail}]"
                if s.detail and s.detail not in ("not installed", "not found on PATH")
                else ""
            )
            notes.append(f"  {s.name}:{why} {s.hint}")
    if notes:
        lines += ["", "To install what is missing:", *notes]
    return lines


@app.command()
def doctor(
    config: Path | None = typer.Argument(
        None, help="Optional config file: validate it and show which stages would run or be skipped."
    ),
    as_json: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Report which tools are installed and, for a config, which stages can run."""
    cfg = None
    config_error = None
    if config is not None:
        try:
            cfg = load_config(config)
        except ConfigError as err:
            config_error = str(err)

    tools = default_tools(cfg.tools if cfg else None)
    statuses = [t.status() for t in tools]
    core_ok = all(s.available for s in statuses if s.tier == "core")
    plans = plan_stages(cfg, {s.name: s.available for s in statuses}) if cfg else []
    notes: list[str] = []
    if cfg is not None and config is not None:
        plans, notes = _preflight_generate(cfg, config, plans)
    plan_ok = not any(p.action == "error" for p in plans)

    if as_json:
        payload = {
            "mofsift": __version__,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "tools": [s.as_dict() for s in statuses],
            "config": None
            if config is None
            else {
                "path": str(config),
                "valid": cfg is not None,
                "error": config_error,
                "project": cfg.project if cfg else None,
                "config_hash": cfg.config_hash() if cfg else None,
                "stages": [p.as_dict() for p in plans],
                "notes": notes,
            },
        }
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(f"mofsift {__version__} | Python {platform.python_version()} | {platform.platform()}")
        typer.echo("")
        for line in _tool_lines(statuses):
            typer.echo(line)
        if config is not None:
            typer.echo("")
            if cfg is None:
                typer.echo(config_error, err=True)
            else:
                typer.echo(f"Config {config}: valid (project {cfg.project}, hash {cfg.config_hash()[:12]})")
                rows = [[p.stage, p.action, p.reason or ""] for p in plans]
                for line in _table(rows, ["stage", "plan", "note"]):
                    typer.echo(line)
                for note in notes:
                    typer.echo(f"  note: {note}")
        if not core_ok:
            typer.echo("\nA core dependency is missing or broken; reinstall mofsift.", err=True)

    if config_error is not None:
        raise typer.Exit(code=2)
    if not core_ok or not plan_ok:
        raise typer.Exit(code=1)


def _preflight_generate(
    cfg: Config, config: Path, plans: list[StagePlan]
) -> tuple[list[StagePlan], list[str]]:
    """Check the names in the generate section and count the candidates, without building."""
    out, notes = [], []
    for plan in plans:
        if plan.stage == "generate" and plan.action == "run":
            try:
                n, notes = preview_generate(cfg, config.resolve().parent)
                what = "CIF file(s)" if cfg.generate.source == "cifs" else "candidate(s)"
                plan = StagePlan("generate", "run", f"{n} {what}")
            except (GenerateError, PormakeMissingError) as err:
                plan = StagePlan("generate", "error", "; ".join(str(err).splitlines()))
        out.append(plan)
    return out, notes


def _load_run(config: Path) -> Run:
    try:
        return Run.load(config)
    except ConfigError as err:
        typer.echo(str(err), err=True)
        raise typer.Exit(code=2) from err


def _progress(quiet: bool):
    if quiet:
        return None

    def show(pos: int, total: int, name: str, status: str, reason: str | None) -> None:
        typer.echo(f"  [{pos}/{total}] {name}: {status}" + (f" ({reason})" if reason else ""))

    return show


def _report(outcome: StageOutcome) -> None:
    c = outcome.counts
    line = (
        f"{outcome.stage}: {len(outcome.table)} row(s): "
        f"{c['ok']} ok, {c['skipped']} skipped, {c['failed']} failed"
    )
    if outcome.stage == "generate" and outcome.details.get("reused"):
        line += f" ({outcome.details['reused']} reused from an earlier run)"
    if outcome.stage == "qc":
        line += f"; {outcome.details['passed']} passed the checks, {outcome.details['rejected']} did not"
    typer.echo(line)
    for note in outcome.notes:
        typer.echo(f"  note: {note}")
    typer.echo(f"  table: {outcome.path}")


def _do_generate(run_: Run, force: bool, quiet: bool) -> StageOutcome:
    cfg = run_.cfg
    if cfg.generate.source == "pormake":
        tool = next(t for t in default_tools(cfg.tools) if t.name == "pormake")
        ok, detail = tool.check()
        if not ok:
            typer.echo(
                f"generate cannot run: pormake is {detail or 'not available'}. Install it with: {tool.hint}\n"
                "Alternatively set generate.source: cifs to start from existing structures.",
                err=True,
            )
            raise typer.Exit(code=1)
    try:
        outcome = run_generate(run_, force=force, progress=_progress(quiet))
    except GenerateError as err:
        typer.echo(f"{run_.config_path}: the generate section cannot be carried out", err=True)
        for line in str(err).splitlines():
            typer.echo(f"  - {line}", err=True)
        raise typer.Exit(code=2) from err
    _report(outcome)
    return outcome


def _do_qc(run_: Run) -> StageOutcome:
    try:
        outcome = run_qc(run_)
    except RunnerError as err:
        typer.echo(f"qc cannot run: {err}", err=True)
        raise typer.Exit(code=1) from err
    _report(outcome)
    return outcome


def _describe_progress(quiet: bool):
    if quiet:
        return None

    def show(substage: str, pos: int, total: int, name: str, status: str, detail: str | None) -> None:
        typer.echo(f"  {substage} [{pos}/{total}] {name}: {status}" + (f" ({detail})" if detail else ""))

    return show


def _do_describe(run_: Run, only: tuple[str, ...], block: tuple[int, int] | None, force: bool, quiet: bool):
    available = {t.name: t.check()[0] for t in default_tools(run_.cfg.tools)}
    try:
        outcome = run_describe(
            run_, available, only=only, block=block, force=force, progress=_describe_progress(quiet)
        )
    except RunnerError as err:
        typer.echo(f"describe cannot run: {err}", err=True)
        raise typer.Exit(code=1) from err
    n = len(outcome.table)
    for name, rep in outcome.details["substages"].items():
        c = rep["status_counts"]
        line = f"describe.{name}: {n} row(s): {c['ok']} ok, {c['skipped']} skipped, {c['failed']} failed"
        if name != "composition" and (rep["computed"] or rep["from_cache"]):
            line += f" (computed {rep['computed']}, reused {rep['from_cache']})"
        typer.echo(line)
        for reason, count in rep["reasons"].items():
            typer.echo(f"    {count} x {reason}")
    if outcome.written:
        typer.echo(f"  table: {outcome.path}")
    else:
        typer.echo(
            f"  block {outcome.details['block']} stored; when every block is done, run "
            f"'mofsift describe {run_.config_path}' once more to write the table"
        )
    for warning in outcome.warnings:
        typer.echo("", err=True)
        typer.echo(f"WARNING: {warning}", err=True)
        typer.echo(
            "         Its columns are empty. Pore geometry and xTB are the main descriptors of mofsift;\n"
            "         see docs/install.md (on Windows they run under WSL, or on a cluster).",
            err=True,
        )
    return outcome


_CONFIG_ARG = typer.Argument(..., help="Config file (see 'mofsift init').")
_QUIET_OPT = typer.Option(False, "--quiet", "-q", help="Do not print one line per structure.")
_FORCE_OPT = typer.Option(False, "--force", help="Build every structure again instead of reusing files.")


@app.command()
def generate(config: Path = _CONFIG_ARG, force: bool = _FORCE_OPT, quiet: bool = _QUIET_OPT) -> None:
    """Create the candidate table and the structure files (PORMAKE, or existing CIFs)."""
    _do_generate(_load_run(config), force, quiet)


@app.command()
def qc(config: Path = _CONFIG_ARG) -> None:
    """Run the structural checks on every generated structure."""
    _do_qc(_load_run(config))


@app.command(name="describe")
def describe_command(
    config: Path = _CONFIG_ARG,
    only: str = typer.Option(
        None, "--only", help=f"Compute only these sub-stages, comma separated: {', '.join(SUBSTAGES)}."
    ),
    block: str = typer.Option(
        None,
        "--block",
        help="I/N: compute only block I of N (for cluster job arrays). The table is not written.",
    ),
    force: bool = typer.Option(False, "--force", help="Compute again instead of reusing stored results."),
    quiet: bool = _QUIET_OPT,
) -> None:
    """Add composition, pore geometry (Zeo++) and xTB descriptors."""
    try:
        chosen, part = parse_only(only), parse_block(block)
    except ValueError as err:
        typer.echo(str(err), err=True)
        raise typer.Exit(code=2) from err
    _do_describe(_load_run(config), chosen, part, force, quiet)


def _do_score(run_: Run) -> StageOutcome:
    try:
        outcome = run_score(run_)
    except RunnerError as err:
        typer.echo(f"score cannot run: {err}", err=True)
        raise typer.Exit(code=1) from err
    c = outcome.counts
    typer.echo(
        f"score: {len(outcome.table)} row(s): {c['ok']} ok, {c['skipped']} skipped, {c['failed']} failed"
    )
    for name, n in outcome.details["rows_with_a_value"].items():
        typer.echo(f"    {name}: {n} row(s) with a value")
    typer.echo(f"  table: {outcome.path}")
    return outcome


@app.command(name="score")
def score_command(config: Path = _CONFIG_ARG) -> None:
    """Add the synthetic-accessibility components (linker scores, precedent counts)."""
    _do_score(_load_run(config))


def _do_filter(run_: Run) -> StageOutcome:
    try:
        outcome = run_filter(run_)
    except InvalidFilterError as err:
        typer.echo(f"{run_.config_path}: {err}", err=True)
        raise typer.Exit(code=2) from err
    except RunnerError as err:
        typer.echo(f"filter cannot run: {err}", err=True)
        raise typer.Exit(code=1) from err
    n = len(outcome.table)
    reports = outcome.details["filters"]
    if not reports:
        typer.echo(f"filter: no filters in the config; all {n} row(s) are kept")
    else:
        typer.echo(
            f"filter: {n} row(s): {outcome.details['selected']} satisfy every filter, "
            f"{outcome.details['rejected']} do not"
        )
        for report in reports:
            line = f"    {report['expression']}: {report['passed']} true, {report['rejected']} false"
            if report["undecided"]:
                line += f", {report['undecided']} undecided because a value is missing"
            typer.echo(line)
    typer.echo(f"  table: {outcome.path}")
    return outcome


@app.command(name="filter")
def filter_command(config: Path = _CONFIG_ARG) -> None:
    """Mark the rows that satisfy the filters of the config (no row is removed)."""
    _do_filter(_load_run(config))


def _do_export(run_: Run, out: Path | None, force: bool) -> ExportOutcome:
    try:
        outcome = run_export(run_, out, force=force)
    except RunnerError as err:
        typer.echo(f"export cannot run: {err}", err=True)
        raise typer.Exit(code=1) from err
    typer.echo(
        f"export: {outcome.n_selected} of {outcome.n_enumerated} row(s), "
        f"{outcome.n_structures} structure file(s)"
    )
    typer.echo(f"  bundle: {outcome.out_dir}")
    for warning in outcome.warnings:
        typer.echo(f"WARNING: {warning}", err=True)
    return outcome


@app.command(name="export")
def export_command(
    config: Path = _CONFIG_ARG,
    out: Path = typer.Option(
        None,
        "--out",
        help="Folder of the bundle, relative to the current folder. Default: export.out_dir of the "
        "config, or export/<project> next to the config file.",
    ),
    force: bool = typer.Option(
        False, "--force", help="Replace the bundle of another project found in that folder."
    ),
) -> None:
    """Write the rows that passed the filters as a bundle: table, records, structures, manifest."""
    _do_export(_load_run(config), out, force)


@app.command()
def validate(
    bundle: Path = typer.Argument(..., help="Folder of a bundle (the one that holds manifest.json)."),
    as_json: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Check a bundle: manifest, checksums, table, records and structure files."""
    try:
        result = validate_bundle(bundle)
    except BundleError as err:
        typer.echo(str(err), err=True)
        raise typer.Exit(code=2) from err
    if as_json:
        typer.echo(json.dumps(result.as_dict(), indent=2))
    else:
        if result.ok:
            summary = result.summary
            typer.echo(
                f"{bundle}: valid bundle of project {summary['project']} "
                f"({summary['records']} record(s), {summary['structures']} structure file(s))"
            )
        else:
            typer.echo(f"{bundle}: NOT a valid bundle; {len(result.problems)} problem(s):", err=True)
            for problem in result.problems:
                typer.echo(f"  - {problem}", err=True)
        for note in result.notes:
            typer.echo(f"  note: {note}")
    if not result.ok:
        raise typer.Exit(code=1)


@app.command()
def merge(
    bundles: list[Path] = typer.Argument(..., help="Two or more bundle folders. The first one wins ties."),
    out: Path = typer.Option(..., "--out", help="Folder of the merged bundle."),
    project: str = typer.Option("merged", "--project", help="Name of the merged bundle."),
    on_conflict: str = typer.Option(
        "error",
        "--on-conflict",
        help="When two bundles give different values for the same record: error (stop and list "
        "them), first or last (keep the value of the first or last bundle).",
    ),
    table_format: str = typer.Option("csv", "--table-format", help="csv or parquet."),
    force: bool = typer.Option(
        False, "--force", help="Replace the bundle of another project found in the output folder."
    ),
) -> None:
    """Merge bundles into one: union of records and columns, values checked for conflicts."""
    if on_conflict not in ("error", "first", "last"):
        typer.echo("--on-conflict must be error, first or last", err=True)
        raise typer.Exit(code=2)
    if table_format not in ("csv", "parquet"):
        typer.echo("--table-format must be csv or parquet", err=True)
        raise typer.Exit(code=2)
    missing = [str(b) for b in bundles if not b.is_dir()]
    if missing:
        typer.echo(f"not a folder: {', '.join(missing)}", err=True)
        raise typer.Exit(code=2)
    if not valid_project_name(project):
        typer.echo(
            "--project: use letters, digits, '_', '-' or '.', starting with a letter or digit", err=True
        )
        raise typer.Exit(code=2)
    try:
        result = merge_bundles(
            bundles,
            out,
            project=project,
            on_conflict=on_conflict,  # type: ignore[arg-type]
            table_format=table_format,
            force=force,
        )
    except (MergeError, BundleError) as err:
        typer.echo(f"merge cannot be done: {err}", err=True)
        raise typer.Exit(code=1) from err
    typer.echo(
        f"merge: {result.n_records} record(s) from {len(bundles)} bundles, "
        f"{result.n_structures} structure file(s)"
    )
    typer.echo(f"  bundle: {result.out_dir}")
    for warning in result.warnings:
        typer.echo(f"WARNING: {warning}", err=True)


@app.command(name="run")
def run_all(config: Path = _CONFIG_ARG, force: bool = _FORCE_OPT, quiet: bool = _QUIET_OPT) -> None:
    """Run every stage in order: generate, qc, describe, score, filter, export."""
    run_ = _load_run(config)
    # A filter that names an unknown column, or an export that cannot be written, would only
    # show after the long stages; check both before starting.
    available = {t.name: t.check()[0] for t in default_tools(run_.cfg.tools)}
    for plan in plan_stages(run_.cfg, available):
        if plan.stage in ("filter", "export") and plan.action == "error":
            typer.echo(f"{config}: {plan.stage} cannot run: {plan.reason}", err=True)
            raise typer.Exit(code=2 if plan.stage == "filter" else 1)
    _do_generate(run_, force, quiet)
    _do_qc(run_)
    _do_describe(run_, SUBSTAGES, None, False, quiet)
    _do_score(run_)
    _do_filter(run_)
    _do_export(run_, None, False)


def run() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
