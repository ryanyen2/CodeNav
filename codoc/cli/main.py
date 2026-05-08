"""codoc CLI — root Typer application."""

from __future__ import annotations

import typer

from codoc.cli.bootstrap import bootstrap_app
from codoc.cli.feature import feature_app
from codoc.cli.gate_run import gate_run
from codoc.cli.init import init
from codoc.cli.reflect import reflect_command
from codoc.cli.server import server
from codoc.cli.tx import tx_app

app = typer.Typer(
    name="codoc",
    help="Feature-tree synchronization for your codebase.",
    no_args_is_help=True,
)

# Single commands registered directly on the root app.
app.command("init")(init)
app.command("reflect")(reflect_command)
app.command("gate-run")(gate_run)
app.command("server")(server)

# Sub-apps with their own subcommands.
app.add_typer(bootstrap_app, name="bootstrap")
app.add_typer(tx_app, name="tx")
app.add_typer(feature_app, name="feature")
