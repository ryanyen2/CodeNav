"""Property and unit tests for AWMap, LWWRegister, and ORSet."""

from __future__ import annotations

import pycrdt
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from codoc.core.crdt.aw_map import AWMap
from codoc.core.crdt.lww import LWWRegister
from codoc.core.crdt.or_set import ORSet
from codoc.model.hlc import HLC


# ---------------------------------------------------------------------------
# AWMap
# ---------------------------------------------------------------------------


def _new_awmap() -> AWMap:
    doc = pycrdt.Doc()
    ymap: pycrdt.Map = pycrdt.Map()
    doc["aw"] = ymap
    return AWMap(ymap)


def test_awmap_add_then_get() -> None:
    m = _new_awmap()
    m.add("uuid-a", {"x": 1})
    assert m.get("uuid-a") == {"x": 1}
    assert m.has("uuid-a") is True


def test_awmap_remove_makes_get_return_none() -> None:
    m = _new_awmap()
    m.add("u1", {"v": 1})
    m.remove("u1")
    assert m.get("u1") is None
    assert m.has("u1") is False


def test_awmap_add_wins_over_remove() -> None:
    m = _new_awmap()
    m.add("u1", {"v": 1})
    m.remove("u1")
    m.add("u1", {"v": 2})
    assert m.get("u1") == {"v": 2}


def test_awmap_add_idempotent_same_value() -> None:
    m = _new_awmap()
    m.add("u1", {"v": 1})
    m.add("u1", {"v": 1})
    assert m.get("u1") == {"v": 1}
    assert m.has_concurrent_add_collision("u1") is False


def test_awmap_concurrent_add_collision_flag() -> None:
    m = _new_awmap()
    m.add("u1", {"v": 1})
    m.add("u1", {"v": 2})  # different value for same uuid → collision flag
    assert m.has_concurrent_add_collision("u1") is True


def test_awmap_remove_nonexistent_is_noop() -> None:
    m = _new_awmap()
    m.remove("missing")  # should not raise
    assert m.get("missing") is None


def test_awmap_items_excludes_tombstoned() -> None:
    m = _new_awmap()
    m.add("a", {"v": 1})
    m.add("b", {"v": 2})
    m.remove("a")
    items = dict(m.items())
    assert "a" not in items
    assert items["b"] == {"v": 2}


@given(
    keys=st.lists(st.text(min_size=1, max_size=8), min_size=1, max_size=10),
    values=st.lists(st.integers(min_value=0, max_value=100), min_size=1, max_size=10),
)
@settings(max_examples=25, deadline=None)
def test_awmap_replay_idempotence_property(keys: list[str], values: list[int]) -> None:
    m = _new_awmap()
    for k, v in zip(keys, values):
        m.add(k, {"v": v})
    # Replay every add with the same value: should be a no-op for non-tombstoned entries.
    snapshot = dict(m.items())
    for k, v in zip(keys, values):
        m.add(k, {"v": v})
    assert dict(m.items()) == snapshot


# ---------------------------------------------------------------------------
# LWWRegister
# ---------------------------------------------------------------------------


def _new_lww() -> LWWRegister:
    doc = pycrdt.Doc()
    ymap: pycrdt.Map = pycrdt.Map()
    doc["m"] = ymap
    return LWWRegister(ymap, slot="reg")


def test_lww_set_and_get() -> None:
    reg = _new_lww()
    reg.set("hello", HLC(logical_time=1, wall_clock=10, node_id="n"))
    value, has_conflict = reg.get()
    assert value == "hello"
    assert has_conflict is False


def test_lww_higher_hlc_overwrites() -> None:
    reg = _new_lww()
    reg.set("v1", HLC(logical_time=1, wall_clock=10, node_id="n"))
    reg.set("v2", HLC(logical_time=2, wall_clock=10, node_id="n"))
    value, _ = reg.get()
    assert value == "v2"


def test_lww_lower_hlc_discarded() -> None:
    reg = _new_lww()
    reg.set("winner", HLC(logical_time=10, wall_clock=100, node_id="n"))
    reg.set("loser", HLC(logical_time=1, wall_clock=10, node_id="n"))
    value, _ = reg.get()
    assert value == "winner"


def test_lww_idempotent_replay_same_node_same_hlc() -> None:
    reg = _new_lww()
    h = HLC(logical_time=5, wall_clock=10, node_id="n")
    reg.set("x", h)
    reg.set("x", h)  # idempotent
    value, has_conflict = reg.get()
    assert value == "x"
    assert has_conflict is False


def test_lww_concurrent_writes_record_conflict() -> None:
    reg = _new_lww()
    h_a = HLC(logical_time=1, wall_clock=10, node_id="a")
    h_b = HLC(logical_time=1, wall_clock=10, node_id="b")
    reg.set("from-a", h_a)
    reg.set("from-b", h_b)  # concurrent: same logical/wall, different node
    value, has_conflict = reg.get()
    assert has_conflict is True
    # Lex-higher node_id wins.
    assert value == "from-b"


def test_lww_concurrent_same_value_no_conflict() -> None:
    reg = _new_lww()
    h_a = HLC(logical_time=1, wall_clock=10, node_id="a")
    h_b = HLC(logical_time=1, wall_clock=10, node_id="b")
    reg.set("same", h_a)
    reg.set("same", h_b)
    value, has_conflict = reg.get()
    assert value == "same"
    assert has_conflict is False


def test_lww_resolve_conflict_clears_conflict_slot() -> None:
    reg = _new_lww()
    reg.set("from-a", HLC(logical_time=1, wall_clock=10, node_id="a"))
    reg.set("from-b", HLC(logical_time=1, wall_clock=10, node_id="b"))
    assert reg.get()[1] is True
    reg.resolve_conflict("chosen")
    value, has_conflict = reg.get()
    assert value == "chosen"
    assert has_conflict is False


# ---------------------------------------------------------------------------
# ORSet
# ---------------------------------------------------------------------------


def _new_orset() -> ORSet:
    doc = pycrdt.Doc()
    ymap: pycrdt.Map = pycrdt.Map()
    doc["set"] = ymap
    return ORSet(ymap)


def test_orset_add_makes_active() -> None:
    s = _new_orset()
    s.add("item-1", {"data": 1})
    assert s.is_active("item-1") is True


def test_orset_remove_then_inactive() -> None:
    s = _new_orset()
    s.add("item-1", {"data": 1})
    s.remove("item-1")
    assert s.is_active("item-1") is False


def test_orset_add_after_remove_wins() -> None:
    s = _new_orset()
    s.add("item-1", {"data": 1})
    s.remove("item-1")
    s.add("item-1", {"data": 2})  # new add-tag not seen by prior remove
    assert s.is_active("item-1") is True


def test_orset_active_items_returns_only_uncancelled() -> None:
    s = _new_orset()
    s.add("a", {"v": 1})
    s.add("b", {"v": 2})
    s.remove("a")
    actives = s.active_item_uuids()
    assert "a" not in actives
    assert "b" in actives


def test_orset_remove_nonexistent_noop() -> None:
    s = _new_orset()
    s.remove("nope")
    assert s.is_active("nope") is False


def test_orset_double_add_keeps_active() -> None:
    s = _new_orset()
    s.add("x", {"v": 1})
    s.add("x", {"v": 2})  # Second add updates value, mints new add-tag.
    assert s.is_active("x") is True


@given(
    item_ids=st.lists(st.text(min_size=1, max_size=4), min_size=1, max_size=8, unique=True),
)
@settings(max_examples=15, deadline=None)
def test_orset_add_remove_yields_inactive_property(item_ids: list[str]) -> None:
    s = _new_orset()
    for i, iid in enumerate(item_ids):
        s.add(iid, {"v": i})
    for iid in item_ids:
        s.remove(iid)
    for iid in item_ids:
        assert s.is_active(iid) is False
