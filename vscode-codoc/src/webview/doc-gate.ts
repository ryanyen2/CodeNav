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
import { NODE_FEATURE_HEADING, type PMNode } from '../state/pm-doc';

/** The per-feature version carried on a `featureHeading`'s attrs by the store
 *  projection (U2: `feature.updated_at.to_str()`). Empty/absent for a not-yet-minted
 *  authored heading. */
export function headingVersion(heading: PMNode): string {
    const v = (heading.attrs as { version?: unknown } | undefined)?.version;
    return typeof v === 'string' ? v : '';
}

export function headingFid(heading: PMNode): string | null {
    const f = (heading.attrs as { fid?: unknown } | undefined)?.fid;
    return typeof f === 'string' && f ? f : null;
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

/**
 * W5 composer-drop fix — whether an incoming projection must be DEFERRED rather
 * than applied right now, because a comment composer or selection bubble is open
 * over the doc (applying would remap/destroy the captured range under it). The
 * caller stashes the latest deferred projection and re-applies it on close.
 * Pure so the defer/keep-latest contract is testable without the editor.
 */
export function shouldDeferProjection(composerOpen: boolean, bubbleOpen: boolean): boolean {
    return composerOpen || bubbleOpen;
}

/** A feature's slice of the flat doc: its heading + the body blocks that follow it
 *  (until the next heading). */
interface FeatureSlice {
    fid: string | null;
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
            cur = { fid: headingFid(block), version: headingVersion(block), blocks: [block] };
            out.push(cur);
        } else if (cur) {
            cur.blocks.push(block);
        } else {
            cur = { fid: null, version: '', blocks: [block] };
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
    const localByFid = new Map<string, FeatureSlice>();
    for (const s of localSlices) if (s.fid) localByFid.set(s.fid, s);

    const content: PMNode[] = [];
    const adopted = new Map<string, string>();
    const placedFids = new Set<string>();

    for (const inc of slices(incoming)) {
        const fid = inc.fid;
        if (!fid) {
            // A projected heading with no fid is unusual (the store always has one);
            // take it as-is — there's no local identity to compare against.
            content.push(...inc.blocks);
            continue;
        }
        placedFids.add(fid);
        const localSlice = localByFid.get(fid);
        const hasPending = pendingFids.has(fid);
        const localVersion = localVersions.get(fid) ?? '';
        // No local copy of this feature → nothing to clobber; adopt.
        if (!localSlice || shouldAdopt(inc.version, localVersion, hasPending)) {
            content.push(...inc.blocks);
            adopted.set(fid, inc.version);
        } else {
            // Keep the optimistic local edit at the projection's structural position.
            content.push(...localSlice.blocks);
        }
    }

    // Local features the projection doesn't carry yet: keep those with a pending edit
    // (optimistic add / rename racing the daemon) and any null-fid authored heading
    // (mid-mint — patchMintedIds fills its fid). A clean local-only feature with no
    // pending edit and a fid the projection dropped is a deletion → let it go.
    for (const s of localSlices) {
        if (s.fid && placedFids.has(s.fid)) continue;
        if (s.fid === null || pendingFids.has(s.fid ?? '')) content.push(...s.blocks);
    }

    return { doc: { type: local.type, content }, adopted };
}
