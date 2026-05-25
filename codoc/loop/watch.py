"""The watch daemon — one process running both loops.

Routing: a ``tree.codoc`` edit runs Loop B first (authored intent leads; Loop B
itself re-reflects what the agent wrote); any other source-file change runs
Loop A. Indexing artifacts under ``.codoc`` are filtered out so they never
self-trigger, and a content-hash guard ignores codoc's own re-render of
``tree.codoc`` so the two loops can't ping-pong.

``process_batch`` is the pure, testable per-cycle step (loops injectable);
``run_watch`` wires it to ``watchfiles``.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from codoc.codoc_file.render import tree_path
from codoc.loop.loop_a import run_loop_a
from codoc.loop.loop_b import run_loop_b
from codoc.store.db import open_store

CODE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".rb", ".cpp", ".c", ".h"}
_SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", ".pytest_cache", ".mypy_cache", ".codoc"}


@dataclass
class WatchState:
    last_tree_hash: str = ""


def _hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _is_code(path: Path) -> bool:
    return path.suffix in CODE_EXTENSIONS


def _classify(paths: list[str], root_dir: str, codoc_dir: str) -> tuple[bool, set[str]]:
    tp = tree_path(codoc_dir).resolve()
    root = Path(root_dir).resolve()
    codoc_touched = False
    code_files: set[str] = set()
    for p in paths:
        rp = Path(p).resolve()
        if rp == tp:
            codoc_touched = True
            continue
        if any(part in _SKIP_DIRS for part in rp.parts):
            continue
        if _is_code(rp):
            try:
                code_files.add(str(rp.relative_to(root)))
            except ValueError:
                pass
    return codoc_touched, code_files


def watch_filter(codoc_dir: str):
    """A watchfiles filter: allow tree.codoc + code files; drop everything else
    (notably .codoc indexing artifacts that churn during update_index)."""
    tp = tree_path(codoc_dir).resolve()

    def _f(_change, path: str) -> bool:
        rp = Path(path).resolve()
        if rp == tp:
            return True
        if any(part in _SKIP_DIRS for part in rp.parts):
            return False
        return _is_code(rp)

    return _f


def _render(codoc_dir: str) -> None:
    from codoc.codoc_file.render import write_tree

    store = open_store(codoc_dir)
    try:
        write_tree(store, codoc_dir)
    finally:
        store.close()


def process_batch(
    paths: list[str],
    root_dir: str,
    codoc_dir: str,
    state: WatchState,
    *,
    no_realize: bool = False,
    dry_run: bool = False,
    loop_a=run_loop_a,
    loop_b=run_loop_b,
    render=_render,
) -> tuple[str, str] | None:
    """Handle one debounced change batch. Returns (label, summary) or None."""
    tp = tree_path(codoc_dir)
    codoc_touched, code_files = _classify(paths, root_dir, codoc_dir)

    # Ignore our own re-render of tree.codoc (content-hash guard).
    if codoc_touched and _hash(tp) == state.last_tree_hash:
        codoc_touched = False

    if codoc_touched:
        res = loop_b(root_dir, codoc_dir, dry_run=dry_run or no_realize)
        label, summary = "codoc→code", res.summary()
    elif code_files:
        res = loop_a(root_dir, codoc_dir, file_scope=code_files)
        label, summary = "code→codoc", f"({len(code_files)} files) {res.summary()}"
    else:
        return None

    render(codoc_dir)
    state.last_tree_hash = _hash(tp)
    return label, summary


def run_watch(
    root_dir: str,
    codoc_dir: str,
    *,
    no_realize: bool = False,
    dry_run: bool = False,
    printer=print,
) -> None:  # pragma: no cover - blocking I/O loop
    import watchfiles

    _render(codoc_dir)
    state = WatchState(last_tree_hash=_hash(tree_path(codoc_dir)))
    printer(f"codoc watching {root_dir} — edit code or .codoc/tree.codoc (Ctrl-C to stop)")
    for changes in watchfiles.watch(root_dir, watch_filter=watch_filter(codoc_dir), debounce=600):
        out = process_batch([p for _, p in changes], root_dir, codoc_dir, state,
                            no_realize=no_realize, dry_run=dry_run)
        if out:
            printer(f"▸ {out[0]}  {out[1]}")
