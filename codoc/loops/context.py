"""Relevance selection for an LLM pass or an agent read.

Seeds are the features bound in the touched files (plus any changed symbols),
expanded one hop along the code graph, plus each feature's parent and
children. Features with queued work and no bindings (planned) are always in
scope, so a fresh chunk that fulfils an accepted plan is attached to it rather
than duplicated beside it.

Every feature entry is block-keyed: its description is a list of
``{id, text}`` blocks, so an amend names the block it changes.
"""
from __future__ import annotations

from codoc.calculus import block_md
from codoc.calculus.derive import block_spec, block_written_by, kind_words, markdown, phase
from codoc.calculus.doc import BUILTIN, HEADING, PROSE, SOURCE, parent_of
from codoc.calculus.store import Store
from codoc.index.graph import Graph

RECENT_OPS = 5      # the latest ops on the seed features a pass is shown
_ID_KEYS = frozenset({"id", "parent_id", "feature_id", "current_feature_id"})


def aliases(store: Store, subtree: list[dict] = ()) -> dict[str, str]:
    """Short names for the ids a model pass is shown, which it copies back
    intact where a twelve-digit block id returns with a digit changed:
    ``f<n>`` for the n-th feature in document order, and ``f<n>.<k>`` for the
    k-th block of a ``subtree`` entry's description. The MCP tools show real
    ids: an agent copies them out of a tool result, not out of a prompt."""
    names = {h.id: f"f{i}" for i, h in enumerate(store.headings(), 1)}
    for entry in subtree:
        for k, b in enumerate(entry.get("blocks", []), 1):
            names[b["id"]] = f"{names[entry['id']]}.{k}"
    return names


def renamed(obj, names: dict[str, str]):
    """JSON-ready ``obj`` with each id under an id key (``id``, ``parent_id``,
    ``feature_id``, ``current_feature_id``) replaced by its entry in ``names``.
    An id ``names`` lacks is kept as it is."""
    if isinstance(obj, list):
        return [renamed(x, names) for x in obj]
    if isinstance(obj, dict):
        return {k: names.get(v, v) if k in _ID_KEYS and isinstance(v, str) else renamed(v, names)
                for k, v in obj.items()}
    return obj


def owner_of(store: Store, symbol: str) -> str | None:
    b = store.bindings.get(symbol)
    return b.feature if b else None


def written_by(store: Store, fid: str) -> str:
    """``human`` when a person wrote any word of the feature's prose, else the
    newest machine actor. Feeds the prompt's ``written_by``; the amend gate
    and translate's plan-or-trunk choice are per block (``Workspace.amend``). A
    block out of the reading flow (a margin comment) is a request about the
    prose, not the prose: a person's note on a loop-written feature does not
    make the feature theirs."""
    authors = [block_written_by(store, b.id) for b in store.body(fid) if b.layout.flow]
    if "human" in authors:
        return "human"
    return next((a for a in reversed(authors) if a), "")


def description_blocks(store: Store, fid: str, kinds: frozenset = PROSE | SOURCE, *, mode: str = "rejected") -> list[dict]:
    """The feature's description as ``[{id, text}]``, one entry per block, in
    a read mode (``rejected``; the reflect pass reads ``loop``). ``text`` is
    that block alone as Markdown (``block_md.render_one``), which is what an
    amend names and rewrites."""
    out = []
    for b in store.body(fid)[1:]:
        if b.kind in kinds and (spec := block_spec(store, b, mode)) is not None:
            out.append({"id": b.id, "text": block_md.render_one(spec)})
    return out


def feature_entry(store: Store, fid: str, *, full: bool = True, mode: str = "rejected") -> dict:
    headings = store.headings()
    idx = next(i for i, h in enumerate(headings) if h.id == fid)
    entry = {
        "id": fid,
        "title": markdown(store.blocks[fid].text(), mode, store),
        "parent_id": parent_of(headings, idx),
    }
    if full:
        entry["blocks"] = description_blocks(store, fid, mode=mode)
        # An image, a link, a pdf: read, not amended, so not in `blocks`.
        if kinds := [w for b in store.body(fid) if b.kind not in BUILTIN and b.layout.flow
                     if (w := kind_words(b, store))]:
            entry["kind_blocks"] = kinds
        entry["bindings"] = sorted(b.symbol for b in store.bindings.values() if b.feature == fid)
        wb = written_by(store, fid)
        if wb:
            entry["written_by"] = wb
    if phase(store, fid) == "planned":
        entry["planned"] = True
    return entry


def select_context(store: Store, graph: Graph, files: set[str], changed_symbols: set[str] | None = None,
                   *, mode: str = "rejected") -> tuple[list[dict], list[dict], dict]:
    """``(subtree, all_titles, context)`` as JSON-ready dicts, the features
    read in ``mode``."""
    changed_symbols = set(changed_symbols or ())
    seed_bindings = [b for b in store.bindings.values() if b.file in files]
    seed_features = {b.feature for b in seed_bindings}
    seed_symbols = {b.symbol for b in seed_bindings}
    related = set(seed_features)
    for sym in graph.ego(seed_symbols | changed_symbols):
        f = owner_of(store, sym)
        if f:
            related.add(f)
    headings = store.headings()
    index = {h.id: i for i, h in enumerate(headings)}
    ids = set(related)
    for fid in list(related):
        if fid not in index:
            continue
        p = parent_of(headings, index[fid])
        if p:
            ids.add(p)
        # children: headings dominated by fid
        for b in store.range(fid)[1:]:
            if b.kind == "heading" and parent_of(headings, index[b.id]) == fid:
                ids.add(b.id)
    for h in headings:
        if phase(store, h.id) == "planned":
            ids.add(h.id)
    subtree = [feature_entry(store, fid, mode=mode) for fid in sorted(ids) if fid in index]
    all_titles = [feature_entry(store, h.id, full=False, mode=mode) for h in headings]
    context = _graph_context(store, graph, changed_symbols or seed_symbols, seed_features)
    return subtree, all_titles, context


def feature_context(store: Store, graph: Graph, fid: str) -> tuple[list[dict], list[dict], dict]:
    """What an agent building ``fid`` reads, as ``select_context`` returns it:
    the feature and every feature under it, then each feature owning a symbol
    one call, import or inheritance away from their code, either way: what it
    uses, so a change to a shared symbol is made knowing what its owner says,
    and what uses it, which a changed signature breaks."""
    in_range = [b.id for b in store.range(fid) if b.kind == HEADING]
    symbols = {b.symbol for b in store.bindings.values() if b.feature in in_range}
    near: list[str] = []
    for sym in sorted(symbols):
        for other in graph.neighbors(sym, kinds={"call", "import", "type_ref", "inherit"}):
            f = owner_of(store, other)
            if f and f not in in_range and f not in near:
                near.append(f)
    return ([feature_entry(store, f) for f in in_range + near],
            [feature_entry(store, h.id, full=False) for h in store.headings()],
            _graph_context(store, graph, symbols, set(in_range)))


def _graph_context(store: Store, graph: Graph, symbols: set[str], seed_features: set[str]) -> dict:
    edges: list[dict] = []
    seen: set[tuple] = set()
    for sym in sorted(symbols):
        for e in graph.edges_out(sym) + graph.edges_in(sym):
            k = (e["src_symbol"], e.get("dst_symbol") or "", e["kind"])
            if k not in seen:
                seen.add(k)
                edges.append({"from": e["src_symbol"], "to": e.get("dst_symbol"), "kind": e["kind"]})
    ids: set[str] = set()
    for f in seed_features:
        if f in store.blocks:
            ids.update(b.id for b in store.body(f))
    recent = [{"kind": op.kind, "feature_id": op.target.get("fid") or store.feature_of(op.payload.get("bid") or op.target.get("bid", "")),
               "actor": op.actor, "at": op.at}
              for op in reversed(store.ops_on(ids)) if op.conflict is None][:RECENT_OPS]
    return {"edges": edges[:20], "recent": recent}


def impacted_features(store: Store, graph: Graph, changed_symbols: set[str]) -> dict[str, list[str]]:
    """Features whose code calls or imports the changed symbols (upstream
    dependents), with the symbols that make them so."""
    out: dict[str, list[str]] = {}
    changed_owner = {s: owner_of(store, s) for s in changed_symbols}
    for sym in changed_symbols:
        for caller in graph.neighbors(sym, kinds={"call", "import", "type_ref", "inherit"}, direction="in"):
            f = owner_of(store, caller)
            if f and f != changed_owner.get(sym):
                out.setdefault(f, []).append(sym)
    return out
