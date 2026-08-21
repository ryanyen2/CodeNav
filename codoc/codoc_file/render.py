"""Render the store as ``tree.codoc`` text.

Live features render depth-first as ``- Title  ⟨f-id⟩`` with indented description
lines beneath. The ``⟨f-id⟩`` marker is the durable identity anchor; the IDE
collapses it with a decoration so a human never sees or types it (it is also
mirrored into ``tree.bindings.json``). Authors never write ids: a hand-added
``- Title`` line gets one minted on the next render.

Descriptions are free prose and may span multiple paragraphs (blank lines are
preserved); the node boundary is the *next* feature-marker line, never a blank
line. Code is cited inline with markdown links — ``[label](codoc:file.py#symbol)``
— so refs live inside the sentence they explain. Derived bindings (computed by
Loop A) are NOT written into the text; they ride in ``tree.bindings.json`` and
the IDE renders them as inlay-hint chips.

Pending proposals render as an **in-place overlay**. ADD and MOVE proposals emit
a ghost hunk in the text at their destination parent — a line carrying the diff op
char in column 0 (``+`` add, ``~`` move) followed by the node indented to its tree
depth, and a hidden ``⟨e-id⟩`` — because an add has no live node and a move shows
where the node will land. RETIRE and AMEND, by contrast, modify an *existing*
node, so they emit NO text: they ride in the sidecar's ``proposals`` map
(``_proposals_map``) and the IDE decorates the live node in place (strike-through
for retire, an inline title/description diff for amend). Keeping the live node's
text byte-identical to a clean render is what makes render→parse→diff a no-op for
retire/amend; the ADD/MOVE ghost hunks stay round-trip-safe because the parser
skips any block whose first line matches both the proposal shape and a ``⟨e-id⟩``
marker (live nodes carry ``⟨f-id⟩``). There is no accept/reject *syntax*; the
IDE's Accept/Reject actions write verdicts to ``.codoc/inbox.json`` (see
:mod:`codoc.loop.inbox`).
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from codoc.codoc_file.tree_order import children_map
from codoc.doclang import language_tag_for, workspace_doc_language
from codoc.model.annotation import in_margin
from codoc.model.hlc import HLC
from codoc.model.event import (
    ACTOR_HUMAN, LOOP_A_AGENT_SOURCE, MODE_AUTO, PLAN_SOURCE, Event, NodeOpKind,
)
from codoc.store.db import Store

TREE_FILENAME = "tree.codoc"
BINDINGS_FILENAME = "tree.bindings.json"
INDEX_FILENAME = "tree.index.json"

# Max length of a derived one-line feature pitch (the first sentence of the
# description, flattened of inline refs). Shared by the Python writer and the TS
# parity test so both trim to the same length.
PITCH_MAX_LEN = 120

# Legacy sentinel that opened the old bottom-of-file pending-changes block.
# Proposals now render in situ, but the parser still honours this sentinel so it
# can read trees written by older versions of codoc.
PENDING_SENTINEL = "# ── pending changes"


def tree_path(codoc_dir: str | Path) -> Path:
    return Path(codoc_dir) / TREE_FILENAME


def _description_lines(description: str, indent: str) -> list[str]:
    """Indent each prose line by ``indent + 4``; keep blank lines truly blank.

    Blank lines are paragraph breaks inside one description — the parser keeps
    them, so they must round-trip as empty lines (no indentation to strip)."""
    out: list[str] = []
    for dl in description.split("\n"):
        out.append(f"{indent}    {dl}" if dl.strip() else "")
    return out


def render_tree(store: Store) -> str:
    lines: list[str] = []

    pending = store.pending_events()
    # Only ADD/MOVE proposals emit *text* (a ghost node at the destination parent):
    # an ADD has no live node to anchor to, and a MOVE shows where the node will
    # land. RETIRE/AMEND mutate an existing node, so they ride in the sidecar
    # (`_proposals_map`) and the IDE decorates the live node in place — keeping the
    # node's text identical to a clean render, which preserves render→parse→diff.
    ghosts_by_parent: dict[str | None, list[Event]] = defaultdict(list)
    for e in pending:
        if e.op.kind in (NodeOpKind.ADD_NODE, NodeOpKind.MOVE_NODE):
            ghosts_by_parent[e.op.parent_id].append(e)
    emitted: set[str] = set()

    def emit_proposal(e: Event, depth: int) -> None:
        lines.extend(_render_proposal(e, store, depth))
        lines.append("")
        emitted.add(e.id)

    # A single map (shared with the doc projection, tree_order.children_map) so a
    # feature orphaned by a retired/dangling parent_id is promoted to a root here
    # exactly as it is in tree.doc.json — never silently dropped from the nav.
    children = children_map(store.list_features())

    def walk(parent_id: str | None, depth: int) -> None:
        indent = "  " * depth
        for f in children.get(parent_id, []):
            marker = "~" if f.retired else "-"
            lines.append(f"{indent}{marker} {f.title}  ⟨{f.id}⟩")
            if f.description:
                lines.extend(_description_lines(f.description, indent))
            lines.append("")
            walk(f.id, depth + 1)
        for e in ghosts_by_parent.get(parent_id, []):
            emit_proposal(e, depth)

    walk(None, 0)
    # ADD/MOVE ghosts whose destination parent isn't in the live tree (stale ref)
    # still surface at the root so a verdict is always reachable.
    for e in pending:
        if e.id not in emitted and e.op.kind in (NodeOpKind.ADD_NODE, NodeOpKind.MOVE_NODE):
            emit_proposal(e, 0)

    return "\n".join(lines).rstrip() + "\n"


def _source_tag(e: Event) -> str:
    """Human-readable label for the origin of a proposal.

    Not every proposal comes from the machine. ``loop_b._resolve_content`` DEFERS a
    contended edit — two peers rewrote the same lines and neither outranks the other —
    by parking the AUTHOR's own text as a pending proposal. Falling through to "code
    drift" showed a person their own sentence attributed to the codebase and offered it
    back for a verdict; the honest sentence existed in ``edit_notes`` and reached no
    surface. ``actor`` is the ledger's own answer to "who wrote this" (the Event
    validator fills it in even for rows written before provenance existed), so it is
    what decides here — no new channel, and no keying off ``source`` strings that only
    happen to correlate.
    """
    if e.source == PLAN_SOURCE:
        return "agent plan"
    if e.source == LOOP_A_AGENT_SOURCE:
        return "agent reflection"
    if e.actor == ACTOR_HUMAN:
        return "your edit"
    return "code drift"


def _proposals_map(store: Store, voted: set[str] | None = None) -> dict[str, dict]:
    """Sidecar payload describing pending proposals for the IDE to render in place.

    ``by_feature`` keys RETIRE/AMEND (and the *source* annotation of a MOVE) by
    the live ``feature_id`` they decorate; ``by_event`` keys ADD/MOVE *ghosts*
    (the text hunks) by ``event_id`` so the IDE can show details + Accept/Reject
    without re-parsing. ``by_parent`` lists the ADD/MOVE event ids landing under
    each destination parent (``""`` = top level) so the IDE can anchor an
    Accept/Reject affordance at the parent node, not only on the ghost line. Both
    halves carry the origin ``tag`` and ``rationale``.

    Two fields exist so the IDE can tell the user what ACCEPTING will actually do,
    which is the one thing the old payload could not express. Every proposal looks
    alike on screen, but only two kinds hand work to the agent:

    * ``writes_code`` — ``"build"`` for a plan placeholder (``ADD_NODE`` with
      ``realized=False``: describes code that does not exist yet, so accepting is a
      build request) and ``"remove"`` for a delete-code retire. Absent/None for the
      majority, which merely reconcile the tree to code that already exists.
    * ``verdict_pending`` — a verdict for this proposal is already sitting in
      ``inbox.json``, un-drained. The click WAS registered; the loop has not applied
      it yet (no daemon running, or a code-implying accept deferred to a realize
      pass). Without this the IDE could only show a fresh Accept button again, which
      reads as "your click did nothing".
    """
    voted = voted or set()
    by_feature: dict[str, dict] = {}
    by_event: dict[str, dict] = {}
    by_parent: dict[str, list[str]] = {}
    for e in store.pending_events():
        op = e.op
        tag = _source_tag(e)
        prov = {"actor": e.actor, "mode": e.mode, "caused_by": e.caused_by,
                "verdict_pending": e.id in voted}
        if op.kind is NodeOpKind.RETIRE_NODE and op.feature_id:
            by_feature[op.feature_id] = {
                "op": "retire", "event_id": e.id, "tag": tag, "rationale": op.rationale,
                # A default retire is detach-only: it untracks the feature and leaves
                # the code alone. Only an explicit delete_code retire removes code.
                "writes_code": "remove" if op.delete_code else None,
                **prov,
            }
        elif op.kind is NodeOpKind.AMEND and op.feature_id:
            by_feature[op.feature_id] = {
                "op": "amend", "event_id": e.id, "tag": tag, "rationale": op.rationale,
                "title": op.title, "description": op.description,
                # A reflection amend restates code that already changed and asks for
                # nothing; a PLAN amend (realized=False — see mcp.tools.propose_amend's
                # `builds`) says what the feature will do once the work lands, so
                # accepting it is a build request exactly like accepting a plan
                # placeholder. Both are AMENDs and only this flag separates them.
                "writes_code": "build" if op.realized is False else None,
                **prov,
            }
        elif op.kind is NodeOpKind.ADD_NODE:
            by_event[e.id] = {
                "op": "add", "parent_id": op.parent_id, "tag": tag, "rationale": op.rationale,
                "title": op.title, "description": op.description,
                "writes_code": "build" if op.realized is False else None,
                # Sibling anchors, so the IDE can draw the ghost WHERE the node
                # will actually land. apply honours these on accept
                # (store.rank_between); omitting them from the payload is why a
                # ghost drawn "last child" used to jump on accept.
                "after_id": op.after_id or None, "before_id": op.before_id or None,
                **prov,
            }
            by_parent.setdefault(op.parent_id or "", []).append(e.id)
        elif op.kind is NodeOpKind.MOVE_NODE:
            # The destination ghost (text) conveys the move; the IDE can dim the
            # source node by scanning `by_event` for op=="move" and its feature_id.
            by_event[e.id] = {
                "op": "move", "feature_id": op.feature_id, "parent_id": op.parent_id,
                "tag": tag, "rationale": op.rationale,
                "writes_code": None,   # reorganizing the tree is never code work
                "after_id": op.after_id or None, "before_id": op.before_id or None,
                **prov,
            }
            by_parent.setdefault(op.parent_id or "", []).append(e.id)
    return {"by_feature": by_feature, "by_event": by_event, "by_parent": by_parent}


_CHANGES_FEED_LIMIT = 50


def _changes_feed(events: list) -> list[dict]:
    """The last N *applied* events as a provenance feed (newest first).

    This is how the IDE learns WHO last changed each feature without reading the
    event log itself: an agent-authored AMEND shows up here so the doc view can
    re-stamp the new prose as pencil ink instead of resetting authorship, and a
    ``caused_by`` directive id lets it group a reflection cascade under the doc
    edit that triggered it. Legacy events carry empty strings — render as today.

    Takes the shared recent-events list (write_sidecar reads it ONCE for both this
    feed and the per-feature history feed — two scans per pass would be pure waste).
    """
    out: list[dict] = []
    for e in events:
        if not e.applied:
            continue
        out.append({
            "event_id": e.id, "at": e.at.to_str(), "kind": e.op.kind.value,
            "feature_id": e.op.feature_id or "", "actor": e.actor, "mode": e.mode,
            "caused_by": e.caused_by,
        })
        if len(out) >= _CHANGES_FEED_LIMIT:
            break
    return out


# Blame feed (W2): a bounded newest-first scan grouped per feature, so the IDE can
# render an edit history (who / when / why) without reading the event log itself. One
# indexed range scan (recent_events → idx_events_at), NOT an O(F) per-feature query
# storm — a feature untouched within the window simply carries no history slice (its
# deep past is still reachable via `codoc history` / the codoc_history MCP tool).
_HISTORY_FEED_SCAN = 300       # events read per pass (one indexed LIMIT query)
_HISTORY_PER_FEATURE = 6       # newest entries kept per feature
_HISTORY_RATIONALE_CAP = 200   # trim a long rationale so the sidecar stays lean


def _auto_edits(events: list, live_ids: set[str]) -> dict[str, dict]:
    """`{feature_id: {at, prev, written_by, rationale}}` — descriptions the LOOP
    rewrote on its own authority, newest per feature.

    Deliberately the narrowest possible slice of "what happened automatically". Loop A
    auto-applies four op kinds and only one of them is worth a person's attention:

      * REFRESH recomputes a fingerprint on every edit to a bound symbol. Announcing it
        would set the noise floor and teach the reader to ignore this channel entirely.
      * ATTACH / DETACH are the index doing its job. The only version anyone cares
        about — a feature that now describes nothing — is already reported as
        ``feature_drift`` / unrealized, and a second signal for one fact is worse
        than none.
      * AMEND rewrote prose. Nobody was asked, and unlike every other automatic op it
        changes what the document SAYS. That is the whole slice.

    ``prev`` is the displaced wording (recorded at the write boundary — see
    ``NodeOp.prev_description``) so the IDE can show what changed rather than merely
    that something did, and ``written_by`` says whose sentences were displaced so the
    cue can be weighted: the loop revising its own bootstrap prose is housekeeping,
    the loop editing a person's words is not.
    """
    out: dict[str, dict] = {}
    for e in events:
        if not e.applied or e.mode != MODE_AUTO:
            continue
        op = e.op
        if op.kind is not NodeOpKind.AMEND or op.prev_description is None:
            continue
        fid = op.feature_id or ""
        if not fid or fid not in live_ids or fid in out:
            continue  # newest wins; a retired feature has nothing to show
        out[fid] = {
            "at": e.at.to_str(),
            "prev": op.prev_description,
            "written_by": op.prev_written_by,
            "rationale": op.rationale[:_HISTORY_RATIONALE_CAP],
        }
    return out



def _history_feed(events: list, live_ids: set[str]) -> dict[str, list[dict]]:
    """`{feature_id: [ {at, kind, actor, mode, caused_by, rationale}, … ]}` newest
    first, at most :data:`_HISTORY_PER_FEATURE` per feature, only for LIVE features
    that changed within the scan window. Rationale is the one-line 'why' the ledger
    already stores per op; empty strings are omitted so the payload stays lean, and
    a pathologically long one is capped. Takes the shared recent-events list (read
    once by write_sidecar — see :func:`_changes_feed`)."""
    out: dict[str, list[dict]] = {}
    for e in events:
        if not e.applied:
            continue
        fid = e.op.feature_id or ""
        if not fid or fid not in live_ids:
            continue
        bucket = out.setdefault(fid, [])
        if len(bucket) >= _HISTORY_PER_FEATURE:
            continue
        entry = {"at": e.at.to_str(), "kind": e.op.kind.value,
                 "actor": e.actor, "mode": e.mode}
        if e.caused_by:
            entry["caused_by"] = e.caused_by
        if e.op.rationale:
            r = e.op.rationale.strip()
            entry["rationale"] = (r[:_HISTORY_RATIONALE_CAP] + "…") if len(r) > _HISTORY_RATIONALE_CAP else r
        bucket.append(entry)
    return out


def _title_of(store: Store, feature_id: str | None) -> str:
    if not feature_id:
        return "(root)"
    f = store.get_feature(feature_id)
    return f.title if f else feature_id


def _render_proposal(e: Event, store: Store, depth: int) -> list[str]:
    """An ADD/MOVE proposal as an in-situ ghost hunk (the only proposals in text).

    Column 0 is the diff op char (``+`` add, ``~`` move); a single space follows;
    then the ghost node rendered at its tree ``depth`` (``{indent}- Title  ⟨e-id⟩``),
    so the IDE colours the block like a git diff and can recover the event id from
    the hidden marker. Continuation lines repeat the op char so the parser can skip
    the whole block; a blank line terminates it. (RETIRE/AMEND never reach here —
    they decorate the live node via the sidecar ``_proposals_map``.)
    """
    op = e.op
    eid = e.id
    tag = _source_tag(e)
    indent = "  " * depth

    def title_line(o: str, marker: str, title: str) -> str:
        return f"{o} {indent}{marker} {title}  ⟨{eid}⟩"

    def cont(o: str, text: str) -> list[str]:
        return [f"{o} {indent}    {dl}" for dl in text.split("\n") if dl.strip()]

    if op.kind is NodeOpKind.ADD_NODE:
        meta = f"{tag} · {op.rationale}" if op.rationale else tag
        return [title_line("+", "-", op.title or "Untitled"),
                *cont("+", op.description or ""), *cont("+", meta)]
    if op.kind is NodeOpKind.MOVE_NODE:
        meta = f"move → {_title_of(store, op.parent_id)} · {tag}" + (f" · {op.rationale}" if op.rationale else "")
        return [title_line("~", "-", _title_of(store, op.feature_id)), *cont("~", meta)]
    # RETIRE/AMEND/safe ops are not emitted as text; render a generic hunk just in case.
    return [title_line("~", "-", op.kind.value)]


def _compute_feature_edges(store: Store, edges: list | None = None) -> dict[str, list[dict]]:
    """Aggregate symbol-level call/import edges into feature-level coupling.

    Returns ``{src_feature_id: [{to: dst_feature_id, weight: int, kinds: list[str]}]}``.
    Used by the VS Code extension for dependency-focus opacity dimming.

    ``edges`` lets the caller pass rows it has already read; the sidecar shares one
    read with ``graph.query.feature_impact``, which needs the same table.
    """
    sym2feat = {b.symbol_path: b.feature_id for b in store.all_bindings()}
    agg: dict[tuple[str, str], dict] = {}  # (src_fid, dst_fid) → {weight, kinds}
    for e in (store.all_edges(internal_only=True) if edges is None else edges):
        dst = e["dst_symbol"]
        if not dst:
            continue
        sf = sym2feat.get(e["src_symbol"])
        df = sym2feat.get(dst)
        if not sf or not df or sf == df:
            continue
        slot = agg.setdefault((sf, df), {"weight": 0, "kinds": set()})
        slot["weight"] += 1
        slot["kinds"].add(e["kind"])
    out: dict[str, list[dict]] = {}
    for (sf, df), v in agg.items():
        out.setdefault(sf, []).append(
            {"to": df, "weight": v["weight"], "kinds": sorted(v["kinds"])}
        )
    return out


def _symbol_leaf(symbol_path: str) -> str:
    """The trailing bare-name segment of a qualified ``symbol_path``.

    Mirrors ``completion.ts:leaf`` / ``extension.ts:openRef``: drop everything
    before ``::`` (the file-qualifier), then take the last ``.``-segment of what
    remains (``file.py::Class.method`` → ``method``)."""
    qualified = symbol_path.split("::", 1)[-1]
    return qualified.rsplit(".", 1)[-1]


def _ref_matches_binding(ref_symbol: str, binding_symbol_path: str) -> bool:
    """True when an authored ref's *leaf* symbol resolves to a qualified binding.

    Authored ``codoc:`` refs carry the leaf symbol (``method``) or a partial
    dotted path (``Class.method``), while bindings store the qualified
    ``symbol_path`` (``file.py::Class.method``). Mirroring the navigation rule in
    ``extension.ts``/``completion.ts``, a ref resolves when, ignoring the file
    qualifier, it equals the binding's qualified name, is a trailing
    dotted-segment suffix of it, or equals its bare leaf — NOT by constructing
    ``file::symbol`` and comparing for equality (which would mark every live
    nested-symbol ref dead)."""
    qualified = binding_symbol_path.split("::", 1)[-1]  # "Class.method"
    if ref_symbol == qualified or ref_symbol == binding_symbol_path:
        return True
    # Trailing dotted-segment suffix: "method" / "Class.method" of "Outer.Class.method".
    if qualified.endswith("." + ref_symbol):
        return True
    # Bare-leaf equality (the leaf the IDE navigates to).
    return _symbol_leaf(binding_symbol_path) == ref_symbol


def _resolve_ref(
    ref_file: str,
    ref_symbol: str | None,
    bindings_by_file: dict[str, list[str]],
) -> bool:
    """Resolution rule for one inline ``codoc:`` ref against the binding index.

    A ref ``(file, symbol)`` resolves when some binding in the SAME ``file`` has a
    ``symbol_path`` whose leaf/suffix matches ``symbol`` (leaf-matching, per
    :func:`_ref_matches_binding`). A file-only ref (``symbol is None``) resolves
    when the file carries any binding."""
    paths = bindings_by_file.get(ref_file)
    if not paths:
        return False
    if ref_symbol is None:
        return True
    return any(_ref_matches_binding(ref_symbol, p) for p in paths)


def _compute_registry(store: Store) -> dict:
    """The cross-reference registry: every feature, every binding, every ref.

    Pure derived state, written to ``tree.index.json`` each loop pass. ``refs``
    is built by running :func:`~codoc.codoc_file.parse.extract_refs` over each
    live feature's ``description`` and tagging it ``resolved`` per the
    leaf-matching rule in :func:`_resolve_ref` — so the IDE can decorate dead
    ``codoc:`` links without re-deriving anything host-side."""
    from codoc.codoc_file.parse import extract_refs

    features = store.list_features()
    all_bindings = store.all_bindings()

    feats_meta: dict[str, dict] = {
        f.id: {"title": f.title, "parent_id": f.parent_id} for f in features
    }
    bindings = [
        {"file": b.file, "symbol_path": b.symbol_path, "feature_id": b.feature_id}
        for b in all_bindings
    ]
    bindings_by_file: dict[str, list[str]] = {}
    for b in all_bindings:
        bindings_by_file.setdefault(b.file, []).append(b.symbol_path)

    refs: list[dict] = []
    for f in features:
        for ref in extract_refs(f.description):
            refs.append({
                "feature_id": f.id,
                "label": ref.label,
                "file": ref.file,
                "symbol": ref.symbol,
                "resolved": _resolve_ref(ref.file, ref.symbol, bindings_by_file),
            })

    return {
        "version": 1,
        "features": feats_meta,
        "bindings": bindings,
        "refs": refs,
    }


def write_registry(store: Store, codoc_dir: str | Path) -> None:
    """Write ``.codoc/tree.index.json`` atomically (tmp → rename).

    The registry is *pure derived state* (features, bindings, resolved refs) — it
    is never hand-edited, so it is always safe to regenerate. It is emitted from
    inside :func:`write_sidecar` so every call-site (``write_tree``,
    ``safe_write_tree``, bootstrap, Loop B) emits it through one seam with no
    double-write, and stays live even when the ``tree.codoc`` text render is held
    back."""
    from codoc.loop.fsio import atomic_write_json

    atomic_write_json(Path(codoc_dir) / INDEX_FILENAME, _compute_registry(store))


# A sentence boundary: ``.``/``!``/``?`` followed by whitespace or end-of-string.
# Used only to take the *first* sentence for the derived pitch.
_SENTENCE_END_RE = re.compile(r"[.!?](?=\s|$)")


def _flatten_refs(text: str) -> str:
    """Replace each inline ``[label](codoc:file#symbol)`` ref with its label.

    A description that leads with a citation would otherwise yield raw link
    markdown as its pitch; flattening to the human-readable label first keeps the
    pitch readable prose. Reuses the parser's :data:`~codoc.codoc_file.parse._REF_RE`."""
    from codoc.codoc_file.parse import _REF_RE

    return _REF_RE.sub(lambda m: m.group("label"), text)


def _pitch(description: str, title: str) -> str:
    """A derived one-line pitch for a feature: the first sentence of its prose.

    Inline ``codoc:`` refs are flattened to their labels first (so a
    citation-leading description reads as prose, not markdown). The pitch is the
    first sentence — split on a ``.!?`` sentence boundary — trimmed to
    :data:`PITCH_MAX_LEN`. Falls back to ``title`` when the description is
    empty/blank or the first sentence is empty after flattening (e.g. it was only
    a citation). Pure derivation — never an LLM call, never a model field."""
    flat = _flatten_refs(description or "").strip()
    if not flat:
        return title
    # First non-blank line, then first sentence within it. Multi-paragraph
    # descriptions contribute only their opening sentence.
    first_line = next((ln.strip() for ln in flat.splitlines() if ln.strip()), "")
    m = _SENTENCE_END_RE.search(first_line)
    sentence = first_line[: m.end()].strip() if m else first_line
    if not sentence:
        return title
    if len(sentence) > PITCH_MAX_LEN:
        sentence = sentence[:PITCH_MAX_LEN].rstrip()
    return sentence


# Max number of See-Also neighbours emitted per feature. feature_edges can be
# noisy on highly-coupled nodes, so the slice is ranked by coupling weight and
# capped at this many. Shared by the Python writer and the TS parity test.
SEE_ALSO_MAX = 5


def _compute_kinds(store: Store, features: list | None = None) -> dict[str, str]:
    """A derived Diátaxis-lite ``kind`` hint per feature (sidecar metadata).

    A pure structural heuristic over the binding-less taxonomy — never an LLM
    call, never a model field, never written into ``tree.codoc``:

    - retired                                  → ``"retired"`` (suppressed in UI)
    - binding-less + has children + realized   → ``"overview"`` (an org-pass theme
      parent — binding-less *by design*, fully real; it must NOT read as unrealized,
      see :func:`codoc.loop.apply._mutate`)
    - has bindings                             → ``"reference"`` (a real, code-bound
      feature)
    - binding-less leaf (no children, no code) → ``"unclassified"`` (a just-detached
      node or a pre-attach ``/codoc:plan`` placeholder)

    Returns ``{feature_id: kind}`` for every live + retired feature.

    ``features`` may be passed pre-read (include_retired) to avoid a redundant
    ``list_features`` query; falls back to its own read for standalone callers.
    """
    # include_retired so a retired feature gets its suppressing tag, not omitted.
    if features is None:
        features = store.list_features(include_retired=True)
    bound = store.bound_feature_ids()
    has_children: set[str] = {f.parent_id for f in features if f.parent_id}

    out: dict[str, str] = {}
    for f in features:
        if f.retired:
            out[f.id] = "retired"
        elif f.id in bound:
            out[f.id] = "reference"
        elif f.id in has_children and f.realized:
            out[f.id] = "overview"
        else:
            # Binding-less leaf: a just-detached node, a not-yet-realized plan
            # placeholder, or a theme parent that lost its children. Not enough
            # signal to call it overview/reference.
            out[f.id] = "unclassified"
    return out


def _compute_see_also(edges: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Top-N coupled-feature neighbours per feature (sidecar metadata ONLY).

    Built from :func:`_compute_feature_edges` (symbol-level call/import edges
    aggregated to feature coupling) — passed in precomputed so ``write_sidecar``
    derives the edges once and shares them with the ``feature_edges`` slot.
    Ranked by coupling ``weight`` (heaviest first) and capped at
    :data:`SEE_ALSO_MAX`. Each row carries the destination feature id, its weight,
    and the edge ``kinds`` (``calls``/``imports``) as a one-line rationale.

    Emitted as derived data only, and OUTGOING: these are the features this one
    depends on. The question a reader actually asks before editing runs the other way
    ("what would break?"), and that is the ``feature_impact`` slice, which is the one
    the document surface draws. Never a ``> …`` steering line and never enters
    ``tree.codoc`` / ``tree.doc.json`` (KTD4). Returns
    ``{src_feature_id: [{to, weight, kinds, rationale}]}``; a feature with no edges
    is absent (an empty See-Also).
    """
    out: dict[str, list[dict]] = {}
    for src, neighbours in edges.items():
        ranked = sorted(neighbours, key=lambda n: n["weight"], reverse=True)[:SEE_ALSO_MAX]
        if not ranked:
            continue
        out[src] = [
            {
                "to": n["to"],
                "weight": n["weight"],
                "kinds": n["kinds"],
                # One-line rationale: which edge kind couples them. e.g. "calls".
                "rationale": ", ".join(n["kinds"]) or "coupled",
            }
            for n in ranked
        ]
    return out


def _comments_map(store: Store) -> dict[str, list[dict]]:
    """`{feature_id: [thread, …]}` — the durable inline comment threads (W8).

    The IDE used to hold comment bodies in extension-host memory: close the tab and every
    body was gone, leaving the projection's anchor underline with nothing behind it. They
    live in the store now, so they ride the sidecar like every other derived view — and
    a thread can finally report what became of the work it asked for (``directive_id``,
    and a ``status`` that reaches ``resolved`` when that directive lands).

    A RESOLVED thread lingers briefly and then leaves (``annotation.in_margin``): the
    reader is owed one look at "your request landed, here is the code it produced", and
    nothing after that. Keeping them forever — which is what shipped first — made
    "resolve" a button that could not do its job, since every projection brought the card
    back. The RECORD is not deleted either way; it stays in the store as the durable
    answer to "why does this code look like this", reachable from history.
    """
    now_ms = HLC.now().wall_clock
    out: dict[str, list[dict]] = {}
    for c in store.all_comments():
        if not c.feature_id or not in_margin(c, now_ms):
            continue
        row = {
            "id": c.id, "feature_id": c.feature_id, "body": c.body,
            "author": c.author.value, "status": c.status.value,
            "anchor_start": c.anchor_start, "anchor_end": c.anchor_end,
            "created_at": c.created_at.to_str(), "updated_at": c.updated_at.to_str(),
        }
        # Presence-keyed, so a thread that carries none of this costs nothing and an
        # older reader ignores what it does not know.
        for key, val in (("anchor_text", c.anchor_text), ("scope", c.scope.value),
                         ("directive_id", c.directive_id), ("media_ref", c.media_ref)):
            if val and not (key == "scope" and val == "code"):
                row[key] = val
        if c.code_refs:
            row["code_refs"] = list(c.code_refs)
        if c.replies:
            row["replies"] = [{"author": r.author, "body": r.body, "at": r.at.to_str()}
                              for r in c.replies]
        out.setdefault(c.feature_id, []).append(row)
    for rows in out.values():
        rows.sort(key=lambda r: (r["anchor_start"], r["created_at"]))
    return out


def _blocks_map(store: Store, feature_ids: set[str]) -> dict[str, list[dict]]:
    """The sidecar ``blocks`` slice: persistent typed-media blocks per feature
    (v6). Prose is NOT here — it is block-zero, carried by the feature description
    that ``by_feature``/``feats_meta`` already render. Transient blocks (a bug
    screenshot) are NOT here either — they ride the one-shot steers channel and are
    consumed by realization, never a durable sidecar row (KTD4).

    Each entry carries the stable block ``id`` (KTD8) so a host preserves identity
    across renders, plus ``kind``/``content``/``lifecycle``/``provenance``/``ord``
    so any host renders by kind with no host-side derivation. Ordered by ``ord``."""
    from codoc.model.block import BlockLifecycle

    out: dict[str, list[dict]] = {}
    for b in store.blocks_for_features(feature_ids):
        if b.lifecycle is not BlockLifecycle.PERSISTENT:
            continue
        out.setdefault(b.feature_id, []).append({
            "id": b.id, "kind": b.kind, "content": b.content,
            "lifecycle": b.lifecycle.value, "provenance": b.provenance.value, "ord": b.ord,
        })
    for entries in out.values():
        entries.sort(key=lambda e: e["ord"])
    return out


def _voted_event_ids(codoc_dir: str | Path) -> set[str]:
    """Event ids with a verdict already waiting in ``inbox.json``.

    Read here rather than modelled as a loop outcome so the IDE gets the same honest
    answer whichever way a verdict is stuck: the daemon is down, the pass has not run
    yet, or Loop B deliberately deferred a code-implying accept to a realize pass. In
    every case the fact the user needs is the same — "recorded, not applied yet" —
    and re-offering a fresh Accept button instead reads as the click having failed.

    Never raises: a missing or malformed inbox just means nothing is pending.
    """
    try:
        from codoc.loop import inbox

        return {v.event_id for v in inbox.read_verdicts(codoc_dir)}
    except Exception:  # pragma: no cover — a derived hint must never break the render
        return set()


def write_sidecar(store: Store, codoc_dir: str | Path) -> None:
    """Write ``.codoc/tree.bindings.json`` atomically (tmp → rename).

    The sidecar is *pure derived state* (bindings, feature meta, proposals) — it is
    never hand-edited, so it is always safe to regenerate. ``safe_write_tree``
    refreshes it on every pass even when the ``tree.codoc`` *text* render is held
    back to preserve a human edit, so the IDE always sees current proposal/binding
    state (an accepted verdict reflects immediately rather than appearing to do
    nothing)."""
    # One feature-table read (include_retired) threaded into the body +
    # _compute_kinds (which needs retired features for its suppressing tag); the
    # body filters to live features to keep by_feature/feats_meta byte-identical.
    all_features = store.list_features(include_retired=True)
    features = [f for f in all_features if not f.retired]
    by_feature: dict[str, list[dict]] = {}
    by_file: dict[str, list[dict]] = {}
    feats_meta: dict[str, dict] = {}

    # One bulk bindings read grouped by feature — the per-feature
    # bindings_for_feature loop was an O(F) query storm on every render pass.
    # Grouped/sorted to match bindings_for_feature so the sidecar is byte-identical.
    grouped = store.bindings_by_feature()

    # The tree's authoring language, and per-node deviations from it. A tree is
    # allowed to be bilingual (an author who describes intent in Chinese may have
    # written one node in English and meant to), so the view cannot render from a
    # single document-level language: fonts, line-breaking, and quotation
    # conventions are all per-element decisions the host makes from `lang`.
    doc_lang = workspace_doc_language(codoc_dir)

    for f in features:
        bindings = grouped.get(f.id, [])
        by_feature[f.id] = [{"file": b.file, "symbol": b.symbol_path} for b in bindings]
        node_lang = language_tag_for(f.description or f.title, doc_lang)
        feats_meta[f.id] = {
            "title": f.title,
            "parent_id": f.parent_id,
            # v6: present ONLY when this node reads as a different language than the
            # tree's, so an all-one-language tree pays nothing and the field's
            # presence is itself the "this node is the exception" signal.
            **({"lang": node_lang} if node_lang != doc_lang.code else {}),
            # A1: the authoritative named state; `realized` kept for back-compat.
            "lifecycle": f.lifecycle.value,
            "realized": f.realized,
            # v6: the hand-authored node's client id (KTD8), present only for features
            # minted from a webview ADD. Lets the host match a freshly-minted fid back to
            # the exact in-progress node (localId→fid), killing the title/order guesswork.
            **({"local_id": f.local_id} if f.local_id else {}),
            # v5: a derived one-line pitch (first sentence of the prose, refs
            # flattened to labels, else the title) for overview / glance rendering.
            "pitch": _pitch(f.description, f.title),
        }
        for b in bindings:
            by_file.setdefault(b.file, []).append(
                {"symbol": b.symbol_path, "feature_id": f.id, "feature_title": f.title}
            )

    from codoc.loop.phase import project_from_store

    # The SINGLE mid-flight projection (Proposal B): one pure pass over the
    # authoritative + loop-computed inputs produces holds / hold_detail /
    # feature_drift / feature_resolution as thin views off one source of truth,
    # plus the new per-feature `feature_phase` slice. Replaces the four hand-synced
    # helpers (_live_drift / _live_resolution / _hold_detail / _intent_gloss) this
    # file used to carry. Doc-wins is resolved in the primary `feature_phase` slice;
    # the drift/resolution slices keep the former filters (see phase.Projection).
    proj = project_from_store(store, codoc_dir)

    # ONE read of the edge table, shared by every slice derived from it: the
    # feature_edges aggregate, _compute_see_also above it, and the impact index. This
    # pass runs on every loop tick, so a slice-per-read would be three scans a tick.
    from codoc.graph.query import feature_impact

    edge_rows = store.all_edges(internal_only=True)
    edges = _compute_feature_edges(store, edge_rows)
    impact = feature_impact(store, edge_rows)

    # ONE recent-events read shared by the changes feed (last 50 applied) AND the
    # per-feature blame history (grouped from a wider window) — two indexed scans
    # per render pass would be pure waste (the pass runs on every loop tick).
    recent_events = store.recent_events(_HISTORY_FEED_SCAN)
    live_ids = {f.id for f in features}

    # W8: the timeline transport, off the SAME scan. Its own file rather than a slice
    # here because it is the one view that carries prose, and the sidecar is re-read on
    # every pass by everything (see loop/revisions.py). Self-skipping when the window
    # hasn't moved, so an event-free pass writes nothing.
    from codoc.loop.revisions import write_revisions
    write_revisions(recent_events, codoc_dir)

    sidecar = {
        # v6: adds the `blocks` slice (typed-media blocks). Presence-keyed — the TS
        # reader and the hub key on field presence, so a v5 sidecar (no `blocks`)
        # still parses and a host that predates blocks ignores the slice.
        "version": 6,
        # v6: the tree's authoring language (`codoc/doclang.py`). The host stamps it
        # on the document root so CJK prose gets CJK fonts and line-breaking, and
        # offers the switch that writes `.codoc/config.json`.
        "doc_language": {"code": doc_lang.code, "name": doc_lang.name,
                         "script": doc_lang.script.name},
        "by_feature": by_feature,
        "by_file": by_file,
        "features": feats_meta,
        "feature_edges": edges,
        "proposals": _proposals_map(store, _voted_event_ids(codoc_dir)),
        # v4: the provenance ledger surfaced to the IDE — recent applied events
        # (who/how/why-chained) + the doc-wins hold set.
        "changes": _changes_feed(recent_events),
        # v6: descriptions the loop rewrote unasked — see _auto_edits for why
        # this is the ONLY automatic op the IDE is told about.
        "auto_edits": _auto_edits(recent_events, {f.id for f in features}),
        "holds": proj.holds,
        # v5: per-held-feature detail for the in-situ "pending intent" decoration —
        # the queued directive's kind + a plain-language intent gloss, so the IDE can
        # show WHAT codoc understood (hover title on the pending rail), not just that
        # something is queued. Keyed by feature id; a subset of `holds` (only features
        # with a queued directive). A thin view of the single phase projection.
        "hold_detail": proj.hold_detail,
        # v5: lightweight INFERRED structure (additive optional slices, no version
        # bump). `feature_kind` is a Diátaxis-lite hint rendered as a chip below the
        # feature title; `feature_see_also` is the top-N coupled neighbours emitted
        # as data only (the Connections panel already surfaces coupled features) —
        # NEVER a `> …` steering line, never tree.codoc/tree.doc.json content.
        "feature_kind": _compute_kinds(store, all_features),
        "feature_see_also": _compute_see_also(edges),
        # v6: which features would feel a change to this one — Sillito's group-4
        # question ("what happens if I change this?"), which the dependency graph has
        # always known and no description states. The INCOMING direction, unlike
        # `feature_edges`/`feature_see_also` above, because that is the direction the
        # question runs in: a reader about to edit wants the callers, not the callees.
        # Each row carries the symbols doing the depending, so the claim is checkable
        # rather than a number to take on trust. Absent for a feature nothing depends
        # on. Kept OUT of the prose deliberately (see graph.query.feature_impact).
        "feature_impact": impact,
        # v5: the per-feature drift/trust signal (questioned / binding-lost). This
        # is RE-EMITTED passively from the loop-computed `drift.json` — render has
        # NO live index, so it cannot recompute fingerprint-vs-tokens_hash here
        # (KTD2). An interactive write (Accept/Reject, MCP reflect) thus re-emits
        # the last loop-computed drift, but FILTERED against live store state
        # (`_live_drift`) so an ATTACH that re-bound a `binding-lost` feature, or a
        # retired/removed feature, no longer shows a contradictory badge before the
        # next loop pass. `followed` features are absent.
        "feature_drift": proj.drift,
        # v5 (U5): the per-feature realize-divergence signal — a feature changed
        # BEYOND a directive's target ("scope") during a realization, surfaced for
        # "review what the AI did" (F3). Re-emitted from the loop-computed
        # resolution.json, filtered to features whose surfaced proposal is still
        # pending (a resolved one drops the flag). Faithful realizations are absent.
        "feature_resolution": proj.resolution,
        # v5 (Proposal B): the SINGLE mid-flight phase per feature — the one place
        # "where is this feature in its lifecycle?" is named, from which holds /
        # hold_detail / feature_drift / feature_resolution are thin views above.
        # `synced` features are absent (no dot); doc-wins is applied here once (a
        # held feature is `drafting`/`queued`, never also `drifted`/`divergent`).
        "feature_phase": proj.phase,
        # v6: typed-media blocks per feature (diagram / image / latex / url / …),
        # persistent only — prose is block-zero (the description) and transient
        # blocks ride the steers channel. A feature with no typed media is absent.
        "blocks": _blocks_map(store, {f.id for f in features}),
        # W2 (blame): bounded per-feature edit history (who/when/why) for the IDE's
        # History stance + hover timeline. Only features changed within the scan
        # window appear; deep history stays in `codoc history` / codoc_history MCP.
        "feature_history": _history_feed(recent_events, live_ids),
        # W8: durable inline comment threads (see _comments_map). A workspace with no
        # comments emits an empty map, and the reader keys on presence.
        "comments": _comments_map(store),
    }
    # Route through the shared atomic writer (per-writer-unique tmp) rather than a
    # hand-rolled fixed-name tmp: two writers of this sidecar (two daemons, or a daemon
    # racing an MCP reflection) must not collide on `tree.bindings.json.tmp` and crash on
    # os.replace when the other already renamed it.
    from codoc.loop.fsio import atomic_write_json
    atomic_write_json(Path(codoc_dir) / BINDINGS_FILENAME, sidecar)

    # The cross-reference registry rides on the same seam as the bindings
    # sidecar (one write per pass, every call-site, no double-write). It is pure
    # derived state and the IDE degrades gracefully (loadRegistry → null) when it
    # is absent or stale, so a disk error writing it (EROFS / disk-full on the
    # tmp file) must NOT abort the caller — that would propagate out of
    # write_tree → run_loop_b and skip directive queueing + status refresh.
    try:
        write_registry(store, codoc_dir)
    except OSError as exc:
        import logging
        logging.getLogger(__name__).warning(
            "write_registry failed (%s); registry left stale", exc)


def write_tree(store: Store, codoc_dir: str | Path, *, sidecar: bool = True) -> Path:
    path = tree_path(codoc_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = render_tree(store)
    # Skip the text write when the on-disk file is already byte-identical. A redundant
    # rewrite bumps the file mtime, and when the IDE has tree.codoc open (the custom
    # editor) that external change races its own save → "content of the file is newer".
    # The common daemon path — re-render after a clean round-trip — is byte-identical,
    # so this removes the dominant write-conflict. The sidecar is pure derived state
    # and is refreshed below unless the caller just wrote it itself
    # (safe_write_tree passes sidecar=False — the compute is O(F+B+E) and was
    # being done twice per tick).
    if not (path.exists() and path.read_text(encoding="utf-8") == rendered):
        path.write_text(rendered, encoding="utf-8")
    if sidecar:
        write_sidecar(store, codoc_dir)
    return path
