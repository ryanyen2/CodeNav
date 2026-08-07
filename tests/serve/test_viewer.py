"""The hub tells each viewer what it may do — per connection, never shared.

The hub has always enforced capabilities, but the browser client had no way to
learn its own role: it drew the maintainer's affordances for everyone, a read
collaborator's settle came back 403, and the client's outbox dropped it (a
capability you lack never succeeds on retry) with nobody told.
"""
from __future__ import annotations

from codoc.serve.auth import Capability
from codoc.serve.payload import viewer_block


def test_each_capability_states_what_it_may_do():
    assert viewer_block(Capability.HANDOFF, "grace") == {
        "capability": "handoff", "login": "grace", "canSuggest": True, "canHandOff": True,
    }
    assert viewer_block(Capability.SUGGEST, "ada") == {
        "capability": "suggest", "login": "ada", "canSuggest": True, "canHandOff": False,
    }
    assert viewer_block(Capability.NONE) == {
        "capability": "none", "login": "", "canSuggest": False, "canHandOff": False,
    }


def test_the_block_derives_from_the_capability_not_a_parallel_table():
    """The two booleans must be the capability's own answers. A second table
    would drift from the one the routes enforce with, and the client would draw
    an affordance the hub then refuses — the exact failure this exists to end."""
    for cap in Capability:
        block = viewer_block(cap)
        assert block["canSuggest"] is cap.can_suggest()
        assert block["canHandOff"] is cap.can_hand_off()


def test_the_shared_payload_carries_no_viewer(tmp_path):
    """The load-bearing separation. ``PayloadStream`` computes one payload and
    hands it to every connected viewer, so a capability built into it would be
    whatever the first viewer happened to have — telling a contributor they can
    hand off, unlocking a button the server refuses. Worse than not knowing."""
    from codoc.serve.payload import build_browser_payload

    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    payload = build_browser_payload(codoc_dir)

    assert "viewer" not in payload
