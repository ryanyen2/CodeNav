"""U8 — host conformance: the sidecar writer and the hub payload derive the SAME
blocks from the same protocol (the multi-surface parity claim, Python side)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from codoc.blocks.conformance import canonical_block_view
from codoc.codoc_file.render import BINDINGS_FILENAME, write_sidecar
from codoc.model.block import Block, Provenance
from codoc.model.feature import Feature
from codoc.serve.payload import build_browser_payload
from codoc.store.db import open_store

FIXTURE = Path(__file__).parent.parent / "fixtures" / "blocks_conformance.json"


def test_fixture_canonical_view_is_ordered():
    sidecar = json.loads(FIXTURE.read_text())
    view = canonical_block_view(sidecar)
    kinds = [b["kind"] for b in view["f-auth"]]
    assert kinds == ["diagram", "url"]  # ordered by ord


def test_v5_sidecar_has_empty_block_view():
    # a host fed a pre-blocks sidecar derives no blocks (back-compat)
    assert canonical_block_view({"version": 5, "features": {}}) == {}


def test_writer_and_hub_agree_on_blocks(tmp_path):
    """The two Python hosts (sidecar writer, hub payload) reproduce the same
    canonical block view — neither drops, mistypes, or reorders."""
    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()
    with open_store(codoc_dir) as s:
        f = Feature(id="f-auth", title="Auth")
        s.upsert_feature(f)
        s.upsert_block(Block(feature_id=f.id, kind="url", content="u", ord=1,
                             provenance=Provenance.HUMAN))
        s.upsert_block(Block(feature_id=f.id, kind="diagram", content="d", ord=0,
                             provenance=Provenance.DERIVED))
        write_sidecar(s, codoc_dir)

    sidecar = json.loads((codoc_dir / BINDINGS_FILENAME).read_text())
    writer_view = canonical_block_view(sidecar)
    hub_view = canonical_block_view({"blocks": build_browser_payload(codoc_dir)["blocks"]})

    assert writer_view == hub_view
    assert [b["kind"] for b in writer_view["f-auth"]] == ["diagram", "url"]
