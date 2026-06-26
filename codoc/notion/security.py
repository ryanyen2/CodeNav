"""security.py — treat Notion-authored content as untrusted data.

A Notion page is remote, multi-author input. Two consequences:

* **Consult links are attacker-authorable.** A `[label](https://…)` link in a
  Notion description that the realizing agent would WebFetch must pass the same
  SSRF guard the hub applies (``serve/consult.consult_url_allowed``): https-only,
  host in a default-empty allowlist, and every resolved IP public (no loopback /
  link-local / RFC1918 / CGNAT / metadata). :func:`safe_consult_links` filters a
  description's links through that guard.
* **Prose is data, never instructions.** Text authored in Notion is fed to codoc as
  intent content; it is never interpreted as a command to the bridge. (The only
  command surface is the explicit ``/accept`` · ``/reject`` verdict in a comment on a
  proposal callout — and that only writes a verdict for that callout's event id.)

The inbound webhook boundary itself is hardened in :mod:`codoc.notion.webhook`
(mandatory signature verification on raw bytes; the caller rate-limits the endpoint).
"""
from __future__ import annotations

import socket
from collections.abc import Iterable

from codoc.codoc_file.parse import extract_links
from codoc.serve.consult import consult_url_allowed


def _default_resolve(host: str) -> list[str]:
    """Resolve ``host`` to all its IPs (A + AAAA), for the resolve-and-pin check."""
    infos = socket.getaddrinfo(host, None)
    return [info[4][0] for info in infos]


def safe_consult_links(text: str, allowlist: Iterable[str] = (), *, resolve=None) -> list[str]:
    """Return the SSRF-safe https consult links found in Notion-authored ``text``.

    Default allowlist is empty → every link is rejected unless the operator opts a
    host in (the same default-deny posture as the hub). A link whose host resolves to
    a private/loopback/metadata address is dropped."""
    resolver = resolve or _default_resolve
    allow = set(allowlist or ())
    out: list[str] = []
    for link in extract_links(text or ""):
        ok, _reason = consult_url_allowed(link.url, allow, resolve=resolver)
        if ok:
            out.append(link.url)
    return out
