"""Command-line interface.

This version provides ``init`` and ``doctor``. The stage commands (generate, qc, describe,
score, export, run, merge, validate) are added as each stage is implemented.
"""

from __future__ import annotations

import json
import platform
import sys
from pathlib import Path

import typer

from mofsift import __version__
from mofsift.config import ConfigError, load_config
from mofsift.plan import plan_stages
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
                typer.echo("")
                typer.echo(
                    "Note: this version of mofsift implements 'init' and 'doctor'. "
                    "The plan shows what the stages will do once they are implemented."
                )
        if not core_ok:
            typer.echo("\nA core dependency is missing or broken; reinstall mofsift.", err=True)

    if config_error is not None:
        raise typer.Exit(code=2)
    if not core_ok or not plan_ok:
        raise typer.Exit(code=1)


def run() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
