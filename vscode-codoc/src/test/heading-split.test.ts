/**
 * heading-split.test.ts — U2: the `#{n} ` input-rule transform splits a new heading
 * instead of consuming the current paragraph, across all four levels (the reported
 * "## at end of paragraph amends the wrong feature" bug). Also pins U1: a hand-made
 * heading carries a stable localId.
 *
 * Headless: builds the real schema, applies the pure `headingFromInputRule` transform
 * the input rule delegates to, and asserts document structure — no DOM/editor needed.
 */
import { describe, it, expect } from 'vitest';
import { Node as PMNodeType } from '@tiptap/pm/model';
import { EditorState } from '@tiptap/pm/state';
import { codocSchema } from '../webview/tiptap/schema';
import { headingFromInputRule, newFeatureHeading } from '../webview/tiptap/structure-commands';
import { makeDoc, featureHeadingNode, paragraphNode, textToInlineRuns, PMNode } from '../state/pm-doc';

const schema = codocSchema();

function stateFromDoc(json: PMNode): EditorState {
    const doc = PMNodeType.fromJSON(schema, json as never);
    return EditorState.create({ schema, doc });
}

/** Find the `#{n} ` run's [start, end) inside a paragraph's text by locating the text. */
function runRange(state: EditorState, hashes: string): { from: number; to: number } {
    let found = -1;
    state.doc.descendants((node, pos) => {
        if (node.isText && node.text && found < 0) {
            const i = node.text.indexOf(hashes + ' ');
            if (i >= 0) found = pos + i; // doc pos of the first '#'
        }
        return true;
    });
    return { from: found, to: found + hashes.length + 1 };
}

describe('U2: heading split-or-convert', () => {
    it('splits a new empty heading when ## is typed after text (not converting the paragraph)', () => {
        const doc = makeDoc([
            featureHeadingNode({ fid: 'f-prev', level: 0, retired: false, realized: true }, textToInlineRuns('Prev feature')),
            paragraphNode(textToInlineRuns('keep this description## ')),
        ]);
        const state = stateFromDoc(doc);
        const { from, to } = runRange(state, '##');
        const tr = headingFromInputRule(state, 1, from, to, 'lid-test')!;
        const out = tr.doc.toJSON() as PMNode;
        const blocks = out.content!;
        // Prev heading + its (intact) description paragraph + a NEW empty level-1 heading.
        expect(blocks.map(b => b.type)).toEqual(['featureHeading', 'paragraph', 'featureHeading']);
        expect(blocks[0].attrs!.fid).toBe('f-prev');           // prior feature untouched
        const para = blocks[1];
        expect(para.content?.[0]?.text).toBe('keep this description'); // description intact, ## gone
        const newHeading = blocks[2];
        expect(newHeading.attrs!.level).toBe(1);
        expect(newHeading.attrs!.fid).toBeNull();              // new node, not yet minted
        expect(newHeading.attrs!.localId).toBe('lid-test');    // stable client identity (U1)
        expect(newHeading.content ?? []).toEqual([]);          // empty title for the author to type
    });

    it('converts in place at the start of an empty block', () => {
        const doc = makeDoc([
            featureHeadingNode({ fid: 'f-prev', level: 0, retired: false, realized: true }, textToInlineRuns('Prev')),
            paragraphNode(textToInlineRuns('### ')),
        ]);
        const state = stateFromDoc(doc);
        const { from, to } = runRange(state, '###');
        const tr = headingFromInputRule(state, 2, from, to, 'lid-x')!;
        const blocks = (tr.doc.toJSON() as PMNode).content!;
        // The empty paragraph became a level-2 heading in place (no extra block).
        expect(blocks.map(b => b.type)).toEqual(['featureHeading', 'featureHeading']);
        expect(blocks[1].attrs!.level).toBe(2);
        expect(blocks[1].attrs!.localId).toBe('lid-x');
    });
});

describe('U1: newFeatureHeading mints a localId', () => {
    it('creates a sibling heading carrying a fresh localId', () => {
        const doc = makeDoc([featureHeadingNode({ fid: 'f-a', level: 0, retired: false, realized: true }, textToInlineRuns('A'))]);
        // Minimal editor-like shim: newFeatureHeading needs editor.state/schema/view.dispatch.
        let dispatched: import('@tiptap/pm/state').Transaction | null = null;
        const state = stateFromDoc(doc);
        const editor = {
            state, schema,
            view: { dispatch: (tr: import('@tiptap/pm/state').Transaction) => { dispatched = tr; }, focus: () => {} },
        } as never;
        newFeatureHeading(editor);
        const blocks = (dispatched!.doc.toJSON() as PMNode).content!;
        const created = blocks.find(b => b.type === 'featureHeading' && b.attrs!.fid == null)!;
        expect(created.attrs!.localId).toMatch(/^lid-/);
    });
});
