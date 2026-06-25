"""Non-destructive tree.codoc writes.

``tree.codoc`` is rendered from the store, but a human (or the IDE) can save text
edits to it that the store hasn't absorbed yet. A naive ``write_tree`` regenerates
the file from the store and would silently overwrite those saved edits — e.g. when
the coding agent reflects via the MCP tools mid-session, or the watch daemon
re-renders after a code-only Loop A.

``safe_write_tree`` closes that hole: it renders **only when the on-disk file has
no un-applied user edits** (``diff_codoc`` is empty). When the file diverges (a
human edited it), the write is skipped so the edit survives; the proper Loop B
pass — the only place allowed to apply user edits and spawn the coding agent —
absorbs and re-renders it on the next cycle (or at epoch close). The store's own
changes (e.g. an agent's MCP proposal) are not lost either: they live in the store
and flush to the file as soon as it is clean again.
"""
from __future__ import annotations

from codoc.codoc_file.diff import CodocDiff, diff_codoc
from codoc.codoc_file.doc_parse import parse_doc_file
from codoc.codoc_file.parse import parse_tree_file
from codoc.codoc_file.render import write_sidecar, write_tree
from codoc.store.db import Store, open_store


def pending_user_edits(store: Store, codoc_dir: str) -> CodocDiff:
    """User ops implied by the current on-disk ``tree.codoc`` vs the store."""
    return diff_codoc(parse_tree_file(codoc_dir), store)


def has_pending_user_edits(codoc_dir: str) -> bool:
    """True if ``tree.codoc`` holds saved edits the store hasn't absorbed yet.

    Opens the store itself (convenience for the watch daemon, which classifies a
    batch before deciding whether to run a loop)."""
    with open_store(codoc_dir) as store:
        return not pending_user_edits(store, codoc_dir).is_empty()


def has_pending_doc_edits(codoc_dir: str) -> bool:
    """True if ``tree.doc.json`` (the webview's authored doc, U2b) holds feature
    edits the store hasn't absorbed yet — the doc-side analogue of
    :func:`has_pending_user_edits`. The daemon uses it to skip a Loop B pass for a
    non-edit doc.json write (a comment-reconcile / suggestion-rebase persist), so a
    payload-driven write never ping-pongs the loop. (Inline-comment steers ride
    ``edits.json`` and are caught by the daemon's separate ``edits_touched`` signal.)"""
    if parse_doc_file(codoc_dir) is None:
        return False
    with open_store(codoc_dir) as store:
        # Doc channel → has_local_ids=True so a TipTap-undo'd node (fid reset to null,
        # local_id intact) is recognized as the existing feature, not a false-positive
        # pending ADD that would wake Loop B on a no-op.
        return not diff_codoc(parse_doc_file(codoc_dir), store, has_local_ids=True).is_empty()


def safe_write_tree(store: Store, codoc_dir: str) -> bool:
    """Refresh ``tree.codoc`` non-destructively.

    The sidecar (``tree.bindings.json``) is pure derived state and is ALWAYS
    rewritten — so applied verdicts, new bindings, and proposal changes surface in
    the IDE immediately (an accept/reject is never a dead click). The ``tree.codoc``
    *text* is regenerated only when the on-disk file has no un-applied human edits;
    when it diverges the text write is skipped so the edit survives until Loop B
    absorbs it. Returns True if the text was (re)written, False if it was skipped.
    """
    # Hold the shared codoc-loop lock for the whole render so this derived re-render
    # cannot race a concurrent loop's write_tree (a torn / stale tree.codoc). Safe:
    # this only READS the store and writes the derived .codoc files (no SQLite writes),
    # so it cannot deadlock against a loop that holds the lock and writes the store.
    from codoc.loop.locks import loop_lock

    with loop_lock(codoc_dir):
        write_sidecar(store, codoc_dir)
        if not pending_user_edits(store, codoc_dir).is_empty():
            return False
        # U2b: also yield to a pending WEBVIEW edit (tree.doc.json ahead of the store).
        # Rendering tree.codoc from the old store while a doc edit awaits Loop B would
        # push stale text the host then adopts — reverting the user's settle. Only Loop B
        # applies the doc edit; once it has, the doc is in sync and this renders normally.
        doc_parsed = parse_doc_file(codoc_dir)
        if doc_parsed is not None and not diff_codoc(doc_parsed, store, has_local_ids=True).is_empty():
            return False
        write_tree(store, codoc_dir)
        return True
