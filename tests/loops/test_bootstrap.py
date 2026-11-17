"""Bootstrap: a tree from the per-file and organising passes, with coverage."""
from codoc.calculus.derive import description, title
from codoc.calculus.doc import fold_tree
from codoc.index.graph import Graph
from codoc.loops.bootstrap import bootstrap
from codoc.workspace import Workspace
from tests.helpers import ROWS, fake_file, fake_org


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
