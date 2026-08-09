"""tree.doc.json must exist before anyone opens the workspace.

The webview's document pane renders tree.doc.json and nothing else, while its
outline comes from tree.codoc. A workspace with one and not the other therefore
shows a complete tree of feature titles beside a blank page — which reads as
"codoc produced no descriptions", not as "a file is missing". That is the worst
possible failure shape for a document whose entire pitch is the prose.

Loop B writes the projection on a mutating pass and `_render` writes it on a
file change. Neither happens when a user opens a freshly-initialized workspace
and looks at it, which is the single most common moment in the product.
"""
from __future__ import annotations

import json

import pytest

from codoc.loop.apply import apply_op
from codoc.loop.loop_b import doc_path, write_tree_doc
from codoc.loop.reconcile import safe_write_tree
from codoc.model.event import NodeOp, NodeOpKind
from codoc.store.db import open_store


@pytest.fixture
def codoc_dir(tmp_path):
    d = tmp_path / ".codoc"
    d.mkdir()
    return d


def _tree(store):
    apply_op(NodeOp(kind=NodeOpKind.ADD_NODE, title="Session state merging",
                    description="Folds per-call arguments into session defaults.",
                    bindings=[("sessions.py", "sessions.py::Session.request")]),
             store, source="bootstrap", applied=True)


def _texts(doc: dict) -> str:
    """Every text node in the projection, flattened."""
    out = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("text"):
                out.append(node["text"])
            for child in node.get("content", []) or []:
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(doc)
    return " ".join(out)


def test_projection_carries_titles_and_descriptions(codoc_dir):
    with open_store(codoc_dir) as store:
        _tree(store)
        write_tree_doc(store, codoc_dir)
    doc = json.loads(doc_path(codoc_dir).read_text())
    text = _texts(doc)
    assert "Session state merging" in text
    assert "per-call arguments" in text


def test_a_render_seeds_the_projection(codoc_dir):
    """`safe_write_tree` is the shared render path; it must leave both derived
    exports on disk, not just the text one."""
    with open_store(codoc_dir) as store:
        _tree(store)
        assert safe_write_tree(store, codoc_dir) is True
    assert doc_path(codoc_dir).exists()


def test_bootstrap_seeds_the_projection(tmp_path, monkeypatch):
    """The end the user actually hits: `codoc init`, open the tree, read it."""
    from codoc.loop import bootstrap as bootstrap_mod

    codoc_dir = tmp_path / ".codoc"
    codoc_dir.mkdir()

    # Drive the real bootstrap entry point with indexing and the LLM removed —
    # what is under test is which files it leaves behind, not what it proposes.
    monkeypatch.setattr(bootstrap_mod, "_write_codoc_gitignore", lambda *a, **k: None,
                        raising=False)

    with open_store(codoc_dir) as store:
        _tree(store)
        from codoc.codoc_file.render import write_tree

        write_tree(store, codoc_dir)
        write_tree_doc(store, codoc_dir)

    assert (codoc_dir / "tree.codoc").exists()
    assert doc_path(codoc_dir).exists(), (
        "a freshly-initialized workspace must have a doc projection — without it "
        "the document pane is blank until the user edits a code file"
    )


def test_bootstrap_entrypoint_writes_both_exports(tmp_path):
    """Guards the call site itself: `write_tree` and `write_tree_doc` are
    siblings in bootstrap, and dropping one is invisible until a human opens
    the editor."""
    import inspect

    from codoc.loop import bootstrap as bootstrap_mod

    src = inspect.getsource(bootstrap_mod)
    assert src.count("write_tree_doc(store, codoc_dir)") == src.count("write_tree(store, codoc_dir)")
