"""The one applier, and how every other process reaches it.

- ``daemon``:   ``codoc watch``: drain client ops, reflect code changes, publish.
- ``client``:   how a second process writes: through the daemon's inbox, or
                as the applier when no daemon owns the checkout.
- ``reads``:    what the CLI and the MCP tools read: the queue, a feature's history.
- ``publish``:  the derived files an applier writes after a turn, and the
                realizing lease in ``status.json``.
- ``pidfile``:  whether a daemon owns the checkout.
"""
