"""doc_render.py — render the store's feature tree into a ProseMirror doc
(``tree.doc.json`` shape), the inverse of :mod:`codoc.codoc_file.doc_parse`.

The VS Code host normally authors ``tree.doc.json`` (it is the webview-authoritative
rich doc, U2b). But the ``codoc serve`` hub is a file-channel client with no editor
attached, so on a workspace that has never been opened in VS Code the doc file is
empty and the hub would serve a blank page. This module lets the hub derive the doc
itself from ``tree.codoc`` (already on disk), so it is self-sufficient.

It is the exact inverse of ``doc_parse._inline_text`` / ``_description``: a feature
becomes a ``featureHeading`` (carrying ``fid`` + outliner ``level`` = tree depth)
followed by one ``paragraph`` per description paragraph, with inline
``[label](codoc:file#symbol)`` citations re-materialised as ``codeRef`` atoms. The
round-trip ``build_doc(parse_text(t))`` → ``parse_doc`` recovers the same titles +
descriptions (guarded by a test), so a hub-rendered doc is indistinguishable from an
editor-authored one to the rest of the pipeline.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from codoc.codoc_file.parse import ParsedTree, normalize_description, parse_text
from codoc.codoc_file.tree_order import preorder

if TYPE_CHECKING:  # avoid a hard import cycle at module load
    from codoc.model.annotation import CommentThread, Mark
    from codoc.store.db import Store

# Inline code citation, mirroring parse._REF_RE: [label](codoc:file#symbol), symbol optional.
_REF_RE = re.compile(r"\[(?P<label>[^\]]*)\]\(codoc:(?P<file>[^)#]+)(?:#(?P<symbol>[^)]+))?\)")
# A paragraph break is one or more blank lines.
_PARA_SPLIT = re.compile(r"\n\s*\n")


def _inline_runs(text: str) -> list[dict]:
    """Project a paragraph's text into PM inline nodes: plain text + ``codeRef`` atoms
    for each ``[label](codoc:…)`` citation. The inverse of ``doc_parse._inline_text``."""
    runs: list[dict] = []
    pos = 0
    for m in _REF_RE.finditer(text):
        if m.start() > pos:
            runs.append({"type": "text", "text": text[pos:m.start()]})
        runs.append({
            "type": "codeRef",
            "attrs": {
                "label": m.group("label") or "",
                "file": m.group("file") or "",
                "symbol": m.group("symbol") or "",
            },
        })
        pos = m.end()
    if pos < len(text):
        runs.append({"type": "text", "text": text[pos:]})
    return runs


def _paragraphs(description: str, owner_id: str | None = None) -> list[dict]:
    """Description text → PM ``paragraph`` blocks (split on blank lines). Empty
    paragraphs are dropped, matching ``doc_parse._description``'s projection.

    When ``owner_id`` is given, each block carries ``attrs.ownerId`` — the feature
    identity the prose is anchored to (invariant I2), so the webview attributes it by
    identity rather than by position. ``None`` (the frozen ``build_doc`` text path)
    omits the attr, leaving the block byte-identical to the pre-I2 shape."""
    blocks: list[dict] = []
    for chunk in _PARA_SPLIT.split(description or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        runs = _inline_runs(chunk)
        if runs:
            block: dict = {"type": "paragraph", "content": runs}
            if owner_id:
                block["attrs"] = {"ownerId": owner_id}
            blocks.append(block)
    return blocks


def _depths(tree: ParsedTree) -> dict[str, int]:
    """Tree depth per node id, from the parent_id chain (cycle-safe)."""
    by_id = {n.id: n for n in tree.nodes if n.id}
    cache: dict[str, int] = {}

    def depth(nid: str, seen: frozenset[str]) -> int:
        if nid in cache:
            return cache[nid]
        node = by_id.get(nid)
        pid = node.parent_id if node else None
        d = 0 if (not pid or pid not in by_id or pid in seen) else depth(pid, seen | {nid}) + 1
        cache[nid] = d
        return d

    for nid in by_id:
        depth(nid, frozenset())
    return cache


def build_doc(tree: ParsedTree) -> dict:
    """Render a :class:`ParsedTree` into a ProseMirror ``doc`` node (``tree.doc.json``)."""
    depths = _depths(tree)
    content: list[dict] = []
    for node in tree.nodes:
        if node.retired:
            continue
        level = depths.get(node.id or "", 0)
        heading: dict = {
            "type": "featureHeading",
            "attrs": {"fid": node.id, "level": level, "realized": node.realized is not False},
        }
        title = (node.title or "").strip()
        if title:
            heading["content"] = [{"type": "text", "text": title}]
        content.append(heading)
        content.extend(_paragraphs(node.description))
    return {"type": "doc", "content": content}


def build_doc_from_text(text: str) -> dict:
    """Parse ``tree.codoc`` text and render it to the ``tree.doc.json`` shape."""
    return build_doc(parse_text(text))


# ── store-fed projection (U2) ────────────────────────────────────────────────
#
# ``build_doc`` above renders parsed ``tree.codoc`` text and is frozen for the
# round-trip guard. ``build_doc_from_store`` is the store-fed entry point the local
# path + hub adopt (R3): same PM shape, plus identity (``localId``) + the per-feature
# ``version`` HLC (KTD4's gate) on each heading, and tracked-change ``Mark`` /
# inline ``comment`` annotations projected onto the inline runs.

def _annotated_runs(text: str, base: int, anns: list[tuple[int, int, dict]]) -> list[dict]:
    """Like :func:`_inline_runs`, but the paragraph starts at char offset ``base``
    into the normalized description and each annotation in ``anns`` (``(start, end,
    mark)``, offsets into the *normalized description*) is applied to the text it
    covers. Text runs are split at every annotation boundary that falls inside them;
    a ``codeRef`` atom occupies the length of its ``[label](codoc:…)`` projection (so
    offsets stay aligned with the text the anchors index into) but carries no marks."""
    # Collect the boundary offsets (absolute, into the normalized description) that
    # split this paragraph: every annotation start/end clamped to the paragraph.
    end_of_para = base + len(text)
    bounds: set[int] = set()
    for s, e, _ in anns:
        for x in (s, e):
            if base <= x <= end_of_para:
                bounds.add(x)
    # Zero-width annotation carets (s == e) that fall inside this paragraph. A caret
    # marks no text, so it cannot ride a normal split run; it is projected as an
    # explicit zero-width text run at its offset (FIX G), so a (0,0)-anchored
    # comment/mark — a feature-level note, or a collapsed caret — still projects
    # instead of being silently dropped.
    carets: dict[int, list[dict]] = {}
    for s, e, m in anns:
        if s == e and base <= s <= end_of_para:
            carets.setdefault(s, []).append(m)

    def marks_at(lo: int, hi: int) -> list[dict]:
        """Marks covering the half-open run ``[lo, hi)`` (a zero-width caret does NOT
        ride a text run — it is emitted separately as a zero-width run; see ``carets``)."""
        out: list[dict] = []
        for s, e, m in anns:
            if s < hi and e > lo:
                out.append(m)
        return out

    runs: list[dict] = []
    emitted_carets: set[int] = set()

    def emit_caret(off: int) -> None:
        """Emit a zero-width text run carrying every caret annotation at ``off`` (once)."""
        if off in carets and off not in emitted_carets:
            emitted_carets.add(off)
            runs.append({"type": "text", "text": "", "marks": carets[off]})

    def emit_text(seg: str, seg_start: int) -> None:
        """Emit ``seg`` (starting at absolute offset ``seg_start``) split at every
        boundary inside it, each piece carrying the marks covering its span; a
        zero-width caret at a boundary is emitted as its own zero-width run."""
        emit_caret(seg_start)
        pos = seg_start
        cuts = sorted(b for b in bounds if seg_start < b < seg_start + len(seg))
        for cut in cuts + [seg_start + len(seg)]:
            piece = seg[pos - seg_start: cut - seg_start]
            if piece:
                run = {"type": "text", "text": piece}
                ms = marks_at(pos, cut)
                if ms:
                    run["marks"] = ms
                runs.append(run)
            pos = cut
            emit_caret(cut)

    cur = base
    last = 0
    for m in _REF_RE.finditer(text):
        if m.start() > last:
            emit_text(text[last:m.start()], cur)
        else:
            emit_caret(cur)  # a caret sitting just before a leading codeRef
        cur += m.start() - last
        ref_text = m.group(0)
        runs.append({
            "type": "codeRef",
            "attrs": {
                "label": m.group("label") or "",
                "file": m.group("file") or "",
                "symbol": m.group("symbol") or "",
            },
        })
        cur += len(ref_text)
        last = m.end()
    if last < len(text):
        emit_text(text[last:], cur)
    elif last == len(text):
        emit_caret(cur)  # a caret at the very end of the paragraph
    # Any caret not yet placed (e.g. an empty paragraph: base == end_of_para and no
    # text walked) is emitted now so a feature-level note on empty prose still projects.
    for off in sorted(carets):
        emit_caret(off)
    return runs


def _mark_dict(m: Mark) -> dict:
    """A tracked-change :class:`Mark` → a PM inline mark for the inline run."""
    return {
        "type": m.kind.value,
        "attrs": {"markId": m.id, "provenance": m.provenance.value},
    }


def _comment_dict(c: CommentThread) -> dict:
    """An inline :class:`CommentThread` → a ``comment`` PM mark (the webview's
    ``comment-mark`` keys off ``threadId``)."""
    return {"type": "comment", "attrs": {"threadId": c.id}}


def _annotations_for(marks: list[Mark], comments: list[CommentThread]) -> list[tuple[int, int, dict]]:
    """Flatten stored marks + comment threads into ``(anchor_start, anchor_end, pm_mark)``
    triples indexing into the normalized description."""
    anns: list[tuple[int, int, dict]] = []
    for m in marks:
        anns.append((m.anchor_start, m.anchor_end, _mark_dict(m)))
    for c in comments:
        anns.append((c.anchor_start, c.anchor_end, _comment_dict(c)))
    return anns


def _annotated_paragraphs(
    description: str, anns: list[tuple[int, int, dict]], owner_id: str | None = None
) -> list[dict]:
    """Description → PM ``paragraph`` blocks, like :func:`_paragraphs`, but with
    annotations whose offsets index into the *normalized* description mapped onto the
    matching inline runs. ``owner_id`` (invariant I2) stamps ``attrs.ownerId`` on every
    emitted block, exactly as :func:`_paragraphs` does.

    The anchors are offsets into ``normalize_description(description)``, so we split
    that canonical string on blank lines (the same ``_PARA_SPLIT`` shape the parser
    uses) and walk a running cursor through it, advancing past each ``\\n\\n`` joiner,
    so each paragraph's ``base`` is the canonical offset its anchors are relative to."""
    canon = normalize_description(description)
    blocks: list[dict] = []
    cursor = 0
    for raw in _PARA_SPLIT.split(canon):
        # Locate this paragraph's slice inside ``canon`` from the running cursor so
        # the base offset accounts for the dropped blank-line separators.
        start = canon.find(raw, cursor) if raw else cursor
        if start < 0:
            start = cursor
        chunk = raw.strip()
        # ``strip()`` only ever trims edges; canonical paragraphs are already stripped,
        # so the anchor base is ``start`` (no interior shift to correct for).
        cursor = start + len(raw)
        if not chunk:
            continue
        runs = _annotated_runs(chunk, start, anns) if anns else _inline_runs(chunk)
        if runs:
            block: dict = {"type": "paragraph", "content": runs}
            if owner_id:
                block["attrs"] = {"ownerId": owner_id}
            blocks.append(block)
    # Empty / whitespace-only description with annotations (a feature-level note, which
    # can only anchor at offset 0): no paragraph was emitted above, so the annotation
    # would be dropped (FIX G). Emit a paragraph carrying its zero-width caret run(s) so
    # the (0,0)-anchored comment/mark still projects.
    if not blocks and anns:
        caret_runs = _annotated_runs("", 0, anns)
        if caret_runs:
            caret_block: dict = {"type": "paragraph", "content": caret_runs}
            if owner_id:
                caret_block["attrs"] = {"ownerId": owner_id}
            blocks.append(caret_block)
    return blocks


def _store_depths(features: list) -> dict[str, int]:
    """Tree depth per feature id from the ``parent_id`` chain (cycle-safe) — the
    store-row analogue of :func:`_depths`."""
    by_id = {f.id: f for f in features}
    cache: dict[str, int] = {}

    def depth(fid: str, seen: frozenset[str]) -> int:
        if fid in cache:
            return cache[fid]
        f = by_id.get(fid)
        pid = f.parent_id if f else None
        d = 0 if (not pid or pid not in by_id or pid in seen) else depth(pid, seen | {fid}) + 1
        cache[fid] = d
        return d

    for fid in by_id:
        depth(fid, frozenset())
    return cache


_preorder = preorder
"""Reorder live features into the tree's depth-first pre-order — the same
:func:`codoc.codoc_file.tree_order.preorder` walk ``render_tree`` uses for the
left-nav, so an orphan (dangling ``parent_id``) is promoted to a root in both
projections rather than surfacing in only one of them.

``store.list_features()`` returns a FLAT list (ordered by sibling ``rank``), so
emitting the doc in that order lays a child out wherever its key happens to fall —
not under its parent. That desynchronizes the doc body from the nav (scroll-spy then
jumps). Walking parent→children here makes the doc order faithful to the tree.

Siblings keep their ``rank`` order (``list_features`` already sorts by it, and
``store.children`` sorts the same way), so this matches the nav 1:1 — and a reorder
the user performed shows up here without this walk knowing about it."""


def build_doc_from_store(store: Store) -> dict:
    """Render the store's live feature tree into a ProseMirror ``doc`` (``tree.doc.json``).

    The store-fed sibling of :func:`build_doc`: same shape, but each ``featureHeading``
    additionally carries ``localId`` (from ``feature.local_id``) and ``version`` (the
    feature's ``updated_at`` HLC string, for KTD4's per-feature version gate), and
    stored tracked-change marks + inline comment threads are projected onto the inline
    runs at their char-offset anchors. Retired features stay excluded (as in
    :func:`build_doc`).

    Features are emitted in tree PRE-ORDER (:func:`_preorder`) so the doc body lines up
    with the left-nav's ``render_tree`` walk — not the flat ``created_at`` order
    ``list_features`` returns, which desynchronizes the two panes."""
    features = _preorder(store.list_features())  # live only (retired excluded), tree order
    depths = _store_depths(features)
    content: list[dict] = []
    for f in features:
        level = depths.get(f.id, 0)
        heading: dict = {
            "type": "featureHeading",
            "attrs": {
                "fid": f.id,
                "localId": f.local_id or "",
                "version": f.updated_at.to_str(),
                "level": level,
                "realized": f.realized is not False,
            },
        }
        title = (f.title or "").strip()
        if title:
            heading["content"] = [{"type": "text", "text": title}]
        content.append(heading)
        anns = _annotations_for(store.marks_for_feature(f.id), store.comments_for_feature(f.id))
        # ownerId=f.id anchors each description paragraph to its feature by identity (I2),
        # so the webview never re-attributes prose to a heading inserted above it.
        content.extend(_annotated_paragraphs(f.description, anns, owner_id=f.id))
    return {"type": "doc", "content": content}
