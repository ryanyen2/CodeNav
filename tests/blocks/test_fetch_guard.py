"""SSRF guard tests for codoc/blocks/fetch_guard.py.

`safe_get` is the one chokepoint a url/pdf block's `lift` uses to fetch on the
maintainer's own daemon. These tests exercise the guard logic in isolation
(scheme, DNS-resolved IP classification) without network access, and the fetch
mechanics (redirect handling, size cap) against a local HTTP server with the
guard's host-check monkeypatched to allow 127.0.0.1 — loopback is exactly what the
guard blocks in production, so a real end-to-end success case needs that one seam
opened deliberately, matching how the guard itself is meant to be tested.
"""
from __future__ import annotations

import http.server
import threading
from contextlib import contextmanager

import pytest

from codoc.blocks import fetch_guard as fg

httpx = pytest.importorskip("httpx")


# ── pure guard-logic tests (no network) ────────────────────────────────────

def test_rejects_non_http_scheme():
    assert fg._url_is_safe("file:///etc/passwd") is False
    assert fg._url_is_safe("ftp://example.com/x") is False


def test_rejects_literal_loopback_and_link_local():
    assert fg._is_blocked_ip("127.0.0.1") is True
    assert fg._is_blocked_ip("::1") is True
    assert fg._is_blocked_ip("169.254.169.254") is True  # cloud metadata endpoint
    assert fg._is_blocked_ip("10.0.0.5") is True
    assert fg._is_blocked_ip("192.168.1.1") is True


def test_allows_public_ip():
    assert fg._is_blocked_ip("93.184.216.34") is False  # example.com-class public IP


def test_host_resolving_to_private_ip_is_blocked(monkeypatch):
    # A hostname whose DNS resolves to a private address must be blocked even
    # though the hostname string itself looks innocuous (DNS-rebinding shape).
    monkeypatch.setattr(fg.socket, "getaddrinfo",
                         lambda host, port: [(2, 1, 6, "", ("169.254.169.254", 0))])
    assert fg._host_is_safe("metadata.internal.example") is False


def test_host_resolving_to_public_ip_is_allowed(monkeypatch):
    monkeypatch.setattr(fg.socket, "getaddrinfo",
                         lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0))])
    assert fg._host_is_safe("example.com") is True


def test_unresolvable_host_is_blocked(monkeypatch):
    def _raise(host, port):
        raise OSError("nodename nor servname provided")
    monkeypatch.setattr(fg.socket, "getaddrinfo", _raise)
    assert fg._host_is_safe("nowhere.invalid") is False


def test_safe_get_returns_none_without_httpx(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "httpx":
            raise ImportError("no httpx")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert fg.safe_get("https://example.com") is None


def test_safe_get_blocks_loopback_target():
    # No monkeypatch here — loopback must be blocked by the real guard.
    assert fg.safe_get("http://127.0.0.1:1/anything") is None


# ── fetch-mechanics tests (real local server, guard's host-check opened) ──

class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence test noise
        pass

    def do_GET(self):
        if self.path == "/big":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"x" * 5000)
        elif self.path == "/redirect-once":
            self.send_response(302)
            self.send_header("Location", "/big")
            self.end_headers()
        elif self.path == "/redirect-loop":
            self.send_response(302)
            self.send_header("Location", "/redirect-loop")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


@contextmanager
def _local_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        thread.join(timeout=2)


@pytest.fixture
def allow_loopback(monkeypatch):
    """Open the one seam needed to test fetch mechanics locally — production
    code path (_is_blocked_ip) is untouched; only the host-safety check used by
    `safe_get`'s loop is relaxed for 127.0.0.1."""
    monkeypatch.setattr(fg, "_host_is_safe", lambda host: host == "127.0.0.1")


def test_safe_get_enforces_size_cap(allow_loopback):
    with _local_server() as port:
        body = fg.safe_get(f"http://127.0.0.1:{port}/big", max_bytes=100)
        assert body is not None
        assert len(body) <= 100


def test_safe_get_follows_a_redirect(allow_loopback):
    with _local_server() as port:
        body = fg.safe_get(f"http://127.0.0.1:{port}/redirect-once", max_bytes=5000)
        assert body == b"x" * 5000


def test_safe_get_caps_redirect_loop(allow_loopback):
    with _local_server() as port:
        assert fg.safe_get(f"http://127.0.0.1:{port}/redirect-loop") is None


def test_safe_get_returns_none_on_404(allow_loopback):
    with _local_server() as port:
        assert fg.safe_get(f"http://127.0.0.1:{port}/nope") is None
