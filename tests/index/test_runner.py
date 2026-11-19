"""A real index update, in a subprocess: cocoindex runs one index per process."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _in_a_process(code: str, root) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "LANCEDB_LOG"} | {"PYTHONPATH": str(ROOT)}
    proc = subprocess.run([sys.executable, "-c", "import sys; root = sys.argv[1]; " + code, str(root)],
                          capture_output=True, text=True, env=env, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return proc


def test_a_first_index_prints_no_lance_warning(tmp_path):
    (tmp_path / "m.py").write_text("def a():\n    return 1\n")
    proc = _in_a_process("from codoc.index.runner import update_index; update_index(root, root + '/.codoc')", tmp_path)
    assert "WARN" not in proc.stderr and (tmp_path / ".codoc" / "lancedb").is_dir()


def test_a_table_with_other_columns_is_rebuilt_from_scratch(tmp_path):
    """An index an older codoc wrote: cocoindex's memo says every file is done,
    and the table's columns are not CodeChunk's. The next update rebuilds both."""
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
                         "print(sorted(c.symbol for c in compute_changeset(root, root + '/.codoc').added))", tmp_path)
    assert proc.stdout.strip() == "['m.py::foo']"


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
