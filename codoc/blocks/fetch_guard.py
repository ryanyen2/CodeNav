"""fetch_guard.py — the one SSRF chokepoint for local block `lift` fetches.

Only ever called from a block plugin's `lift` (``codoc/blocks/reference.py``),
dispatched by Loop A on the maintainer's own daemon against locally-authored block
content — never from ``codoc/serve/*``, which has no reason to import this and
never fetches on behalf of a remote suggestion (a hub-submitted url/pdf block just
carries the raw reference until the maintainer's own daemon lifts it on a later
pass). That is a deliberate trust-boundary decision: the fetch is trusted because
it runs locally against content the daemon's own store already holds, not because
the URL itself is trusted.

Guards, in order:
- scheme allowlist (``http``/``https`` only — no ``file://``, no ``data:``, …).
- DNS-resolve the hostname and reject if ANY resolved address is
  private/loopback/link-local/multicast/reserved/unspecified (the ``ipaddress``
  module's own classification). This is what stops a bare-IP or DNS-rebinding SSRF
  attempt aimed at cloud metadata endpoints (169.254.169.254), localhost, or an
  internal service — a check on the literal hostname string alone would not catch
  a hostname that *resolves* to one of those.
- manual redirect handling (``follow_redirects=False``) — each hop is re-resolved
  and re-validated before being followed, capped at a small number of hops, so a
  public URL that redirects to an internal one is still caught.
- a streamed read capped at ``max_bytes``, with short connect/read timeouts.

Residual risk (documented, not silently assumed away): there is a TOCTOU gap
between the DNS check here and the connection httpx makes internally — a
sufficiently active DNS-rebinding attacker could in principle swap the resolved
address between our check and the actual connect. Closing that fully requires
pinning the connection to the validated IP (a custom transport + manual TLS SNI
handling), which is real complexity this scope doesn't carry given the trust
boundary above (this never runs against attacker-supplied input server-side). If
this helper is ever reused somewhere that fetches on behalf of untrusted remote
input, that gap needs closing first.

Any violation returns ``None`` rather than raising — a blocked/unreachable URL
just means "nothing to lift this pass," never a loop crash.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_MAX_REDIRECTS = 5
_DEFAULT_MAX_BYTES = 2_000_000
_DEFAULT_TIMEOUT = 8.0
_USER_AGENT = "codoc-block-fetch/1.0"


def _is_blocked_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # unparsable → fail closed
    return (
        addr.is_private or addr.is_loopback or addr.is_link_local
        or addr.is_multicast or addr.is_reserved or addr.is_unspecified
    )


def _host_is_safe(hostname: str) -> bool:
    """True iff EVERY address ``hostname`` resolves to is public/routable."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError:
        return False
    if not infos:
        return False
    return all(not _is_blocked_ip(info[4][0]) for info in infos)


def _url_is_safe(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.hostname:
        return False
    return _host_is_safe(parsed.hostname)


def safe_get(
    url: str,
    *,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    timeout: float = _DEFAULT_TIMEOUT,
) -> bytes | None:
    """Fetch ``url`` and return up to ``max_bytes`` of its body, or ``None`` if the
    URL (or any redirect hop) fails the SSRF guard, times out, or errors.

    Lazily imports ``httpx`` (an optional ``media`` extra) so a codoc install
    without it simply never fetches — the caller (a plugin's ``lift``) treats
    ``None`` as "no enrichment this pass," not an error.
    """
    try:
        import httpx
    except ImportError:
        return None

    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        if not _url_is_safe(current):
            return None
        try:
            with httpx.Client(
                follow_redirects=False, timeout=timeout,
                headers={"User-Agent": _USER_AGENT},
            ) as client:
                with client.stream("GET", current) as resp:
                    if resp.is_redirect:
                        location = resp.headers.get("location")
                        if not location:
                            return None
                        current = str(httpx.URL(current).join(location))
                        continue
                    if resp.status_code >= 400:
                        return None
                    chunks = bytearray()
                    for chunk in resp.iter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) >= max_bytes:
                            break
                    return bytes(chunks[:max_bytes])
        except httpx.HTTPError:
            return None
    return None  # too many redirect hops
