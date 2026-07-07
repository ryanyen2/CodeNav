"""U8 — the serve hub is a second block host (the surfaces axis).

The hub derives its browser payload from the `.codoc` file channel (KTD7) and
surfaces the v6 `blocks` slice READ-ONLY. This proves "many surfaces": the webview
and the hub render the SAME blocks from one protocol, with no host-side
re-derivation — the hub re-shapes the sidecar slice straight through.
"""
from __future__ import annotations

import pytest

from codoc.codoc_file.render import write_tree
from codoc.serve.payload import build_browser_payload
from codoc.model.binding import Binding
from codoc.model.block import Block, BlockLifecycle, Provenance
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def codoc_dir(tmp_path):
    d = tmp_path / ".codoc"; d.mkdir()
    with open_store(d) as s:
        f = Feature(title="Auth", description="Login.")
        s.upsert_feature(f)
        s.upsert_binding(Binding(feature_id=f.id, file="a.py",
                                 symbol_path="a.py::login", fingerprint="h"))
        s.upsert_block(Block(feature_id=f.id, kind="diagram", ord=0,
                             content="flowchart TB\n  login --> token",
                             lifecycle=BlockLifecycle.PERSISTENT, provenance=Provenance.DERIVED))
        s.upsert_block(Block(feature_id=f.id, kind="url", ord=1,
                             content="https://example.com/spec",
                             lifecycle=BlockLifecycle.PERSISTENT, provenance=Provenance.HUMAN))
        # a transient block must NEVER reach the sidecar / hub.
        s.upsert_block(Block(feature_id=f.id, kind="screenshot", ord=2,
                             content=".codoc/media/x.png",
                             lifecycle=BlockLifecycle.TRANSIENT, provenance=Provenance.HUMAN))
        write_tree(s, d)
    return str(d), f.id


def test_hub_payload_surfaces_persistent_blocks(codoc_dir):
    d, fid = codoc_dir
    payload = build_browser_payload(d)
    blocks = payload["blocks"][fid]
    kinds = [b["kind"] for b in blocks]
    assert kinds == ["diagram", "url"]            # ordered by ord, transient excluded
    assert all(b["lifecycle"] == "persistent" for b in blocks)


def test_hub_excludes_transient_blocks(codoc_dir):
    d, fid = codoc_dir
    payload = build_browser_payload(d)
    assert "screenshot" not in [b["kind"] for b in payload["blocks"][fid]]


def test_hub_and_sidecar_agree(codoc_dir):
    """The hub re-shapes the sidecar slice straight through — no re-derivation
    (the one exception, image mediaSrc, is additive and covered separately)."""
    from codoc.serve.payload import _sidecar
    d, fid = codoc_dir
    payload = build_browser_payload(d)
    assert payload["blocks"] == (_sidecar(d).get("blocks") or {})


def test_hub_payload_resolves_image_media_src(tmp_path):
    d = tmp_path / ".codoc"
    d.mkdir()
    with open_store(d) as s:
        f = Feature(title="Landing", description="The landing page.")
        s.upsert_feature(f)
        s.upsert_block(Block(feature_id=f.id, kind="image", ord=0,
                             content=".codoc/media/mock.png",
                             lifecycle=BlockLifecycle.PERSISTENT, provenance=Provenance.HUMAN))
        write_tree(s, d)
    payload = build_browser_payload(str(d))
    img = payload["blocks"][f.id][0]
    assert img["mediaSrc"] == "/api/media/mock.png"


def test_hub_payload_image_media_src_passthrough_for_external_url(tmp_path):
    d = tmp_path / ".codoc"
    d.mkdir()
    with open_store(d) as s:
        f = Feature(title="Landing", description="The landing page.")
        s.upsert_feature(f)
        s.upsert_block(Block(feature_id=f.id, kind="image", ord=0,
                             content="https://cdn.example/mock.png",
                             lifecycle=BlockLifecycle.PERSISTENT, provenance=Provenance.HUMAN))
        write_tree(s, d)
    payload = build_browser_payload(str(d))
    assert payload["blocks"][f.id][0]["mediaSrc"] == "https://cdn.example/mock.png"
