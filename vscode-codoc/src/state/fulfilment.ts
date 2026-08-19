/**
 * fulfilment.ts — noticing that a claim reached the code, so the marker can say so.
 *
 * Every other state in the settlement model is READ from what is on screen: a claim
 * exists because two texts differ, and it stops existing when they stop differing. That
 * is what makes the marker impossible to desynchronise from the prose.
 *
 * Fulfilment is the one fact that cannot work that way, and the reason is structural
 * rather than incidental: it is precisely the moment the difference DISAPPEARS. Your
 * edit is realized, the code now says what you wrote, the claim clears — and if the
 * marker were still a pure function of the claims, the only thing the surface would
 * ever show you is your edit silently ceasing to be marked. The one outcome you were
 * waiting for is the one it cannot report.
 *
 * So this watches the TRANSITION and remembers it briefly. Two signals, both already on
 * the wire, neither invented for this:
 *
 *   • a feature LEAVES the hold set — its queued directive landed, so the edit that
 *     put it there has been built.
 *   • a plan layer STOPS BEING OFFERED while its node is still in the tree — accepted
 *     and applied, rather than rejected (a rejected node leaves with it).
 *
 * And it records whether what landed matches what was asked for, because "built" and
 * "built as written" are different answers and the second one is the one nobody
 * currently gets. That comparison is the code channel's own claims at the moment of
 * transition: a fulfilment arriving alongside a `code` claim on the same feature landed
 * DIFFERENTLY, in the direction those claims report.
 *
 * Pure — `now` is a parameter. See node-status.ts for how long the marker then lives.
 */
import type { Claim } from './settlement';
import type { DiffMark, Fulfilment } from './node-status';

/** What the tracker needs to know about one moment. */
export interface Snapshot {
    /** Features whose edits are with the agent (the daemon's hold set). */
    held: ReadonlySet<string>;
    /** Plan layers currently on offer, by the feature key they are filed under. */
    planLayers: ReadonlyMap<string, string>;
    /** Feature keys present in the document. */
    present: ReadonlySet<string>;
}

export function emptySnapshot(): Snapshot {
    return { held: new Set(), planLayers: new Map(), present: new Set() };
}

/** The direction the build diverged, from the code claims standing on that feature at
 *  the moment it landed. `none` ⇒ it landed as written. */
export function divergenceOf(claims: readonly Claim[]): DiffMark {
    let add = false, del = false;
    for (const c of claims) {
        if (c.channel !== 'code') continue;
        if (c.edit === 'add') add = true; else del = true;
    }
    return add && del ? 'both' : add ? 'add' : del ? 'del' : 'none';
}

/**
 * The fulfilments this payload reveals, keyed by feature.
 *
 * `claimsByFeature` is consulted only for the features that just transitioned, so this
 * costs nothing on a payload where nothing landed — which is almost all of them.
 */
export function fulfilments(
    prev: Snapshot, next: Snapshot,
    claimsByFeature: (key: string) => readonly Claim[],
    now: number,
): Map<string, Fulfilment[]> {
    const out = new Map<string, Fulfilment[]>();
    const push = (key: string, f: Fulfilment): void => {
        const list = out.get(key);
        if (list) list.push(f); else out.set(key, [f]);
    };

    for (const key of prev.held) {
        if (next.held.has(key)) continue;
        // A feature that left the document did not get built; it was retired, or the
        // reader deleted the node. Nothing to acknowledge.
        if (!next.present.has(key)) continue;
        push(key, { channel: 'human', at: now, diverged: divergenceOf(claimsByFeature(key)) });
    }

    for (const [key, layer] of prev.planLayers) {
        if (next.planLayers.get(key) === layer) continue;
        // Gone WITH its node ⇒ rejected (or the node was retired). A rejection is not a
        // fulfilment and must not fill the ring; the reader declined it and the surface
        // saying "built" would be a lie about their own decision.
        //
        // KNOWN GAP, deliberately left as a silence rather than a guess: this test cannot
        // see an accepted ADD. An add proposal is filed under its own EVENT id (it has no
        // feature yet), and once it resolves that id is gone from the document either way
        // — accepted, it comes back as a real node under a freshly minted fid; rejected,
        // it is simply gone. So an accepted add produces no fulfilment marker instead of a
        // wrong one. Telling the two apart needs the ledger's `caused_by`, which the
        // sidecar records but the payload does not yet carry to the webview; the fix is to
        // ship the `changes` slice and match `add_node` events citing this layer.
        if (!next.present.has(key)) continue;
        // Both slots can land together — a plan that was built, carrying an edit of
        // yours built with it — and they are separate slots in the marker, so this
        // appends rather than replacing whatever the hold set already recorded.
        push(key, { channel: 'plan', at: now, diverged: divergenceOf(claimsByFeature(key)) });
    }

    return out;
}

/**
 * Merge new fulfilments into the remembered set and drop the expired ones.
 *
 * Kept as one step because the two must happen together: a set that only grows is a
 * changelog in the margin, and one that only prunes forgets the thing it exists to say.
 */
export function mergeFulfilments(
    known: ReadonlyMap<string, readonly Fulfilment[]>,
    arriving: ReadonlyMap<string, readonly Fulfilment[]>,
    now: number,
    ttl: number,
): Map<string, Fulfilment[]> {
    const out = new Map<string, Fulfilment[]>();
    const keep = (key: string, list: readonly Fulfilment[]): void => {
        const live = list.filter(f => now - f.at < ttl);
        if (live.length) out.set(key, [...(out.get(key) ?? []), ...live]);
    };
    for (const [key, list] of known) keep(key, list);
    for (const [key, list] of arriving) {
        // An arriving fulfilment REPLACES a remembered one on the same channel — the
        // feature landed again, and the new landing is the one the reader has not seen.
        const channels = new Set(list.map(f => f.channel));
        out.set(key, [...(out.get(key) ?? []).filter(f => !channels.has(f.channel)), ...list]);
    }
    return out;
}
