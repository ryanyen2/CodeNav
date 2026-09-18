/**
 * commands.ts — the structural gestures, each emitting its op and performing
 * the equivalent ProseMirror change (09-editor §4).
 *
 * Every command returns `{ tr, ops }` or null when the gesture does not apply
 * (the keymap then lets the caret move or does nothing). The transaction is
 * tagged `RECORDED` so the recorder plugin skips it: the ops here are the
 * record. Shadows are advanced in step with the document.
 *
 * The rules a gesture may not break: a heading never merges with prose in
 * either direction; a heading disappears only through `deleteFeature`; the
 * half of a split that keeps the block's id is the half that keeps its text.
 */
import type { Node as PMNode } from '@tiptap/pm/model';
import { type EditorState, TextSelection, type Transaction } from '@tiptap/pm/state';

import { START, type OpBody } from '../calc/projection';
import { scalarOffset } from '../calc/recorder';
import { Shadow } from '../calc/shadow';
import { MAX_LEVEL } from './schema';

export const RECORDED = 'codoc:recorded';

export interface Gesture { tr: Transaction; ops: OpBody[]; cids: string[] }

export interface Env {
    shadows: Map<string, Shadow>;
    nextBid: () => string;
    nextCid: () => string;
}

function blockAt(state: EditorState, pos: number): { node: PMNode; pos: number; index: number } | null {
    const $p = state.doc.resolve(pos);
    if ($p.depth < 1) return null;
    return { node: $p.node(1), pos: $p.before(1), index: $p.index(0) };
}

function tag(tr: Transaction, ops: OpBody[]): Transaction {
    return tr.setMeta(RECORDED, ops).setMeta('addToHistory', false);
}

/** Enter: split the caret's block. */
export function splitBlock(state: EditorState, env: Env): Gesture | null {
    const { from, empty } = state.selection;
    if (!empty) return null;
    const b = blockAt(state, from);
    if (!b) return null;
    const bid = b.node.attrs.bid as string;
    const shadow = env.shadows.get(bid);
    if (!shadow) return null;
    const $from = state.doc.resolve(from);
    const off = scalarOffset(b.node, $from.parentOffset);
    const nid = env.nextBid();
    const at = off === 0 ? START : shadow.ref(off - 1);
    const headHasText = off > 0;
    const tailHasText = off < shadow.length;
    const newFirst = !headHasText && tailHasText;      // the id stays with the text
    const isHeading = b.node.type.name === 'heading';
    const tailType = isHeading ? state.schema.nodes.paragraph : b.node.type;
    let tr = state.tr.split(from, 1, [{ type: tailType, attrs: isHeading ? { bid: null, head: 0 } : { ...b.node.attrs } }]);
    // Assign ids: the half that keeps the text keeps `bid`.
    const headPos = b.pos;
    const tailPos = tr.mapping.map(from) ;
    const $tail = tr.doc.resolve(tailPos);
    const tailStart = $tail.before(1);
    if (newFirst) {
        tr = tr.setNodeMarkup(headPos, undefined, { ...b.node.attrs, bid: nid, head: 0, ...(isHeading ? {} : {}) });
        tr = tr.setNodeMarkup(tailStart, undefined, { ...tr.doc.nodeAt(tailStart)!.attrs, bid, head: b.node.attrs.head });
    } else {
        tr = tr.setNodeMarkup(tailStart, undefined, { ...tr.doc.nodeAt(tailStart)!.attrs, bid: nid, head: 0 });
    }
    // Headings split into heading + paragraph; when the new block is first it is the paragraph? No:
    // spec: splitting a heading yields heading + paragraph, the heading keeps its text.
    if (isHeading && newFirst) {
        // Empty head before a heading's text: the new empty block is a paragraph BEFORE the heading.
        tr = tr.setNodeMarkup(headPos, state.schema.nodes.paragraph, { bid: nid, head: 0 });
        tr = tr.setNodeMarkup(tailStart, state.schema.nodes.heading, { bid, head: b.node.attrs.head, level: b.node.attrs.level });
    }
    const tail = shadow.split(off);
    if (newFirst) { env.shadows.set(nid, shadow); env.shadows.set(bid, tail); }
    else env.shadows.set(nid, tail);
    tr = tr.setSelection(TextSelection.create(tr.doc, tailStart + 1));
    const ops: OpBody[] = [{ kind: 'block.split', target: { bid, path: 'text' }, payload: { at, new: nid } }];
    return { tr: tag(tr, ops), ops, cids: [] };
}

/** Backspace at the start of a paragraph: merge into the previous paragraph. */
export function mergeBackward(state: EditorState, env: Env): Gesture | null {
    const { from, empty } = state.selection;
    if (!empty) return null;
    const $from = state.doc.resolve(from);
    if ($from.depth !== 1 || $from.parentOffset !== 0 || $from.index(0) === 0) return null;
    const cur = $from.node(1);
    const prev = state.doc.child($from.index(0) - 1);
    if (cur.type.name !== 'paragraph' || prev.type.name !== 'paragraph') return null;
    return merge(state, env, prev, $from.before(1) - prev.nodeSize, cur, $from.before(1));
}

/** Delete at the end of a paragraph: merge the next paragraph into it. */
export function mergeForward(state: EditorState, env: Env): Gesture | null {
    const { from, empty } = state.selection;
    if (!empty) return null;
    const $from = state.doc.resolve(from);
    if ($from.depth !== 1 || $from.parentOffset !== $from.parent.content.size) return null;
    const idx = $from.index(0);
    if (idx + 1 >= state.doc.childCount) return null;
    const cur = $from.node(1);
    const next = state.doc.child(idx + 1);
    if (cur.type.name !== 'paragraph' || next.type.name !== 'paragraph') return null;
    return merge(state, env, cur, $from.before(1), next, $from.after(1));
}

function merge(state: EditorState, env: Env, into: PMNode, intoPos: number, dying: PMNode, dyingPos: number): Gesture | null {
    const a = env.shadows.get(into.attrs.bid as string);
    const b = env.shadows.get(dying.attrs.bid as string);
    if (!a || !b) return null;
    const joinAt = dyingPos;   // the boundary between the two blocks
    const caret = intoPos + into.nodeSize - 1;
    let tr = state.tr.join(joinAt);
    tr = tr.setSelection(TextSelection.create(tr.doc, caret));
    a.append(b);
    env.shadows.delete(dying.attrs.bid as string);
    const ops: OpBody[] = [{ kind: 'block.merge', target: { into: into.attrs.bid, bid: dying.attrs.bid, path: 'text' }, payload: {} }];
    return { tr: tag(tr, ops), ops, cids: [] };
}

/** Turn the caret's block into a heading of `level` (the `## ` input rule),
 *  dropping the typed marker characters `markerLen` scalars long at the start. */
export function makeHeading(state: EditorState, env: Env, level: number, markerLen: number): Gesture | null {
    const b = blockAt(state, state.selection.from);
    if (!b) return null;
    const bid = b.node.attrs.bid as string;
    const shadow = env.shadows.get(bid);
    if (!shadow) return null;
    const ops: OpBody[] = [];
    let tr = state.tr;
    if (markerLen > 0) {
        const refs = shadow.delete(0, markerLen);
        ops.push({ kind: 'text.delete', target: { bid, path: 'text' }, payload: { from: refs[0], to: refs[refs.length - 1] } });
        tr = tr.delete(b.pos + 1, b.pos + 1 + markerLen);
    }
    const lvl = Math.min(MAX_LEVEL, Math.max(1, level));
    ops.push({ kind: 'block.set', target: { bid, path: 'kind' }, payload: { value: 'heading', level: lvl } });
    tr = tr.setNodeMarkup(b.pos, state.schema.nodes.heading, { bid, head: b.node.attrs.head, level: lvl });
    return { tr: tag(tr, ops), ops, cids: [] };
}

/** Turn the caret's heading back into a paragraph (the `/` menu), dropping
 *  the typed marker characters. A heading with a body keeps its body: the
 *  paragraphs simply join the previous feature. */
export function makeParagraph(state: EditorState, env: Env, markerLen: number): Gesture | null {
    const b = blockAt(state, state.selection.from);
    if (!b) return null;
    const bid = b.node.attrs.bid as string;
    const shadow = env.shadows.get(bid);
    if (!shadow) return null;
    const ops: OpBody[] = [];
    let tr = state.tr;
    if (markerLen > 0) {
        const refs = shadow.delete(0, markerLen);
        ops.push({ kind: 'text.delete', target: { bid, path: 'text' }, payload: { from: refs[0], to: refs[refs.length - 1] } });
        tr = tr.delete(b.pos + 1, b.pos + 1 + markerLen);
    }
    if (b.node.type.name === 'heading') {
        ops.push({ kind: 'block.set', target: { bid, path: 'kind' }, payload: { value: 'paragraph' } });
        tr = tr.setNodeMarkup(b.pos, state.schema.nodes.paragraph, { bid, head: b.node.attrs.head });
    }
    if (!ops.length) return null;
    return { tr: tag(tr, ops), ops, cids: [] };
}

/** Tab / Shift-Tab on a heading: one level deeper or shallower. */
export function relevel(state: EditorState, env: Env, delta: number): Gesture | null {
    const b = blockAt(state, state.selection.from);
    if (!b || b.node.type.name !== 'heading') return null;
    const level = Math.min(MAX_LEVEL, Math.max(1, (b.node.attrs.level as number) + delta));
    if (level === b.node.attrs.level) return null;
    const ops: OpBody[] = [{ kind: 'block.set', target: { bid: b.node.attrs.bid, path: 'level' }, payload: { value: level } }];
    const tr = state.tr.setNodeMarkup(b.pos, undefined, { ...b.node.attrs, level });
    return { tr: tag(tr, ops), ops, cids: [] };
}

/** The blocks a heading dominates in the document: itself and every following
 *  block until a heading at the same or a shallower level. */
export function dominatedRange(doc: PMNode, index: number): [number, number] {
    const h = doc.child(index);
    if (h.type.name !== 'heading') return [index, index + 1];
    let end = index + 1;
    while (end < doc.childCount) {
        const n = doc.child(end);
        if (n.type.name === 'heading' && n.attrs.level <= h.attrs.level) break;
        end++;
    }
    return [index, end];
}

/** Drag: move a block (with what it dominates) to just after `afterBid` (or START). */
export function moveBlock(state: EditorState, env: Env, bid: string, afterBid: string | 'START', levelDelta = 0): Gesture | null {
    const doc = state.doc;
    let idx = -1, afterIdx = -1;
    doc.forEach((n, _pos, i) => { if (n.attrs.bid === bid) idx = i; if (n.attrs.bid === afterBid) afterIdx = i; });
    if (idx < 0 || (afterBid !== 'START' && afterIdx < 0)) return null;
    const [s, e] = dominatedRange(doc, idx);
    if (afterBid !== 'START' && afterIdx >= s && afterIdx < e) return null;
    const nodes: PMNode[] = [];
    let start = 0, end = 0;
    doc.forEach((n, pos, i) => { if (i === s) start = pos; if (i === e - 1) end = pos + n.nodeSize; if (i >= s && i < e) nodes.push(n); });
    const moved = nodes.map(n => n.type.name === 'heading' && levelDelta
        ? n.type.create({ ...n.attrs, level: Math.min(MAX_LEVEL, Math.max(1, n.attrs.level + levelDelta)) }, n.content, n.marks)
        : n);
    let tr = state.tr.delete(start, end);
    let insertAt: number;
    if (afterBid === 'START') insertAt = 0;
    else {
        let p = -1;
        tr.doc.forEach((n, pos) => { if (n.attrs.bid === afterBid) p = pos + n.nodeSize; });
        insertAt = p;
    }
    tr = tr.insert(insertAt, moved);
    const ops: OpBody[] = [{ kind: 'block.move', target: { bid }, payload: { after: afterBid, level_delta: levelDelta } }];
    return { tr: tag(tr, ops), ops, cids: [] };
}

/** Delete a feature: its heading and own body; its children are promoted one
 *  level; its bindings are detached (the daemon's `caused_by` chain records why). */
export function deleteFeature(state: EditorState, env: Env, fid: string, bindings: Array<{ file: string; symbol: string }>): Gesture | null {
    const doc = state.doc;
    let idx = -1;
    doc.forEach((n, _p, i) => { if (n.attrs.bid === fid) idx = i; });
    if (idx < 0 || doc.child(idx).type.name !== 'heading') return null;
    const level = doc.child(idx).attrs.level as number;
    // Own body: heading + following paragraphs.
    let end = idx + 1;
    while (end < doc.childCount && doc.child(end).type.name !== 'heading') end++;
    const [, domEnd] = dominatedRange(doc, idx);
    const ops: OpBody[] = [{ kind: 'block.delete', target: { bid: fid }, payload: {} }];
    let tr = state.tr;
    // Promote descendant headings first (positions unchanged by attr edits).
    for (let i = end; i < domEnd; i++) {
        const n = doc.child(i);
        if (n.type.name === 'heading') {
            let pos = 0;
            doc.forEach((x, p, j) => { if (j === i) pos = p; });
            tr = tr.setNodeMarkup(pos, undefined, { ...n.attrs, level: Math.max(1, n.attrs.level - 1) });
            ops.push({ kind: 'block.set', target: { bid: n.attrs.bid, path: 'level' }, payload: { value: Math.max(1, n.attrs.level - 1) } });
        }
    }
    for (const b of bindings) ops.push({ kind: 'bind.detach', target: { fid }, payload: { file: b.file, symbol: b.symbol } });
    let start = 0, stop = 0;
    doc.forEach((n, pos, i) => { if (i === idx) start = pos; if (i === end - 1) stop = pos + n.nodeSize; });
    tr = tr.delete(start, stop);
    for (let i = idx; i < end; i++) env.shadows.delete(doc.child(i).attrs.bid as string);
    if (tr.doc.childCount === 0) return null;   // never empty the tree by one gesture
    void level;
    return { tr: tag(tr, ops), ops, cids: [] };
}
