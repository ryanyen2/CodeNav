/**
 * authoring-cues.test.ts — the editor tells you what your text became.
 *
 * Descriptions carry markdown-native signals into realize directives. `**bold**`
 * renders bold via StarterKit, so it says so. `[label](https://…)` became a
 * `Consult:` instruction the agent WebFetches — and said nothing at all. And an
 * empty document said nothing either, which left `/` and `@` undiscoverable.
 */
import { describe, it, expect } from 'vitest';
import { Node as PMNodeType } from '@tiptap/pm/model';
import { EditorState, TextSelection } from '@tiptap/pm/state';
import { codocSchema } from '../webview/tiptap/schema';
import { CONSULT_RE, consultSpans } from '../webview/tiptap/consult-decorations';
import {
    buildPlaceholders, placeholderFor, isEmptyDocument,
    EMPTY_DOC_HINT, EMPTY_BLOCK_HINT, EMPTY_TITLE_HINT,
} from '../webview/tiptap/placeholder';
import type { PMNode } from '../state/pm-doc';

const schema = codocSchema();

function docOf(blocks: unknown[]): PMNode {
    return { type: 'doc', content: blocks } as unknown as PMNode;
}
const heading = (text: string, fid = 'f-1') => ({
    type: 'featureHeading', attrs: { fid, localId: `l-${fid}`, level: 0 },
    content: text ? [{ type: 'text', text }] : [],
});
const para = (text: string, owner = 'f-1') => ({
    type: 'paragraph', attrs: { ownerId: owner },
    content: text ? [{ type: 'text', text }] : [],
});
const state = (blocks: unknown[]) =>
    EditorState.create({ schema, doc: PMNodeType.fromJSON(schema, docOf(blocks) as never) });

/** The pattern the daemon uses, transcribed from codoc/codoc_file/parse.py. */
// A hand-transcribed copy of `parse.py:_LINK_RE` — nothing can import a Python regex
// here. The other half of the guard lives in Python
// (tests/codoc_file/test_steering.py::test_the_link_pattern_is_pinned_…), which pins the
// literal so a change there cannot silently pass while this copy drifts.
const PY_LINK_RE = /\[([^\]]*)\]\((https?:\/\/[^)\s]+)\)/g;

describe('the consult cue matches what the daemon reads', () => {
    const cases = [
        'see [the spec](https://example.com/spec) first',
        'plain prose with no link at all',
        'a [ref](codoc:auth.py#login) is not a consult',
        'both [a ref](codoc:x.py#y) and [a page](https://example.com/p)',
        '[](https://example.com/bare-label)',
        'two [one](https://a.example/1) and [two](https://b.example/2)',
        'insecure [page](http://example.com/x) still counts',
        'not a link: https://example.com/naked',
        '[unclosed](https://example.com',
    ];

    it.each(cases)('agrees with the parser on %j', text => {
        // The whole contract. A cue that highlights a character more than the
        // daemon reads is a lie about what the agent was told.
        const s = state([heading('F'), para(text)]);
        const found = consultSpans(s.doc).map(x => `${x.label}|${x.url}`);
        PY_LINK_RE.lastIndex = 0;
        const expected = [...text.matchAll(PY_LINK_RE)].map(m => `${m[1]}|${m[2]}`);
        expect(found).toEqual(expected);
    });

    it('covers exactly the markdown, not the surrounding prose', () => {
        const text = 'see [the spec](https://example.com/spec) first';
        const s = state([heading('F'), para(text)]);
        const [span] = consultSpans(s.doc);
        const covered = s.doc.textBetween(span.from, span.to);
        expect(covered).toBe('[the spec](https://example.com/spec)');
    });

    it('finds links across separate paragraphs', () => {
        const s = state([
            heading('F'),
            para('first [a](https://a.example/1)'),
            para('second [b](https://b.example/2)'),
        ]);
        expect(consultSpans(s.doc).map(x => x.url))
            .toEqual(['https://a.example/1', 'https://b.example/2']);
    });

    it('is not left stateful between scans by the global regex', () => {
        // A /g regex carries lastIndex. Reusing it without resetting would make
        // the second document silently miss its first link.
        const s = state([heading('F'), para('[a](https://a.example/1)')]);
        expect(consultSpans(s.doc)).toHaveLength(1);
        expect(consultSpans(s.doc)).toHaveLength(1);
        expect(CONSULT_RE.lastIndex).toBe(0);
    });
});

describe('what an empty document says', () => {
    it('recognises a document with nothing authored in it', () => {
        expect(isEmptyDocument(state([para('')]))).toBe(true);
        expect(isEmptyDocument(state([heading('')]))).toBe(false);   // a heading IS authoring
        expect(isEmptyDocument(state([para('some prose')]))).toBe(false);
    });

    it('offers the first action, since the tree pane cannot', () => {
        // The nav's message says "run codoc init", which a hub contributor cannot
        // do — and it never mentions the two authoring affordances that exist.
        const decos = buildPlaceholders(state([para('')])).find();
        expect(decos).toHaveLength(1);
        expect(EMPTY_DOC_HINT).toMatch(/\//);
        expect(EMPTY_DOC_HINT).toMatch(/⌘K/);
    });
});

describe('what an empty description says', () => {
    function withCaretIn(blocks: unknown[], blockIndex: number): EditorState {
        const s = state(blocks);
        let pos = 0, i = 0;
        s.doc.forEach((node, p) => { if (i++ === blockIndex) pos = p + 1; });
        return s.apply(s.tr.setSelection(TextSelection.create(s.doc, pos)));
    }

    it('prompts on the block holding the caret', () => {
        expect(placeholderFor(withCaretIn([heading('Auth'), para('')], 1))?.text)
            .toBe(EMPTY_BLOCK_HINT);
    });

    it('names a heading differently from prose', () => {
        expect(placeholderFor(withCaretIn([heading(''), para('x')], 0))?.text)
            .toBe(EMPTY_TITLE_HINT);
    });

    it('prompts on ONE block, not every empty one', () => {
        // Prompting everywhere at once turns a document into a form.
        const s = withCaretIn([heading('Auth'), para(''), heading('Db', 'f-2'), para('', 'f-2')], 1);
        expect(buildPlaceholders(s).find()).toHaveLength(1);
    });

    it('says nothing on a block that already has prose', () => {
        expect(placeholderFor(withCaretIn([heading('Auth'), para('written')], 1))).toBeNull();
    });

    it('says nothing while text is selected', () => {
        // During a selection the reader is acting on text, not looking for a prompt.
        const s = state([heading('Auth'), para('some prose here')]);
        const sel = s.apply(s.tr.setSelection(TextSelection.create(s.doc, 3, 8)));
        expect(placeholderFor(sel)).toBeNull();
    });

    it('never puts text in the document', () => {
        // Placeholders are decorations with CSS ::before content. Text in the doc
        // would serialize into tree.codoc and settle as if the user typed it.
        const s = withCaretIn([heading('Auth'), para('')], 1);
        buildPlaceholders(s);
        expect(s.doc.textContent).toBe('Auth');
    });
});
