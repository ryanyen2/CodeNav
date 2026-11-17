/**
 * paste.ts — what a paste brings, as pieces for `pasteBlocks` (commands.ts).
 *
 * Plain text that reads as Markdown with some structure (a heading, a list
 * item, a table, a fence, or an inline mark) becomes those blocks and marks,
 * read by the port of the store's own reader (calc/markdown.ts). Anything
 * else, and anything copied from this editor, is taken from the slice
 * ProseMirror parsed from the clipboard: its blocks, with their kinds, levels
 * and marks. Every text is NFC, as the applier records it.
 */
import type { Node as PMNode, Slice } from '@tiptap/pm/model';

import { parseBlocks, parseInline } from '../calc/markdown';
import { SOURCE, isOneLine, kindOf, nfc, type Span } from '../calc/projection';
import { SENT_MARKS, markAttrs } from '../calc/recorder';
import type { Piece } from './commands';

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

export function piecesFromMarkdown(md: string): Piece[] {
    return parseBlocks(nfc(md)).map((b): Piece => {
        if (isOneLine(b.kind)) return { kind: b.kind, level: b.level, ...parseInline(b.text), content: {} };
        if (b.kind === 'code') return { kind: 'code', level: 0, text: b.text, spans: [], content: typeof b.content.lang === 'string' ? { lang: b.content.lang } : {} };
        if (SOURCE.has(b.kind)) return { kind: b.kind, level: 0, text: b.text, spans: [], content: {} };
        // A plugin kind's `codoc:` fence: its JSON, kept visible in a code block.
        return { kind: 'code', level: 0, text: JSON.stringify(b.content, null, 2), spans: [], content: { lang: 'json' } };
    });
}

/** The slice's blocks. A plugin block is left out: the editor does not author one. */
export function piecesFromSlice(slice: Slice): Piece[] {
    const out: Piece[] = [];
    slice.content.forEach(node => {
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
