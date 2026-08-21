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

from codoc.agent.bootstrap_agent import (
    propose_brief, propose_file_features, propose_organization,
)
from codoc.doclang import DocLanguage
from codoc.loop.apply import apply_op
from codoc.loop.bootstrap import BootstrapResult, _title_from_file
from codoc.loop.surface import flow_lines
from codoc.loop.why import commit_rationales
from codoc.loop.payload import passes
from codoc.model.event import NodeOp, NodeOpKind
from codoc.model.ids import new_feature_id
from codoc.store.db import Store

_CALLS_CAP = 6           # per-symbol call/called-by edges shown to the file pass
_COUPLING_CAP = 40       # feature→feature coupling lines shown to the org pass
# Chunk source is budgeted per FILE, not per chunk — see `loop/payload.py`. A file
# that fits is passed through at 600 chars a chunk exactly as before; a generated
# module of 786 symbols is spent down instead of sending a quarter-megabyte prompt.


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

def _in_file_order(rows: list) -> list:
    """One file's chunks in the order a person reads them.

    The index hands them back by name, which scatters the file: a module's
    constants land between its classes, and ``Store.__enter__`` comes before
    ``Store.__init__``. The prompt shows this list in order, and a crowded file is
    now CUT along it (`loop/payload.py`), so the order decides what each pass sees
    together — by name, `test/altair`'s `channels.py` gives four passes that each
    span nearly the whole 1.2 MB file and overlap one another; in file order it
    gives four contiguous regions of it.

    ``start_byte`` is the file's own order. The symbol path breaks ties, so the
    order stays total and stable where the value is absent — an unindexed row
    carries 0 for every chunk, and that is exactly the old behaviour.
    """
    return sorted(rows, key=lambda r: (r.start_byte, r.symbol_path))


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


# How much of the project's own prose the orientation pass reads. A README is
# usually short; the cap is there so a repo that ships a book does not blow the
# context on chapter one.
_README_CAP = 12_000
_HEADER_CAP = 1_200


def _project_prose(root_dir: str | None, rows: list) -> tuple[str, list[dict]]:
    """What the project says about itself: its README, and each file's opening.

    A file's opening is its module docstring and any comment block above the
    first definition. That is where an author writes the things the code cannot
    say — what a rule gives up, which order matters, what the program is not for
    — and until now no bootstrap pass ever read it, because chunks start at the
    first symbol.
    """
    import os as _os
    import re as _re

    readme = ""
    if root_dir:
        for name in ("README.md", "README.rst", "README.txt", "README",
                     "pyproject.toml", "package.json"):
            path = _os.path.join(root_dir, name)
            if _os.path.isfile(path):
                try:
                    with open(path, encoding="utf-8", errors="replace") as handle:
                        readme += f"\n\n--- {name} ---\n{handle.read()[:_README_CAP]}"
                except OSError:
                    continue
            if len(readme) > _README_CAP:
                break

    headers: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        if row.file in seen:
            continue
        seen.add(row.file)
        if not root_dir:
            continue
        path = _os.path.join(root_dir, row.file)
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                text = handle.read(_HEADER_CAP * 3)
        except OSError:
            continue
        # Everything before the first definition: the module docstring, the
        # imports, and any comment that came with them.
        cut = _re.search(r"^(?:def |class |async def )", text, _re.M)
        opening = text[: cut.start()] if cut else text
        opening = opening.strip()[:_HEADER_CAP]
        if opening:
            headers.append({"file": row.file, "opening": opening})
    return readme.strip(), headers


def bootstrap_hier_from_chunks(
    rows: list,
    store: Store,
    *,
    propose_file=propose_file_features,
    propose_org=propose_organization,
    propose_the_brief=propose_brief,
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

    # Phase 0: read what the project says about itself, before describing any
    # part of it. One call, and everything it produces rides in every file
    # prompt below. Without it each file is named on its own terms and the tree
    # has no account of what the program is for.
    brief: dict = {}
    readme, headers = _project_prose(root_dir, rows)
    if readme or headers:
        say("  · reading what the project says about itself")
        try:
            brief = propose_the_brief(readme, headers, repo_name=repo_name,
                                      config=config, doc_language=doc_language) or {}
        except Exception as exc:  # noqa: BLE001
            # A bootstrap without a brief is the old behaviour, which worked.
            # Losing every file's description because the orientation call
            # failed would be a much worse trade.
            say(f"  ⚠ could not read the project's own prose ({exc}); "
                "describing each file on its own terms")
        else:
            found = sum(len(brief.get(k, [])) for k in ("decisions", "ordering"))
            say(f"    {found} recorded decision{'' if found == 1 else 's'} "
                "and ordering constraint" + ("" if found == 1 else "s"))

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
    # Which FILES lost something, separately from which calls failed. A crowded
    # file is described over several calls, and the reader's unit is the file: the
    # warning and `skipped` name paths, the fatal guard counts calls.
    failed_files: set[str] = set()
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
                file_rows = _in_file_order(by_file[file])
                # Usually one group, so usually one call: a file is split only when
                # one call could not be shown its definitions at full allowance, and
                # that is 3 files of the 813 in this repo and its corpora (see
                # `loop/payload.py`). Splitting by top-level owner keeps a class
                # whole, so no pass is asked to name a feature over half a class.
                groups = passes({r.symbol_path: r.source or "" for r in file_rows})
                edges = _file_edges(file_rows, store)
                # Each group is shown the edges about ITS OWN symbols. An entry
                # about a symbol this pass is not naming is context it cannot act
                # on, and on a split file there are hundreds of them.
                chunk_groups = [
                    ([{"symbol_path": path, "source": source}
                      for path, source in shown.items()],
                     [e for e in edges if e["symbol"] in shown])
                    for shown in groups
                ]
                # Commit rationale for this file. The first call warms one
                # repo-wide log; every later file slices the same cached scan,
                # so this is one subprocess per bootstrap, not one per file.
                why = commit_rationales(root_dir, [file]) if root_dir else []
                prepared.append((file, chunk_groups, why))

            def _call(item):
                """One file's proposal, retried once per group, then given up on.

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
                (no key, no CLI), and that fails every call there is, which the
                caller below still treats as fatal.

                A split file's groups run in SEQUENCE, and each one is told the
                titles the ones before it minted. They are slices of a single
                namespace, so a group that cannot see what its predecessor named
                will name it again — and a duplicate is exactly what a single
                whole-file call was buying. Concurrency is unaffected: it is
                across files, and one file's groups were one call's worth of work.

                A group that fails is recorded and the rest still run. Its symbols
                fall to ``_ensure_file_coverage`` along with anything else the
                model left out, so a failure costs that slice's prose and not the
                file's.
                """
                file, chunk_groups, why = item
                titles = list(titles_snapshot)
                ops: list[NodeOp] = []
                for group_index, (chunks, edges) in enumerate(chunk_groups):
                    last: Exception | None = None
                    for _attempt in (1, 2):
                        try:
                            got = propose_file(
                                file, chunks, edges, titles,
                                repo_name=repo_name, config=config, why=why,
                                brief=brief, doc_language=doc_language)
                            break
                        except Exception as exc:  # noqa: BLE001 — per-file tolerance
                            last = exc
                    else:
                        label = (file if len(chunk_groups) == 1
                                 else f"{file} (part {group_index + 1} of "
                                      f"{len(chunk_groups)})")
                        failures.append((label, last))
                        failed_files.add(file)
                        continue
                    ops.extend(got)
                    titles.extend(op.title for op in got
                                  if op.kind is NodeOpKind.ADD_NODE and op.title)
                return ops

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

            for offset, ((file, chunk_groups, _w), ops) in enumerate(zip(prepared, results)):
                idx = wave_start + offset + 1
                file_rows = _in_file_order(by_file[file])
                if idx == 1 or idx == total or idx % step == 0:
                    parts = (f" ({len(chunk_groups)} parts)"
                             if len(chunk_groups) > 1 else "")
                    say(f"  · [{idx}/{total}] {file}{parts}")
                fps = {(r.file, r.symbol_path): r.tokens_hash for r in file_rows}
                ths = {(r.file, r.symbol_path): r.types_hash for r in file_rows}
                ops = _ensure_file_coverage(ops, file_rows, file)
                _apply_ops_with_local_ids(ops, store, fps, source="bootstrap", ths=ths)
                existing_titles.extend(
                    op.title for op in ops if op.kind is NodeOpKind.ADD_NODE and op.title)
                # What this counts is CALLS, so a split file counts as the several
                # it made — the figure is reported as a cost and would understate it.
                calls += len(chunk_groups)
    finally:
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    # Every call failing is not bad luck, it is a broken setup — no key, no
    # `claude` CLI, an unreachable endpoint. Tolerating that would hand back a
    # tree of empty filename nodes and call it a success, so it still raises and
    # the caller's transaction rolls back to nothing.
    #
    # CALLS is the unit, not files: a crowded file is described over several of
    # them, so a lost part of it is a partial failure however few files the repo
    # has, while a missing key loses every call there is.
    if failures and len(failures) == calls:
        raise RuntimeError(
            f"every bootstrap call failed ({calls}/{calls}); last error: {failures[-1][1]}"
        )
    if failures:
        # Files, not calls, because that is the unit the reader has: one file split
        # into four parts losing one of them is one file described incompletely, and
        # the label says which part so the loss is not reported as the whole file's.
        say(f"  ⚠ {len(failed_files)} of {total} files could not be fully described "
            f"(retried once each): {', '.join(f for f, _ in failures[:5])}"
            + (" …" if len(failures) > 5 else ""))
        # WHY each one failed, not just which. Without this the message names a
        # file and stops, so the only way to tell a truncated response from a
        # rate limit from a bad key is to reproduce it by hand — and the two
        # want opposite responses (re-run vs fix the config). One line per
        # distinct cause, because a rate limit hits several files at once and
        # printing it once per file buries the message it is trying to give.
        seen: dict[str, list[str]] = {}
        for file, exc in failures:
            seen.setdefault(f"{type(exc).__name__}: {exc}"[:200], []).append(file)
        for cause, files in seen.items():
            say(f"    {cause}")
            say(f"      ({len(files)}×: {', '.join(files[:4])}"
                + (" …)" if len(files) > 4 else ")"))
        say("    Each is in the tree as a node named after its file, with no "
            "description — fill them in, or re-run `codoc init` to retry.")

    top_level = store.children(None)
    if organize and 1 < len(top_level) <= _ORG_FEATURE_CAP:
        features = [
            {"id": f.id, "title": f.title, "description": f.description}
            for f in top_level
        ]
        coupling = _feature_coupling(store)
        ops = propose_org(features, coupling, repo_name=repo_name, config=config, brief=brief,
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
        skipped=sorted(failed_files),
    )
