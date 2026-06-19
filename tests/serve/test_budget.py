"""U8 — Denial-of-Wallet budget guard, SSRF Consult allowlist, done-tracking."""
from __future__ import annotations

from codoc.serve.budget import BudgetGuard
from codoc.serve.consult import consult_url_allowed
from codoc.serve.realize_trigger import filter_undone, mark_done, read_done


# ── budget guard (5 flows) ───────────────────────────────────────────────────

def test_cost_cap():
    g = BudgetGuard(max_cost=1.0, max_tool_calls=100)
    assert g.charge(0.6) is True
    assert g.charge(0.5) is False  # 1.1 > 1.0
    assert g.tripped() is True


def test_tool_call_cap():
    g = BudgetGuard(max_cost=100, max_tool_calls=2)
    assert g.allow_tool_call() is True
    assert g.allow_tool_call() is True
    assert g.allow_tool_call() is False
    assert g.tripped() is True


def test_circuit_breaker_opens_and_resets():
    g = BudgetGuard(max_cost=100, max_tool_calls=100, breaker_threshold=2)
    g.record_failure()
    assert g.breaker_open() is False
    g.record_failure()
    assert g.breaker_open() is True
    assert g.allow_tool_call() is False  # open breaker blocks further calls

    g2 = BudgetGuard(max_cost=100, max_tool_calls=100, breaker_threshold=2)
    g2.record_failure()
    g2.record_success()  # a success resets the streak
    g2.record_failure()
    assert g2.breaker_open() is False


def test_liveness_timeout():
    g = BudgetGuard(max_cost=100, max_tool_calls=100, liveness_timeout_s=30)
    assert g.expired(started_at=1000.0, now=1029.0) is False
    assert g.expired(started_at=1000.0, now=1031.0) is True


def test_within_budget_not_tripped():
    g = BudgetGuard(max_cost=10, max_tool_calls=10)
    g.charge(1.0)
    g.allow_tool_call()
    assert g.tripped() is False


# ── SSRF Consult allowlist (5 flows) ─────────────────────────────────────────

def _resolver(mapping):
    def resolve(host):
        return mapping[host]
    return resolve


def test_https_public_allowlisted_host_allowed():
    ok, _ = consult_url_allowed(
        "https://docs.example.com/x", {"docs.example.com"},
        resolve=_resolver({"docs.example.com": ["93.184.216.34"]}))
    assert ok is True


def test_http_scheme_blocked():
    ok, reason = consult_url_allowed(
        "http://docs.example.com", {"docs.example.com"},
        resolve=_resolver({"docs.example.com": ["93.184.216.34"]}))
    assert ok is False and "https" in reason


def test_host_not_in_allowlist_blocked():
    ok, reason = consult_url_allowed(
        "https://evil.example.com", {"docs.example.com"},
        resolve=_resolver({"evil.example.com": ["93.184.216.34"]}))
    assert ok is False and "allowlist" in reason


def test_private_and_metadata_ips_blocked():
    for host, ip in [("a", "127.0.0.1"), ("b", "169.254.169.254"),
                     ("c", "10.1.2.3"), ("d", "192.168.0.5"), ("e", "100.64.0.1")]:
        ok, reason = consult_url_allowed(
            f"https://{host}.example.com", {f"{host}.example.com"},
            resolve=_resolver({f"{host}.example.com": [ip]}))
        assert ok is False, f"{ip} should be blocked"
        assert "blocked" in reason


def test_empty_allowlist_denies_everything():
    ok, _ = consult_url_allowed(
        "https://docs.example.com", set(),
        resolve=_resolver({"docs.example.com": ["93.184.216.34"]}))
    assert ok is False


# ── done-tracking ────────────────────────────────────────────────────────────

def test_done_tracking_filters_and_persists(tmp_path):
    cd = str(tmp_path)
    ready = [{"id": "d-1"}, {"id": "d-2"}, {"id": "d-3"}]
    assert read_done(cd) == set()
    mark_done(cd, "d-2")
    assert read_done(cd) == {"d-2"}
    undone = filter_undone(ready, read_done(cd))
    assert [d["id"] for d in undone] == ["d-1", "d-3"]
    # a re-fire after realizing the rest finds nothing
    mark_done(cd, "d-1")
    mark_done(cd, "d-3")
    assert filter_undone(ready, read_done(cd)) == []
