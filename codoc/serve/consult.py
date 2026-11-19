"""The Consult-URL gate for a realize run the hub starts.

A feature description can carry external `[label](https://…)` links that the
realizing agent fetches. On a run a remote contributor asked for, anyone with
suggest access could have written those links, so a fetch is allowed only over
https, to a host in an allowlist that is empty by default, at an address that
resolves to public IPs: loopback,
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
    ``resolve(host) -> list[str]`` returns the host's IPs; every one must be public."""
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
