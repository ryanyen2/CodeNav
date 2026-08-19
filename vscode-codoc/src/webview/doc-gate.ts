/**
 * doc-gate.ts — the per-feature HLC version gate (U5 / R14 / KTD4).
 *
 * Replaces the removed whole-doc `docAhead`/`rev` gate. When a returning or
 * daemon-pushed projection (`tree.doc.json`, rendered by `build_doc_from_store`)
 * arrives, we must NOT blanket-clobber the live editor: the author may be mid-edit on
 * feature A while the daemon advanced an UNRELATED feature B. A single whole-tree
 * version (`payload_version()`) cannot distinguish those — it would revert A.
 *
 * So we gate PER FEATURE, keyed by `fid`, using the per-feature `version` HLC the
 * projection carries on each `featureHeading`'s attrs (U2). HLC `to_str()` is
 * lexicographically sortable (`codoc/model/hlc.py`), so we compare versions as plain
 * strings.
 *
 * Decision (per feature):
 *   - no pending local edit on this fid → ADOPT the projection (always; this also
 *     covers a post-reload daemon-restart batch, whose in-memory pending set is empty);
 *   - a pending local edit AND the projected version is strictly NEWER than the
 *     version we last adopted for it → ADOPT (the daemon's state genuinely moved past
 *     the local edit);
 *   - a pending local edit AND the projected version is NOT newer → KEEP LOCAL (no
 *     clobber of the optimistic edit).
 *
 * The whole-tree `payload_version()` remains the coarse staleness signal; it is not
 * consulted here. There is no merge — a genuine concurrent same-feature edit surfaces
 * through the agent↔human diff surface (R13), not here.
 *
 * Pure + DOM-free → unit-testable in isolation (the editor wiring lives in
 * whole-doc-editor.ts's `setDoc`).
 */
import { NODE_FEATURE_HEADING, headingVersion, type PMNode } from '../state/pm-doc';

export { headingVersion };

export function headingFid(heading: PMNode): string | null {
    const f = (heading.attrs as { fid?: unknown } | undefined)?.fid;
    return typeof f === 'string' && f ? f : null;
}

/** The proposal that put this heading in the document, if it is a materialized plan
 *  node (`state/plan-materialize.ts`) rather than a real feature. */
export function headingProposed(heading: PMNode): string | null {
    const p = (heading.attrs as { proposed?: unknown } | undefined)?.proposed;
    return typeof p === 'string' && p ? p : null;
}

/**
 * The identity the gate matches a slice by.
 *
 * `fid` for a real feature; the PROPOSAL id for a materialized plan node, which has no
 * fid and must not appear to have one. Without the second rung a plan node matched
 * nothing on either side, so the projection's copy was placed in position AND the local
 * copy appended at the end — a duplicate per payload, compounding, because the next
 * payload's `local` already held both.
 */
export function sliceKey(heading: PMNode): string | null {
    return headingFid(heading) ?? headingProposed(heading);
}

/**
 * Per-feature adopt decision (KTD4). `incomingVersion` is the projected feature's HLC;
 * `localVersion` is the version we last adopted for it (or '' if never); `hasPendingEdit`
 * is whether the user has an un-acked local edit to this feature since that adopt.
 *
 *   - no pending edit → always adopt;
 *   - pending edit → adopt only when the projection is strictly newer (string >).
 */
export function shouldAdopt(incomingVersion: string, localVersion: string, hasPendingEdit: boolean): boolean {
    if (!hasPendingEdit) return true;
    return incomingVersion > localVersion;
}

/** Conditions under which the document must not be replaced out from under the user. */
export interface DeferConditions {
    /** A comment composer is open over the doc (its captured range would be remapped). */
    composerOpen: boolean;
    /** The selection bubble is open (same reason). */
    bubbleOpen: boolean;
    /**
     * An IME composition is in flight (`EditorView.composing`). Replacing the document
     * mid-composition force-flushes the DOM and aborts the composition, so the
     * characters a Japanese, Chinese or Korean writer is part-way through either
     * vanish or double. Everyone typing in those scripts hits this on every word,
     * which is why it belongs beside the composer case and not in a special case
     * somewhere downstream.
     */
    imeComposing: boolean;
    /**
     * The author is mid-thought: the editor is focused AND there is unsettled text
     * or a keystroke within the last ~1.5 s. Adopting a projection here replaces
     * the document under the caret — the absolute-position restore lands the caret
     * on the next node's title, and worse, the adopt path force-settles whatever
     * half-typed fragment exists, which round-trips into ANOTHER projection: the
     * observed feedback loop that shipped a title as "D" → "Dra" → "Draf" and
     * yanked the caret between every word. Deferral is bounded — the flush retries
     * once typing stops, and blur/commit flush immediately.
     */
    activelyEditing?: boolean;
}

/**
 * Whether an incoming projection must be DEFERRED rather than applied right now.
 * The caller stashes the latest deferred projection and re-applies it the moment
 * the condition clears — deferring never drops (W5).
 *
 * Pure so the defer/keep-latest contract is testable without the editor.
 */
export function shouldDeferProjection(conditions: DeferConditions): boolean {
    return conditions.composerOpen || conditions.bubbleOpen || conditions.imeComposing
        || !!conditions.activelyEditing;
}

/** A feature's slice of the flat doc: its heading + the body blocks that follow it
 *  (until the next heading). */
interface FeatureSlice {
    /** Match identity: `fid`, else the proposal id. Null only for a not-yet-minted
     *  authored heading (which has a localId) or a synthetic lead slice. */
    key: string | null;
    fid: string | null;
    /** Set ⇒ this slice is an agent's proposal, not a feature. */
    proposed: string | null;
    version: string;
    blocks: PMNode[];   // heading first, then its body blocks
}

/** Split a flat ProseMirror doc into ordered per-feature slices. Leading non-heading
 *  blocks (there normally are none) attach to a synthetic null-fid lead slice so they
 *  are never dropped. */
function slices(doc: PMNode): FeatureSlice[] {
    const out: FeatureSlice[] = [];
    let cur: FeatureSlice | null = null;
    for (const block of doc.content ?? []) {
        if (block.type === NODE_FEATURE_HEADING) {
            cur = {
                key: sliceKey(block), fid: headingFid(block), proposed: headingProposed(block),
                version: headingVersion(block), blocks: [block],
            };
            out.push(cur);
        } else if (cur) {
            cur.blocks.push(block);
        } else {
            cur = { key: null, fid: null, proposed: null, version: '', blocks: [block] };
            out.push(cur);
        }
    }
    return out;
}

export interface GateInput {
    /** The incoming projection doc (daemon-rendered). */
    incoming: PMNode;
    /** The live editor's current doc. */
    local: PMNode;
    /** Per-fid version last adopted from a projection. */
    localVersions: Map<string, string>;
    /** Fids with an un-acked local edit since their last adopt. */
    pendingFids: Set<string>;
}

export interface GateResult {
    /** The merged doc to load into the editor: adopted features take the projected
     *  slice, kept features retain the local slice. Structure (order) follows the
     *  incoming projection (the daemon is authoritative for structure); a kept feature
     *  contributes its LOCAL blocks at the projected feature's position. A local-only
     *  feature with a pending edit (e.g. mid-mint, or not yet in the projection) is
     *  appended so it is never dropped. */
    doc: PMNode;
    /** Fids whose projected version was adopted, with the version adopted — the caller
     *  folds these into `localVersions` and clears them from `pendingFids`. */
    adopted: Map<string, string>;
}

/**
 * Gate an incoming projection against the live doc, per feature (KTD4). Returns the
 * merged doc plus the set of fids+versions adopted (so the caller can advance its
 * tracking and clear those pending edits).
 *
 * Order follows the incoming projection. For each projected feature: adopt its slice
 * (per `shouldAdopt`) or substitute the local slice for the same fid (keep-local). Any
 * local feature NOT present in the projection that has a pending edit is appended so an
 * optimistic add/edit is never lost before the daemon has rendered it.
 */
export function gateProjection(input: GateInput): GateResult {
    const { incoming, local, localVersions, pendingFids } = input;
    const localSlices = slices(local);
    const localByKey = new Map<string, FeatureSlice>();
    for (const s of localSlices) if (s.key) localByKey.set(s.key, s);

    const content: PMNode[] = [];
    const adopted = new Map<string, string>();
    const placedKeys = new Set<string>();

    for (const inc of slices(incoming)) {
        const key = inc.key;
        if (!key) {
            // A projected heading with neither a fid nor a proposal id is unusual (the
            // store always has one, and a materialized plan node always carries the
            // other); take it as-is — there's no local identity to compare against.
            content.push(...inc.blocks);
            continue;
        }
        placedKeys.add(key);
        const localSlice = localByKey.get(key);
        const hasPending = pendingFids.has(key);
        const localVersion = localVersions.get(key) ?? '';
        // No local copy of this feature → nothing to clobber; adopt.
        //
        // A PROPOSAL carries no HLC (it is not in the store), so both versions are ''
        // and `shouldAdopt` reduces to "adopt unless there is a pending local edit" —
        // which is the rule that matters for one: a participant may reshape a proposal
        // before accepting it, and re-materializing over their words would erase them.
        if (!localSlice || shouldAdopt(inc.version, localVersion, hasPending)) {
            content.push(...inc.blocks);
            if (inc.fid) adopted.set(inc.fid, inc.version);
        } else {
            // Keep the optimistic local edit at the projection's structural position.
            content.push(...localSlice.blocks);
        }
    }

    // Local features the projection doesn't carry yet: keep those with a pending edit
    // (optimistic add / rename racing the daemon) and any not-yet-minted authored
    // heading (patchMintedIds fills its fid). A clean local-only feature with no
    // pending edit and a fid the projection dropped is a deletion → let it go.
    //
    // A PROPOSAL the projection has dropped is always let go, pending edit or not: the
    // reader accepted it (it comes back as a real node), or rejected it, or the daemon
    // withdrew it. Keeping it would leave a ghost of a decision already made — and
    // keeping one the reader had typed into would leave their words attached to a node
    // that no longer has any way to be accepted.
    for (const s of localSlices) {
        if (s.key && placedKeys.has(s.key)) continue;
        if (s.proposed) continue;
        if (s.fid === null || pendingFids.has(s.fid ?? '')) content.push(...s.blocks);
    }

    return { doc: { type: local.type, content }, adopted };
}
