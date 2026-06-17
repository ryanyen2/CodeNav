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

import json
import os
import re
from collections import defaultdict
from pathlib import Path

from codoc.model.event import LOOP_A_AGENT_SOURCE, PLAN_SOURCE, Event, NodeOpKind
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

    def walk(parent_id: str | None, depth: int) -> None:
        indent = "  " * depth
        for f in store.children(parent_id):
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
    """Human-readable label for the origin of a proposal."""
    if e.source == PLAN_SOURCE:
        return "agent plan"
    if e.source == LOOP_A_AGENT_SOURCE:
        return "agent reflection"
    return "code drift"


def _proposals_map(store: Store) -> dict[str, dict]:
    """Sidecar payload describing pending proposals for the IDE to render in place.

    ``by_feature`` keys RETIRE/AMEND (and the *source* annotation of a MOVE) by
    the live ``feature_id`` they decorate; ``by_event`` keys ADD/MOVE *ghosts*
    (the text hunks) by ``event_id`` so the IDE can show details + Accept/Reject
    without re-parsing. ``by_parent`` lists the ADD/MOVE event ids landing under
    each destination parent (``""`` = top level) so the IDE can anchor an
    Accept/Reject affordance at the parent node, not only on the ghost line. Both
    halves carry the origin ``tag`` and ``rationale``.
    """
    by_feature: dict[str, dict] = {}
    by_event: dict[str, dict] = {}
    by_parent: dict[str, list[str]] = {}
    for e in store.pending_events():
        op = e.op
        tag = _source_tag(e)
        prov = {"actor": e.actor, "mode": e.mode, "caused_by": e.caused_by}
        if op.kind is NodeOpKind.RETIRE_NODE and op.feature_id:
            by_feature[op.feature_id] = {
                "op": "retire", "event_id": e.id, "tag": tag, "rationale": op.rationale,
                **prov,
            }
        elif op.kind is NodeOpKind.AMEND and op.feature_id:
            by_feature[op.feature_id] = {
                "op": "amend", "event_id": e.id, "tag": tag, "rationale": op.rationale,
                "title": op.title, "description": op.description, **prov,
            }
        elif op.kind is NodeOpKind.ADD_NODE:
            by_event[e.id] = {
                "op": "add", "parent_id": op.parent_id, "tag": tag, "rationale": op.rationale,
                "title": op.title, "description": op.description, **prov,
            }
            by_parent.setdefault(op.parent_id or "", []).append(e.id)
        elif op.kind is NodeOpKind.MOVE_NODE:
            # The destination ghost (text) conveys the move; the IDE can dim the
            # source node by scanning `by_event` for op=="move" and its feature_id.
            by_event[e.id] = {
                "op": "move", "feature_id": op.feature_id, "parent_id": op.parent_id,
                "tag": tag, "rationale": op.rationale, **prov,
            }
            by_parent.setdefault(op.parent_id or "", []).append(e.id)
    return {"by_feature": by_feature, "by_event": by_event, "by_parent": by_parent}


_CHANGES_FEED_LIMIT = 50


def _changes_feed(store: Store) -> list[dict]:
    """The last N *applied* events as a provenance feed (newest first).

    This is how the IDE learns WHO last changed each feature without reading the
    event log itself: an agent-authored AMEND shows up here so the doc view can
    re-stamp the new prose as pencil ink instead of resetting authorship, and a
    ``caused_by`` directive id lets it group a reflection cascade under the doc
    edit that triggered it. Legacy events carry empty strings — render as today.
    """
    out: list[dict] = []
    for e in store.recent_events(_CHANGES_FEED_LIMIT * 2):
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


def _compute_feature_edges(store: Store) -> dict[str, list[dict]]:
    """Aggregate symbol-level call/import edges into feature-level coupling.

    Returns ``{src_feature_id: [{to: dst_feature_id, weight: int, kinds: list[str]}]}``.
    Used by the VS Code extension for dependency-focus opacity dimming.
    """
    sym2feat = {b.symbol_path: b.feature_id for b in store.all_bindings()}
    agg: dict[tuple[str, str], dict] = {}  # (src_fid, dst_fid) → {weight, kinds}
    for e in store.all_edges(internal_only=True):
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

    This OVERLAPS the IDE's Connections panel (Depends-on / Used-by from the same
    ``feature_edges``), so it is emitted purely as derived data for completeness +
    future consumers — it is *never* a ``> …`` steering line and never enters
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


def _live_drift(store: Store, drift: dict[str, str]) -> dict[str, str]:
    """Filter a loop-computed drift map against live store state (pure store
    reads — NO index access, keeping render index-free).

    An interactive re-emit (MCP reflect ATTACH, Accept/Reject) re-writes the
    sidecar without recomputing drift, so a stale entry can outlive the state it
    described until the next loop pass. We drop the entries that are now provably
    contradicted by the store:

    - ``binding-lost`` for a feature that NOW owns >=1 binding (an ATTACH re-bound
      it) — the badge would directly contradict the visible bindings.
    - any entry for a feature that is now retired or absent.

    ``questioned`` entries (the prose may be stale) are kept: only a loop pass
    with a fresh index can tell whether the code drift was resolved, so render
    must not guess. From the same module's :data:`~codoc.loop.edits.DRIFT_*`."""
    if not drift:
        return drift
    from codoc.loop.edits import DRIFT_BINDING_LOST

    out: dict[str, str] = {}
    for fid, state in drift.items():
        f = store.get_feature(fid)
        if f is None or f.retired:
            continue  # feature gone/retired — its badge is meaningless now
        if state == DRIFT_BINDING_LOST and store.bindings_for_feature(fid):
            continue  # re-bound since the loop pass — badge contradicts state
        out[fid] = state
    return out


def _live_resolution(store: Store, resolution: dict[str, str]) -> dict[str, str]:
    """Filter the loop-computed realize-divergence map (U5) against live store state
    (pure store reads). A divergence flag is only meaningful while its surfaced
    proposal is still pending review: an interactive re-emit after the human
    accepts/rejects that proposal must drop the flag (the loop's own prune handles
    the daemon path; this covers the no-loop re-render). Also drop gone/retired
    features."""
    if not resolution:
        return resolution
    pend = {e.op.feature_id for e in store.pending_events() if e.op.feature_id}
    out: dict[str, str] = {}
    for fid, reason in resolution.items():
        f = store.get_feature(fid)
        if f is None or f.retired or fid not in pend:
            continue
        out[fid] = reason
    return out


def _intent_gloss(kind: str) -> str:
    """A one-line, plain-language summary of what the queued directive will DO,
    surfaced as the held feature's hover title. The point is recognition, not just
    a count: the author can confirm codoc understood the *kind* of work their edit
    implied (update vs implement vs remove vs steer), in their own words."""
    k = (kind or "").lower()
    if "steer" in k:
        return "apply your note to this feature's code"
    if "retire" in k:
        return "remove this feature's code"
    if "add" in k:
        return "implement this feature in code"
    return "update the code to match your new intent"  # amend / default


def _hold_detail(store: Store, codoc_dir: str | Path) -> dict[str, dict]:
    """Per-held-feature detail for the in-situ "pending intent" decoration: the
    queued directive's ``kind`` + a plain-language intent gloss, keyed by feature id.

    Read from the realize.json manifest — the same source ``hold_set`` reads, so a
    feature that appears here always also appears in ``holds``. A held feature with
    only a live (payload-less) intent and no queued directive is absent: it still
    gets the plain hold rail via ``holds``, just no gloss. First directive per
    feature wins. Pure derived state (no model fields)."""
    from codoc.loop.edits import read_manifest

    out: dict[str, dict] = {}
    for d in read_manifest(codoc_dir):
        if not d.feature_id or d.feature_id in out:
            continue
        f = store.get_feature(d.feature_id)
        if f is None or f.retired:
            continue
        out[d.feature_id] = {"kind": d.kind, "intent": _intent_gloss(d.kind)}
    return out


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

    for f in features:
        bindings = store.bindings_for_feature(f.id)
        by_feature[f.id] = [{"file": b.file, "symbol": b.symbol_path} for b in bindings]
        feats_meta[f.id] = {
            "title": f.title,
            "parent_id": f.parent_id,
            "realized": f.realized,
            # v5: a derived one-line pitch (first sentence of the prose, refs
            # flattened to labels, else the title) for overview / glance rendering.
            "pitch": _pitch(f.description, f.title),
        }
        for b in bindings:
            by_file.setdefault(b.file, []).append(
                {"symbol": b.symbol_path, "feature_id": f.id, "feature_title": f.title}
            )

    from codoc.loop.edits import hold_set, read_drift, read_resolution

    # Compute feature-coupling edges ONCE and share them between the feature_edges
    # slot and _compute_see_also (which is derived from the same edges).
    edges = _compute_feature_edges(store)

    sidecar = {
        "version": 5,
        "by_feature": by_feature,
        "by_file": by_file,
        "features": feats_meta,
        "feature_edges": edges,
        "proposals": _proposals_map(store),
        # v4: the provenance ledger surfaced to the IDE — recent applied events
        # (who/how/why-chained) + the doc-wins hold set.
        "changes": _changes_feed(store),
        "holds": sorted(hold_set(codoc_dir)),
        # v5: per-held-feature detail for the in-situ "pending intent" decoration —
        # the queued directive's kind + a plain-language intent gloss, so the IDE can
        # show WHAT codoc understood (hover title on the pending rail), not just that
        # something is queued. Keyed by feature id; a subset of `holds` (only features
        # with a queued directive). Pure derived from the realize.json manifest.
        "hold_detail": _hold_detail(store, codoc_dir),
        # v5: lightweight INFERRED structure (additive optional slices, no version
        # bump). `feature_kind` is a Diátaxis-lite hint rendered as a chip below the
        # feature title; `feature_see_also` is the top-N coupled neighbours emitted
        # as data only (the Connections panel already surfaces coupled features) —
        # NEVER a `> …` steering line, never tree.codoc/tree.doc.json content.
        "feature_kind": _compute_kinds(store, all_features),
        "feature_see_also": _compute_see_also(edges),
        # v5: the per-feature drift/trust signal (questioned / binding-lost). This
        # is RE-EMITTED passively from the loop-computed `drift.json` — render has
        # NO live index, so it cannot recompute fingerprint-vs-tokens_hash here
        # (KTD2). An interactive write (Accept/Reject, MCP reflect) thus re-emits
        # the last loop-computed drift, but FILTERED against live store state
        # (`_live_drift`) so an ATTACH that re-bound a `binding-lost` feature, or a
        # retired/removed feature, no longer shows a contradictory badge before the
        # next loop pass. `followed` features are absent.
        "feature_drift": _live_drift(store, read_drift(codoc_dir)),
        # v5 (U5): the per-feature realize-divergence signal — a feature changed
        # BEYOND a directive's target ("scope") during a realization, surfaced for
        # "review what the AI did" (F3). Re-emitted from the loop-computed
        # resolution.json, filtered to features whose surfaced proposal is still
        # pending (a resolved one drops the flag). Faithful realizations are absent.
        "feature_resolution": _live_resolution(store, read_resolution(codoc_dir)),
    }
    dest = Path(codoc_dir) / BINDINGS_FILENAME
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(sidecar, indent=2))
    os.replace(tmp, dest)

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


def write_tree(store: Store, codoc_dir: str | Path) -> Path:
    path = tree_path(codoc_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = render_tree(store)
    # Skip the text write when the on-disk file is already byte-identical. A redundant
    # rewrite bumps the file mtime, and when the IDE has tree.codoc open (the custom
    # editor) that external change races its own save → "content of the file is newer".
    # The common daemon path — re-render after a clean round-trip — is byte-identical,
    # so this removes the dominant write-conflict. The sidecar is pure derived state
    # and is always refreshed below.
    if not (path.exists() and path.read_text() == rendered):
        path.write_text(rendered)
    write_sidecar(store, codoc_dir)
    return path
