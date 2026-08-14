"""Hierarchical bootstrap — per-file proposal + top-level organization.

Two scoped LLM passes replace the old flat, attach-biased, leaf-first global
batching that produced cross-file junk-drawer nodes and an almost-flat tree:

  Phase 1 (per file) — one ``propose_file_features`` call per source file. The
    model only ever sees ONE file's chunks, so it structurally cannot dump
    unrelated symbols from other files into the same node. It returns a small,
    coherent set of features for that file, optionally nested.

  Phase 2 (organize) — one ``propose_organization`` call over all file-level
    features + their call/import coupling, grouping them under a few broad theme
    parents (``add_node`` themes + ``move_node`` of existing features). This is
    what gives the tree real depth.

New nodes carry a temporary local id (in ``NodeOp.feature_id`` for an
``add_node``); children and moves reference it via ``parent_id``.
:func:`_apply_ops_with_local_ids` mints the real ids and remaps references before
applying — which is what lets a single call nest a new node under another new
node (the old apply path minted ids only at write time, so within-call nesting
was impossible).
"""
from __future__ import annotations

from collections import Counter

from codoc.agent.bootstrap_agent import propose_file_features, propose_organization
from codoc.doclang import DocLanguage
from codoc.loop.apply import apply_op
from codoc.loop.bootstrap import BootstrapResult, _title_from_file
from codoc.loop.surface import flow_lines
from codoc.loop.why import commit_rationales
from codoc.model.event import NodeOp, NodeOpKind
from codoc.model.ids import new_feature_id
from codoc.store.db import Store

_CALLS_CAP = 6           # per-symbol call/called-by edges shown to the file pass
_COUPLING_CAP = 40       # feature→feature coupling lines shown to the org pass
_SOURCE_CAP = 600        # chars of each chunk's source passed to the model


# ---------------------------------------------------------------------------
# Local-id resolution + apply
# ---------------------------------------------------------------------------

def _apply_ops_with_local_ids(
    ops: list[NodeOp],
    store: Store,
    fps: dict[tuple[str, str], str],
    *,
    source: str,
    ths: dict[tuple[str, str], str] | None = None,
) -> None:
    """Mint real ids for new nodes, remap temporary parent references, apply.

    A new node's temporary local id lives in ``feature_id``; any ``parent_id``
    (or a ``move_node`` target) referencing it is rewritten to the minted id.
    References that resolve to neither a local node nor an existing feature are
    dropped to top level (``add_node``) or skipped harmlessly (``move_node``
    no-ops on an unknown feature). ``add_node`` ops apply first so parents exist.
    """
    local_map: dict[str, str] = {}
    for op in ops:
        if op.kind is NodeOpKind.ADD_NODE:
            temp = op.feature_id
            real = new_feature_id()
            if temp and store.get_feature(temp) is None:
                local_map[temp] = real
            op.feature_id = real

    def resolve(ref: str | None) -> str | None:
        if ref is None:
            return None
        if ref in local_map:
            return local_map[ref]
        if store.get_feature(ref) is not None:
            return ref
        return None  # unknown reference → top level

    for op in ops:
        op.parent_id = resolve(op.parent_id)
        if op.kind is not NodeOpKind.ADD_NODE and op.feature_id in local_map:
            op.feature_id = local_map[op.feature_id]

    add_nodes = [o for o in ops if o.kind is NodeOpKind.ADD_NODE]
    others = [o for o in ops if o.kind is not NodeOpKind.ADD_NODE]
    for op in add_nodes + others:
        apply_op(op, store, source=source, applied=True, fp_lookup=fps, th_lookup=ths or {})


# ---------------------------------------------------------------------------
# Phase 1 — per-file context + coverage
# ---------------------------------------------------------------------------

def _file_edges(file_rows: list, store: Store) -> list[dict]:
    """Per-symbol call/containment context for one file's chunks."""
    ctx: list[dict] = []
    for r in file_rows:
        out_e = store.edges_out(r.symbol_path, internal_only=True)
        in_e = store.edges_in(r.symbol_path, internal_only=True)
        calls = [e["dst_symbol"] for e in out_e if e["kind"] == "call" and e["dst_symbol"]][:_CALLS_CAP]
        called_by = [e["src_symbol"] for e in in_e if e["kind"] == "call"][:_CALLS_CAP]
        contained_in = next((e["dst_symbol"] for e in out_e if e["kind"] == "contain"), None)
        if calls or called_by or contained_in:
            entry: dict = {"symbol": r.symbol_path}
            if calls:
                entry["calls"] = calls
            if called_by:
                entry["called_by"] = called_by
            if contained_in:
                entry["contained_in"] = contained_in
            ctx.append(entry)
    return ctx


def _ensure_file_coverage(ops: list[NodeOp], file_rows: list, file: str) -> list[NodeOp]:
    """Guarantee every chunk in the file is bound, without leaving the file.

    Uncovered chunks are folded into the file's primary (largest) new node — same
    file, so no cross-file junk drawer. If the model emitted nothing at all, mint
    a single node for the whole file (empty description; the user renames/fills).

    This is also where a model-invented binding is caught. ``added_keys`` is the
    exact set of chunks this file has, so any pair outside it names nothing —
    the prompt shows the model a bare ``symbol_path`` list and asks it to
    reconstruct ``[file, symbol_path]`` from a header, and it sometimes drops the
    basename out of the middle of the path. Filtering here fixes the coverage
    accounting too: an invented pair used to satisfy ``covered`` for a chunk it
    did not actually name, so the real chunk was bound only by accident of also
    being uncovered.
    """
    added_keys = {(r.file, r.symbol_path) for r in file_rows}
    for op in ops:
        kept = [b for b in op.bindings if tuple(b) in added_keys]
        if len(kept) != len(op.bindings):
            op.bindings = kept
    covered = {b for op in ops for b in op.bindings}
    uncovered = sorted(added_keys - covered)
    if not uncovered:
        return ops

    add_nodes = [o for o in ops if o.kind is NodeOpKind.ADD_NODE]
    if add_nodes:
        primary = max(add_nodes, key=lambda o: len(o.bindings))
        primary.bindings = list(primary.bindings) + uncovered
        return ops

    return list(ops) + [
        NodeOp(
            kind=NodeOpKind.ADD_NODE,
            title=_title_from_file(file),
            description="",
            bindings=uncovered,
            rationale="coverage: model returned no nodes for this file",
        )
    ]


# ---------------------------------------------------------------------------
# Phase 2 — feature-level coupling
# ---------------------------------------------------------------------------

def _feature_coupling(store: Store) -> list[str]:
    """Aggregate internal call/import edges to feature→feature coupling lines."""
    pair: Counter[tuple[str, str]] = Counter()
    for e in store.all_edges(internal_only=True):
        if e["kind"] not in ("call", "import") or not e["dst_symbol"]:
            continue
        src, dst = e["src_symbol"], e["dst_symbol"]
        if "::" not in src or "::" not in dst:
            continue
        sb = store.binding_at(src.split("::", 1)[0], src)
        db = store.binding_at(dst.split("::", 1)[0], dst)
        if sb and db and sb.feature_id != db.feature_id:
            pair[(sb.feature_id, db.feature_id)] += 1

    titles = {f.id: f.title for f in store.list_features()}
    lines: list[str] = []
    for (a, b), n in pair.most_common(_COUPLING_CAP):
        lines.append(f"{a} ({titles.get(a, '?')}) → {b} ({titles.get(b, '?')}): {n}")
    return lines


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

# Above this many top-level features the single-prompt organization pass would
# overflow the model's context — skip it and keep the (usable) flat tree.
_ORG_FEATURE_CAP = 400


def bootstrap_hier_from_chunks(
    rows: list,
    store: Store,
    *,
    propose_file=propose_file_features,
    propose_org=propose_organization,
    repo_name: str = "codebase",
    config=None,
    organize: bool = True,
    printer=None,
    root_dir: str | None = None,
    doc_language: DocLanguage | None = None,
) -> BootstrapResult:
    """Two-phase bootstrap: per-file features, then top-level organization.

    ``root_dir`` enables the why-evidence pass: one cached ``git log`` for the
    whole repo, sliced per file. Bootstrap is where it matters most — the
    initial tree is written from code alone, so without commit messages every
    "why" in it is invention.
    """
    import os as _os

    say = printer or (lambda *_a, **_k: None)
    by_file: dict[str, list] = {}
    for r in rows:
        by_file.setdefault(r.file, []).append(r)

    files = sorted(by_file)
    total = len(files)
    # Bootstrap makes ~1 LLM call per file (run in concurrent waves below). On a big
    # repo that still costs — warn, and honour an opt-in hard cap so a user can bound it.
    cap_env = _os.environ.get("CODOC_BOOTSTRAP_MAX_FILES", "").strip()
    max_files = int(cap_env) if cap_env.isdigit() and int(cap_env) > 0 else 0
    if max_files and total > max_files:
        say(f"  ⚠ {total} source files — bootstrapping the first {max_files} "
            "(raise or unset CODOC_BOOTSTRAP_MAX_FILES to include the rest).")
        files = files[:max_files]
        total = max_files
    elif total > 1000:
        say(f"  ⚠ {total} source files — bootstrap makes ~1 LLM call per file, so this "
            "will take a while (and cost, on a paid key). Set CODOC_BOOTSTRAP_MAX_FILES to cap it.")

    calls = 0
    # ~40 progress ticks regardless of repo size, so a large bootstrap shows steady
    # motion (not a silent multi-minute hang) without scrolling thousands of lines.
    step = max(1, total // 40)
    # One feature-table read up front; each file pass appends the titles it just
    # minted, so later files still see them as dedup context without an
    # O(files × features) re-scan.
    existing_titles = [f.title for f in store.list_features()]

    # The per-file LLM calls run in WAVES of `concurrency`: within a wave every
    # call shares the same titles snapshot (identical prompt prefix → prompt-
    # cache hits across the wave) and runs concurrently — the calls are pure
    # network/LLM work; all store reads happen before dispatch and all store
    # writes after the wave, on this thread, in deterministic file order. The
    # titles list extends between waves, so cross-wave dedup context is kept;
    # intra-wave near-duplicates are absorbed by the org pass + the
    # (title,parent) identity guard, the same way concurrent Loop-A passes are.
    conc_env = _os.environ.get("CODOC_BOOTSTRAP_CONCURRENCY", "").strip()
    # Always ≥ 1: a non-positive / non-numeric value falls back to 8.
    concurrency = int(conc_env) if conc_env.isdigit() and int(conc_env) > 0 else 8
    failures: list[tuple[str, Exception | None]] = []
    executor = None
    if concurrency > 1 and total > 1:
        from concurrent.futures import ThreadPoolExecutor

        executor = ThreadPoolExecutor(max_workers=concurrency,
                                      thread_name_prefix="codoc-bootstrap")
    try:
        for wave_start in range(0, total, concurrency):
            wave = files[wave_start:wave_start + concurrency]
            titles_snapshot = list(existing_titles)
            prepared = []
            for file in wave:
                file_rows = sorted(by_file[file], key=lambda r: r.symbol_path)
                chunks = [
                    {"symbol_path": r.symbol_path, "source": (r.source or "")[:_SOURCE_CAP]}
                    for r in file_rows
                ]
                edges = _file_edges(file_rows, store)
                # Commit rationale for this file. The first call warms one
                # repo-wide log; every later file slices the same cached scan,
                # so this is one subprocess per bootstrap, not one per file.
                why = commit_rationales(root_dir, [file]) if root_dir else []
                prepared.append((file, file_rows, chunks, edges, why))

            def _call(item):
                """One file's proposal, retried once, then given up on.

                A bootstrap is a few dozen independent calls, and losing all of
                them because one sample came back with a stray quote in a
                description is a bad trade: the user paid for the rest, and a
                tree missing one file's prose is worth far more than no tree at
                all. Returning nothing hands the file to ``_ensure_file_coverage``,
                which mints one node named after it with no description — a
                visible, fillable gap rather than a silent hole.

                A retry is worth its cost because the common causes — a
                truncated response, a rate limit, a transient network error —
                do not repeat. What does repeat is a hard configuration problem
                (no key, no CLI), and that fails every file, which the caller
                below still treats as fatal.
                """
                file, _rows, chunks, edges, why = item
                last: Exception | None = None
                for _attempt in (1, 2):
                    try:
                        return propose_file(file, chunks, edges, titles_snapshot,
                                            repo_name=repo_name, config=config, why=why,
                                            doc_language=doc_language)
                    except Exception as exc:  # noqa: BLE001 — per-file tolerance
                        last = exc
                failures.append((file, last))
                return []

            if executor is not None and len(wave) > 1:
                try:
                    results = list(executor.map(_call, prepared))
                except BaseException:
                    # In-flight sibling calls can't be killed — say why the exit
                    # isn't instant instead of hanging silently in the
                    # interpreter's thread join for up to CODOC_LLM_TIMEOUT.
                    say("  ✗ a bootstrap LLM call failed — waiting for the "
                        "wave's in-flight calls to finish before rolling back…")
                    executor.shutdown(wait=True, cancel_futures=True)
                    raise
            else:
                results = [_call(item) for item in prepared]

            for offset, ((file, file_rows, _c, _e, _w), ops) in enumerate(zip(prepared, results)):
                idx = wave_start + offset + 1
                if idx == 1 or idx == total or idx % step == 0:
                    say(f"  · [{idx}/{total}] {file}")
                fps = {(r.file, r.symbol_path): r.tokens_hash for r in file_rows}
                ths = {(r.file, r.symbol_path): r.types_hash for r in file_rows}
                ops = _ensure_file_coverage(ops, file_rows, file)
                _apply_ops_with_local_ids(ops, store, fps, source="bootstrap", ths=ths)
                existing_titles.extend(
                    op.title for op in ops if op.kind is NodeOpKind.ADD_NODE and op.title)
                calls += 1
    finally:
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    # Every file failing is not bad luck, it is a broken setup — no key, no
    # `claude` CLI, an unreachable endpoint. Tolerating that would hand back a
    # tree of empty filename nodes and call it a success, so it still raises and
    # the caller's transaction rolls back to nothing.
    if failures and len(failures) == total:
        raise RuntimeError(
            f"every bootstrap call failed ({total}/{total}); last error: {failures[-1][1]}"
        )
    if failures:
        say(f"  ⚠ {len(failures)} of {total} files could not be described "
            f"(retried once each): {', '.join(f for f, _ in failures[:5])}"
            + (" …" if len(failures) > 5 else ""))
        say("    Each is in the tree as a node named after its file, with no "
            "description — fill them in, or re-run `codoc init` to retry.")

    top_level = store.children(None)
    if organize and 1 < len(top_level) <= _ORG_FEATURE_CAP:
        features = [
            {"id": f.id, "title": f.title, "description": f.description}
            for f in top_level
        ]
        coupling = _feature_coupling(store)
        ops = propose_org(features, coupling, repo_name=repo_name, config=config,
                          flows=flow_lines(store), doc_language=doc_language)
        _apply_ops_with_local_ids(ops, store, {}, source="bootstrap")
        calls += 1
    elif organize and len(top_level) > _ORG_FEATURE_CAP:
        say(f"  ⚠ {len(top_level)} top-level features — skipping the organization pass "
            "(too large for one prompt). The flat tree is still usable; edit it to group.")

    return BootstrapResult(
        chunks=len(rows),
        features=len(store.list_features()),
        batches=calls,
        skipped=[f for f, _ in failures],
    )
