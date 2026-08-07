/**
 * tx-ranges.ts — "which spans did this batch of transactions INSERT?", in
 * final-document coordinates.
 *
 * One implementation, deliberately. Two plugins need this answer (authorship
 * stamping and mark hygiene) and a second copy would drift: the mapping here is
 * subtle enough that a divergent copy is a bug waiting to happen.
 *
 * The subtlety is that a step's map speaks in the coordinates of the document
 * *at that step*, so a range must be carried forward twice: through the rest of
 * its own transaction's steps, and then through every LATER transaction in the
 * batch. ProseMirror hands `appendTransaction` a whole batch (an IME composition
 * flush, an autocomplete accept, or any `dispatch` chain produces several), and
 * skipping the second hop leaves ranges addressing a document that no longer
 * exists — silently off-by-N on exactly the inputs that are hardest to test.
 *
 * `include` selects which transactions CONTRIBUTE ranges while every transaction
 * still participates in the mapping. That distinction matters: a system
 * transaction (a reflect load, an authorship stamp) must not be treated as user
 * input, but it still moves positions and must be mapped through.
 */
import type { Transaction } from '@tiptap/pm/state';

export type InsertedRange = [from: number, to: number];

export function insertedRanges(
    transactions: readonly Transaction[],
    include: (tr: Transaction) => boolean,
): InsertedRange[] {
    const ranges: InsertedRange[] = [];
    transactions.forEach((tr, txIndex) => {
        if (!include(tr) || !tr.docChanged) return;
        const later = transactions.slice(txIndex + 1);
        tr.steps.forEach((step, stepIndex) => {
            step.getMap().forEach((_fromA, _toA, fromB, toB) => {
                if (toB <= fromB) return;
                const rest = tr.mapping.slice(stepIndex + 1);
                let from = rest.map(fromB, 1);
                let to = rest.map(toB, -1);
                for (const nextTr of later) {
                    from = nextTr.mapping.map(from, 1);
                    to = nextTr.mapping.map(to, -1);
                }
                if (to > from) ranges.push([from, to]);
            });
        });
    });
    return ranges;
}

/**
 * Whether a whole node ARRIVED in this batch, as opposed to merely being edited.
 *
 * This is the discriminator identity repair needs. A node that arrived — pasted,
 * dropped, duplicated — is a newcomer and should adopt the surroundings it landed
 * in. A node that STAYED keeps what it already had, even if the headings around it
 * moved; that is invariant I2, and it is why prose written under one feature is not
 * stolen by a heading later inserted above it.
 *
 * The test is containment of the node's whole span, not overlap: typing inside a
 * paragraph inserts a range strictly within it, and that must not read as the
 * paragraph being new.
 */
export function nodeArrived(ranges: readonly InsertedRange[], pos: number, nodeSize: number): boolean {
    return ranges.some(([from, to]) => from <= pos && to >= pos + nodeSize);
}

/** Clamp ranges into `size` so a caller can never address past the final doc. */
export function clampRanges(ranges: readonly InsertedRange[], size: number): InsertedRange[] {
    const out: InsertedRange[] = [];
    for (const [from, to] of ranges) {
        const f = Math.max(0, Math.min(from, size));
        const t = Math.max(f, Math.min(to, size));
        if (t > f) out.push([f, t]);
    }
    return out;
}
