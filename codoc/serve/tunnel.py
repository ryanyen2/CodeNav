"""tunnel.py — expose the hub over a Cloudflare named tunnel (U6).

The hub binds localhost; remote reach is provided by an OUTBOUND tunnel, so no
inbound port is opened and the home machine has no public IP. The default is a
Cloudflare named Tunnel + Cloudflare Access (deny-by-default, GitHub OIDC at the
edge); the origin still validates the Access JWT AND runs the collaborator check
(defense in depth — see ``codoc/serve/auth.py``). Tailscale Funnel/Serve is the
stronger-isolation alternative when every collaborator can join the tailnet.

This module only builds the launcher argv + spawns it; the tunnel + Access policy
themselves are configured out of band (documented in ``docs/serve-deployment.md``).
The argv builder is pure + tested; the spawn is a thin, documented wrapper.
"""
from __future__ import annotations

import subprocess


def cloudflared_command(port: int, *, config: str | None = None) -> list[str]:
    """argv for ``cloudflared`` forwarding ONLY the hub's local port.

    A named-tunnel ``config`` (recommended: a stable hostname bound to one local
    service, gated by Access) runs via ``tunnel run``; without one, a quick ad-hoc
    ``--url`` tunnel is used (dev only — it has no Access policy). The forward target
    is always ``127.0.0.1:<port>`` — never ``0.0.0.0`` or the whole host."""
    if config:
        return ["cloudflared", "tunnel", "--config", config, "run"]
    return ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{port}"]


def launch_tunnel(port: int, *, config: str | None = None) -> subprocess.Popen:
    """Spawn ``cloudflared`` alongside the hub. The caller terminates it on exit.

    Deployment wrapper (not unit-tested live): requires ``cloudflared`` installed
    and, for a real exposure, a named tunnel + Access policy configured per the
    deploy doc."""
    return subprocess.Popen(cloudflared_command(port, config=config))
