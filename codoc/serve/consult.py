"""The Consult-URL gate for a hub-triggered realize run.

A feature description can carry external `[label](https://…)` links that the
realizing agent WebFetches. On a remote-triggered run those links are attacker-
authorable, so the fetch path is locked down: https only, the host must be in a
default-EMPTY allowlist, and the resolved IP must be public: loopback,
link-local/metadata (169.254/16), RFC1918, CGNAT, and reserved ranges are refused
so a Consult link cannot reach the home LAN or a cloud metadata endpoint. DNS
resolution is injected so the resolve-and-pin check is testable; the caller must
also disable HTTP redirects (or re-validate each hop).
"""
from __future__ import annotations

from urllib.parse import urlparse

from codoc.kinds.fetch_guard import blocked_ip


def consult_url_allowed(url: str, allowlist, *, resolve) -> tuple[bool, str]:
    """(allowed, reason) for fetching ``url``.

    ``allowlist`` is the set of permitted hostnames (empty → deny everything).
    ``resolve(host) -> list[str]`` returns the host's IPs; ALL must be public."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "unparseable url"
    if parsed.scheme != "https":
        return False, "only https is permitted"
    host = parsed.hostname
    if not host:
        return False, "missing host"
    if host not in set(allowlist or ()):
        return False, "host is not in the consult allowlist"
    try:
        ips = list(resolve(host))
    except Exception:
        return False, "dns resolution failed"
    if not ips:
        return False, "host did not resolve"
    for ip in ips:
        if blocked_ip(ip):
            return False, f"host resolves to a blocked address ({ip})"
    return True, ""
