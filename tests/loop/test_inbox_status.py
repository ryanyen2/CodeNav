"""The verdict inbox (.codoc/inbox.json) and pipeline status (.codoc/status.json)."""
from __future__ import annotations

import json

import pytest

from codoc.loop import inbox, status
from codoc.model.event import Event, NodeOp, NodeOpKind
from codoc.store.db import open_store


@pytest.fixture
def codoc_dir(tmp_path):
    d = tmp_path / ".codoc"
    d.mkdir()
    return str(d)


# -- inbox ----------------------------------------------------------------
def test_inbox_empty_when_missing(codoc_dir):
    assert inbox.read_verdicts(codoc_dir) == []


def test_inbox_append_and_read(codoc_dir):
    inbox.append_verdict(codoc_dir, "e-1", accept=True)
    inbox.append_verdict(codoc_dir, "e-2", accept=False)
    verdicts = inbox.read_verdicts(codoc_dir)
    assert [(v.event_id, v.accept) for v in verdicts] == [("e-1", True), ("e-2", False)]


def test_inbox_clear(codoc_dir):
    inbox.append_verdict(codoc_dir, "e-1", accept=True)
    inbox.clear(codoc_dir)
    assert inbox.read_verdicts(codoc_dir) == []
    inbox.clear(codoc_dir)  # idempotent


def test_inbox_tolerates_garbage(codoc_dir):
    inbox.inbox_path(codoc_dir).write_text("{ not json")
    assert inbox.read_verdicts(codoc_dir) == []


# -- the IDE's verdict append-log (inbox.host.jsonl) ----------------------
# The extension host holds no cross-process lock, so its old read-modify-write of
# inbox.json could land inside drop_verdicts' locked window and erase a click.
# It now APPENDS one JSON line per click; every reader merges the log first.

def _host_click(codoc_dir, event_id, accept=True):
    with inbox.host_verdicts_path(codoc_dir).open("a", encoding="utf-8") as f:
        f.write(json.dumps({"event_id": event_id, "accept": accept}) + "\n")


def test_host_log_merges_on_read_and_is_consumed(codoc_dir):
    _host_click(codoc_dir, "e-1", True)
    _host_click(codoc_dir, "e-2", False)

    verdicts = inbox.read_verdicts(codoc_dir)

    assert {(v.event_id, v.accept) for v in verdicts} == {("e-1", True), ("e-2", False)}
    assert not inbox.host_verdicts_path(codoc_dir).exists()
    # …and the merged verdicts persisted into inbox.json (a second read agrees).
    assert len(inbox.read_verdicts(codoc_dir)) == 2


def test_host_log_last_click_wins_per_event(codoc_dir):
    """A double-click (or a change of mind) dedups exactly as the old direct
    writer did: one verdict per event id, the newest one."""
    _host_click(codoc_dir, "e-1", True)
    _host_click(codoc_dir, "e-1", False)

    verdicts = inbox.read_verdicts(codoc_dir)

    assert [(v.event_id, v.accept) for v in verdicts] == [("e-1", False)]


def test_host_log_merges_with_existing_inbox_verdicts(codoc_dir):
    inbox.append_verdict(codoc_dir, "e-old", accept=True)
    _host_click(codoc_dir, "e-new", False)

    verdicts = {v.event_id: v.accept for v in inbox.read_verdicts(codoc_dir)}

    assert verdicts == {"e-old": True, "e-new": False}


def test_host_log_torn_final_line_is_skipped_not_fatal(codoc_dir):
    """A crashed host can leave a half-written last line — everything parseable
    still merges, and the log is consumed rather than wedging every reader."""
    _host_click(codoc_dir, "e-1", True)
    with inbox.host_verdicts_path(codoc_dir).open("a", encoding="utf-8") as f:
        f.write('{"event_id": "e-2", "acc')  # torn append

    verdicts = inbox.read_verdicts(codoc_dir)

    assert [(v.event_id, v.accept) for v in verdicts] == [("e-1", True)]
    assert not inbox.host_verdicts_path(codoc_dir).exists()


def test_click_during_drop_window_survives(codoc_dir):
    """THE lost-click scenario: the daemon's drop_verdicts is mid-flight when a new
    click lands. As an append it cannot be erased by drop's write-back — drop's
    locked read merges it in and only removes the ids it processed."""
    inbox.append_verdict(codoc_dir, "e-processed", accept=True)
    _host_click(codoc_dir, "e-fresh-click", True)

    inbox.drop_verdicts(codoc_dir, {"e-processed"})

    verdicts = inbox.read_verdicts(codoc_dir)
    assert [(v.event_id, v.accept) for v in verdicts] == [("e-fresh-click", True)]


# -- status ---------------------------------------------------------------
def _state(codoc_dir):
    return json.loads(status.status_path(codoc_dir).read_text())["state"]


def test_status_in_sync_when_no_pending(codoc_dir):
    store = open_store(codoc_dir)
    try:
        status.refresh_status(codoc_dir, store)
    finally:
        store.close()
    assert _state(codoc_dir) == status.IN_SYNC


def test_status_code_drift_with_pending(codoc_dir):
    store = open_store(codoc_dir)
    try:
        store.append_event(Event(source="loop_a", applied=False,
                                 op=NodeOp(kind=NodeOpKind.ADD_NODE, title="x", description="y")))
        status.refresh_status(codoc_dir, store)
    finally:
        store.close()
    payload = json.loads(status.status_path(codoc_dir).read_text())
    assert payload["state"] == status.CODE_DRIFT and payload["pending"] == 1


def test_status_realizing_override(codoc_dir):
    store = open_store(codoc_dir)
    try:
        status.refresh_status(codoc_dir, store, realizing=True, detail="implementing")
    finally:
        store.close()
    payload = json.loads(status.status_path(codoc_dir).read_text())
    assert payload["state"] == status.REALIZING and payload["detail"] == "implementing"


def test_status_awaiting_impl_when_realize_md_present(codoc_dir):
    """A queued realize.md is an active obligation: refresh_status must report
    awaiting_impl (not in_sync) even with zero pending proposals, so a later
    code-side pass cannot orphan the directive."""
    from pathlib import Path
    Path(codoc_dir, "realize.md").write_text(
        "preamble\n\n### 1. RETIRE FEATURE: \"x\"\n\n### 2. NEW FEATURE: \"y\"\n")
    store = open_store(codoc_dir)
    try:
        status.refresh_status(codoc_dir, store)  # no awaiting_impl flag passed
    finally:
        store.close()
    payload = json.loads(status.status_path(codoc_dir).read_text())
    assert payload["state"] == status.AWAITING_IMPL
    assert payload["pending"] == 2  # one per ### directive heading


def test_status_ignores_empty_realize_md(codoc_dir):
    from pathlib import Path
    Path(codoc_dir, "realize.md").write_text("   \n")
    store = open_store(codoc_dir)
    try:
        status.refresh_status(codoc_dir, store)
    finally:
        store.close()
    assert _state(codoc_dir) == status.IN_SYNC


def test_code_side_pass_does_not_orphan_queued_realize_md(codoc_dir):
    """The regression the fix targets: a code-side reflection (which calls
    refresh_status with no awaiting_impl flag and zero pending proposals) must NOT
    clobber a queued realize.md back to in_sync — it stays awaiting_impl."""
    from pathlib import Path
    Path(codoc_dir, "realize.md").write_text("### 1. RETIRE FEATURE: \"x\"\n")
    store = open_store(codoc_dir)
    try:
        # simulate the tail of a Loop A / reconcile pass: no proposals, no flags
        status.refresh_status(codoc_dir, store)
    finally:
        store.close()
    assert _state(codoc_dir) == status.AWAITING_IMPL


# -- realizing lease (WS1.5) ----------------------------------------------
def test_realizing_lease_preserved_when_fresh(codoc_dir):
    """A live realize pass (fresh progress write, queue still present) must
    survive an interleaved refresh_status call with no explicit flag — this is
    the fix for the bug where `codoc_status` silently clobbered a genuinely
    active pass back to `awaiting_impl` (nothing ever passed realizing=True)."""
    from pathlib import Path
    Path(codoc_dir, "realize.md").write_text("### 1. NEW FEATURE: \"y\"\n")
    store = open_store(codoc_dir)
    try:
        # A progress write stamps REALIZING directly (as codoc_realize_progress does).
        status.write_status(codoc_dir, status.REALIZING, detail="implementing 1/2")
        # An unrelated call (no realizing= flag) must NOT clobber it.
        status.refresh_status(codoc_dir, store)
    finally:
        store.close()
    assert _state(codoc_dir) == status.REALIZING


def test_realizing_lease_decays_when_stale(codoc_dir):
    """A crashed/cancelled pass (no progress write in REALIZING_LEASE_SECONDS)
    must NOT be preserved — this is what un-wedges a fresh /codoc:sync."""
    from pathlib import Path
    import os
    import time

    Path(codoc_dir, "realize.md").write_text("### 1. NEW FEATURE: \"y\"\n")
    store = open_store(codoc_dir)
    try:
        status.write_status(codoc_dir, status.REALIZING, detail="implementing 1/2")
        # Backdate the file's mtime past the lease TTL.
        old = time.time() - status.REALIZING_LEASE_SECONDS - 1
        os.utime(status.status_path(codoc_dir), (old, old))
        status.refresh_status(codoc_dir, store)
    finally:
        store.close()
    # Decays to the ground truth: realize.md is still queued → awaiting_impl.
    assert _state(codoc_dir) == status.AWAITING_IMPL


def test_realizing_lease_ignored_without_queue(codoc_dir):
    """Even a fresh on-disk `realizing` must not be preserved once the queue is
    gone (realize.md deleted) — nothing can still be "in progress" with no queue."""
    store = open_store(codoc_dir)
    try:
        status.write_status(codoc_dir, status.REALIZING, detail="implementing 2/2")
        status.refresh_status(codoc_dir, store)  # no realize.md present at all
    finally:
        store.close()
    assert _state(codoc_dir) == status.IN_SYNC


def test_refresh_preserves_live_progress_without_rewriting(codoc_dir):
    """Preserving a fresh lease must NOT rewrite status.json: a rewrite would
    blank the live pass's own "implementing M/N" detail/pending AND stamp a new
    mtime — renewing the very lease being checked, so a crashed pass would never
    decay while ambient passes keep calling refresh_status."""
    import json
    import os
    import time
    from pathlib import Path

    Path(codoc_dir, "realize.md").write_text("### 1. NEW FEATURE: \"y\"\n")
    store = open_store(codoc_dir)
    try:
        status.write_status(codoc_dir, status.REALIZING, pending=1,
                            detail="implementing 1/2: y")
        # Age the file a little so an (incorrect) rewrite is detectable by mtime.
        old = time.time() - 100
        os.utime(status.status_path(codoc_dir), (old, old))
        status.refresh_status(codoc_dir, store)
    finally:
        store.close()
    data = json.loads(status.status_path(codoc_dir).read_text())
    assert data["state"] == status.REALIZING
    assert data["detail"] == "implementing 1/2: y"      # progress NOT blanked
    assert data["pending"] == 1
    assert abs(status.status_path(codoc_dir).stat().st_mtime - old) < 1  # NOT rewritten


def test_realizing_lease_decays_despite_intermediate_refreshes(codoc_dir):
    """The lease clock runs from the last GENUINE progress write: repeated
    sub-TTL refresh_status calls in between must not extend it (the
    self-renewal bug), so the state still decays at TTL-from-progress-write."""
    import os
    import time
    from pathlib import Path

    Path(codoc_dir, "realize.md").write_text("### 1. NEW FEATURE: \"y\"\n")
    store = open_store(codoc_dir)
    try:
        status.write_status(codoc_dir, status.REALIZING, detail="implementing 1/2")
        old = time.time() - (status.REALIZING_LEASE_SECONDS - 50)
        os.utime(status.status_path(codoc_dir), (old, old))
        status.refresh_status(codoc_dir, store)   # sub-TTL check: preserved, no rewrite
        old = time.time() - status.REALIZING_LEASE_SECONDS - 1
        os.utime(status.status_path(codoc_dir), (old, old))
        status.refresh_status(codoc_dir, store)   # past TTL: decays to ground truth
    finally:
        store.close()
    assert _state(codoc_dir) == status.AWAITING_IMPL


def test_realizing_explicit_false_overrides_fresh_lease(codoc_dir):
    """An engine's own end-of-pass cleanup (sdk_realize's finally block) must be
    able to force a recompute even when the lease would otherwise look fresh —
    it authoritatively knows the pass just ended."""
    from pathlib import Path
    Path(codoc_dir, "realize.md").write_text("### 1. NEW FEATURE: \"y\"\n")
    store = open_store(codoc_dir)
    try:
        status.write_status(codoc_dir, status.REALIZING, detail="implementing 1/1")
        status.refresh_status(codoc_dir, store, realizing=False)
    finally:
        store.close()
    assert _state(codoc_dir) == status.AWAITING_IMPL


def test_touch_lease_renews_a_live_pass_mid_directive(codoc_dir):
    """Review #12: a single directive that runs longer than the lease TTL without
    an intervening progress write must not decay. A heartbeat re-stamps the lease
    from ongoing activity, so a check just under one TTL AFTER the heartbeat still
    reads fresh — and the live detail/pending are preserved (only the clock moves)."""
    import os
    import time
    from pathlib import Path

    Path(codoc_dir, "realize.md").write_text("### 1. NEW FEATURE: \"y\"\n")
    status.write_status(codoc_dir, status.REALIZING, pending=1, detail="implementing 1/1: y")
    # The pass has been quiet for nearly a full TTL (a long single directive).
    old = time.time() - (status.REALIZING_LEASE_SECONDS - 5)
    os.utime(status.status_path(codoc_dir), (old, old))

    assert status.touch_realizing_lease(codoc_dir) is True

    data = json.loads(status.status_path(codoc_dir).read_text())
    assert data["detail"] == "implementing 1/1: y"   # preserved
    assert data["pending"] == 1
    # The lease clock reset to ~now, so the pass survives another full TTL window.
    assert status.realizing_is_fresh(codoc_dir) is True
    assert time.time() - status.status_path(codoc_dir).stat().st_mtime < 5


def test_touch_lease_is_noop_when_not_realizing(codoc_dir):
    """The heartbeat can only refresh a lease the pass genuinely holds — it must
    never create or resurrect `realizing` from any other state."""
    status.write_status(codoc_dir, status.AWAITING_IMPL, detail="run /codoc:sync")
    assert status.touch_realizing_lease(codoc_dir) is False
    assert _state(codoc_dir) == status.AWAITING_IMPL


def test_realize_md_outranks_code_drift(codoc_dir):
    """A queued realize.md outranks pending proposals: status reports awaiting_impl
    (not code_drift), so the IDE keeps prompting /codoc:sync even when new
    proposals coexist. Proposals still render inline in the tree regardless."""
    from pathlib import Path
    Path(codoc_dir, "realize.md").write_text("### 1. NEW FEATURE: \"y\"\n")
    store = open_store(codoc_dir)
    try:
        store.append_event(Event(source="loop_a", applied=False,
                                 op=NodeOp(kind=NodeOpKind.ADD_NODE, title="x", description="y")))
        status.refresh_status(codoc_dir, store)
    finally:
        store.close()
    assert _state(codoc_dir) == status.AWAITING_IMPL
