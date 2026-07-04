"""U3 — the server-derived browser payload, across five repo states.

Robustness flows: (1) empty/freshly-init repo, (2) a flat tree, (3) a nested
tree, (4) features mid-realization with held drafts, (5) corrupt/missing control
files. Plus the version's monotonicity + restart-safety (the SSE drop-stale
guard depends on it)."""
from __future__ import annotations

import json
from pathlib import Path

from codoc.model.feature import Feature
from codoc.model.hlc import HLC
from codoc.serve.payload import build_browser_payload, payload_version
from codoc.store.db import open_store


def _seed(tmp_path, *, sidecar=None, status=None, doc=None, drafts=None, activity=None) -> str:
    cd = tmp_path / ".codoc"
    cd.mkdir(parents=True, exist_ok=True)
    if sidecar is not None:
        (cd / "tree.bindings.json").write_text(json.dumps(sidecar))
    if status is not None:
        (cd / "status.json").write_text(json.dumps(status))
    if doc is not None:
        (cd / "tree.doc.json").write_text(json.dumps(doc))
    if activity is not None:
        (cd / "activity.json").write_text(json.dumps(activity))
    if drafts is not None:
        (cd / "edits.json").write_text(json.dumps(
            {"version": 1, "edits": [], "intents": [],
             "drafts": [{"feature_id": f} for f in drafts]}))
    return str(cd)


def _status_at(hlc: HLC) -> dict:
    return {"state": "in_sync", "pending": 0, "at": hlc.to_str()}


# Flow 1 — empty / freshly-init repo: no files yet.
def test_empty_repo_degrades_to_empty_payload(tmp_path):
    cd = _seed(tmp_path)
    p = build_browser_payload(cd)
    assert p["nodes"] == {}
    assert p["roots"] == []
    assert p["doc"] is None
    assert p["status"]["state"] == "in_sync"
    assert p["rev"] == 0
    assert p["rootName"] == tmp_path.name


# Flow 2 — flat tree with bindings.
def test_flat_tree_with_bindings(tmp_path):
    sidecar = {
        "version": 5,
        "features": {
            "f-1": {"title": "Auth", "parent_id": None, "realized": True, "pitch": "auth"},
            "f-2": {"title": "Billing", "parent_id": None, "realized": True, "pitch": "pay"},
        },
        "by_feature": {"f-1": [{"file": "a.py", "symbol": "login"}], "f-2": []},
        "holds": [],
    }
    cd = _seed(tmp_path, sidecar=sidecar, status=_status_at(HLC.now()),
               doc={"type": "doc", "content": []})
    p = build_browser_payload(cd)
    assert set(p["nodes"]) == {"f-1", "f-2"}
    assert p["roots"] == ["f-1", "f-2"]  # sorted by title (Auth, Billing)
    assert p["nodes"]["f-1"]["refCount"] == 1
    assert p["nodes"]["f-1"]["bindings"] == [{"file": "a.py", "symbol": "login"}]
    assert p["pitches"]["f-1"] == "auth"
    assert p["doc"] == {"type": "doc", "content": []}
    assert p["rev"] > 0


# Flow 2b — dependency threads (reads / usedBy / refs) drive the flow panel.
def test_threads_from_feature_edges(tmp_path):
    sidecar = {
        "features": {
            "f-1": {"title": "Auth", "parent_id": None},
            "f-2": {"title": "Billing", "parent_id": None},
            "f-3": {"title": "Webhooks", "parent_id": None},
        },
        # f-2 depends on f-1 (weight 5) and f-3 (weight 9); self-loop dropped.
        "feature_edges": {
            "f-2": [
                {"to": "f-1", "weight": 5, "kinds": ["call"]},
                {"to": "f-3", "weight": 9, "kinds": ["import"]},
                {"to": "f-2", "weight": 99, "kinds": ["call"]},
            ],
        },
        "by_feature": {"f-2": [{"file": "pay.py", "symbol": "charge"}]},
    }
    cd = _seed(tmp_path, sidecar=sidecar, status=_status_at(HLC.now()))
    th = build_browser_payload(cd)["threads"]
    # f-2 reads f-3 then f-1 (ranked by weight desc), self-edge dropped
    assert [r["toId"] for r in th["f-2"]["reads"]] == ["f-3", "f-1"]
    assert th["f-2"]["refs"] == [{"file": "pay.py", "symbol": "charge"}]
    # the in-edges show up as usedBy on the targets
    assert [u["toId"] for u in th["f-1"]["usedBy"]] == ["f-2"]
    assert [u["toId"] for u in th["f-3"]["usedBy"]] == ["f-2"]
    # a feature with no edges/bindings is omitted entirely
    assert "f-1" in th and "reads" in th["f-1"]


# Flow 2c — agent phase + steps drive the heading dot / ghost-reveal / ribbon on the hub.
def test_phases_and_steps_from_activity(tmp_path):
    sidecar = {
        "features": {"f-1": {"title": "Auth", "parent_id": None}},
        "by_feature": {}, "by_file": {"login.py": [{"symbol": "login", "feature_id": "f-1", "feature_title": "Auth"}]},
    }
    activity = {
        "epoch": {"id": "e", "origin": "interactive", "open": True, "started_at": None, "ended_at": None},
        "features": {"f-1": {"phase": "editing"}},
        "recent": [
            {"tool": "Read", "file": "login.py", "feature_ids": ["f-1"], "at": "1", "phase": "editing"},
            {"tool": "Edit", "file": "login.py", "feature_ids": [], "at": "2", "phase": "editing"},
        ],
    }
    cd = _seed(tmp_path, sidecar=sidecar, status=_status_at(HLC.now()), activity=activity)
    sync = build_browser_payload(cd)["sync"]
    assert sync["phase"]["f-1"] == "editing"
    steps = sync["steps"]["f-1"]
    assert [s["label"] for s in steps] == ["reading login.py", "editing login.py"]
    assert [s["done"] for s in steps] == [True, False]   # last step active


def test_steps_empty_when_epoch_closed(tmp_path):
    sidecar = {"features": {"f-1": {"title": "Auth", "parent_id": None}}, "by_feature": {}}
    activity = {"epoch": {"id": "e", "origin": "interactive", "open": False, "started_at": None, "ended_at": None},
                "recent": [{"tool": "Edit", "file": "a.py", "feature_ids": ["f-1"], "at": "1", "phase": "editing"}]}
    cd = _seed(tmp_path, sidecar=sidecar, status=_status_at(HLC.now()), activity=activity)
    assert build_browser_payload(cd)["sync"]["steps"] == {}


# Flow 3 — nested tree: depth + sorted children.
def test_nested_tree_depth_and_children(tmp_path):
    sidecar = {
        "features": {
            "p": {"title": "Parent", "parent_id": None},
            "c1": {"title": "Beta", "parent_id": "p"},
            "c2": {"title": "Alpha", "parent_id": "p"},
        },
        "by_feature": {},
    }
    cd = _seed(tmp_path, sidecar=sidecar, status=_status_at(HLC.now()))
    p = build_browser_payload(cd)
    assert p["roots"] == ["p"]
    assert p["nodes"]["p"]["depth"] == 0
    assert p["nodes"]["p"]["children"] == ["c2", "c1"]  # Alpha before Beta
    assert p["nodes"]["c1"]["depth"] == 1
    assert p["nodes"]["c2"]["depth"] == 1


# Flow 4 — features mid-realization with held drafts.
def test_holds_and_drafts(tmp_path):
    sidecar = {
        "features": {"f-1": {"title": "X", "parent_id": None}},
        "by_feature": {},
        "holds": ["f-1", "f-2"],
        "hold_detail": {"f-1": {"kind": "amend"}},
    }
    cd = _seed(tmp_path, sidecar=sidecar,
               status={"state": "awaiting_impl", "pending": 1, "at": HLC.now().to_str()},
               drafts=["f-1"])
    p = build_browser_payload(cd)
    assert p["awaitingAI"] == ["f-1", "f-2"]
    assert p["drafts"] == ["f-1"]  # intersection of holds & host drafts
    assert p["holdDetail"] == {"f-1": {"kind": "amend"}}
    assert p["status"]["state"] == "awaiting_impl"


# Flow 5 — corrupt / malformed control files must not crash a browser.
def test_corrupt_files_are_tolerant(tmp_path):
    cd = tmp_path / ".codoc"
    cd.mkdir(parents=True)
    (cd / "tree.bindings.json").write_text("{ not json")
    (cd / "status.json").write_text("garbage")
    p = build_browser_payload(str(cd))
    assert p["nodes"] == {}
    assert p["rev"] == 0


# Flow 5b — a malformed parent cycle must not recurse forever.
def test_parent_cycle_is_safe(tmp_path):
    sidecar = {
        "features": {
            "a": {"title": "A", "parent_id": "b"},
            "b": {"title": "B", "parent_id": "a"},
        },
        "by_feature": {},
    }
    cd = _seed(tmp_path, sidecar=sidecar, status=_status_at(HLC.now()))
    p = build_browser_payload(cd)  # must terminate
    assert set(p["nodes"]) == {"a", "b"}


# Flow 6 — the store is the source of truth: when codoc.db exists, the doc is the
# store projection (R3), carrying localId + per-feature version on the heading.
def test_payload_doc_uses_store_projection_when_db_present(tmp_path):
    cd = _seed(tmp_path, status=_status_at(HLC.now()))
    with open_store(cd) as s:
        f = Feature(title="Auth", description="Login and sessions.", local_id="lid-1")
        s.upsert_feature(f)
    doc = build_browser_payload(cd)["doc"]
    head = next(b for b in doc["content"] if b["type"] == "featureHeading")
    assert head["attrs"]["fid"] == f.id
    assert head["attrs"]["localId"] == "lid-1"
    assert "version" in head["attrs"]
    assert head["content"][0]["text"] == "Auth"


def test_payload_doc_falls_back_to_text_without_db(tmp_path):
    # No codoc.db → the existing tree.doc.json / tree.codoc behavior is preserved.
    cd = _seed(tmp_path, status=_status_at(HLC.now()), doc={"type": "doc", "content": []})
    (Path(cd) / "tree.codoc").write_text("- Auth  ⟨f-1⟩\n    Login.\n")
    doc = build_browser_payload(cd)["doc"]
    assert [b["attrs"]["fid"] for b in doc["content"] if b["type"] == "featureHeading"] == ["f-1"]


def test_payload_version_monotonic_and_restart_safe(tmp_path):
    cd = _seed(tmp_path, status=_status_at(HLC(wall_clock=1000, logical_time=0)))
    v1 = payload_version(cd)
    _seed(tmp_path, status=_status_at(HLC(wall_clock=1000, logical_time=5)))
    v2 = payload_version(cd)
    _seed(tmp_path, status=_status_at(HLC(wall_clock=2000, logical_time=0)))
    v3 = payload_version(cd)
    assert v1 < v2 < v3
    # restart-safe: the version comes from the durable wall-clock HLC, never a
    # per-process counter, so it cannot regress when the server restarts.
    assert payload_version(cd) == v3
