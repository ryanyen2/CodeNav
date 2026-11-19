/**
 * paste.ts: what a paste brings, and the ops that put it on the page.
 *
 * Into prose: plain text that reads as Markdown with some structure (a
 * heading, a list item, a table, a fence, or an inline mark) becomes those
 * blocks and marks, read by the port of the store's own reader
 * (calc/markdown.ts). Anything else, and anything copied from this editor, is
 * taken from the slice ProseMirror parsed from the clipboard: its blocks, with
 * their kinds, levels and marks. Every text is NFC, as the applier records it.
 * Either way the paste arrives as pieces, and `pasteBlocks` records them.
 * Into a table, a diagram or a code block the clipboard's plain text goes in
 * as it is (editor.ts).
 */
import type { Node as PMNode, Slice } from '@tiptap/pm/model';
import { type EditorState, TextSelection } from '@tiptap/pm/state';

import { inlineNodes } from '../calc/adopt';
import { LAYOUT_KEY, fenceLayout, parseBlocks, parseInline } from '../calc/markdown';
import { SOURCE, START, isBuiltin, isOneLine, kindOf, nfc, pathOf, type CharRef, type Layout, type OpBody,
         type Span } from '../calc/projection';
import { SENT_MARKS, markAttrs, scalarOffset } from '../calc/recorder';
import { Shadow } from '../calc/shadow';
import { deleteSelection, done, isLevelled, lineAttrs, splitAt, type Env, type Gesture } from './commands';
import { cardFence, clampLevel } from './schema';

/** One block as it arrives in a paste: inline Markdown already read into
 *  plain `text` and `spans` of the marks a person sets (`SENT_MARKS`) for a
 *  heading or prose kind, raw text for a source
 *  kind, whose `content` holds `lang` or `derived`. A block of any other kind
 *  has every field of its `codoc:` fence in `content`, Text fields as inline
 *  Markdown, and the fence's `layout` when it carried one. */
export interface Piece { kind: string; level: number; text: string; spans: Span[]; content: Record<string, unknown>; layout?: Layout }

/** The pieces of a paste, or null when the slice holds inline words only,
 *  which ProseMirror pastes and the recorder reads. */
export function clipboardPieces(slice: Slice, plain: string, html: string): Piece[] | null {
    if (plain.trim() && !html.includes('data-pm-slice')) {
        const md = piecesFromMarkdown(plain);
        if (md.some(p => p.kind !== 'paragraph' || p.spans.length)) return md;
    }
    let blocks = false;
    slice.content.forEach(n => { if (n.isBlock) blocks = true; });
    return blocks ? piecesFromSlice(slice) : null;
}

/** Markdown as pieces. A line of prose stays a block of its own: a paste keeps
 *  the line breaks it brought, even where Markdown would join two lines. */
export function piecesFromMarkdown(md: string): Piece[] {
    return parseBlocks(nfc(md)).map((b): Piece => {
        if (isOneLine(b.kind)) return { kind: b.kind, level: b.level, ...parseInline(b.text), content: {} };
        if (b.kind === 'code') return { kind: 'code', level: 0, text: b.text, spans: [], content: typeof b.content.lang === 'string' ? { lang: b.content.lang } : {} };
        if (SOURCE.has(b.kind)) return { kind: b.kind, level: 0, text: b.text, spans: [], content: {} };
        // Any other kind's `codoc:` fence keeps its kind and every field, and its
        // `$layout` when it has a valid one; `pasteBlocks` splits the fields.
        const { [LAYOUT_KEY]: _layout, ...content } = b.content;
        return { kind: b.kind, level: 0, text: '', spans: [], content, layout: fenceLayout(b.content) ?? undefined };
    });
}

/** The slice's blocks. A card is read as its `codoc:` fence. */
export function piecesFromSlice(slice: Slice): Piece[] {
    const out: Piece[] = [];
    slice.content.forEach(node => {
        if (node.type.name === 'kindBlock') { out.push(...piecesFromMarkdown(cardFence(node))); return; }
        if (!node.isTextblock) return;
        const kind = kindOf(node);
        if (SOURCE.has(kind)) out.push({ kind, level: 0, text: nfc(node.textContent), spans: [], content: node.attrs.lang ? { lang: node.attrs.lang } : {} });
        else out.push({ kind, level: (node.attrs.level as number | undefined) ?? 0, ...inline(node), content: {} });
    });
    return out;
}

/** A one-line block's text and its marks as spans (scalar offsets). */
function inline(node: PMNode): { text: string; spans: Span[] } {
    let text = '';
    let at = 0;
    const spans: Span[] = [];
    node.forEach(child => {
        const t = nfc(child.text ?? '').replace(/\n/g, ' ');
        const len = Array.from(t).length;
        for (const m of child.marks) {
            if (!SENT_MARKS.has(m.type.name)) continue;
            const attrs = markAttrs(m.type.name, m.attrs);
            const open = spans.find(s => s.end === at && s.kind === m.type.name && JSON.stringify(s.attrs) === JSON.stringify(attrs));
            if (open) open.end = at + len;
            else spans.push({ kind: m.type.name, attrs, start: at, end: at + len });
        }
        text += t;
        at += len;
    });
    return { text, spans };
}

/** A block's level as the store keeps it: clamped for a heading or a list item, 0 otherwise. */
const levelOf = (kind: string, level: unknown): number => (isLevelled(kind) ? clampLevel(Number(level)) : 0);

/** The position right after the top-level block with this id, or 0 for START. */
function blockEnd(doc: PMNode, bid: string): number {
    let end = bid === START ? 0 : doc.content.size;
    doc.forEach((n, p) => { if (n.attrs.bid === bid) end = p + n.nodeSize; });
    return end;
}

/**
 * A paste, as ops. The selection is deleted first (`deleteSelection`). In a
 * source block the pieces' text lands as lines. Otherwise a first piece that
 * is a heading or prose joins the caret's block as typed text would, with its
 * marks, and an empty paragraph takes that piece's kind; every further piece
 * is a block of its own after it. When further pieces follow, the words after
 * the caret follow the last of them, as in any editor: the block splits at the
 * caret (with the caret at its start, the pieces go before it), and a last
 * piece that is a heading or prose is typed at the start of those words, which
 * keep their block's kind. The person is the author of all of it. Pieces are
 * NFC, as the applier records text.
 */
export function pasteBlocks(state: EditorState, env: Env, pieces: Piece[]): Gesture | null {
    if (!pieces.length) return null;
    const schema = state.schema;
    const ops: OpBody[] = [];
    const cids: string[] = [];
    /** Insert `text` at offset `at` of a field under one new client id, and
     *  mark the characters it mints with each sent span. */
    const write = (target: { bid: string; path: string }, shadow: Shadow, at: number, text: string, spans: Span[]): void => {
        const cid = env.nextCid();
        ops.push({ kind: 'text.insert', target, payload: { after: shadow.anchorBefore(at), text } });
        cids.push(cid);
        shadow.insert(at, text, cid);
        for (const s of spans) {
            if (s.end <= s.start) continue;
            ops.push({ kind: 'text.mark', target, payload: {
                kind: s.kind, attrs: s.attrs, start: ['$cid', cid, s.start] as CharRef, end: ['$cid', cid, s.end - 1] as CharRef, on: true } });
        }
    };
    let tr = state.tr;
    if (!state.selection.empty) {
        const cut = deleteSelection(state, env, true);
        if (!cut) return null;
        ops.push(...cut.ops);
        tr = cut.tr;
    }
    const $at = tr.doc.resolve(tr.selection.from);
    const host = $at.depth >= 1 ? $at.node(1) : null;
    const bid = host?.attrs.bid as string | undefined;
    const shadow = bid ? env.shadows.get(bid) : undefined;
    if (!host || !bid || !shadow) return ops.length ? done(tr, ops) : null;
    const hostKind = kindOf(host);
    let off = scalarOffset(host, $at.parentOffset);
    let caret = tr.selection.from;

    if (SOURCE.has(hostKind)) {
        const text = pieces.map(p => p.text).join('\n');
        if (text) { write({ bid, path: 'source' }, shadow, off, text, []); tr = tr.insertText(text, caret); }
        return done(tr.setSelection(TextSelection.create(tr.doc, caret + text.length)), ops, cids);
    }

    const rest = pieces.slice();
    if (isOneLine(rest[0].kind)) {
        const first = rest.shift()!;
        const wasEmpty = shadow.length === 0;
        if (first.text) {
            write({ bid, path: 'text' }, shadow, off, first.text, first.spans);
            // On the page the text takes the marks the store gives an insert
            // after the character before the caret (a bold it ends, a mark it
            // is inside; docs/model.md, "Marks"), then its own.
            const at = (k: number) => caret + Array.from(first.text).slice(0, k).join('').length;
            tr = tr.insert(caret, schema.text(first.text, off > 0 ? tr.doc.resolve(caret).marks() : []));
            for (const s of first.spans) tr = tr.addMark(at(s.start), at(s.end), schema.marks[s.kind].create(s.attrs));
            off += Array.from(first.text).length;
            caret += first.text.length;
        }
        if (wasEmpty && hostKind === 'paragraph' && first.kind !== 'paragraph') {
            const lvl = levelOf(first.kind, first.level);
            ops.push({ kind: 'block.set', target: { bid, path: 'kind' }, payload: lvl ? { value: first.kind, level: lvl } : { value: first.kind } });
            tr = tr.setNodeMarkup($at.before(1), schema.nodes[first.kind], lineAttrs(first.kind, bid, host.attrs.head as number, lvl));
        }
    }

    // The words after the caret go after the last piece, in `tail`: the
    // caret's block split there, or the whole block when the caret is at its start.
    let after = bid;
    let tail: { bid: string; shadow: Shadow; kind: string; level: number } | null = null;
    if (rest.length && off < shadow.length) {
        if (off === 0) {
            after = $at.index(0) > 0 ? tr.doc.child($at.index(0) - 1).attrs.bid as string : START;
            tail = { bid, shadow, kind: hostKind, level: levelOf(hostKind, host.attrs.level) };
        } else {
            const split = splitAt(tr, env, host, caret, off);
            tr = split.tr;
            ops.push(split.op);
            const kind = hostKind === 'heading' ? 'paragraph' : hostKind;
            tail = { bid: split.nid, shadow: env.shadows.get(split.nid)!, kind, level: levelOf(kind, host.attrs.level) };
        }
    }
    // A last piece of the tail's kind and level is typed at its start; any other is a block before it.
    const lp = rest[rest.length - 1];
    const last = tail && lp && lp.kind === tail.kind && levelOf(lp.kind, lp.level) === tail.level ? rest.pop()! : null;

    // The rest arrive as blocks, each after the one before. Each position is
    // read from the document as it stands, never mapped: a position computed
    // before the cut and mapped through a mapping that includes the cut is
    // shifted twice.
    let end = caret;
    for (const p of rest) {
        const nb = env.nextBid();
        if (!isBuiltin(p.kind)) {
            // A card. Its fields split by a layout: the fence's, else the one the
            // projection says the kind registers, else a block of that kind on the
            // page. The add records the layout it was split by, as
            // ingest.block_bodies does; knowing none, it sends every field as JSON
            // and no layout.
            const proj = env.projection?.();
            const known = p.layout ?? proj?.kinds?.[p.kind] ?? proj?.blocks.find(b => b.kind === p.kind && b.layout)?.layout;
            const split = known ?? { text: [], flow: true, export: true };
            const json = Object.fromEntries(Object.entries(p.content).filter(([k]) => !split.text.includes(k)));
            const payload: Record<string, unknown> = { kind: p.kind, after, ...(Object.keys(json).length ? { content: json } : {}),
                                                       ...(known ? { layout: known } : {}) };
            ops.push({ kind: 'block.add', target: { bid: nb }, payload });
            const fields: Record<string, unknown> = { ...json };
            const shadows = new Map<string, Shadow>();
            for (const path of split.text) {
                const md = p.content[path];
                const t = typeof md === 'string' ? parseInline(md) : { text: '', spans: [] };
                const sh = new Shadow();
                shadows.set(path, sh);
                fields[path] = { text: t.text, runs: [], spans: t.spans };
                if (t.text) write({ bid: nb, path }, sh, 0, t.text, t.spans);
            }
            // The card reads these shadows, not the fields' runs, which this page has not seen yet.
            env.cards?.set(nb, { from: fields, shadows });
            tr = tr.insert(blockEnd(tr.doc, after), schema.nodes.kindBlock.create({ bid: nb, head: 0, kind: p.kind, fields, layout: split }));
            after = nb;
            continue;
        }
        const lvl = levelOf(p.kind, p.level);
        const source = SOURCE.has(p.kind);
        const payload: Record<string, unknown> = { kind: p.kind, after };
        if (lvl) payload.level = lvl;
        if (source && Object.keys(p.content).length) payload.content = p.content;
        ops.push({ kind: 'block.add', target: { bid: nb }, payload });
        const node = source
            ? schema.nodes.source.create({ bid: nb, head: 0, kind: p.kind, lang: p.content.lang ?? null, derived: p.content.derived === true },
                                         p.text ? schema.text(p.text) : undefined)
            : schema.nodes[p.kind].create(lineAttrs(p.kind, nb, 0, lvl), inlineNodes(schema, p.text, p.spans));
        const sh = new Shadow();
        env.shadows.set(nb, sh);
        if (p.text) write({ bid: nb, path: pathOf(p.kind) }, sh, 0, p.text, p.spans);
        const pos = blockEnd(tr.doc, after);
        tr = tr.insert(pos, node);
        end = pos + node.nodeSize - 1;
        after = nb;
    }
    if (last && tail) {
        // The tail stands right after the last block added.
        end = blockEnd(tr.doc, after) + 1;
        if (last.text) {
            write({ bid: tail.bid, path: 'text' }, tail.shadow, 0, last.text, last.spans);
            tr = tr.insert(end, inlineNodes(schema, last.text, last.spans));
            end += last.text.length;
        }
    }
    tr = tr.setSelection(TextSelection.create(tr.doc, Math.min(end, tr.doc.content.size)));
    return done(tr, ops, cids);
}
