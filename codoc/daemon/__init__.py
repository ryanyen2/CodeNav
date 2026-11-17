"""The one applier, and how every other process reaches it.

- ``daemon``:   ``codoc watch``: drain client ops, reflect code changes, publish.
- ``client``:   how a second process writes and reads: through the daemon's
                inbox, or as the applier when no daemon owns the checkout.
- ``publish``:  the derived files an applier writes after a turn, and the
                realizing lease in ``status.json``.
- ``epoch``:    ``activity.json``: the agent session the daemon stands down for.
- ``pidfile``:  whether a daemon owns the checkout.
"""
