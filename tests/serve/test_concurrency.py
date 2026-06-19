"""U9 — optimistic concurrency on settle (no stale clobber).

A whole-doc last-write-wins guarded by the store-derived version: a settle that
declares the version it edited from (``baseRev``) is rejected when the hub has
advanced past it, so a stale browser can't overwrite a newer state. Five flows:
no guard, equal, stale→conflict, ahead-accepted, and the no-clobber property."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from codoc.model.hlc import HLC
from codoc.serve.auth import Capability
from codoc.serve.dispatch import CommandError, dispatch
from codoc.serve.payload import payload_version


def _set_version(cd: str, wall: int) -> None:
    Path(cd).mkdir(parents=True, exist_ok=True)
    (Path(cd) / "status.json").write_text(json.dumps(
        {"state": "in_sync", "pending": 0, "at": HLC(wall_clock=wall, logical_time=0).to_str()}))


def test_settle_without_baserev_is_accepted(tmp_path):
    cd = str(tmp_path)
    _set_version(cd, 1000)
    res = dispatch({"kind": "doc-settle", "doc": {"type": "doc"}}, Capability.SUGGEST, cd)
    assert res["held"] is True


def test_settle_with_current_baserev_is_accepted(tmp_path):
    cd = str(tmp_path)
    _set_version(cd, 1000)
    res = dispatch({"kind": "doc-settle", "doc": {"type": "doc"},
                    "baseRev": payload_version(cd)}, Capability.SUGGEST, cd)
    assert res["held"] is True


def test_stale_settle_is_rejected_with_conflict(tmp_path):
    cd = str(tmp_path)
    _set_version(cd, 2000)
    with pytest.raises(CommandError) as ei:
        dispatch({"kind": "doc-settle", "doc": {"type": "doc"}, "baseRev": 0},
                 Capability.SUGGEST, cd)
    assert ei.value.status == 409


def test_ahead_baserev_is_not_treated_as_stale(tmp_path):
    cd = str(tmp_path)
    _set_version(cd, 1000)
    res = dispatch({"kind": "doc-settle", "doc": {"type": "doc"},
                    "baseRev": payload_version(cd) + 10 ** 9}, Capability.SUGGEST, cd)
    assert res["held"] is True


def test_stale_settle_does_not_clobber_newer_doc(tmp_path):
    cd = str(tmp_path)
    _set_version(cd, 1000)
    v1 = payload_version(cd)
    dispatch({"kind": "doc-settle", "doc": {"marker": "first"}, "baseRev": v1},
             Capability.SUGGEST, cd)
    _set_version(cd, 5000)  # another writer advanced the hub
    with pytest.raises(CommandError):
        dispatch({"kind": "doc-settle", "doc": {"marker": "stale"}, "baseRev": v1},
                 Capability.SUGGEST, cd)
    # the stale write was rejected BEFORE persisting → the first doc survives
    assert json.loads((tmp_path / "tree.doc.json").read_text()) == {"marker": "first"}
