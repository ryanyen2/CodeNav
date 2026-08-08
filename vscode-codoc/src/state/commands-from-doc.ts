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
}

function headingAttrs(node: PMNode): FeatureHeadingAttrs {
    const a = (node.attrs ?? {}) as Partial<FeatureHeadingAttrs>;
    return {
        fid: a.fid ?? null,
        level: typeof a.level === 'number' ? a.level : 0,
        retired: !!a.retired,
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
                     title: string; parentId: string | null; retired: boolean; desc: PMNode[]; }
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
                     parentId, retired: attrs.retired, desc: [] });
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

    return heads.map(h => ({
        fid: h.fid, localId: h.localId, title: h.title, parentId: h.parentId,
        retired: h.retired, description: blocksToDescriptionText(h.desc),
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
    known?: ReadonlyMap<string, FeatureUnit>,
    session = '',
): CommandEntry[] {
    const out: CommandEntry[] = [];
    const beforeByFid = new Map<string, FeatureUnit>();
    for (const u of prev) if (u.fid) beforeByFid.set(u.fid, u);
    const reorder = reorderTargets(prev, next);

    for (const u of next) {
        if (!u.fid) {
            // Newly authored node — an `add` keyed to its localId so the minted fid
            // echoes back. A node with neither fid nor localId is skipped (can't key it);
            // a brand-new node already flagged retired never reached the store, so there
            // is nothing to add and nothing to retire.
            if (!u.localId || u.retired) continue;
            out.push({
                id: addCommandId(u.localId),  // deterministic (no salt) → replay collides on the ledger (FIX B)
                kind: 'add',
                local_id: u.localId,
                payload: { title: u.title, description: u.description, parent_id: u.parentId },
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
        // What we believe the STORE holds right now — which is not the same as the
        // baseline this diff is computed against. The diff baseline is what the user
        // was looking at; `known` also folds in the commands this editor has already
        // emitted but whose projection has not returned yet. Without that, two settles
        // in a row (perfectly ordinary typing) would look like a conflict with itself.
        const stored = known?.get(u.fid) ?? b;
        if (b.title !== u.title) {
            out.push({ id: commandId('set_title', u.fid, token), kind: 'set_title',
                       feature_id: u.fid, base_text: stored.title, session,
                       payload: { title: u.title } });
        }
        if (b.description !== u.description) {
            out.push({ id: commandId('set_description', u.fid, token), kind: 'set_description',
                       feature_id: u.fid, base_text: stored.description, session,
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
    const payload: Record<string, unknown> = { parent_id: newParentId };
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
 */
export function settleCommands(
    history: readonly Baseline[],
    baselineId: number | undefined,
    fallbackUnits: FeatureUnit[],
    nextUnits: FeatureUnit[],
    token: string,
    known?: ReadonlyMap<string, FeatureUnit>,
    session = '',
): CommandEntry[] {
    const cited = baselineId != null ? history.find(b => b.id === baselineId) : undefined;
    const prevUnits = cited ? cited.units : fallbackUnits;
    return commandsForSettle(prevUnits, nextUnits, token, known, session);
}
