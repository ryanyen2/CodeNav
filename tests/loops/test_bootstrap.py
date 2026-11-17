"""Bootstrap: a tree from the per-file and organising passes, with coverage."""
from codoc.calculus.derive import description, title
from codoc.calculus.doc import fold_tree
from codoc.index.graph import Graph
from codoc.loops.bootstrap import _ensure_file_coverage, bootstrap
from codoc.workspace import Workspace
from tests.helpers import ROWS, fake_file, fake_org, row


def test_bootstrap_builds_a_tree_with_coverage_and_themes(tmp_path):
    ws = Workspace(tmp_path / ".codoc")
    res = bootstrap(ws, ROWS, Graph(), propose_file=fake_file, propose_org=fake_org,
                    propose_the_brief=lambda *a, **k: {}, concurrency=1)
    assert res.features == 4 and not res.skipped
    roots = fold_tree(ws.store.headings())
    titles = {r.id: title(ws.store, r.id) for r in roots}
    assert sorted(titles.values()) == ["Networking", "Store"]
    net = next(r for r in roots if titles[r.id] == "Networking")
    assert [title(ws.store, c.id) for c in net.children] == ["HTTP client"]
    assert [title(ws.store, g.id) for g in net.children[0].children] == ["Sending"]
    client = net.children[0].id
    assert description(ws.store, client) == "Sends requests.\n\nRetries **once** on timeout."
    # Coverage: the helper the model ignored is bound to the file's node; the invented symbol is dropped.
    owner = {b.symbol: b.feature for b in ws.store.bindings.values()}
    assert "store/db.py::_helper" in owner and "store/db.py::nope" not in owner
    assert owner["store/db.py::_helper"] == owner["store/db.py::open"]
    # Every segment is the loop's.
    assert all(s.actor == "loop" for b in ws.store.ordered() for s in b.text().segs)


def test_the_module_chunk_joins_the_files_first_feature(tmp_path):
    """A node the model made for ``__module__`` alone is dropped and the chunk
    goes to the first top-level node; a parent that owns only it is kept."""
    f = "eval.py"
    rows = [row(f, f"{f}::__module__"), row(f, f"{f}::run"), row(f, f"{f}::score")]
    ops = [{"id": "n1", "kind": "add_node", "title": "Running", "bindings": [f"{f}::run"]},
           {"id": "n2", "kind": "add_node", "parent_id": "n1", "title": "Scoring", "bindings": [f"{f}::score"]},
           {"id": "n3", "kind": "add_node", "title": "Module imports", "bindings": [f"{f}::__module__"]}]
    out = _ensure_file_coverage(ops, rows, f)
    assert [o["title"] for o in out] == ["Running", "Scoring"]
    assert out[0]["bindings"] == [f"{f}::run", f"{f}::__module__"]

    parent = [{"id": "n1", "kind": "add_node", "title": "Optimizer", "bindings": [f"{f}::__module__"]},
              {"id": "n2", "kind": "add_node", "parent_id": "n1", "title": "Steps", "bindings": [f"{f}::run", f"{f}::score"]}]
    assert [o["bindings"] for o in _ensure_file_coverage(parent, rows, f)] == [
        [f"{f}::__module__"], [f"{f}::run", f"{f}::score"]]


def test_the_top_level_follows_the_order_of_the_reply(tmp_path):
    """A standalone feature is placed by a move with no parent; a move that
    names a theme added later waits for it."""
    ws = Workspace(tmp_path / ".codoc")

    def org(features, edges, **kw):
        ids = {f["title"]: f["id"] for f in features}
        return [{"kind": "move_node", "feature_id": ids["HTTP client"], "parent_id": "t1"},
                {"kind": "move_node", "feature_id": ids["Store"]},
                {"id": "t1", "kind": "add_node", "title": "Networking", "description": "On the wire."}]

    bootstrap(ws, ROWS, Graph(), propose_file=fake_file, propose_org=org,
              propose_the_brief=lambda *a, **k: {}, concurrency=1)
    roots = fold_tree(ws.store.headings())
    assert [title(ws.store, r.id) for r in roots] == ["Store", "Networking"]
    assert [title(ws.store, c.id) for c in roots[1].children] == ["HTTP client"]


def test_a_theme_titled_like_a_feature_is_dropped_with_its_moves(tmp_path):
    ws = Workspace(tmp_path / ".codoc")

    def org(features, edges, **kw):
        client = next(f["id"] for f in features if f["title"] == "HTTP client")
        return [{"id": "t1", "kind": "add_node", "title": "http client", "description": "Same name."},
                {"kind": "move_node", "feature_id": client, "parent_id": "t1"}]

    bootstrap(ws, ROWS, Graph(), propose_file=fake_file, propose_org=org,
              propose_the_brief=lambda *a, **k: {}, concurrency=1)
    assert [title(ws.store, r.id) for r in fold_tree(ws.store.headings())] == ["HTTP client", "Store"]
