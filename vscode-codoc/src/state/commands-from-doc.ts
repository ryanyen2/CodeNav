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
 *   • a fid present before but absent now                                 → `retire`
 *   • a fid whose title changed                                           → `set_title`
 *   • a fid whose description changed                                     → `set_description`
 *   • a fid whose parent changed                                          → `move`
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
} from './pm-doc';
import type { CommandEntry } from './edits-channel';

/** One feature as it appears in a doc: identity + the fields a command targets. */
export interface FeatureUnit {
    fid: string | null;
    localId: string | null;
    title: string;
    description: string;
    parentId: string | null;
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
 *  from a level stack (the same depth-clamp render uses). A heading's identity is its
 *  fid (minted) else its localId (a brand-new node before the mint echoes back). */
export function featureUnits(doc: PMNode): FeatureUnit[] {
    const blocks = doc.content ?? [];
    const units: FeatureUnit[] = [];
    // Stack of (clampedDepth → identity) so a child resolves its parent's fid|localId.
    const stack: Array<{ depth: number; id: string | null }> = [];
    let prevDepth = -1;
    let i = 0;
    while (i < blocks.length) {
        const b = blocks[i];
        if (b.type !== NODE_FEATURE_HEADING) { i++; continue; }
        const attrs = headingAttrs(b);
        const depth = Math.max(0, Math.min(attrs.level, prevDepth + 1));
        prevDepth = depth;
        const fid = attrs.fid ?? null;
        const localId = attrs.localId ?? null;
        const selfId = fid ?? localId;
        // Pop the stack to this depth; the top is now the parent.
        while (stack.length && stack[stack.length - 1].depth >= depth) stack.pop();
        const parentId = stack.length ? stack[stack.length - 1].id : null;
        stack.push({ depth, id: selfId });

        const title = inlineRunsToText(b.content).trim();
        const descBlocks: PMNode[] = [];
        i++;
        while (i < blocks.length && blocks[i].type !== NODE_FEATURE_HEADING) {
            if (blocks[i].type === NODE_PARAGRAPH) descBlocks.push(blocks[i]);
            i++;
        }
        units.push({ fid, localId, title, description: blocksToDescriptionText(descBlocks), parentId });
    }
    return units;
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
    const nextFids = new Set<string>();

    for (const u of next) {
        if (!u.fid) {
            // Newly authored node — an `add` keyed to its localId so the minted fid
            // echoes back. A node with neither fid nor localId is skipped (can't key it).
            if (!u.localId) continue;
            out.push({
                id: addCommandId(u.localId),  // deterministic (no salt) → replay collides on the ledger (FIX B)
                kind: 'add',
                local_id: u.localId,
                payload: { title: u.title, description: u.description, parent_id: u.parentId },
            });
            continue;
        }
        nextFids.add(u.fid);
        const b = beforeByFid.get(u.fid);
        if (!b) {
            // A fid present now but not in the prior projection — treat as an `add`
            // carrying the fid would be wrong (fid is store-minted); this is a node the
            // projection hasn't shown yet, so leave it (the projection is the baseline).
            continue;
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

    // A fid that was projected but is gone from the settled doc → an explicit retire
    // (R7: a delete is a command; no path re-introduces it from text).
    for (const [fid] of beforeByFid) {
        if (!nextFids.has(fid)) {
            out.push({ id: commandId('retire', fid, salt), kind: 'retire', feature_id: fid });
        }
    }
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
 * units. Diffing against a newer projection is the phantom-retire bug: a feature the
 * daemon added after the editor's baseline would appear in `prev` but not `next` and be
 * misread as a user deletion → a `retire` command.
 *
 * When the settle cites no baseline, or one that has been evicted from the bounded
 * history, we cannot trust a "feature disappeared" signal (it may be a daemon-added
 * feature the stale fallback never had), so RETIRE — the one destructive, irreversible
 * command — is suppressed; every other command is safe against the fallback.
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
    const commands = commandsForSettle(prevUnits, nextUnits, salt);
    return cited ? commands : commands.filter(c => c.kind !== 'retire');
}
