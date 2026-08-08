/**
 * decoration-policy.ts — when a decoration layer is actually invalid.
 *
 * Nine layers independently shipped the same wrong policy:
 *
 *     if (tr.getMeta(X_UPDATED) || tr.docChanged) return buildEverything(doc, …)
 *     return old.map(tr.mapping, tr.doc)
 *
 * `tr.docChanged` is true for every character typed, so the cheap branch — the
 * one ProseMirror provides for precisely this, repositioning existing decorations
 * through the transaction's mapping — runs only when the document did NOT change,
 * which is when nobody needs it. Typing a character into a 300-feature tree with
 * History on rebuilds ~1200 decorations to redraw none of them (7.3ms, measured
 * in `decoration-cost.perf.test.ts`, against 2.4ms to map them).
 *
 * The mistake is treating "the document changed" as "everything I computed is
 * invalid". A decoration layer is a projection of (structure, state). Typing
 * inside a paragraph changes neither: it changes POSITIONS, which is exactly what
 * `tr.mapping` fixes. So:
 *
 *   • the layer's own state changed (its meta) → rebuild
 *   • the heading structure changed            → rebuild
 *   • otherwise                                → map
 *
 * This is deliberately NOT offered to every layer. `hold`, `captured`, `reveal`
 * and `glance` derive their decorations from the TEXT (a changed-range
 * underline, a per-word animation, a title-vs-pitch comparison), so for them a
 * keystroke really is invalidating and mapping would show a stale span. They
 * are cheap anyway — they decorate the one feature being edited, not all of
 * them. Structure-keyed layers (blame, phases, blocks, threads, drag handles)
 * are the ones that pay for the whole document to redraw nothing, and they are
 * the ones this serves.
 */
import type { Transaction } from '@tiptap/pm/state';
import type { Node as PMModelNode } from '@tiptap/pm/model';
import { DecorationSet } from '@tiptap/pm/view';
import { REFLECT_META } from './edit-origin';

/**
 * Whether this transaction changed the sequence of blocks a structure-keyed layer
 * is drawn from — the set of headings, their identities, their levels, and which
 * feature each paragraph belongs to.
 *
 * Memoised on the transaction itself. Nine layers ask the same question about the
 * same transaction, and a WeakMap answers it once without introducing an ordering
 * dependency between plugins (reading another plugin's state during `apply` would
 * get its OLD value unless that plugin happened to be registered first — a bug
 * that shows up only when someone reorders the extension list).
 */
const cache = new WeakMap<Transaction, boolean>();

export function structureChanged(tr: Transaction): boolean {
    const memo = cache.get(tr);
    if (memo !== undefined) return memo;
    const value = compute(tr);
    cache.set(tr, value);
    return value;
}

function compute(tr: Transaction): boolean {
    if (!tr.docChanged) return false;
    const before = tr.before, after = tr.doc;
    if (before.childCount !== after.childCount) return true;
    for (let i = 0; i < after.childCount; i++) {
        const a = after.child(i), b = before.child(i);
        if (a.type !== b.type) return true;
        if (!sameIdentity(a, b)) return true;
    }
    return false;
}

/** The attrs a structure-keyed decoration is positioned and labelled by. Text is
 *  deliberately absent: a layer that cares about text is not structure-keyed and
 *  must not use this policy. */
function sameIdentity(a: PMModelNode, b: PMModelNode): boolean {
    return a.attrs.fid === b.attrs.fid
        && a.attrs.localId === b.attrs.localId
        && a.attrs.level === b.attrs.level
        && a.attrs.ownerId === b.attrs.ownerId;
}

/**
 * The `apply` body for a structure-keyed decoration layer.
 *
 * `stateChanged` is the layer's own meta test — the projection brought new blame,
 * new phases, new blocks. `rebuild` is its full builder, called only when the
 * answer really did change.
 */
export function nextDecorations(
    tr: Transaction,
    old: DecorationSet,
    stateChanged: boolean,
    rebuild: () => DecorationSet,
): DecorationSet {
    // A projection reload replaces the whole doc in ONE ReplaceStep; mapping
    // through it deletes every decoration inside the replaced range, and for a
    // text-only external change structureChanged() is false — the layer would
    // come back empty and stay empty until its next meta. REFLECT transactions
    // are rare (a daemon write, an id mint), so always rebuilding on them costs
    // nothing per keystroke.
    if (stateChanged || tr.getMeta(REFLECT_META) || structureChanged(tr)) return rebuild();
    return old.map(tr.mapping, tr.doc);
}
