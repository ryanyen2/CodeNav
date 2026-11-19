"""A real index update, in a subprocess: cocoindex runs one index per process."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _in_a_process(code: str, root, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "LANCEDB_LOG"} | {"PYTHONPATH": str(ROOT)}
    proc = subprocess.run([sys.executable, "-c", "import sys; root = sys.argv[1]; " + code, str(root), *args],
                          capture_output=True, text=True, env=env, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return proc


def test_a_first_index_prints_no_lance_warning(tmp_path):
    (tmp_path / "m.py").write_text("def a():\n    return 1\n")
    proc = _in_a_process("from codoc.index.runner import update_index; update_index(root, root + '/.codoc')", tmp_path)
    assert "WARN" not in proc.stderr and (tmp_path / ".codoc" / "lancedb").is_dir()


def test_a_table_with_other_columns_is_rebuilt_from_scratch(tmp_path):
    """An index an older codoc wrote: cocoindex's memo says every file is done,
    and the table's columns are not CodeChunk's. The next update rebuilds both,
    and its change set is a baseline."""
    import lancedb
    from codoc.index.schema import LANCE_TABLE_NAME
    (tmp_path / "m.py").write_text("def foo():\n    return 1\n")
    _in_a_process("from codoc.index.runner import update_index; update_index(root, root + '/.codoc')", tmp_path)
    db = lancedb.connect(str(tmp_path / ".codoc" / "lancedb"))
    old = db.open_table(LANCE_TABLE_NAME).to_arrow().rename_columns(
        ["id", "file", "symbol_path", "language", "source", "tokens_hash", "types_hash", "start_byte", "end_byte"])
    db.drop_table(LANCE_TABLE_NAME)
    db.create_table(LANCE_TABLE_NAME, old)
    proc = _in_a_process("from codoc.index.changes import compute_changeset; "
                         "cs = compute_changeset(root, root + '/.codoc'); "
                         "print(cs.baseline, sorted(r.symbol for r in cs.rows))", tmp_path)
    assert proc.stdout.strip() == "True ['m.py::foo']"


def test_a_batch_reports_every_file_that_changed_since_the_last_update(tmp_path):
    """b.py changed while no daemon ran. The batch that sees a.py change
    updates the index for both, so it reports both: a change it indexed and
    left out would be in no later diff."""
    (tmp_path / "a.py").write_text("def a():\n    return 1\n")
    (tmp_path / "b.py").write_text("def b():\n    return 1\n")
    _in_a_process("from codoc.index.changes import compute_changeset; compute_changeset(root, root + '/.codoc')", tmp_path)
    (tmp_path / "b.py").write_text("def b():\n    return 2\n")
    (tmp_path / "a.py").write_text("def a():\n    return 2\n")
    proc = _in_a_process(
        "import codoc.index.changes as C; real = C.compute_changeset; seen = []; "
        "C.compute_changeset = lambda *a, **k: seen.append(real(*a, **k)) or seen[-1]; "
        "from codoc.daemon.daemon import Daemon; "
        "d = Daemon(root, propose=lambda *a, **k: [], printer=lambda *_: None); "
        "d.process_batch([root + '/a.py']); d.ws.close(); "
        "print(sorted(c.symbol for c in seen[0].modified))", tmp_path)
    assert proc.stdout.strip().splitlines()[-1] == "['a.py::a', 'b.py::b']"


def test_a_fresh_clone_judges_only_bound_code_whose_hashes_moved(tmp_path):
    """The log is tracked and the index is not, so a fresh clone's first pass
    has no table to diff. It compares the code with the bindings instead: the
    model is asked about alpha, which the person edited, and not about beta,
    whose agent-set binding still matches its code, nor gamma, which a person
    detached. The graph is built whole, so it has the edge from gamma, in a
    file nobody changed."""
    import shutil
    (tmp_path / "a.py").write_text("def alpha():\n    return 1\n")
    (tmp_path / "b.py").write_text("def beta():\n    return 1\n\n\ndef gamma():\n    return beta()\n")
    _in_a_process(
        "from codoc.calculus import ops as K; from codoc.calculus.doc import HEADING; "
        "from codoc.calculus.ids import START; from codoc.index.changes import compute_changeset; "
        "from codoc.workspace import Workspace; "
        "fp = {r.symbol: (r.fingerprint, r.types_hash) for r in compute_changeset(root, root + '/.codoc').rows}; "
        "ws = Workspace(root + '/.codoc'); "
        "head = lambda fid, after: [{'kind': K.BLOCK_ADD, 'target': {'bid': fid}, "
        "    'payload': {'kind': HEADING, 'level': 1, 'after': after}}, "
        "    {'kind': K.TEXT_INSERT, 'target': {'bid': fid, 'path': 'text'}, 'payload': {'after': START, 'text': fid}}]; "
        "bind = lambda fid, s: {'kind': K.BIND_ATTACH, 'target': {'fid': fid}, "
        "    'payload': {'symbol': s, 'fingerprint': fp[s][0], 'types_hash': fp[s][1]}}; "
        "ws.submit(head('h1', START) + [bind('h1', 'a.py::alpha')] + head('h2', 'h1'), actor='loop'); "
        "ws.submit([bind('h2', 'b.py::beta')], actor='agent:claude-code'); "
        "ws.close()", tmp_path)
    for index in ("lancedb", "cocoindex.db"):     # untracked: a clone does not carry them
        shutil.rmtree(tmp_path / ".codoc" / index)
    (tmp_path / "a.py").write_text("def alpha():\n    return 2\n")
    proc = _in_a_process(
        "from codoc.daemon.daemon import Daemon; asked = []; "
        "d = Daemon(root, propose=lambda changes, *a, **k: asked.append(changes) or [], printer=lambda *_: None); "
        "n = len(d.ws.store.ops); d.process_batch([root + '/a.py']); "
        "print([([x['symbol'] for x in c['added']], [x['symbol'] for x in c['modified']]) for c in asked]); "
        "print([(o.kind, o.payload.get('symbol')) for o in d.ws.store.ops[n:]]); "
        "print(sorted({e['src_symbol'] for e in d.graph.all_edges()})); d.ws.close()", tmp_path)
    asked, ops, sources = proc.stdout.strip().splitlines()[-3:]
    assert asked == "[([], ['a.py::alpha'])]"
    assert ops == "[('bind.refresh', 'a.py::alpha')]"
    assert sources == "['b.py::gamma']"



# Run in a child: index `first` (argv[2]), bind alpha to hA and beta to hB,
# then save each of the rest in turn through a daemon's batch. Prints the
# binding ops of each batch, then the symbols hB is bound to at the end.
_SAVES = """
import json
from codoc.calculus import ops as K
from codoc.calculus.doc import HEADING
from codoc.calculus.ids import START
from codoc.daemon.daemon import Daemon
from codoc.index.changes import compute_changeset
from codoc.workspace import Workspace
name, *saves = json.loads(sys.argv[2])
fp = {r.symbol: (r.fingerprint, r.types_hash) for r in compute_changeset(root, root + "/.codoc").rows}
ws = Workspace(root + "/.codoc")
body, after = [], START
for fid, fn in (("hA", "alpha"), ("hB", "beta")):
    s = f"{name}::{fn}"
    body += [{"kind": K.BLOCK_ADD, "target": {"bid": fid}, "payload": {"kind": HEADING, "level": 1, "after": after}},
             {"kind": K.TEXT_INSERT, "target": {"bid": fid, "path": "text"}, "payload": {"after": START, "text": fn}},
             {"kind": K.BIND_ATTACH, "target": {"fid": fid},
              "payload": {"symbol": s, "fingerprint": fp[s][0], "types_hash": fp[s][1]}}]
    after = fid
ws.submit(body, actor="loop")
ws.close()
d = Daemon(root, propose=lambda *a, **k: [], printer=lambda *_: None)
for text in saves:
    open(f"{root}/{name}", "w").write(text)
    n = len(d.ws.store.ops)
    d.process_batch([f"{root}/{name}"])
    print(json.dumps([[o.kind, o.payload["symbol"]] for o in d.ws.store.ops[n:] if o.kind.startswith("bind.")]))
print(json.dumps(sorted(s for s, b in d.ws.store.bindings.items() if b.feature == "hB")))
d.ws.close()
"""


def _saves(tmp_path, name: str, first: str, *saves: str) -> list:
    import json
    (tmp_path / name).write_text(first)
    proc = _in_a_process(_SAVES, tmp_path, json.dumps([name, *saves]))
    return [json.loads(line) for line in proc.stdout.strip().splitlines()[-len(saves) - 1:]]


@pytest.mark.parametrize("name, first, typing, done", [
    ("m.py", "def alpha():\n    return 1\n\n\ndef beta(x):\n    return x\n",
     "def alpha():\n    return foo(1,\n\n\ndef beta(x):\n    return x\n",
     "def alpha():\n    return foo(1, 2)\n\n\ndef beta(x):\n    return x\n"),
    ("m.ts", "function alpha() {\n  return 1;\n}\n\nfunction beta(x) {\n  return x;\n}\n",
     "function alpha() {\n  if (x {\n  return 1;\n}\n\nfunction beta(x) {\n  return x;\n}\n",
     "function alpha() {\n  if (x) {}\n  return 1;\n}\n\nfunction beta(x) {\n  return x;\n}\n"),
])
def test_a_save_in_the_middle_of_typing_keeps_the_code_its_syntax_error_hides(tmp_path, name, first, typing, done):
    """An autosave of half-typed code: the parse folds beta into an error. The
    index keeps the file as it was, so beta is never detached, and finishing
    the edit refreshes alpha alone."""
    while_typing, finished, beta = _saves(tmp_path, name, first, typing, done)
    assert while_typing == []
    assert finished == [["bind.refresh", f"{name}::alpha"]]
    assert beta == [f"{name}::beta"]


def test_a_definition_deleted_from_a_file_that_never_parses_is_removed(tmp_path):
    """The bundled TypeScript grammar does not know `accessor`, so this file
    always parses with an error. A function deleted from it takes its name
    with it, so the error hides nothing and the deletion is indexed."""
    head = "class Box {\n  accessor size = 1;\n}\n\nfunction alpha() {\n  return 1;\n}\n"
    deleted, beta = _saves(tmp_path, "m.ts", head + "\nfunction beta(x) {\n  return x;\n}\n", head)
    assert deleted == [["bind.detach", "m.ts::beta"]] and beta == []
