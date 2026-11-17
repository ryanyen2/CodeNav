/**
 * paste.ts: what a paste brings, as pieces for `pasteBlocks` (commands.ts).
 *
 * Into prose: plain text that reads as Markdown with some structure (a
 * heading, a list item, a table, a fence, or an inline mark) becomes those
 * blocks and marks, read by the port of the store's own reader
 * (calc/markdown.ts). Anything else, and anything copied from this editor, is
 * taken from the slice ProseMirror parsed from the clipboard: its blocks, with
 * their kinds, levels and marks. Every text is NFC, as the applier records it.
 * Into a table, a diagram or a code block the clipboard's plain text goes in
 * as it is (editor.ts).
 */
import type { Node as PMNode, Slice } from '@tiptap/pm/model';

import { LAYOUT_KEY, fenceLayout, parseBlocks, parseInline } from '../calc/markdown';
import { SOURCE, isOneLine, kindOf, nfc, type Span } from '../calc/projection';
import { SENT_MARKS, markAttrs } from '../calc/recorder';
import type { Piece } from './commands';
import { cardFence } from './schema';

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
    return parseBlocks(nfc(md), true, false).map((b): Piece => {
        if (isOneLine(b.kind)) return { kind: b.kind, level: b.level, ...parseInline(b.text), content: {} };
        if (b.kind === 'code') return { kind: 'code', level: 0, text: b.text, spans: [], content: typeof b.content.lang === 'string' ? { lang: b.content.lang } : {} };
        if (SOURCE.has(b.kind)) return { kind: b.kind, level: 0, text: b.text, spans: [], content: {} };
        // Any other kind's `codoc:` fence keeps its kind and every field, and its
        // `$layout` when it has a valid one; pasteBlocks splits the fields.
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
