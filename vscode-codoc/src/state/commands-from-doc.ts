/**
 * commands-from-doc.ts — derive identity-keyed commands (U3 / KTD3) from a settled
 * ProseMirror doc, by comparing it against the daemon's last-projected doc.
 *
 * Store-authoritative model (U4): the webview is a projection consumer + command
 * emitter. On a settle the host does NOT persist a doc; it diffs the settled doc
 * against the projection it last rendered — KEYED BY IDENTITY (fid, else localId) —
 * and emits the minimal command set:
 *
 *   • a heading with no fid (a freshly authored node) carrying a localId  → `add`
 *   • a fid the user toggled `retired` (the explicit `~ retire` gesture)   → `retire`
 *   • a fid whose title changed                                           → `set_title`
 *   • a fid whose description changed                                     → `set_description`
 *   • a fid whose parent changed                                          → `move`
 *
 * DESTRUCTION IS EXPLICIT, NEVER INFERRED FROM ABSENCE (invariant I1). A fid that was
 * in the baseline but is gone from the settled doc is a NO-OP — not a retire. A heading
 * vanishes for many innocent reasons under char-by-char editing (backspace at the start
 * of a heading merges it up, select-all-delete, a mid-edit transient before the next
 * keystroke), and none of them mean "destroy this feature and detach its bindings". The
 * only retire signal is the `retired` attr the `~ retire` toolbar sets — a node that
 * STAYS in the doc, flagged. This is the robustness fix for the accidental-retire class
 * (see docs/plans/2026-08-01-002-doc-attribution-robustness-plan.md).
 *
 * PLANNED NODES ARE NOT THE HUMAN'S. An agent's proposed ADD is materialized into the
 * document (state/agent-proposals.ts) so it can be read where it will land rather than in
 * a widget beside it — and the moment it is in the document, this module would otherwise
 * see a heading with a localId and no fid and emit `add`, authoring the machine's proposal
 * as the reader's own edit the next time anything settled. The `proposed` attr is the
 * signal: a unit carrying one is skipped entirely, in both passes. It leaves the document
 * the way it arrived, by verdict.
 *
 * This is NOT the lossy text→doc reconcile that dropped localId (deleted with
 * doc-reconcile.ts, R18): the comparison is identity-keyed, so no node is re-minted
 * and no delete resurrects. Pure + side-effect-free so vitest pins the contract; the
 * host (tree-editor.ts) owns the edits.json write.
 */
import {
    PMNode,
    NODE_FEATURE_HEADING,
    NODE_PARAGRAPH,
    FeatureHeadingAttrs,
    inlineRunsToText,
    blocksToDescriptionText,
    paragraphOwner,
} from './pm-doc';
import type { CommandEntry } from './edits-channel';
import type { KnownStore } from './known-store';

/** One feature as it appears in a doc: identity + the fields a command targets. */
export interface FeatureUnit {
    fid: string | null;
    localId: string | null;
    title: string;
    description: string;
    parentId: string | null;
    /** The `retired` attr the `~ retire` gesture toggles. The ONLY retire signal — a
     *  retired node stays in the doc, flagged; absence is never a retire (invariant I1). */
    retired: boolean;
    /** The `realized` attr the `◇ plan` gesture clears. `false` = an authored BUILD
     *  REQUEST, and the only thing that makes an `add` mint a realize directive
     *  (classify.edit_mints_directive: ADD mints iff `realized is False`).
     *
     *  It is on the unit because the toolbar wrote it onto the heading and this walk
     *  then dropped it: the payload carried no such field, the daemon built its
     *  ADD_NODE without one, `realized` defaulted True, and a node the user created
     *  through a button captioned "the agent implements it" became an ordinary
     *  feature whose placeholder title said "(plan)". Required, not optional, so a
     *  future unit source has to decide rather than silently lose it again. */
    realized: boolean;
}

function headingAttrs(node: PMNode): FeatureHeadingAttrs {
    const a = (node.attrs ?? {}) as Partial<FeatureHeadingAttrs>;
    return {
        fid: a.fid ?? null,
        level: typeof a.level === 'number' ? a.level : 0,
        retired: !!a.retired,
        proposed: (a as { proposed?: string | null }).proposed ?? null,
        realized: a.realized !== false,
        localId: (a as { localId?: string | null }).localId ?? null,
    };
}

/** Walk a whole-tree doc into ordered feature units, resolving each heading's parent
 *  from a level stack (the same depth-clamp render uses) and each paragraph's feature by
 *  IDENTITY (invariant I2). A heading's identity is its fid (minted) else its localId (a
 *  brand-new node before the mint echoes back).
 *
 *  Prose attribution is identity-first: a paragraph goes to the feature named by its
 *  `ownerId` when that identity is a live heading; otherwise it falls back to POSITION —
 *  the nearest heading above it (the pre-I2 behaviour, and the correct home for prose that
 *  has no owner yet). This is what makes "type prose under A, then insert heading B above
 *  it" keep the prose with A: the paragraph's ownerId still points at A, so B never steals
 *  it. When no paragraph carries an ownerId (older docs / a projection before the daemon
 *  stamps them) every paragraph falls back to position → identical to the old walk. */
export function featureUnits(doc: PMNode): FeatureUnit[] {
    const blocks = doc.content ?? [];

    // Pass 1 — headings → their identity + parent (level stack) + a bucket for prose.
    interface Head { fid: string | null; localId: string | null; selfId: string | null;
                     title: string; parentId: string | null; retired: boolean;
                     realized: boolean; proposed: string | null; desc: PMNode[]; }
    const heads: Head[] = [];
    const indexBySelfId = new Map<string, number>();  // identity → its heading's index (first wins)
    const stack: Array<{ depth: number; id: string | null }> = [];
    let prevDepth = -1;
    for (const b of blocks) {
        if (b.type !== NODE_FEATURE_HEADING) continue;
        const attrs = headingAttrs(b);
        const depth = Math.max(0, Math.min(attrs.level, prevDepth + 1));
        prevDepth = depth;
        const fid = attrs.fid ?? null;
        const localId = attrs.localId ?? null;
        const selfId = fid ?? localId;
        while (stack.length && stack[stack.length - 1].depth >= depth) stack.pop();
        const parentId = stack.length ? stack[stack.length - 1].id : null;
        stack.push({ depth, id: selfId });
        if (selfId && !indexBySelfId.has(selfId)) indexBySelfId.set(selfId, heads.length);
        heads.push({ fid, localId, selfId, title: inlineRunsToText(b.content).trim(),
                     parentId, retired: attrs.retired, realized: attrs.realized,
                     proposed: attrs.proposed ?? null, desc: [] });
    }

    // Pass 2 — route each paragraph to a feature: its ownerId if that names a live
    // heading, else the nearest heading above (positional fallback). Prose before the
    // first heading (nearestIdx < 0 and no resolvable owner) is dropped, as before.
    let nearestIdx = -1;
    for (const b of blocks) {
        if (b.type === NODE_FEATURE_HEADING) { nearestIdx++; continue; }
        if (b.type !== NODE_PARAGRAPH) continue;
        const owner = paragraphOwner(b);
        const ownedIdx = owner != null ? indexBySelfId.get(owner) : undefined;
        const idx = ownedIdx !== undefined ? ownedIdx : nearestIdx;
        if (idx >= 0) heads[idx].desc.push(b);
    }

    // A planned node is dropped here rather than filtered at each call site: every
    // consumer of `featureUnits` is asking "what has the author got?", and a proposal
    // is not part of that answer anywhere.
    return heads.filter(h => !h.proposed).map(h => ({
        fid: h.fid, localId: h.localId, title: h.title, parentId: h.parentId,
        retired: h.retired, realized: h.realized,
        description: blocksToDescriptionText(h.desc),
    }));
}

/**
 * A command's id is (what kind of edit, to which feature, in which emission).
 *
 * `token` names the emission — one settle, or one drag. Within a single settle a
 * feature gets at most one command per kind, so (kind, feature, token) is unique;
 * across emissions the token differs. The host mints tokens from a per-session
 * counter, which matters more than it looks.
 *
 * The predecessor salted with `Date.now()`. A debounced settle firing in the same
 * millisecond as a Cmd-S commit produced the SAME id for DIFFERENT content, and
 * the daemon's ledger folded the second as a replay — the edit was gone, with
 * nothing anywhere recording that it had ever existed.
 *
 * Content-addressing looks like the tidier answer and is a trap: type "A", settle,
 * type "B", settle, type "A" again, and — with no projection in between to advance
 * the base version — the third command hashes to the first and folds, leaving "B"
 * in the store. Every edit a person makes is a NEW instruction, even when it
 * restores earlier text. Only the replay of a RECORDED command is a replay, and
 * that carries its recorded id, so the ledger still catches it.
 */
function commandId(kind: string, id: string, token: string): string {
    return `c-${kind}-${id}-${token}`;
}

/** The DETERMINISTIC id for an `add` command, derived purely from the node's localId
 *  (no salt). A localId is minted once per authored node and never changes, so a
 *  re-emitted `add` for the same node (a settle that fired again before the fid echoed
 *  back) produces the SAME command id — colliding on the store's applied-command ledger
 *  so the daemon skips it instead of minting a duplicate feature (FIX B). The other
 *  command kinds key off a stable fid and a fresh settle is a legitimately new edit, so
 *  they keep the salted id (a re-edit SHOULD apply); only `add` must be replay-safe by
 *  id because the fid does not yet exist to dedup against. */
function addCommandId(localId: string): string {
    return `c-add-${localId}`;
}

/**
 * Diff the previously-projected feature units against the just-settled ones and emit
 * the minimal identity-keyed command set. `salt` (e.g. Date.now()) disambiguates the
 * generated command ids across successive settles.
 *
 * Parent resolution note: a brand-new node's children reference its localId as their
 * parent; until the fid is minted those `move`/`add` parent_ids carry the localId, and
 * the daemon correlates them on the same pass (apply commands in order). For a settle
 * that only renames/re-describes existing features, no add/move is produced.
 */
export function commandsForSettle(
    prev: FeatureUnit[],
    next: FeatureUnit[],
    token: string,
    known?: KnownStore,
    session = '',
): CommandEntry[] {
    const out: CommandEntry[] = [];
    const beforeByFid = new Map<string, FeatureUnit>();
    for (const u of prev) if (u.fid) beforeByFid.set(u.fid, u);
    const reorder = reorderTargets(prev, next);

    // Order anchors for a brand-new node: its nearest baseline-known siblings
    // under the same parent (the same restriction reorderTargets applies). A
    // heading typed BETWEEN two features must land there, not at the end of its
    // parent — apply's ADD ranks from these exactly as move does.
    const addAnchors = (idx: number): { afterId: string; beforeId: string } => {
        const parent = next[idx].parentId ?? null;
        let afterId = '', beforeId = '';
        for (let j = idx - 1; j >= 0; j--) {
            const s = next[j];
            if ((s.parentId ?? null) === parent && s.fid && beforeByFid.has(s.fid)) { afterId = s.fid; break; }
        }
        for (let j = idx + 1; j < next.length; j++) {
            const s = next[j];
            if ((s.parentId ?? null) === parent && s.fid && beforeByFid.has(s.fid)) { beforeId = s.fid; break; }
        }
        return { afterId, beforeId };
    };

    for (let idx = 0; idx < next.length; idx++) {
        const u = next[idx];
        if (!u.fid) {
            // Newly authored node — an `add` keyed to its localId so the minted fid
            // echoes back. A node with neither fid nor localId is skipped (can't key it);
            // a brand-new node already flagged retired never reached the store, so there
            // is nothing to add and nothing to retire.
            if (!u.localId || u.retired) continue;
            const anchors = addAnchors(idx);
            const payload: NonNullable<CommandEntry['payload']> =
                { title: u.title, description: u.description, parent_id: u.parentId };
            // Sent only for a PLAN node, the way after_id/before_id are sent only when
            // they say something: `realized` is a three-state field end to end (absent
            // ⇒ realized, the daemon's NodeOp default), so an ordinary add's payload
            // stays byte-identical to before the plan flag existed.
            if (!u.realized) payload.realized = false;
            if (anchors.afterId) payload.after_id = anchors.afterId;
            if (anchors.beforeId) payload.before_id = anchors.beforeId;
            out.push({
                id: addCommandId(u.localId),  // deterministic (no salt) → replay collides on the ledger (FIX B)
                kind: 'add',
                local_id: u.localId,
                payload,
            });
            continue;
        }
        const b = beforeByFid.get(u.fid);
        if (!b) {
            // A fid present now but not in the prior projection — carrying the fid on an
            // `add` would be wrong (fid is store-minted); this is a node the projection
            // hasn't shown yet, so leave it (the projection is the baseline).
            continue;
        }
        // Retire is EXPLICIT (invariant I1): the ONLY retire signal is the user toggling
        // the `retired` flag on a node that STAYS in the doc. Absence — a heading that
        // vanished from the settled doc (backspace-merge, select-all delete, a mid-edit
        // transient) — is deliberately a no-op below; it never emits a destructive
        // command. Baseline projections exclude retired features, so `!b.retired` always
        // holds in practice; the guard documents the false→true transition.
        if (u.retired && !b.retired) {
            out.push({ id: commandId('retire', u.fid, token), kind: 'retire', feature_id: u.fid });
            continue;  // a retiring node's title/description/parent edits are moot
        }
        // `base_text` — the value the AUTHOR last knew (see known-store.ts). Two
        // sources, in order: this editor's own already-emitted-but-unechoed writes
        // (`known`, or two settles in a row — ordinary typing faster than the round
        // trip — would each cite text the store had moved past and read as a conflict
        // with itself), else the CITED baseline `b`, which is what the author was
        // looking at. Never a newer projection: the author may never have adopted it,
        // and claiming they saw a stranger's write is what makes the daemon apply this
        // text verbatim over it.
        const known1 = known?.get(u.fid);
        if (b.title !== u.title) {
            out.push({ id: commandId('set_title', u.fid, token), kind: 'set_title',
                       feature_id: u.fid, base_text: known1?.title ?? b.title, session,
                       payload: { title: u.title } });
        }
        if (b.description !== u.description) {
            out.push({ id: commandId('set_description', u.fid, token), kind: 'set_description',
                       feature_id: u.fid, base_text: known1?.description ?? b.description, session,
                       payload: { description: u.description } });
        }
        // One move per node, whether it changed parent, changed position among its
        // siblings, or both — a reparent that also lands at a chosen spot is still
        // one gesture, and emitting two commands for it would make the second race
        // the first's result.
        const moved = reorder.get(u.fid);
        if ((b.parentId ?? null) !== (u.parentId ?? null) || moved) {
            out.push(moveCommand(u.fid, u.parentId ?? null, token,
                                 moved?.afterId ?? '', moved?.beforeId ?? ''));
        }
    }

    // NO absence loop: a fid gone from the settled doc is NOT retired (I1). Destruction
    // flows only from the explicit `retired` flag handled above.
    return out;
}

/** A single explicit move command (the tree-pane / drag handlers), keyed by fid.
 *  `token` names this drag: dragging a feature back where it was is a fresh
 *  instruction, not a replay of the drag that first put it there.
 *
 *  `afterId`/`beforeId` name the siblings it was dropped between. Omitted means
 *  no opinion about order, which appends — the behaviour every caller had before
 *  ordering existed. */
export function moveCommand(
    fid: string, newParentId: string | null, token: string,
    afterId = '', beforeId = '',
): CommandEntry {
    const payload: NonNullable<CommandEntry['payload']> = { parent_id: newParentId };
    if (afterId) payload.after_id = afterId;
    if (beforeId) payload.before_id = beforeId;
    return { id: commandId('move', fid, token), kind: 'move', feature_id: fid, payload };
}

/** Sentinel parent key for root-level features (`parentId === null`). A plain
 *  string that cannot collide with a feature id, which is always `f-…`. */
const ROOT_KEY = 'root:';

/** Sibling sequences per parent, restricted to `keep`.
 *
 *  Restriction is what makes a concurrent change cost nothing: limited to the ids
 *  both sides know, the agent inserting a sibling between A and B — or retiring
 *  one — leaves every surviving neighbour relationship intact, so no move is
 *  emitted for prose nobody dragged. It also guarantees every anchor is a fid the
 *  daemon can resolve; a heading typed a moment ago has only a localId. */
function siblingSequences(units: FeatureUnit[], keep: Set<string>): Map<string, string[]> {
    const out = new Map<string, string[]>();
    for (const u of units) {
        const self = u.fid ?? u.localId;
        if (!self || !keep.has(self)) continue;
        const parent = u.parentId ?? ROOT_KEY;
        const seq = out.get(parent) ?? [];
        seq.push(self);
        out.set(parent, seq);
    }
    return out;
}

/** Indices of a longest strictly-increasing subsequence of `xs` (patience sort). */
function longestIncreasing(xs: number[]): Set<number> {
    const tails: number[] = [];                       // index in xs of each length's smallest tail
    const prev: number[] = new Array(xs.length).fill(-1);
    for (let i = 0; i < xs.length; i++) {
        let lo = 0, hi = tails.length;
        while (lo < hi) {
            const mid = (lo + hi) >> 1;
            if (xs[tails[mid]] < xs[i]) lo = mid + 1; else hi = mid;
        }
        prev[i] = lo > 0 ? tails[lo - 1] : -1;
        tails[lo] = i;
    }
    const keep = new Set<number>();
    for (let i = tails.length ? tails[tails.length - 1] : -1; i >= 0; i = prev[i]) keep.add(i);
    return keep;
}

/** Where each REORDERED feature was dropped, keyed by fid, in document order.
 *
 * The nodes that MOVED are everything outside a longest increasing subsequence of
 * the settled order — the standard minimum. Dragging one node emits one command,
 * which matters beyond tidiness: every write stamps `feature_writers`, so a
 * reorder that touched all N siblings would mark them all as freshly written, and
 * the author's next edit to any of them would read as a conflict with a stranger.
 *
 * Anchoring is what makes the minimum safe, and it is asymmetric on purpose:
 *
 *   • `afterId` is the immediately preceding sibling, whoever it is. Commands are
 *     emitted in document order, so a preceding node is either untouched or was
 *     emitted earlier — either way it is already in its final place by the time
 *     this one applies.
 *   • `beforeId` is the nearest following sibling THAT IS NOT MOVING. A following
 *     mover has not been placed yet, so naming it would anchor against a position
 *     that is about to change.
 *
 * Giving both bounds wherever they exist is what makes the result independent of
 * WHICH longest subsequence was chosen. With only one bound, a run of adjacent
 * movers can satisfy every anchor it was given and still land in the wrong order.
 */
export function reorderTargets(
    base: FeatureUnit[], next: FeatureUnit[],
): Map<string, { afterId: string; beforeId: string }> {
    const idsOf = (us: FeatureUnit[]) =>
        new Set(us.map(u => u.fid ?? u.localId).filter(Boolean) as string[]);
    const inNext = idsOf(next);
    const common = new Set([...idsOf(base)].filter(x => inNext.has(x)));

    const baseSeqs = siblingSequences(base, common);
    const nextSeqs = siblingSequences(next, common);
    const spots = new Map<string, { afterId: string; beforeId: string }>();

    for (const [parent, seq] of nextSeqs) {
        const wasAt = new Map((baseSeqs.get(parent) ?? []).map((id, i) => [id, i] as const));
        // A node that arrived from another parent has no baseline index HERE. It is
        // moving by definition, and must never be treated as a fixed point that
        // other nodes anchor against — hence the sentinel that keeps it out of the
        // increasing subsequence.
        const positions = seq.map(id => wasAt.get(id) ?? Number.MAX_SAFE_INTEGER);
        const fixed = longestIncreasing(positions);
        const isFixed = (i: number) => fixed.has(i) && positions[i] !== Number.MAX_SAFE_INTEGER;

        for (let i = 0; i < seq.length; i++) {
            if (isFixed(i)) continue;
            let before = '';
            for (let j = i + 1; j < seq.length; j++) {
                if (isFixed(j)) { before = seq[j]; break; }
            }
            spots.set(seq[i], { afterId: i > 0 ? seq[i - 1] : '', beforeId: before });
        }
    }

    // Re-key in document order, so the caller emits each anchor before the node
    // that references it.
    const ordered = new Map<string, { afterId: string; beforeId: string }>();
    for (const u of next) {
        if (u.fid && spots.has(u.fid)) ordered.set(u.fid, spots.get(u.fid)!);
    }
    return ordered;
}

/** One entry in the host's short projection-baseline history (#4). */
export interface Baseline { id: number; units: FeatureUnit[]; }

/**
 * #4 — compute the settle command set against the baseline the settle CITES, not against
 * whatever projection last landed on the host. A settle carries the `baselineId` of the
 * projection the editor was showing when the user typed; we diff against THAT baseline's
 * units. Diffing against a newer projection would misread a daemon-side change (a feature
 * the daemon added/renamed after the editor's baseline) as a user edit — e.g. a
 * `set_title` that reverts the daemon's rename, because the user's stale baseline still
 * carries the old title.
 *
 * Since retire is now explicit (the `retired` flag, never absence — invariant I1), the
 * old phantom-retire class is gone and there is nothing destructive to suppress on an
 * uncited/evicted baseline: the fallback can only ever produce (possibly redundant)
 * content commands, which the daemon's ledger + idempotent apply absorb. An explicit
 * retire the user actually toggled is honoured even against the fallback.
 *
 * The cited baseline is also the `base_text` of last resort (see known-store.ts), which
 * is why the citation has to name the baseline the settled CONTENT was computed from —
 * not whatever the editor is showing now. The editor stamps it at the end of its adopt
 * (`whole-doc-editor.setDoc`), so a settle flushed BY an arriving projection still cites
 * the baseline it was typed against. Reading the newest payload's id at post time was
 * finding #2: the flushed pre-adoption text diffed against post-adoption units, so every
 * feature the daemon had just changed read as a user edit that reverted it.
 *
 */
export function settleCommands(
    history: readonly Baseline[],
    baselineId: number | undefined,
    fallbackUnits: FeatureUnit[],
    nextUnits: FeatureUnit[],
    token: string,
    known?: KnownStore,
    session = '',
): CommandEntry[] {
    const cited = baselineId != null ? history.find(b => b.id === baselineId) : undefined;
    // When the citation cannot be resolved, fall back to the OLDEST baseline still
    // retained, not the newest projection. The asymmetry is the point: an under-claimed
    // base makes the daemon cautious (it sees divergence and merges, or parks a proposal),
    // while an over-claimed one makes it blind (base == current reads as a clean
    // continuation and applies verbatim over somebody else's write). Both are wrong; only
    // one loses text. The extra commands a stale baseline produces are text the author
    // already has, so they resolve to no-ops.
    const prevUnits = cited ? cited.units
        : history.length ? history[0].units
        : fallbackUnits;
    return commandsForSettle(prevUnits, nextUnits, token, known, session);
}
