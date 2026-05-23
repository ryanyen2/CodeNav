"""codoc CLI — root Typer application."""

from __future__ import annotations

import typer

from codoc.cli.sync import sync_command
from codoc.cli.diff import diff_command
from codoc.cli.watch import watch_command
from codoc.cli.gate_run import gate_run
from codoc.cli.plan import plan_command
from codoc.cli.server import server
from codoc.cli.commit_preflight import commit_preflight
from codoc.cli.init import init
from codoc.cli.reflect import reflect_command
from codoc.cli.bootstrap import bootstrap_app
from codoc.cli.projection import proj_app
from codoc.cli.doctor import doctor
from codoc.cli.health import health_command
from codoc.cli.commands import (
    cmd_list,
    cmd_show,
    cmd_proposals,
    cmd_accept,
    cmd_reject,
    cmd_edit,
    cmd_rename,
    cmd_retire,
    cmd_search,
    cmd_status,
    cmd_conflicts,
)


def _version_callback(value: bool) -> None:
    if value:
        from importlib.metadata import version, PackageNotFoundError
        try:
            ver = version("codoc")
        except PackageNotFoundError:
            ver = "0.1.1-dev"
        typer.echo(f"codoc {ver}")
        raise typer.Exit()


app = typer.Typer(
    name="codoc",
    help="codoc keeps a feature-tree view of your code, synced as you commit.",
    no_args_is_help=True,
)


@app.callback()
def _main(
    version: bool = typer.Option(
        None,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    pass


# ------------------------------------------------------------------
# Bootstrap and initialization
# ------------------------------------------------------------------
app.command("init")(init)
app.add_typer(bootstrap_app, name="bootstrap")

# ------------------------------------------------------------------
# Reflective pipeline
# ------------------------------------------------------------------
app.command("reflect")(reflect_command)

# ------------------------------------------------------------------
# Projection (tree ↔ DB lens)
# ------------------------------------------------------------------
app.add_typer(proj_app, name="projection")

# ------------------------------------------------------------------
# Primary sync verb — start here
# ------------------------------------------------------------------
app.command("sync")(sync_command)

# ------------------------------------------------------------------
# Browse and curate the feature tree
# ------------------------------------------------------------------
app.command("list")(cmd_list)
app.command("show")(cmd_show)
app.command("proposals")(cmd_proposals)
app.command("accept")(cmd_accept)
app.command("reject")(cmd_reject)
app.command("edit")(cmd_edit)
app.command("rename")(cmd_rename)
app.command("retire")(cmd_retire)
app.command("search")(cmd_search)
app.command("status")(cmd_status)
app.command("conflicts")(cmd_conflicts)
app.command("diff")(diff_command)

# ------------------------------------------------------------------
# FS watcher + realize
# ------------------------------------------------------------------
app.command("watch")(watch_command)

# ------------------------------------------------------------------
# Infra commands
# ------------------------------------------------------------------
app.command("plan")(plan_command)
app.command("gate-run")(gate_run)
app.command("server")(server)
app.command("commit-preflight")(commit_preflight)
app.command("doctor")(doctor)
app.command("health")(health_command)

