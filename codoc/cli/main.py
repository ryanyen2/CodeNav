"""codoc CLI — root Typer application."""

from __future__ import annotations

import typer

from codoc.cli.bootstrap import bootstrap_app
from codoc.cli.feature import feature_app
from codoc.cli.gate_run import gate_run
from codoc.cli.init import init
from codoc.cli.projection import proj_app
from codoc.cli.plan import plan_command
from codoc.cli.reflect import reflect_command
from codoc.cli.server import server
from codoc.cli.commit_preflight import commit_preflight
from codoc.cli.tx import tx_app
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
# Top-level plain-English commands (preferred surface)
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

# ------------------------------------------------------------------
# Scaffolding and pipeline commands
# ------------------------------------------------------------------
app.command("init")(init)
app.command("plan")(plan_command)
app.command("reflect")(reflect_command)
app.command("gate-run")(gate_run)
app.command("server")(server)
app.command("commit-preflight")(commit_preflight)

# ------------------------------------------------------------------
# Sub-apps  (bootstrap, projection kept; tx/feature kept as aliases)
# ------------------------------------------------------------------
app.add_typer(bootstrap_app, name="bootstrap")
app.add_typer(proj_app, name="projection")

# tx / feature kept as hidden aliases with deprecation notices.
# They remain functional but emit a stderr hint on use.
tx_app.info.deprecated = True  # type: ignore[attr-defined]
feature_app.info.deprecated = True  # type: ignore[attr-defined]
app.add_typer(tx_app, name="tx", hidden=True)
app.add_typer(feature_app, name="feature", hidden=True)
