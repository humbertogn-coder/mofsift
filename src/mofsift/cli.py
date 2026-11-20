"""Command-line interface.

This version provides ``init``, ``doctor``, ``generate``, ``qc``, ``describe``, ``score`` and
``run``. The other commands (export, merge, validate) are added as each stage is implemented.

Exit codes: 0 = done; 1 = a stage could not run; 2 = the config is invalid.
"""

from __future__ import annotations

import json
import platform
import sys
from pathlib import Path

import typer

from mofsift import __version__
from mofsift.config import Config, ConfigError, load_config
from mofsift.describe import SUBSTAGES, parse_block, parse_only
from mofsift.generate import GenerateError
from mofsift.generate import preview as preview_generate
from mofsift.generate.pormake_adapter import PormakeMissingError
from mofsift.plan import StagePlan, plan_stages
from mofsift.runner import (
    IMPLEMENTED,
    Run,
    RunnerError,
    StageOutcome,
    run_describe,
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
                typer.echo("")
                typer.echo(
                    f"Note: this version of mofsift runs {', '.join(IMPLEMENTED)}. "
                    "For the other stages the plan shows what they will do once they are implemented."
                )
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


@app.command(name="run")
def run_all(config: Path = _CONFIG_ARG, force: bool = _FORCE_OPT, quiet: bool = _QUIET_OPT) -> None:
    """Run every stage that this version implements, in order."""
    run_ = _load_run(config)
    _do_generate(run_, force, quiet)
    _do_qc(run_)
    _do_describe(run_, SUBSTAGES, None, False, quiet)
    _do_score(run_)
    later = [p.stage for p in plan_stages(run_.cfg, {}) if p.stage.split(".")[0] not in IMPLEMENTED]
    typer.echo(f"Not implemented in this version, so not run: {', '.join(later)}")


def run() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
