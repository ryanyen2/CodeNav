"""Expose the hub over a Cloudflare named tunnel.

The hub binds localhost; remote reach is an outbound tunnel, so no inbound port
is opened and the machine needs no public IP. The default is a Cloudflare named
tunnel behind Cloudflare Access (deny by default, GitHub sign-in at the edge); the
hub still checks the Access token and the collaborator permission itself
(``codoc/serve/auth.py``). Tailscale Funnel or Serve isolates more when every
collaborator can join the tailnet.

This module builds the ``cloudflared`` argv and starts it; the tunnel and its
Access policy are configured outside codoc (``docs/serve-deployment.md``).
"""
from __future__ import annotations

import subprocess


def cloudflared_command(port: int, *, config: str | None = None) -> list[str]:
    """argv for ``cloudflared`` forwarding the hub's local port and nothing else.

    A named-tunnel ``config`` (recommended: a stable hostname bound to one local
    service, gated by Access) runs via ``tunnel run``; without one, a quick ad-hoc
    ``--url`` tunnel is used (dev only: it has no Access policy). The forward target
    is always ``127.0.0.1:<port>``, never ``0.0.0.0`` or the whole host."""
    if config:
        return ["cloudflared", "tunnel", "--config", config, "run"]
    return ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{port}"]


def launch_tunnel(port: int, *, config: str | None = None) -> subprocess.Popen:
    """Spawn ``cloudflared`` alongside the hub. The caller terminates it on exit.

    Deployment wrapper (not unit-tested live): requires ``cloudflared`` installed
    and, for a real exposure, a named tunnel + Access policy configured per the
    deploy doc."""
    return subprocess.Popen(cloudflared_command(port, config=config))
