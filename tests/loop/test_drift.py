"""U4 — the per-feature drift/trust signal (loop-computed slice + drift.json).

Drift is computed in the loop pass (which has the fresh index) and persisted to
``.codoc/drift.json``; ``render.write_sidecar`` re-emits it passively as the
``feature_drift`` slice — never reading the index itself (KTD2).

Three states, of which only two are recorded (``followed`` is the absence of an
entry → no badge): ``questioned`` (realized feature owns a modified bound chunk,
prose un-amended) and ``binding-lost`` (realized feature lost its last binding).
Held features and unrealized placeholders are excluded (KTD5 / classify row 13).
"""
from __future__ import annotations

import pytest

from codoc.codoc_file.render import write_sidecar
from codoc.loop import edits as edits_channel
from codoc.loop.diff import ChangeSet, ChunkRef
from codoc.loop.edits import (
    DRIFT_BINDING_LOST,
    DRIFT_QUESTIONED,
    drift_path,
    read_drift,
    write_drift,
)
from codoc.loop.loop_a import apply_changeset
from codoc.model.binding import Binding
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def codoc_dir(tmp_path):
    d = tmp_path / ".codoc"
    d.mkdir()
    return str(d)


@pytest.fixture
def store(codoc_dir):
    s = open_store(codoc_dir)
    yield s
    s.close()


def _propose_nothing(*_a, **_k):
    return []


def _bind(store, fid, file, sym, fp):
    store.upsert_binding(Binding(feature_id=fid, file=file, symbol_path=sym, fingerprint=fp))


def _modified(file, sym, tok="new"):
    return ChangeSet(modified=[ChunkRef(file, sym, tok, source="src")])


def _removed(file, sym, tok="old"):
    return ChangeSet(removed=[ChunkRef(file, sym, tok)])


# ─── the three states ────────────────────────────────────────────────────────

def test_modified_bound_chunk_is_questioned(store, codoc_dir):
    f = Feature(title="Validator", description="Validates input.")
    store.upsert_feature(f)
    _bind(store, f.id, "v.py", "v.py::check", "old")  # fingerprint != new code

    apply_changeset(_modified("v.py", "v.py::check"), store,
                    propose=_propose_nothing, codoc_dir=codoc_dir)

    assert read_drift(codoc_dir) == {f.id: DRIFT_QUESTIONED}


def test_lost_last_binding_is_binding_lost(store, codoc_dir):
    f = Feature(title="Validator", description="Validates input.")
    store.upsert_feature(f)
    _bind(store, f.id, "v.py", "v.py::check", "h")

    apply_changeset(_removed("v.py", "v.py::check"), store,
                    propose=_propose_nothing, codoc_dir=codoc_dir)

    assert read_drift(codoc_dir) == {f.id: DRIFT_BINDING_LOST}


def test_matching_fingerprint_is_followed_not_recorded(store, codoc_dir):
    """A chunk whose fingerprint still matches is `followed` — no entry, no badge.
    An empty change set drifts nothing → an empty (cleared) drift map."""
    f = Feature(title="Validator", description="Validates input.")
    store.upsert_feature(f)
    _bind(store, f.id, "v.py", "v.py::check", "h")

    apply_changeset(ChangeSet(), store, propose=_propose_nothing, codoc_dir=codoc_dir)

    assert read_drift(codoc_dir) == {}  # absence = followed = no badge


def test_amended_this_pass_is_not_questioned(store, codoc_dir):
    """When the LLM amends a feature's prose this pass (bringing it in line with
    the new code) the feature is `followed`, not `questioned`."""
    from codoc.model.event import NodeOp, NodeOpKind

    f = Feature(title="Validator", description="Validates input.")
    store.upsert_feature(f)
    _bind(store, f.id, "v.py", "v.py::check", "old")

    # amend_on_change so the LLM pass runs on the in-place modification; the
    # injected propose returns an AMEND for the drifted feature.
    amend = NodeOp(kind=NodeOpKind.AMEND, feature_id=f.id,
                   description="Validates and sanitizes input.")
    apply_changeset(_modified("v.py", "v.py::check"), store,
                    propose=lambda *a, **k: [amend], amend_on_change=True,
                    codoc_dir=codoc_dir)

    assert read_drift(codoc_dir) == {}  # prose was addressed → followed


# ─── doc-wins exclusions (KTD5 / classify row 13) ────────────────────────────

def test_held_feature_excluded_even_when_modified(store, codoc_dir):
    f = Feature(title="Validator", description="Validates input.")
    store.upsert_feature(f)
    _bind(store, f.id, "v.py", "v.py::check", "old")

    apply_changeset(_modified("v.py", "v.py::check"), store,
                    propose=_propose_nothing, held={f.id}, codoc_dir=codoc_dir)

    assert read_drift(codoc_dir) == {}  # doc wins — never flagged stale


def test_unrealized_placeholder_excluded(store, codoc_dir):
    """An unrealized placeholder (realized=False) is never a trust signal — losing
    its (transient) binding mid-implementation must not light a `binding-lost`
    badge. (A *modified* placeholder binding would auto-realize the node via the
    REFRESH in apply._mutate, so the removal path is what exercises this gate.)"""
    f = Feature(title="Rate limiting", description="Caps request rates.", realized=False)
    store.upsert_feature(f)
    _bind(store, f.id, "v.py", "v.py::rl", "old")

    apply_changeset(_removed("v.py", "v.py::rl"), store,
                    propose=_propose_nothing, codoc_dir=codoc_dir)

    assert read_drift(codoc_dir) == {}  # placeholder — not a trust signal yet


# ─── mixed bindings: worst case wins ─────────────────────────────────────────

def test_mixed_bindings_binding_lost_dominates_questioned(store, codoc_dir):
    """A feature with one modified binding (questioned) AND its other (last)
    binding removed lands as `binding-lost` — having no code left is graver."""
    f = Feature(title="Validator", description="Validates input.")
    store.upsert_feature(f)
    _bind(store, f.id, "v.py", "v.py::check", "old")
    _bind(store, f.id, "v.py", "v.py::also", "old")

    # both owned chunks leave the index → the feature loses its last binding.
    cs = ChangeSet(removed=[ChunkRef("v.py", "v.py::check", "old"),
                            ChunkRef("v.py", "v.py::also", "old")])
    apply_changeset(cs, store, propose=_propose_nothing, codoc_dir=codoc_dir)

    assert read_drift(codoc_dir) == {f.id: DRIFT_BINDING_LOST}


def test_one_removed_but_code_remains_is_questioned_not_lost(store, codoc_dir):
    """A feature that loses ONE binding but keeps another is not `binding-lost`;
    if its surviving binding's code changed it is `questioned` instead."""
    f = Feature(title="Validator", description="Validates input.")
    store.upsert_feature(f)
    _bind(store, f.id, "v.py", "v.py::check", "old")  # survives, but modified
    _bind(store, f.id, "v.py", "v.py::gone", "old")   # removed

    cs = ChangeSet(modified=[ChunkRef("v.py", "v.py::check", "new", source="s")],
                   removed=[ChunkRef("v.py", "v.py::gone", "old")])
    apply_changeset(cs, store, propose=_propose_nothing, codoc_dir=codoc_dir)

    assert read_drift(codoc_dir) == {f.id: DRIFT_QUESTIONED}  # still owns code


# ─── persistence + render re-emission WITHOUT an index read ───────────────────

def test_drift_written_to_control_file(store, codoc_dir):
    f = Feature(title="Validator", description="Validates input.")
    store.upsert_feature(f)
    _bind(store, f.id, "v.py", "v.py::check", "old")

    apply_changeset(_modified("v.py", "v.py::check"), store,
                    propose=_propose_nothing, codoc_dir=codoc_dir)

    p = drift_path(codoc_dir)
    assert p.exists()
    import json
    data = json.loads(p.read_text())
    assert data["drift"] == {f.id: DRIFT_QUESTIONED}


def test_write_sidecar_reemits_drift_without_reading_index(store, codoc_dir, monkeypatch):
    """render.write_sidecar re-emits the persisted drift unchanged and NEVER
    reads the index (KTD2). We persist a drift map, blow up update_index /
    read_all_chunks if anyone calls them, and assert the sidecar carries the
    drift verbatim — proving render is a pure store/file read."""
    f = Feature(title="Validator", description="Validates input.")
    store.upsert_feature(f)
    _bind(store, f.id, "v.py", "v.py::check", "h")

    # The drift signal a prior loop pass left behind.
    write_drift(codoc_dir, {f.id: DRIFT_QUESTIONED})

    def _boom(*_a, **_k):  # pragma: no cover - asserts render never indexes
        raise AssertionError("write_sidecar must not read the index")

    monkeypatch.setattr("codoc.pipelines.indexing.runner.update_index", _boom)
    monkeypatch.setattr("codoc.pipelines.indexing.reader.read_all_chunks", _boom)

    write_sidecar(store, codoc_dir)

    import json
    from codoc.codoc_file.render import BINDINGS_FILENAME
    from pathlib import Path
    sidecar = json.loads((Path(codoc_dir) / BINDINGS_FILENAME).read_text())
    assert sidecar["version"] == 5
    assert sidecar["feature_drift"] == {f.id: DRIFT_QUESTIONED}  # re-emitted unchanged


def test_write_sidecar_missing_drift_file_is_empty(store, codoc_dir):
    """No drift.json (never a loop pass) → an empty feature_drift slice, tolerant."""
    f = Feature(title="Validator", description="Validates input.")
    store.upsert_feature(f)

    write_sidecar(store, codoc_dir)

    import json
    from codoc.codoc_file.render import BINDINGS_FILENAME
    from pathlib import Path
    sidecar = json.loads((Path(codoc_dir) / BINDINGS_FILENAME).read_text())
    assert sidecar["feature_drift"] == {}
