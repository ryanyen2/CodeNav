"""U3 / KTD3 — one lifecycle projection, not per-plugin status.

Block lifecycle does NOT spawn a parallel per-plugin phase. A transient block
contributes no durable state at all (it rides the one-shot steers channel and is
consumed by realization); a persistent block inherits its feature's single
`compute_phases` value. This guards against re-fragmenting the projection — the
documented dominant source of past merge fragility.
"""
from __future__ import annotations

import pytest

from codoc.codoc_file.render import write_tree
from codoc.loop.phase import project_from_store
from codoc.model.binding import Binding
from codoc.model.block import Block, BlockLifecycle, Provenance
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def codoc_dir(tmp_path):
    d = tmp_path / ".codoc"; d.mkdir()
    return d


def _seed(d):
    with open_store(d) as s:
        f = Feature(title="Auth", description="Login.")
        s.upsert_feature(f)
        s.upsert_binding(Binding(feature_id=f.id, file="a.py",
                                 symbol_path="a.py::login", fingerprint="h"))
        write_tree(s, d)
    return f.id


def _phase_of(d, fid):
    with open_store(d) as s:
        return project_from_store(s, str(d)).phase.get(fid)


def test_persistent_block_does_not_change_feature_phase(codoc_dir):
    fid = _seed(codoc_dir)
    before = _phase_of(codoc_dir, fid)
    with open_store(codoc_dir) as s:
        s.upsert_block(Block(feature_id=fid, kind="diagram", content="flowchart TB\n a-->b",
                             lifecycle=BlockLifecycle.PERSISTENT, provenance=Provenance.DERIVED))
    # the feature's single phase value is unchanged by adding a block — blocks are
    # NOT a parallel phase source (KTD3).
    assert _phase_of(codoc_dir, fid) == before


def test_transient_block_never_enters_the_sidecar(codoc_dir):
    fid = _seed(codoc_dir)
    with open_store(codoc_dir) as s:
        s.upsert_block(Block(feature_id=fid, kind="screenshot", content=".codoc/media/x.png",
                             lifecycle=BlockLifecycle.TRANSIENT, provenance=Provenance.HUMAN))
        write_tree(s, codoc_dir)
    from codoc.loop.fsio import read_json
    sidecar = read_json(codoc_dir / "tree.bindings.json", default={})
    # transient blocks contribute no durable projection (KTD4) — absent from the slice.
    assert not (sidecar.get("blocks") or {}).get(fid)


def test_phase_slice_is_keyed_by_feature_not_block(codoc_dir):
    fid = _seed(codoc_dir)
    with open_store(codoc_dir) as s:
        feature_ids = {f.id for f in s.list_features(include_retired=True)}
        s.upsert_block(Block(feature_id=fid, kind="diagram", content="x",
                             lifecycle=BlockLifecycle.PERSISTENT, provenance=Provenance.DERIVED))
        proj = project_from_store(s, str(codoc_dir))
    # every key is a feature id — no per-block phase keys leak in (KTD3). (SYNCED
    # features are omitted from the slice, so it may be empty — but never block-keyed.)
    assert set(proj.phase) <= feature_ids
