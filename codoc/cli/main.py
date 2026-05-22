"""codoc CLI — root Typer application."""

from __future__ import annotations

import typer

from codoc.cli.sync import sync_command
from codoc.cli.diff import diff_command
from codoc.cli.gate_run import gate_run
from codoc.cli.plan import plan_command
from codoc.cli.server import server
from codoc.cli.commit_preflight import commit_preflight
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

app = typer.Typer(
    name="codoc",
    help="codoc keeps a feature-tree view of your code, synced as you commit.",
    no_args_is_help=True,
)

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
# Infra commands
# ------------------------------------------------------------------
app.command("plan")(plan_command)
app.command("gate-run")(gate_run)
app.command("server")(server)
app.command("commit-preflight")(commit_preflight)
