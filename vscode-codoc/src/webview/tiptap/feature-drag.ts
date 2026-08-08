/**
 * feature-drag.ts — moving a whole feature, as a document edit.
 *
 * The tree had no drag gesture at all, and could not have had one: siblings were
 * ordered by `created_at`, so any reorder animated and then silently reverted.
 * With `rank` on the store and `reorderTargets` deriving a move from document
 * order, the gesture needs no new persistence path of its own — it edits the
 * ProseMirror document, and the existing settle pipeline notices the order
 * changed and emits exactly one `move` naming the neighbours it landed between.
 * That is the whole reason this file is pure geometry and no channel code.
 *
 * A feature is a heading plus everything under it: its prose, and its nested
 * features. So a drag moves a SLICE of the flat document, not one node.
 */
import type { Node as PMModelNode } from '@tiptap/pm/model';
import type { EditorState, Transaction } from '@tiptap/pm/state';
import { FEATURE_MOVE_META } from './edit-origin';

const HEADING = 'featureHeading';

export interface FeatureSlice {
    /** Index of the heading among the document's top-level blocks. */
    index: number;
    /** Document positions covering the heading and everything beneath it. */
    from: number;
    to: number;
    /** Effective depth, clamped exactly as featureUnits/render clamp it. */
    depth: number;
}

/**
 * Every feature's slice, in document order.
 *
 * Depth is the CLAMPED depth (`min(level, prevDepth + 1)`), not the raw `level`
 * attribute, because that is what decides parentage everywhere else. Using the
 * raw level here would let a drag grab a different subtree than the one the
 * reader sees indented under the heading — the slice and the tree would disagree
 * about what "under" means.
 */
export function featureSlices(doc: PMModelNode): FeatureSlice[] {
    const heads: Array<{ index: number; pos: number; depth: number }> = [];
    let prevDepth = -1;
    let index = 0;
    doc.forEach((node, pos) => {
        if (node.type.name === HEADING) {
            const level = typeof node.attrs.level === 'number' ? node.attrs.level : 0;
            const depth = Math.max(0, Math.min(level, prevDepth + 1));
            prevDepth = depth;
            heads.push({ index, pos, depth });
        }
        index++;
    });

    return heads.map((h, i) => {
        // The slice ends where the next heading at the SAME depth or shallower
        // begins — that is the next sibling, or an ancestor's sibling.
        let end = doc.content.size;
        for (let j = i + 1; j < heads.length; j++) {
            if (heads[j].depth <= h.depth) { end = heads[j].pos; break; }
        }
        return { index: h.index, from: h.pos, to: end, depth: h.depth };
    });
}

/** The slice whose heading sits at `pos`, or null. */
export function sliceAt(doc: PMModelNode, pos: number): FeatureSlice | null {
    return featureSlices(doc).find(s => s.from === pos) ?? null;
}

/**
 * Document positions a slice may be dropped at: the start of each top-level
 * block, plus the end of the document.
 *
 * A slice's own interior is excluded. Dropping a feature inside itself would
 * delete the range and then reinsert it at a position that no longer exists —
 * and conceptually asks a node to become its own descendant, which the store's
 * cycle guard would refuse anyway. Refusing at the gesture is better than
 * animating a move the daemon then declines.
 */
export function dropPositions(doc: PMModelNode, moving: FeatureSlice | null): number[] {
    const out: number[] = [];
    doc.forEach((_node, pos) => {
        if (moving && pos > moving.from && pos < moving.to) return;
        out.push(pos);
    });
    out.push(doc.content.size);
    return moving ? out.filter(p => p !== moving.from) : out;
}

/** The drop position nearest a viewport-independent document position. */
export function nearestDrop(positions: number[], target: number): number {
    let best = positions[0] ?? 0;
    for (const p of positions) {
        if (Math.abs(p - target) < Math.abs(best - target)) best = p;
    }
    return best;
}

/**
 * Move `slice` so it begins at `to`.
 *
 * One transaction, so undo restores the whole feature in one step rather than
 * unpicking a delete and an insert. Returns null when the move is a no-op or
 * lands inside the slice, so callers never dispatch an empty change that would
 * still mark the document dirty and settle.
 */
export function moveSlice(state: EditorState, slice: FeatureSlice, to: number): Transaction | null {
    if (to > slice.from && to < slice.to) return null;   // into itself
    if (to === slice.from) return null;                  // no-op
    const content = state.doc.slice(slice.from, slice.to).content;
    if (!content.size) return null;

    const tr = state.tr.delete(slice.from, slice.to);
    // Deleting first shifts every later position left by the slice's length; map
    // the destination through that same change rather than adjusting by hand.
    const dest = tr.mapping.map(to, -1);
    tr.insert(dest, content);
    // Declared structural (not typing) so author-stamp and mark-hygiene leave the
    // re-inserted slice alone: it keeps its authorship and any agent proposal
    // marks instead of being committed as the dragger's own prose. History stays
    // on — undo restores the move.
    tr.setMeta(FEATURE_MOVE_META, true);
    return tr.docChanged ? tr : null;
}

/**
 * Where a feature lands when nudged one step among its siblings — the keyboard
 * equivalent of the drag.
 *
 * Not an afterthought: a drag is mouse-only, so without this the single gesture
 * for restructuring a tree is unavailable to anyone editing by keyboard.
 * Movement is by SIBLING, not by block, so one press steps over a whole feature
 * (with its prose and children) instead of burrowing into it.
 */
export function nudgeTarget(doc: PMModelNode, slice: FeatureSlice, dir: -1 | 1): number | null {
    const slices = featureSlices(doc);
    const at = slices.findIndex(s => s.from === slice.from);
    if (at < 0) return null;
    // Siblings share a PARENT, not merely a depth. The sibling run is the
    // same-depth slices around this one, cut at the nearest SHALLOWER heading on
    // each side — a same-depth slice beyond that boundary lives under a different
    // parent, and stepping onto it would silently reparent the feature when every
    // surface promises "one step among its siblings". Deeper slices in between
    // are a sibling's descendants and are stepped over, not into.
    const siblings: FeatureSlice[] = [slice];
    for (let j = at - 1; j >= 0; j--) {
        if (slices[j].depth < slice.depth) break;
        if (slices[j].depth === slice.depth) siblings.unshift(slices[j]);
    }
    for (let j = at + 1; j < slices.length; j++) {
        if (slices[j].depth < slice.depth) break;
        if (slices[j].depth === slice.depth) siblings.push(slices[j]);
    }
    const i = siblings.findIndex(s => s.from === slice.from);
    if (dir < 0) return i > 0 ? siblings[i - 1].from : null;
    const next = siblings[i + 1];
    if (!next) return null;
    // Moving down means landing after the next sibling's WHOLE slice, otherwise a
    // press would drop the feature between that sibling's heading and its prose.
    return next.to;
}
