"""U6 — the CONSULT arrow reaches realization.

A CONSULT-capable PERSISTENT block (a reference url / image) feeds the realizing
agent context without round-tripping to code (KTD5/AE3). When a feature with such
a block has a directive queued (here via an inline steer), Loop B folds the
block's consult text into that feature's directive as a `Consult:` line — once per
feature — and never queues a directive for the ambient block on its own.
"""
from __future__ import annotations

import pytest

from codoc.codoc_file.render import write_tree
from codoc.loop.edits import Steer, append_steer
from codoc.loop.loop_b import realize_path, run_loop_b
from codoc.model.binding import Binding
from codoc.model.block import Block, BlockLifecycle, Provenance
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def codoc_dir(tmp_path):
    d = tmp_path / ".codoc"; d.mkdir()
    return str(d)


def _seed(codoc_dir, title, desc, *, blocks: list[tuple[str, str]] | None = None):
    """blocks = [(kind, content)] — persistent reference blocks on the feature."""
    with open_store(codoc_dir) as s:
        f = Feature(title=title, description=desc)
        s.upsert_feature(f)
        s.upsert_binding(Binding(feature_id=f.id, file="a.py",
                                 symbol_path="a.py::fn", fingerprint="h"))
        for kind, content in (blocks or []):
            s.upsert_block(Block(feature_id=f.id, kind=kind, content=content,
                                 lifecycle=BlockLifecycle.PERSISTENT,
                                 provenance=Provenance.HUMAN))
        write_tree(s, codoc_dir)
    return f.id


def test_url_block_consult_rides_a_feature_directive(codoc_dir, tmp_path):
    fid = _seed(codoc_dir, "Auth", "Login.", blocks=[("url", "https://example.com/spec")])
    append_steer(codoc_dir, Steer(feature_id=fid, text="use argon2", comment_id="c1"))
    res = run_loop_b(str(tmp_path), codoc_dir)
    assert res.directives
    body = realize_path(codoc_dir).read_text()
    assert "Consult: https://example.com/spec" in body  # the reference reached the agent


def test_image_block_consult_rides_a_feature_directive(codoc_dir, tmp_path):
    fid = _seed(codoc_dir, "Auth", "Login.", blocks=[("image", ".codoc/media/mock.png")])
    append_steer(codoc_dir, Steer(feature_id=fid, text="match the mock", comment_id="c1"))
    run_loop_b(str(tmp_path), codoc_dir)
    body = realize_path(codoc_dir).read_text()
    assert ".codoc/media/mock.png" in body


def test_consult_is_attached_once_per_feature(codoc_dir, tmp_path):
    fid = _seed(codoc_dir, "Auth", "Login.", blocks=[("url", "https://example.com/spec")])
    append_steer(codoc_dir, Steer(feature_id=fid, text="harden it", comment_id="c1"))
    run_loop_b(str(tmp_path), codoc_dir)
    body = realize_path(codoc_dir).read_text()
    assert body.count("https://example.com/spec") == 1


def test_ambient_block_queues_no_directive_on_its_own(codoc_dir, tmp_path):
    """A reference block never produces a realize directive by itself: with no
    edit/steer for the feature, nothing is queued."""
    _seed(codoc_dir, "Auth", "Login.", blocks=[("url", "https://example.com/spec")])
    res = run_loop_b(str(tmp_path), codoc_dir)
    assert not res.directives
    assert not realize_path(codoc_dir).exists()


def test_feature_without_consult_blocks_is_unchanged(codoc_dir, tmp_path):
    fid = _seed(codoc_dir, "Auth", "Login.")
    append_steer(codoc_dir, Steer(feature_id=fid, text="use argon2", comment_id="c1"))
    res = run_loop_b(str(tmp_path), codoc_dir)
    assert res.directives
    # the directive carries no Consult line when the feature has no reference media.
    assert all("Consult:" not in d for d in res.directives)
