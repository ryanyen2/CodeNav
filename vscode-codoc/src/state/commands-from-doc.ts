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

/** Mint an idempotency-key'd command id (KTD8). Stable per (kind, identity) within a
 *  settle so a re-emit of the same logical edit supersedes rather than stacks. */
function commandId(kind: string, id: string, salt: number): string {
    return `c-${kind}-${id}-${salt}`;
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
export function commandsForSettle(prev: FeatureUnit[], next: FeatureUnit[], salt: number): CommandEntry[] {
    const out: CommandEntry[] = [];
    const beforeByFid = new Map<string, FeatureUnit>();
    for (const u of prev) if (u.fid) beforeByFid.set(u.fid, u);

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
            out.push({ id: commandId('retire', u.fid, salt), kind: 'retire', feature_id: u.fid });
            continue;  // a retiring node's title/description/parent edits are moot
        }
        if (b.title !== u.title) {
            out.push({ id: commandId('set_title', u.fid, salt), kind: 'set_title',
                       feature_id: u.fid, payload: { title: u.title } });
        }
        if (b.description !== u.description) {
            out.push({ id: commandId('set_description', u.fid, salt), kind: 'set_description',
                       feature_id: u.fid, payload: { description: u.description } });
        }
        if ((b.parentId ?? null) !== (u.parentId ?? null)) {
            out.push({ id: commandId('move', u.fid, salt), kind: 'move',
                       feature_id: u.fid, payload: { parent_id: u.parentId } });
        }
    }

    // NO absence loop: a fid gone from the settled doc is NOT retired (I1). Destruction
    // flows only from the explicit `retired` flag handled above.
    return out;
}

/** A single explicit move command (the tree-pane drag handler), keyed by fid. */
export function moveCommand(fid: string, newParentId: string | null, salt: number): CommandEntry {
    return { id: commandId('move', fid, salt), kind: 'move', feature_id: fid,
             payload: { parent_id: newParentId } };
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
    salt: number,
): CommandEntry[] {
    const cited = baselineId != null ? history.find(b => b.id === baselineId) : undefined;
    const prevUnits = cited ? cited.units : fallbackUnits;
    return commandsForSettle(prevUnits, nextUnits, salt);
}
