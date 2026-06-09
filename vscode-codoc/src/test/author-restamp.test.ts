/**
 * author-restamp.test.ts — guards U2b's per-span ink re-stamp ("hand to AI" / "take
 * back", whole-doc-editor.ts `setSpanMode`).
 *
 * The `author` mark is declared `excludes: ''` so two DIFFERENT authors' spans never
 * merge into one. The trap: that also means a bare `addMark` over an already-stamped
 * span STACKS a second author mark instead of replacing it. `setSpanMode` must
 * `removeMark` the old author mark before adding the new one. This test pins both the
 * `excludes` spec and the replace-not-stack behavior so a refactor can't quietly
 * regress it. Pure headless ProseMirror — no editor view / DOM (fits the node harness).
 */
import { describe, it, expect } from 'vitest';
import { Transform } from '@tiptap/pm/transform';
import { Node as PMModelNode, MarkType } from '@tiptap/pm/model';
import { codocSchema } from '../webview/tiptap/schema';

const schema = codocSchema();
const authorType = schema.marks.author as MarkType;

/** A doc `[heading "T", paragraph "hello"@author{mode}]` and the "hello" range. */
function docWithStampedText(mode: 'pen' | 'pencil'): { doc: PMModelNode; from: number; to: number } {
    const doc = schema.nodeFromJSON({
        type: 'doc',
        content: [
            { type: 'featureHeading', attrs: { fid: 'f-1', level: 0 }, content: [{ type: 'text', text: 'T' }] },
            {
                type: 'paragraph',
                content: [{ type: 'text', text: 'hello', marks: [{ type: 'author', attrs: { authorId: 'h', role: 'human', mode, ts: 0 } }] }],
            },
        ],
    });
    let from = -1;
    let to = -1;
    doc.descendants((node, pos) => {
        if (node.isText && node.text === 'hello') { from = pos; to = pos + node.nodeSize; }
    });
    return { doc, from, to };
}

function authorModesOnHello(doc: PMModelNode): string[] {
    let modes: string[] = [];
    doc.descendants(node => {
        if (node.isText && node.text === 'hello') {
            modes = node.marks.filter(m => m.type.name === 'author').map(m => m.attrs.mode as string);
        }
    });
    return modes;
}

describe('U2b — per-span ink re-stamp', () => {
    it('the author mark excludes nothing (distinct authors never merge — the reason for the trap)', () => {
        expect(authorType.spec.excludes).toBe('');
    });

    it('removeMark + addMark REPLACES the author mark (one mark, new mode)', () => {
        const { doc, from, to } = docWithStampedText('pen');
        const pencil = authorType.create({ authorId: 'h', role: 'human', mode: 'pencil', ts: 1 });
        const tr = new Transform(doc).removeMark(from, to, authorType).addMark(from, to, pencil);
        expect(authorModesOnHello(tr.doc)).toEqual(['pencil']); // exactly one, replaced
    });

    it('a bare addMark WOULD stack two author marks — proving the removeMark is load-bearing', () => {
        const { doc, from, to } = docWithStampedText('pen');
        const pencil = authorType.create({ authorId: 'h', role: 'human', mode: 'pencil', ts: 1 });
        const tr = new Transform(doc).addMark(from, to, pencil); // NO removeMark
        expect(authorModesOnHello(tr.doc)).toEqual(['pen', 'pencil']); // stacked — the bug
    });
});
