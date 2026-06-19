"""U6 — the cloudflared launcher argv (forward only the local hub port)."""
from __future__ import annotations

from codoc.serve.tunnel import cloudflared_command


def test_adhoc_tunnel_forwards_only_localhost_port():
    cmd = cloudflared_command(8787)
    assert cmd == ["cloudflared", "tunnel", "--url", "http://127.0.0.1:8787"]
    # never the whole host / 0.0.0.0
    assert all("0.0.0.0" not in part for part in cmd)


def test_named_tunnel_uses_config():
    cmd = cloudflared_command(8787, config="/home/u/.cloudflared/config.yml")
    assert cmd == ["cloudflared", "tunnel", "--config", "/home/u/.cloudflared/config.yml", "run"]
