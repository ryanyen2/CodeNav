"""export_markdown — the tool-free document (e.g. a baseline CLAUDE.md)."""
from __future__ import annotations

import pytest

from codoc.codoc_file.export import export_markdown
from codoc.model.binding import Binding
from codoc.model.event import Event, NodeOp, NodeOpKind
from codoc.model.feature import Feature
from codoc.store.db import open_store


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path)
    yield s
    s.close()


def test_exports_headings_prose_and_code_lines(store):
    parent = Feature(title="Build pipeline", description="Turns sources into a site.")
    store.upsert_feature(parent)
    child = Feature(title="Incremental cache", parent_id=parent.id,
                    description="Skips unchanged work between builds.")
    store.upsert_feature(child)
    store.upsert_binding(Binding(feature_id=child.id, file="cache.py",
                                 symbol_path="cache.py::BuildCache", fingerprint=""))

    md = export_markdown(store, title="hearth feature guide")

    assert md.splitlines()[0] == "# hearth feature guide"
    assert "## Build pipeline" in md
    assert "### Incremental cache" in md          # depth → heading level
    assert "Code: `cache.py::BuildCache`" in md
    assert "⟨" not in md                           # no feature ids anywhere


def test_codoc_links_flatten_to_plain_citations(store):
    f = Feature(title="Renderer", description=(
        "Renders pages via [render_markdown()](codoc:markdown.py#render_markdown) "
        "and honours [the config](codoc:config.py#SiteConfig)."))
    store.upsert_feature(f)

    md = export_markdown(store)

    assert "codoc:" not in md
    assert "`markdown.py::render_markdown`" in md
    assert "the config (`config.py::SiteConfig`)" in md   # label kept when it adds info


def test_retired_and_pending_are_omitted(store):
    live = Feature(title="Live")
    store.upsert_feature(live)
    dead = Feature(title="Dead", retired=True)
    store.upsert_feature(dead)
    store.append_event(Event(source="plan", applied=False,
                             op=NodeOp(kind=NodeOpKind.ADD_NODE, title="Ghost")))

    md = export_markdown(store)

    assert "Live" in md and "Dead" not in md and "Ghost" not in md
