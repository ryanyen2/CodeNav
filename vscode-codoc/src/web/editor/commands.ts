/**
 * commands.ts: the structural gestures, each emitting its ops and performing
 * the equivalent ProseMirror change.
 *
 * Every command returns `{ tr, ops }`, or null when the gesture does not apply
 * (the keymap then lets the caret move or does nothing). The transaction is
 * tagged `RECORDED` so the recorder skips it: the ops here are the record.
 * Shadows are advanced in step with the document. Each op is one the applier
 * accepts (docs/model.md, "Block ops"): only heading and prose blocks split,
 * merge or change kind; a heading never merges with anything; a source or
 * plugin block keeps its kind for life.
 *
 * A gesture that is not performed for a reason the person should know says so
 * through `Env.refuse`.
 */
import type { Node as PMNode } from '@tiptap/pm/model';
import { type EditorState, Selection, TextSelection, type Transaction } from '@tiptap/pm/state';

import { ITEMS, MAX_LEVEL, PROSE, SOURCE, START, isOneLine, isProvisional, kindOf, nfc, pathOf, type CharRef, type OpBody,
         type Projection } from '../calc/projection';
import { deleteOf, scalarOffset } from '../calc/recorder';
import { Shadow } from '../calc/shadow';
import type { CardFields } from './card';
import { clampLevel } from './schema';

/** Transaction meta: the ops that record this transaction. The recorder skips
 *  a transaction that carries it, and the structural guard lets it through.
 *  An empty list means "nothing to send" (an adoption, the NFC fix-up). */
export const RECORDED = 'codoc:recorded';
/** Transaction meta: the client ids a gesture minted for its own inserts, in
 *  op order. The submitted ops must carry the same ids, because the shadow's
 *  provisional refs name them and the next keystroke anchors on one. */
export const GESTURE_CIDS = 'codoc:cids';
/** Transaction meta: an adoption replaced the whole document in one step. A
 *  plugin that keeps state per block rebuilds on this instead of mapping. */
export const REPLACED = 'codoc:replaced';

export interface Gesture { tr: Transaction; ops: OpBody[]; cids: string[] }

export interface Env {
    shadows: Map<string, Shadow>;
    nextBid: () => string;
    nextCid: () => string;
    /** Told why a gesture was not performed; the page shows it as a one-line notice. */
    refuse?: (why: string) => void;
    /** The projection in hand, for the features' bindings and phases. */
    projection?: () => Projection;
    /** For block `bid`, whether the viewer wrote a character. Without it, only unacked characters count. */
    mine?: (bid: string) => (r: CharRef) => boolean;
    /** The field shadows of card blocks (card.ts); a paste that adds a card fills its entry. */
    cards?: CardFields;
}

function blockAt(state: EditorState, pos: number): { node: PMNode; pos: number; index: number } | null {
    const $p = state.doc.resolve(pos);
    if ($p.depth < 1) return null;
    return { node: $p.node(1), pos: $p.before(1), index: $p.index(0) };
}

/** A gesture's transaction, tagged with its ops. */
export function done(tr: Transaction, ops: OpBody[], cids: string[] = []): Gesture {
    return { tr: tr.setMeta(RECORDED, ops).setMeta(GESTURE_CIDS, cids).setMeta('addToHistory', false), ops, cids };
}

export const isLevelled = (kind: string): boolean => kind === 'heading' || ITEMS.has(kind);

/** Node attributes for a heading or prose block of `kind`. */
export const lineAttrs = (kind: string, bid: string, head: number, level: number) =>
    (isLevelled(kind) ? { bid, head, level } : { bid, head });

/** A `text.delete` of the first `n` characters of a block, by identity. */
const dropStart = (bid: string, shadow: Shadow, n: number): OpBody => deleteOf({ bid, path: 'text' }, shadow.delete(0, n));

/** The top-level blocks a selection touches, with the part of each it covers. */
export function selectedBlocks(state: EditorState): Array<{ node: PMNode; pos: number; index: number; from: number; to: number }> {
    const { from, to } = state.selection;
    const out: Array<{ node: PMNode; pos: number; index: number; from: number; to: number }> = [];
    state.doc.forEach((node, pos, index) => {
        const start = pos + 1, end = pos + node.nodeSize - 1;
        if (end < from || start > to) return;
        out.push({ node, pos, index, from: Math.max(0, Math.max(from, start) - start), to: Math.max(0, Math.min(to, end) - start) });
    });
    return out;
}

/**
 * Delete what is selected, as ops. Inside one block, the covered text. Across
 * blocks: the covered text of the first and the last block, a `block.delete`
 * of every block between, and a `block.merge` of the last into the first when
 * both are prose. A heading at either end is left alone when none of its
 * characters is selected and deleted when all of them are; a selection that
 * covers part of a heading is refused, because the rest of it could not join
 * anything. A selection that would take every block, or every feature, is
 * refused.
 *
 * The other ends depend on the gesture. A pure delete (`landing` false)
 * deletes a prose or source end that is wholly selected when a block outside
 * the selection is left, so a whole feature selected to the end of its last
 * paragraph goes without leaving an empty paragraph to the feature above;
 * partly selected, an end loses the covered text. When text follows (typing,
 * paste, Enter, an IME composition) the text must land inside what was
 * selected: a prose end is always kept, emptied of the covered text, and the
 * caret goes into the first kept prose end, or the last; a wholly selected
 * source end is deleted, so the text never lands in a code block or a table;
 * a heading is never the landing place. When no prose end is kept, a new
 * empty paragraph stands where the selection was.
 */
export function deleteSelection(state: EditorState, env: Env, landing = false): Gesture | null {
    if (state.selection.empty) return null;
    const parts = selectedBlocks(state);
    if (!parts.length) return null;
    const refuse = (why: string): null => { env.refuse?.(why); return null; };
    const last = parts.length - 1;
    const outside = parts[0].index > 0 || parts[last].index < state.doc.childCount - 1;
    type Fate = 'cut' | 'delete' | 'keep';
    const fates: Fate[] = [];
    for (const [i, p] of parts.entries()) {
        const kind = kindOf(p.node);
        const covered = p.to > p.from;
        const whole = covered && p.from === 0 && p.to === p.node.content.size;
        if (p.node.isAtom || (i > 0 && i < last)) fates.push('delete');
        else if (parts.length === 1) fates.push('cut');
        else if (kind === 'heading') {
            if (covered && !whole) return refuse('a selection that covers part of a heading is not deleted; select the whole heading');
            fates.push(whole ? 'delete' : 'keep');
        } else if (landing) fates.push(SOURCE.has(kind) && whole ? 'delete' : 'cut');
        else fates.push(whole && outside ? 'delete' : 'cut');
    }
    if (parts.length === 1 && !env.shadows.get(parts[0].node.attrs.bid as string)) return null;
    const gone = parts.filter((_p, i) => fates[i] === 'delete');
    let headings = 0;
    state.doc.forEach(n => { if (n.type.name === 'heading') headings++; });
    if (gone.length === state.doc.childCount) return refuse('a selection that takes every block is not deleted');
    if (headings && gone.filter(p => p.node.type.name === 'heading').length === headings) {
        return refuse('a selection that takes every feature is not deleted');
    }
    const first = parts[0], tail = parts[last];
    const merge = parts.length > 1 && fates[0] === 'cut' && fates[last] === 'cut'
        && PROSE.has(kindOf(first.node)) && PROSE.has(kindOf(tail.node));

    // Text first, by identity, before any shadow moves.
    const ops: OpBody[] = [];
    for (const [i, p] of parts.entries()) {
        if (fates[i] !== 'cut' || p.to <= p.from) continue;
        const bid = p.node.attrs.bid as string;
        const shadow = env.shadows.get(bid)!;
        const refs = shadow.delete(scalarOffset(p.node, p.from), scalarOffset(p.node, p.to));
        if (refs.length) ops.push(deleteOf({ bid, path: pathOf(kindOf(p.node)) }, refs));
    }
    // One delete naming exactly the blocks that go, so the applier reads each
    // block's feature before any of them dies. A heading taken this way leaves
    // the blocks after the selection to the feature before.
    if (gone.length) {
        const ids = gone.map(p => p.node.attrs.bid as string);
        ops.push({ kind: 'block.delete', target: { bid: ids[0] }, payload: { ids } });
        for (const bid of ids) env.shadows.delete(bid);
    }
    if (merge) {
        const into = first.node.attrs.bid as string, dying = tail.node.attrs.bid as string;
        ops.push({ kind: 'block.merge', target: { into, bid: dying, path: 'text' }, payload: {} });
        env.shadows.get(into)!.append(env.shadows.get(dying)!);
        env.shadows.delete(dying);
    }

    // The page: the two ends as they now read, nothing between.
    const cut = (p: typeof first) => p.node.copy(p.node.content.cut(0, p.from).append(p.node.content.cut(p.to)));
    const ends: PMNode[] = [];
    let caret = -1;              // offset into `ends` where the caret goes
    if (parts.length === 1 || merge) {
        ends.push(first.node.copy(first.node.content.cut(0, first.from).append(tail.node.content.cut(tail.to))));
        caret = 1 + first.from;
    } else {
        const head = fates[0] === 'delete' ? null : fates[0] === 'cut' ? cut(first) : first.node;
        const back = fates[last] === 'delete' ? null : fates[last] === 'cut' ? cut(tail) : tail.node;
        const prose = (i: number, p: typeof first) => fates[i] === 'cut' && PROSE.has(kindOf(p.node));
        let blank: PMNode | null = null;
        if (landing && !prose(0, first) && !prose(last, tail)) {
            // No prose end is kept: a new paragraph stands where the selection was.
            const nb = env.nextBid();
            const after = head ? first.node.attrs.bid as string : first.index > 0 ? state.doc.child(first.index - 1).attrs.bid as string : START;
            ops.push({ kind: 'block.add', target: { bid: nb }, payload: { kind: 'paragraph', after } });
            env.shadows.set(nb, new Shadow());
            blank = state.schema.nodes.paragraph.create({ bid: nb, head: 0 });
        }
        for (const n of [head, blank, back]) if (n) ends.push(n);
        const backStart = (head?.nodeSize ?? 0) + (blank?.nodeSize ?? 0);
        if (landing) caret = prose(0, first) ? 1 + first.from : prose(last, tail) ? backStart + 1 : (head?.nodeSize ?? 0) + 1;
        else if (fates[0] === 'cut') caret = 1 + first.from;
        else if (back) caret = backStart + 1;
        else if (head) caret = head.nodeSize - 1;          // the end of a heading left alone
    }
    const start = first.pos, end = tail.pos + tail.node.nodeSize;
    let tr = state.tr.replaceWith(start, end, ends);
    // With both ends gone, the caret goes to the end of the block before the selection.
    tr = tr.setSelection(caret >= 0 ? TextSelection.create(tr.doc, start + caret) : Selection.near(tr.doc.resolve(Math.min(start, tr.doc.content.size)), -1));
    return done(tr, ops);
}

/**
 * Split the heading or prose block `node` at document position `pos`, scalar
 * offset `off`. The tail becomes a new block with the tail of the shadow: a
 * paragraph when `node` is a heading, a block like `node` otherwise.
 */
export function splitAt(tr: Transaction, env: Env, node: PMNode, pos: number, off: number): { tr: Transaction; op: OpBody; nid: string } {
    const bid = node.attrs.bid as string;
    const shadow = env.shadows.get(bid)!;
    const nid = env.nextBid();
    const op = { kind: 'block.split', target: { bid, path: 'text' }, payload: { at: off === 0 ? START : shadow.ref(off - 1), new: nid } };
    env.shadows.set(nid, shadow.split(off));
    const heading = node.type.name === 'heading';
    return { op, nid, tr: tr.split(pos, 1, [{ type: heading ? tr.doc.type.schema.nodes.paragraph : node.type,
                                               attrs: heading ? { bid: nid, head: 0 } : { ...node.attrs, bid: nid, head: 0 } }]) };
}

/** Enter: split the caret's heading or prose block. A list item splits into
 *  two items at the same level, a heading into a heading and a paragraph. */
export function splitBlock(state: EditorState, env: Env): Gesture | null {
    const { from, empty } = state.selection;
    if (!empty) return null;
    const b = blockAt(state, from);
    if (!b || !isOneLine(kindOf(b.node))) return null;
    const bid = b.node.attrs.bid as string;
    const shadow = env.shadows.get(bid);
    if (!shadow) return null;
    const off = scalarOffset(b.node, state.doc.resolve(from).parentOffset);
    const newFirst = off === 0 && shadow.length > 0;
    const { op, nid, ...split } = splitAt(state.tr, env, b.node, from, off);
    let tr = split.tr;
    const tailStart = tr.doc.resolve(tr.mapping.map(from)).before(1);
    if (newFirst) {
        // At the start of a block with words, the id stays with the words: the
        // two halves trade ids, kinds and shadows, so the empty head is the new
        // block, and a paragraph when a heading split.
        const head = tr.doc.nodeAt(b.pos)!, tail = tr.doc.nodeAt(tailStart)!;
        tr = tr.setNodeMarkup(b.pos, tail.type, tail.attrs).setNodeMarkup(tailStart, head.type, head.attrs);
        const words = env.shadows.get(nid)!;
        env.shadows.set(nid, env.shadows.get(bid)!);
        env.shadows.set(bid, words);
    }
    tr = tr.setSelection(TextSelection.create(tr.doc, tailStart + 1));
    return done(tr, [op]);
}

/** Backspace at the start of a prose block: merge it into the prose block before. */
export function mergeBackward(state: EditorState, env: Env): Gesture | null {
    const { from, empty } = state.selection;
    if (!empty) return null;
    const $from = state.doc.resolve(from);
    if ($from.depth !== 1 || $from.parentOffset !== 0 || $from.index(0) === 0) return null;
    const cur = $from.node(1);
    const prev = state.doc.child($from.index(0) - 1);
    if (!PROSE.has(kindOf(cur)) || !PROSE.has(kindOf(prev))) return null;
    return merge(state, env, prev, $from.before(1) - prev.nodeSize, cur, $from.before(1));
}

/** Delete at the end of a prose block: merge the prose block after into it. */
export function mergeForward(state: EditorState, env: Env): Gesture | null {
    const { from, empty } = state.selection;
    if (!empty) return null;
    const $from = state.doc.resolve(from);
    if ($from.depth !== 1 || $from.parentOffset !== $from.parent.content.size) return null;
    const idx = $from.index(0);
    if (idx + 1 >= state.doc.childCount) return null;
    const cur = $from.node(1);
    const next = state.doc.child(idx + 1);
    if (!PROSE.has(kindOf(cur)) || !PROSE.has(kindOf(next))) return null;
    return merge(state, env, cur, $from.before(1), next, $from.after(1));
}

/** Merge `dying` into `into`. When `into` is an empty paragraph it is deleted
 *  instead, so the block with the words keeps its id and its kind. */
function merge(state: EditorState, env: Env, into: PMNode, intoPos: number, dying: PMNode, dyingPos: number): Gesture | null {
    const a = env.shadows.get(into.attrs.bid as string);
    const b = env.shadows.get(dying.attrs.bid as string);
    if (!a || !b) return null;
    if (into.type.name === 'paragraph' && a.length === 0) {
        let tr = state.tr.delete(intoPos, intoPos + into.nodeSize);
        tr = tr.setSelection(TextSelection.create(tr.doc, tr.mapping.map(dyingPos) + 1));
        env.shadows.delete(into.attrs.bid as string);
        return done(tr, [{ kind: 'block.delete', target: { bid: into.attrs.bid }, payload: {} }]);
    }
    const caret = intoPos + into.nodeSize - 1;
    let tr = state.tr.join(dyingPos);
    tr = tr.setSelection(TextSelection.create(tr.doc, caret));
    a.append(b);
    env.shadows.delete(dying.attrs.bid as string);
    return done(tr, [{ kind: 'block.merge', target: { into: into.attrs.bid, bid: dying.attrs.bid, path: 'text' }, payload: {} }]);
}

/**
 * Backspace or Delete in an empty block that has nothing to merge with on that
 * side: an empty paragraph next to a heading, a source or a plugin block, or
 * an empty source block next to anything. The block is deleted and the caret
 * goes into its neighbour.
 */
export function removeEmpty(state: EditorState, env: Env, dir: 'back' | 'forward'): Gesture | null {
    const { $from, empty } = state.selection;
    if (!empty || $from.depth !== 1 || $from.parent.content.size || state.doc.childCount < 2) return null;
    const cur = $from.parent;
    const kind = kindOf(cur);
    if (kind !== 'paragraph' && !SOURCE.has(kind)) return null;
    const n = $from.index(0) + (dir === 'back' ? -1 : 1);
    if (n < 0 || n >= state.doc.childCount) return null;
    if (kind === 'paragraph' && PROSE.has(kindOf(state.doc.child(n)))) return null;   // a merge does this
    const bid = cur.attrs.bid as string;
    if (!env.shadows.get(bid)) return null;
    let tr = state.tr.delete($from.before(1), $from.after(1));
    const near = dir === 'back' ? $from.before(1) - 1 : tr.mapping.map($from.after(1) + 1);
    tr = tr.setSelection(Selection.near(tr.doc.resolve(Math.max(0, Math.min(near, tr.doc.content.size))), dir === 'back' ? -1 : 1));
    env.shadows.delete(bid);
    return done(tr, [{ kind: 'block.delete', target: { bid }, payload: {} }]);
}

/**
 * Turn the caret's block into `kind`, a heading or a prose kind, dropping its
 * first `markerUnits` UTF-16 units: the typed `## `, `- ` or `1. ` of an input
 * rule, or the `/query` of the menu. `level` defaults to the block's own when
 * a list item becomes the other list kind, and to 1 otherwise.
 */
export function setKind(state: EditorState, env: Env, kind: string, level?: number, markerUnits = 0): Gesture | null {
    const b = blockAt(state, state.selection.from);
    if (!b || !isOneLine(kind) || !isOneLine(kindOf(b.node))) return null;
    const bid = b.node.attrs.bid as string;
    const shadow = env.shadows.get(bid);
    if (!shadow) return null;
    const cur = kindOf(b.node);
    const lvl = !isLevelled(kind) ? 0
        : clampLevel(level ?? (ITEMS.has(cur) && ITEMS.has(kind) ? b.node.attrs.level as number : 1));
    const ops: OpBody[] = [];
    let tr = state.tr;
    const marker = scalarOffset(b.node, markerUnits);
    if (marker > 0) {
        ops.push(dropStart(bid, shadow, marker));
        tr = tr.delete(b.pos + 1, b.pos + 1 + markerUnits);
    }
    if (cur !== kind || (lvl && lvl !== b.node.attrs.level)) {
        ops.push({ kind: 'block.set', target: { bid, path: 'kind' }, payload: lvl ? { value: kind, level: lvl } : { value: kind } });
        tr = tr.setNodeMarkup(b.pos, state.schema.nodes[kind], lineAttrs(kind, bid, b.node.attrs.head as number, lvl));
    }
    return ops.length ? done(tr, ops) : null;
}

/**
 * A table, a diagram or a code block at the caret. When the caret's block is
 * a paragraph holding nothing but the marker (```` ``` ````, a `|` row, or a
 * `/query`), the paragraph is replaced: deleted, and the new block added after
 * the block before it, in one batch, because a paragraph cannot become a
 * source block. Otherwise the marker is dropped and the new block is added
 * after the caret's block. `source` is the new block's first text.
 */
export function insertSource(state: EditorState, env: Env, kind: string, content: Record<string, unknown> = {},
                             source = '', markerUnits = 0): Gesture | null {
    const b = blockAt(state, state.selection.from);
    if (!b || !SOURCE.has(kind) || !isOneLine(kindOf(b.node))) return null;
    const bid = b.node.attrs.bid as string;
    const shadow = env.shadows.get(bid);
    if (!shadow) return null;
    const marker = scalarOffset(b.node, markerUnits);
    const replace = kindOf(b.node) === 'paragraph' && marker === shadow.length;
    const text = nfc(source);
    const nb = env.nextBid();
    const node = state.schema.nodes.source.create({ bid: nb, head: 0, kind, lang: content.lang ?? null, derived: content.derived === true },
                                                  text ? state.schema.text(text) : undefined);
    const ops: OpBody[] = [];
    let tr = state.tr;
    let at: number;
    if (replace) {
        const prev = b.index > 0 ? state.doc.child(b.index - 1).attrs.bid as string : START;
        ops.push({ kind: 'block.delete', target: { bid }, payload: {} });
        ops.push({ kind: 'block.add', target: { bid: nb }, payload: { kind, after: prev, content } });
        tr = tr.replaceWith(b.pos, b.pos + b.node.nodeSize, node);
        env.shadows.delete(bid);
        at = b.pos;
    } else {
        if (marker > 0) {
            ops.push(dropStart(bid, shadow, marker));
            tr = tr.delete(b.pos + 1, b.pos + 1 + markerUnits);
        }
        ops.push({ kind: 'block.add', target: { bid: nb }, payload: { kind, after: bid, content } });
        at = b.pos + b.node.nodeSize - markerUnits;
        tr = tr.insert(at, node);
    }
    const sh = new Shadow();
    const cids: string[] = [];
    if (text) {
        const cid = env.nextCid();
        ops.push({ kind: 'text.insert', target: { bid: nb, path: 'source' }, payload: { after: START, text } });
        cids.push(cid);
        sh.insert(0, text, cid);
    }
    env.shadows.set(nb, sh);
    tr = tr.setSelection(TextSelection.create(tr.doc, at + 1 + node.content.size));
    return done(tr, ops, cids);
}

/**
 * Enter at the end of a paragraph that holds only ```` ```lang ```` or one
 * table row (`|…|…|`) the viewer wrote: the paragraph becomes a code block (a
 * diagram for `mermaid`) or a table holding that row. Prose such as `|x| is
 * the absolute value`, or another actor's row, splits as usual.
 */
export function sourceFromMarker(state: EditorState, env: Env): Gesture | null {
    const { $from, empty } = state.selection;
    if (!empty || $from.depth !== 1 || $from.parent.type.name !== 'paragraph' || $from.parentOffset !== $from.parent.content.size) return null;
    const text = $from.parent.textContent;
    const fence = /^```\s*([^\s`]*)\s*$/.exec(text);
    if (fence) {
        const lang = fence[1];
        return lang === 'mermaid' ? insertSource(state, env, 'diagram', {}, '', text.length)
            : insertSource(state, env, 'code', lang ? { lang } : {}, '', text.length);
    }
    if (!/^\|.*\|.*\|$/.test(text)) return null;
    // Only the viewer's own row: replacing another actor's paragraph would re-author their words.
    const bid = $from.parent.attrs.bid as string;
    const mine = env.mine?.(bid) ?? isProvisional;
    return env.shadows.get(bid)?.refs.every(mine) ? insertSource(state, env, 'table', {}, text, text.length) : null;
}

/** An empty paragraph after the caret's block: the way out of a source block. */
export function paragraphAfter(state: EditorState, env: Env): Gesture | null {
    const b = blockAt(state, state.selection.from);
    if (!b || !b.node.attrs.bid) return null;
    const nb = env.nextBid();
    const at = b.pos + b.node.nodeSize;
    let tr = state.tr.insert(at, state.schema.nodes.paragraph.create({ bid: nb, head: 0 }));
    tr = tr.setSelection(TextSelection.create(tr.doc, at + 1));
    env.shadows.set(nb, new Shadow());
    return done(tr, [{ kind: 'block.add', target: { bid: nb }, payload: { kind: 'paragraph', after: b.node.attrs.bid } }]);
}

/**
 * Tab / Shift-Tab. A list item goes one level deeper or shallower. A heading
 * does too, with everything under it: a `block.move` of the heading to where
 * it already is, after the block before it, with `level_delta`, which the
 * applier applies to every heading in the range. Anything else: null.
 */
export function indent(state: EditorState, delta: 1 | -1): Gesture | null {
    const b = blockAt(state, state.selection.from);
    if (!b) return null;
    const kind = kindOf(b.node);
    const level = (b.node.attrs.level as number) + delta;
    if (!isLevelled(kind) || level < 1 || level > MAX_LEVEL) return null;
    const bid = b.node.attrs.bid as string;
    if (ITEMS.has(kind)) {
        const tr = state.tr.setNodeMarkup(b.pos, undefined, { ...b.node.attrs, level });
        return done(tr, [{ kind: 'block.set', target: { bid, path: 'level' }, payload: { value: level } }]);
    }
    const after = b.index > 0 ? state.doc.child(b.index - 1).attrs.bid as string : START;
    const g = moveBlock(state, bid, after, delta);
    if (!g) return null;
    const { anchor, head } = state.selection;
    g.tr.setSelection(TextSelection.create(g.tr.doc, anchor, head));
    return g;
}

/** The blocks a heading dominates in the document: itself and every following
 *  block until a heading at the same or a shallower level. */
function dominatedRange(doc: PMNode, index: number): [number, number] {
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

/** Move a block (with what it dominates) to directly after `afterBid` (or START). */
export function moveBlock(state: EditorState, bid: string, afterBid: string, levelDelta = 0): Gesture | null {
    const doc = state.doc;
    let idx = -1, afterIdx = -1;
    doc.forEach((n, _pos, i) => { if (n.attrs.bid === bid) idx = i; if (n.attrs.bid === afterBid) afterIdx = i; });
    if (idx < 0 || (afterBid !== START && afterIdx < 0)) return null;
    const [s, e] = dominatedRange(doc, idx);
    if (afterBid !== START && afterIdx >= s && afterIdx < e) return null;
    const nodes: PMNode[] = [];
    let start = 0, end = 0;
    doc.forEach((n, pos, i) => { if (i === s) start = pos; if (i === e - 1) end = pos + n.nodeSize; if (i >= s && i < e) nodes.push(n); });
    const moved = nodes.map(n => n.type.name === 'heading' && levelDelta
        ? n.type.create({ ...n.attrs, level: clampLevel(n.attrs.level + levelDelta) }, n.content, n.marks)
        : n);
    let tr = state.tr;
    if (afterBid === START ? s === 0 : afterIdx === s - 1) {
        // In place: one replace. Deleting first could empty the document,
        // which ProseMirror refills with a block of its own.
        tr = tr.replaceWith(start, end, moved);
    } else {
        tr = tr.delete(start, end);
        let insertAt = 0;
        if (afterBid !== START) tr.doc.forEach((n, pos) => { if (n.attrs.bid === afterBid) insertAt = pos + n.nodeSize; });
        tr = tr.insert(insertAt, moved);
    }
    return done(tr, [{ kind: 'block.move', target: { bid }, payload: { after: afterBid, level_delta: levelDelta } }]);
}

/** Delete a feature: its heading and own body; its child headings go up one
 *  level. The applier detaches its bindings and records them, so an undo
 *  re-attaches; this gesture sends no detach of its own. */
export function deleteFeature(state: EditorState, env: Env, fid: string): Gesture | null {
    const doc = state.doc;
    let idx = -1;
    doc.forEach((n, _p, i) => { if (n.attrs.bid === fid) idx = i; });
    if (idx < 0 || doc.child(idx).type.name !== 'heading') return null;
    // Own body: the heading and the blocks after it up to the next heading.
    let end = idx + 1;
    while (end < doc.childCount && doc.child(end).type.name !== 'heading') end++;
    // Never empty the tree by one gesture. The check is on the range, not on
    // the result: the schema says a document is `block+`, so ProseMirror refills
    // an emptied one with a default block and `childCount === 0` is never true.
    if (end - idx >= doc.childCount) { env.refuse?.('the only feature cannot be deleted'); return null; }
    const [, domEnd] = dominatedRange(doc, idx);
    const ops: OpBody[] = [{ kind: 'block.delete', target: { bid: fid }, payload: {} }];
    let tr = state.tr;
    // Promote descendant headings first (positions unchanged by attr edits).
    for (let i = end; i < domEnd; i++) {
        const n = doc.child(i);
        if (n.type.name === 'heading') {
            let pos = 0;
            doc.forEach((_n, p, j) => { if (j === i) pos = p; });
            tr = tr.setNodeMarkup(pos, undefined, { ...n.attrs, level: Math.max(1, n.attrs.level - 1) });
            ops.push({ kind: 'block.set', target: { bid: n.attrs.bid, path: 'level' }, payload: { value: Math.max(1, n.attrs.level - 1) } });
        }
    }
    let start = 0, stop = 0;
    doc.forEach((n, pos, i) => { if (i === idx) start = pos; if (i === end - 1) stop = pos + n.nodeSize; });
    tr = tr.delete(start, stop);
    for (let i = idx; i < end; i++) env.shadows.delete(doc.child(i).attrs.bid as string);
    return done(tr, ops);
}
