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
import type { Node as PMNode, Slice } from '@tiptap/pm/model';
import { type EditorState, TextSelection, type Transaction } from '@tiptap/pm/state';

import { START, nfc, type OpBody } from '../calc/projection';
import { scalarOffset } from '../calc/recorder';
import { Shadow } from '../calc/shadow';
import { MAX_LEVEL } from './schema';

export const RECORDED = 'codoc:recorded';
/** The client ids a gesture minted for its own inserts, carried on the
 *  transaction so the submitted ops keep them. Without it the shadow's
 *  provisional refs name cids nobody ever submitted, and the next keystroke
 *  after pasted text anchors on one the daemon has never seen. */
export const GESTURE_CIDS = 'codoc:cids';
/** The document was rebuilt wholesale (an adoption). Mapping a decoration set
 *  of three thousand widgets through a replace of the whole document costs far
 *  more than building it again, so a plugin that keeps one rebuilds on this. */
/** An adoption: the whole document replaced in one step. A reader that keeps
 *  state per block maps it through an ordinary transaction and rebuilds on
 *  this one — mapping three thousand decorations through a whole-document
 *  replace costs several times what building them again does. */
export const REPLACED = 'codoc:replaced';

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

function tag(tr: Transaction, ops: OpBody[], cids: string[] = []): Transaction {
    return tr.setMeta(RECORDED, ops).setMeta(GESTURE_CIDS, cids).setMeta('addToHistory', false);
}

/** The position just past the top-level block with this id. */
function blockEnd(doc: PMNode, bid: string): number {
    let end = doc.content.size;
    doc.forEach((n, p) => { if (n.attrs.bid === bid) end = p + n.nodeSize; });
    return end;
}


/** The top-level blocks a selection touches, with the part of each it covers. */
export function selectedBlocks(state: EditorState): Array<{ node: PMNode; pos: number; index: number; from: number; to: number }> {
    const { from, to } = state.selection;
    const out: Array<{ node: PMNode; pos: number; index: number; from: number; to: number }> = [];
    state.doc.forEach((node, pos, index) => {
        const start = pos + 1, end = pos + node.nodeSize - 1;
        if (end < from || start > to) return;
        out.push({ node, pos, index, from: Math.max(from, start) - start, to: Math.min(to, end) - start });
    });
    return out;
}

/**
 * Delete what is selected, across blocks, as ops: the covered text of every
 * block it touches, the whole paragraphs it swallows, and a merge of the two
 * ends when both are paragraphs. A selection that touches a heading's title is
 * refused (`null`): a feature disappears only through `deleteFeature`, which is
 * the rule that keeps "why is this heading absent" from being a question.
 */
export function deleteSelection(state: EditorState, env: Env): Gesture | null {
    if (state.selection.empty) return null;
    const parts = selectedBlocks(state);
    if (!parts.length) return null;
    if (parts.length > 1 && parts.some(p => p.node.type.name === 'heading')) return null;
    // Every refusal is known before the first shadow moves: this used to walk
    // the parts, advance each shadow, and give up on a later one that had none
    // (a kind block in the middle of the selection), leaving the earlier blocks
    // saying one thing to the page and another to the store.
    if (parts.some(p => !env.shadows.get(p.node.attrs.bid as string))) return null;
    const ops: OpBody[] = [];
    // Text first, by identity, before any position moves.
    for (const part of parts) {
        const bid = part.node.attrs.bid as string;
        const shadow = env.shadows.get(bid)!;
        const a = scalarOffset(part.node, part.from), z = scalarOffset(part.node, part.to);
        if (z <= a) continue;
        const refs = shadow.delete(a, z);
        ops.push({ kind: 'text.delete', target: { bid, path: 'text' }, payload: { from: refs[0], to: refs[refs.length - 1] } });
    }
    const [head, ...rest] = parts;
    const tailPart = rest[rest.length - 1];
    // Whole blocks between the ends go; the two ends become one.
    for (const mid of rest.slice(0, -1)) {
        ops.push({ kind: 'block.delete', target: { bid: mid.node.attrs.bid }, payload: {} });
        env.shadows.delete(mid.node.attrs.bid as string);
    }
    if (tailPart) {
        const into = head.node.attrs.bid as string, dying = tailPart.node.attrs.bid as string;
        ops.push({ kind: 'block.merge', target: { into, bid: dying, path: 'text' }, payload: {} });
        env.shadows.get(into)!.append(env.shadows.get(dying)!);
        env.shadows.delete(dying);
    }
    let tr = state.tr.deleteSelection();
    if (tailPart) {
        // ProseMirror joins the ends for us; make sure the survivor keeps the
        // head's id (the calculus merged into it).
        const $c = tr.doc.resolve(tr.selection.from);
        if ($c.depth >= 1) tr = tr.setNodeMarkup($c.before(1), undefined, { ...head.node.attrs });
    }
    return { tr: tag(tr, ops), ops, cids: [] };
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
    // Splitting a heading yields a heading and a paragraph, and the heading is
    // the half that keeps its text — so when the new block comes first, it is
    // the new one that is the paragraph.
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

/**
 * The `## ` input rule: the hashes are already in the document (the space that
 * completed the rule is not), so exactly the hashes are dropped, by identity,
 * and the block becomes a heading of that level.
 */
export function headingFromRule(state: EditorState, env: Env, match: RegExpMatchArray): Gesture | null {
    const hashes = match[1].length;
    const b = blockAt(state, state.selection.from);
    if (!b || !env.shadows.get(b.node.attrs.bid as string)) return null;
    return makeHeading(state, env, hashes, hashes);
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
 *  level. Its bindings are detached by the applier, which records them so an
 *  undo re-attaches (spec §3); this gesture must not send its own detaches. */
export function deleteFeature(state: EditorState, env: Env, fid: string): Gesture | null {
    const doc = state.doc;
    let idx = -1;
    doc.forEach((n, _p, i) => { if (n.attrs.bid === fid) idx = i; });
    if (idx < 0 || doc.child(idx).type.name !== 'heading') return null;
    // Own body: heading + following paragraphs.
    let end = idx + 1;
    while (end < doc.childCount && doc.child(end).type.name !== 'heading') end++;
    // Never empty the tree by one gesture. The check is on the range, not on
    // the result: the schema says a document is `block+`, so ProseMirror refills
    // an emptied one with a default block and `childCount === 0` is never true.
    if (end - idx >= doc.childCount) return null;
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
    let start = 0, stop = 0;
    doc.forEach((n, pos, i) => { if (i === idx) start = pos; if (i === end - 1) stop = pos + n.nodeSize; });
    tr = tr.delete(start, stop);
    for (let i = idx; i < end; i++) env.shadows.delete(doc.child(i).attrs.bid as string);
    return { tr: tag(tr, ops), ops, cids: [] };
}


/** A paste whose slice holds more than one block: the first paragraph's text
 *  joins the caret's block, and every further paragraph is a block of its own
 *  (`09-editor.md` §4). The person is the author of all of it. */
export function pasteBlocks(state: EditorState, env: Env, slice: Slice): Gesture | null {
    // NFC at the door, like every other text that becomes an op (01 §2): the
    // shadow and the store compose it, and a page left holding the decomposed
    // form disagrees with both about how many characters are there.
    const paras: string[] = [];
    slice.content.forEach(node => { if (node.isBlock) paras.push(nfc(node.textBetween(0, node.content.size, '\n'))); });
    if (paras.length < 2) return null;
    const b = blockAt(state, state.selection.from);
    if (!b || !env.shadows.get(b.node.attrs.bid as string)) return null;
    const ops: OpBody[] = [];
    const cids: string[] = [];
    let tr = state.tr;
    let cut: Gesture | null = null;
    if (!state.selection.empty) {
        cut = deleteSelection(state, env);
        if (!cut) return null;
        ops.push(...cut.ops);
        tr = cut.tr;
    }
    const $at = tr.doc.resolve(tr.selection.from);
    const host = $at.node(1);
    const bid = host.attrs.bid as string;
    const shadow = env.shadows.get(bid)!;
    const off = scalarOffset(host, $at.parentOffset);
    // The first paragraph lands in the caret's block, as typed text would.
    if (paras[0]) {
        const cid = env.nextCid();
        ops.push({ kind: 'text.insert', target: { bid, path: 'text' }, payload: { after: shadow.anchorBefore(off), text: paras[0] } });
        cids.push(cid);
        shadow.insert(off, paras[0], cid);
        tr = tr.insertText(paras[0], tr.selection.from);
    }
    // The rest arrive as blocks after it. Each position is read from the
    // document as it stands, never mapped: a position computed before the cut
    // and mapped through a mapping that includes the cut is shifted twice, and
    // the paragraph then lands INSIDE the host, which ProseMirror fits by
    // splitting it into two nodes carrying one bid.
    let after = bid;
    let pos = blockEnd(tr.doc, bid);
    for (const text of paras.slice(1)) {
        const nb = env.nextBid();
        ops.push({ kind: 'block.add', target: { bid: nb }, payload: { kind: 'paragraph', after } });
        const node = state.schema.nodes.paragraph.create({ bid: nb, head: 0 }, text ? state.schema.text(text) : undefined);
        pos = blockEnd(tr.doc, after);
        tr = tr.insert(pos, node);
        pos += node.nodeSize;
        const sh = new Shadow();
        env.shadows.set(nb, sh);
        if (text) {
            const cid = env.nextCid();
            ops.push({ kind: 'text.insert', target: { bid: nb, path: 'text' }, payload: { after: START, text } });
            cids.push(cid);
            sh.insert(0, text, cid);
        }
        after = nb;
    }
    tr = tr.setSelection(TextSelection.create(tr.doc, Math.min(pos - 1, tr.doc.content.size)));
    return { tr: tag(tr, ops, cids), ops, cids };
}
