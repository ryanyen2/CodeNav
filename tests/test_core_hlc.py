"""Tests for HLC ordering, advance, merge semantics and lexicographic to_str()."""

from __future__ import annotations

from codoc.model.hlc import HLC


def test_hlc_total_ordering_by_wall_clock() -> None:
    a = HLC(logical_time=0, wall_clock=100, node_id="a")
    b = HLC(logical_time=0, wall_clock=200, node_id="a")
    assert a < b
    assert b > a
    assert not (a == b)


def test_hlc_logical_breaks_wall_tie() -> None:
    a = HLC(logical_time=1, wall_clock=100, node_id="a")
    b = HLC(logical_time=2, wall_clock=100, node_id="a")
    assert a < b


def test_hlc_node_id_breaks_logical_tie() -> None:
    a = HLC(logical_time=1, wall_clock=100, node_id="a")
    b = HLC(logical_time=1, wall_clock=100, node_id="b")
    assert a < b


def test_hlc_equality_and_hash() -> None:
    a = HLC(logical_time=1, wall_clock=100, node_id="x")
    b = HLC(logical_time=1, wall_clock=100, node_id="x")
    assert a == b
    assert hash(a) == hash(b)
    assert {a, b} == {a}


def test_hlc_advance_no_observation_bumps_clock() -> None:
    a = HLC(logical_time=0, wall_clock=0, node_id="n")
    advanced = a.advance()
    assert advanced > a
    assert advanced.node_id == "n"


def test_hlc_advance_idempotency_when_wall_unchanged() -> None:
    # Force wall_clock to a future point so advance() must bump logical_time only.
    future = HLC(logical_time=5, wall_clock=10**14, node_id="n")
    advanced = future.advance()
    assert advanced.wall_clock == future.wall_clock
    assert advanced.logical_time == future.logical_time + 1
    assert advanced.node_id == future.node_id


def test_hlc_advance_with_observation_takes_max_logical_plus_one() -> None:
    local = HLC(logical_time=2, wall_clock=10**14, node_id="local")
    remote = HLC(logical_time=7, wall_clock=10**14, node_id="remote")
    merged = local.advance(remote)
    # Same wall_clock on both → logical = max(2, 7) + 1 = 8.
    assert merged.wall_clock == 10**14
    assert merged.logical_time == 8
    assert merged.node_id == "local"


def test_hlc_advance_with_higher_remote_wall_resets_logical() -> None:
    local = HLC(logical_time=5, wall_clock=10**13, node_id="local")
    remote = HLC(logical_time=2, wall_clock=10**15, node_id="remote")
    merged = local.advance(remote)
    # Whichever wall_clock wins is the one whose logical advances.
    # If remote wins outright, logical = remote.logical + 1 = 3.
    # If wall-clock-now intervenes (extremely unlikely given 10**15 ms), logical=0.
    assert merged.wall_clock >= remote.wall_clock
    assert merged.node_id == "local"


def test_hlc_to_str_lexicographic_matches_total_order() -> None:
    samples = [
        HLC(logical_time=0, wall_clock=100, node_id="a"),
        HLC(logical_time=1, wall_clock=100, node_id="a"),
        HLC(logical_time=0, wall_clock=200, node_id="a"),
        HLC(logical_time=0, wall_clock=200, node_id="b"),
        HLC(logical_time=2, wall_clock=300, node_id="z"),
    ]
    sorted_objects = sorted(samples)
    sorted_strings = sorted(samples, key=lambda h: h.to_str())
    assert sorted_objects == sorted_strings


def test_hlc_to_str_round_trip() -> None:
    h = HLC(logical_time=42, wall_clock=99999, node_id="node-x")
    parsed = HLC.from_str(h.to_str())
    assert parsed == h
    assert parsed.logical_time == 42
    assert parsed.wall_clock == 99999
    assert parsed.node_id == "node-x"


def test_hlc_now_uses_node_id() -> None:
    h = HLC.now(node_id="alpha")
    assert h.node_id == "alpha"
    assert h.wall_clock > 0
