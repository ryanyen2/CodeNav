"""U10 — Notion content is untrusted: SSRF filter on authored consult links."""
from __future__ import annotations

from codoc.notion.security import safe_consult_links


def _resolve_to(ip):
    return lambda host: [ip]


def test_public_link_allowed_when_host_allowlisted():
    text = "See [docs](https://docs.example.com/page)."
    links = safe_consult_links(text, allowlist={"docs.example.com"},
                               resolve=_resolve_to("93.184.216.34"))
    assert links == ["https://docs.example.com/page"]


def test_link_rejected_when_not_allowlisted():
    text = "See [docs](https://docs.example.com/page)."
    assert safe_consult_links(text, allowlist=set(), resolve=_resolve_to("93.184.216.34")) == []


def test_loopback_rejected_even_if_allowlisted():
    text = "Fetch [x](https://internal.example.com/secret)."
    assert safe_consult_links(text, allowlist={"internal.example.com"},
                              resolve=_resolve_to("127.0.0.1")) == []


def test_private_rfc1918_rejected():
    text = "[lan](https://lan.example.com/x)"
    assert safe_consult_links(text, allowlist={"lan.example.com"},
                              resolve=_resolve_to("10.0.0.5")) == []


def test_metadata_endpoint_rejected():
    text = "[meta](https://meta.example.com/latest)"
    assert safe_consult_links(text, allowlist={"meta.example.com"},
                              resolve=_resolve_to("169.254.169.254")) == []


def test_http_scheme_rejected():
    text = "[insecure](http://docs.example.com)"
    assert safe_consult_links(text, allowlist={"docs.example.com"},
                              resolve=_resolve_to("93.184.216.34")) == []


def test_codoc_citations_are_not_consult_links():
    # codoc: refs are not external links and must never be fetched.
    text = "binds [auth](codoc:auth.py#login)"
    assert safe_consult_links(text, allowlist={"auth.py"}, resolve=_resolve_to("1.2.3.4")) == []


def test_empty_text():
    assert safe_consult_links("", resolve=_resolve_to("1.2.3.4")) == []
