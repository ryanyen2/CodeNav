/**
 * unique-local-id.test.ts — Step 5: a `localId` stays UNIQUE among live feature
 * headings. Copy-paste of a subtree and a heading split clone node attrs, so two live
 * headings can share one `localId`; the appendTransaction plugin re-mints the duplicate
 * (and clears its fid) so Step 3's Python local_id-keyed diff never emits two ops for
 * one feature. Headless: a real EditorState with the plugin, apply a cloning transaction.
 */
import { describe, it, expect } from 'vitest';
import { Node as PMNodeType } from '@tiptap/pm/model';
import { EditorState } from '@tiptap/pm/state';
import { codocSchema } from '../webview/tiptap/schema';
import { uniqueLocalIdPlugin } from '../webview/tiptap/feature-heading';
import { makeDoc, featureHeadingNode, paragraphNode, textToInlineRuns, PMNode } from '../state/pm-doc';

const schema = codocSchema();

function stateWithPlugin(json: PMNode): EditorState {
    const doc = PMNodeType.fromJSON(schema, json as never);
    return EditorState.create({
        schema, doc,
        plugins: [uniqueLocalIdPlugin(schema.nodes.featureHeading)],
    });
}

function headingLocalIds(state: EditorState): (string | null)[] {
    const ids: (string | null)[] = [];
    state.doc.forEach(node => {
        if (node.type.name === 'featureHeading') ids.push(node.attrs.localId as string | null);
    });
    return ids;
}

describe('Step 5 — unique localId among live headings', () => {
    it('re-mints the localId (and clears fid) on a pasted clone sharing one', () => {
        // Two live headings with the SAME localId + fid (a copy-paste of a subtree).
        const doc = makeDoc([
            featureHeadingNode({ fid: 'f-orig', localId: 'lid-dup', level: 0, retired: false, realized: true },
                textToInlineRuns('Original')),
            paragraphNode(textToInlineRuns('body')),
            featureHeadingNode({ fid: 'f-orig', localId: 'lid-dup', level: 0, retired: false, realized: true },
                textToInlineRuns('Pasted clone')),
            paragraphNode(textToInlineRuns('body')),
        ]);
        const state = stateWithPlugin(doc);
        // A no-op-ish docChanged transaction triggers the appendTransaction sweep.
        const tr = state.tr.insertText('!', 1);
        const next = state.apply(tr);

        const ids = headingLocalIds(next);
        expect(ids[0]).toBe('lid-dup');             // the FIRST keeps its id
        expect(ids[1]).not.toBe('lid-dup');         // the clone got a fresh id
        expect(ids[1]).toMatch(/^lid-/);
        // the clone's fid was cleared (it is a new node, not the original)
        const headings: PMNode[] = [];
        next.doc.forEach(n => { if (n.type.name === 'featureHeading') headings.push(n.toJSON() as PMNode); });
        expect(headings[0].attrs!.fid).toBe('f-orig');
        expect(headings[1].attrs!.fid).toBeNull();
    });

    it('leaves already-unique localIds untouched (no transaction loop)', () => {
        const doc = makeDoc([
            featureHeadingNode({ fid: 'f-a', localId: 'lid-a', level: 0, retired: false, realized: true },
                textToInlineRuns('A')),
            featureHeadingNode({ fid: 'f-b', localId: 'lid-b', level: 0, retired: false, realized: true },
                textToInlineRuns('B')),
        ]);
        const state = stateWithPlugin(doc);
        const next = state.apply(state.tr.insertText('x', 1));
        expect(headingLocalIds(next)).toEqual(['lid-a', 'lid-b']);  // unchanged
    });
});

/**
 * WHICH of a colliding pair is the clone is not answered by document order.
 *
 * Pasting a copied heading ABOVE its original puts the copy first, so keeping
 * "the first" handed the copy the original's identity and re-minted the ORIGINAL
 * as a brand-new feature — its history, bindings and code attribution silently
 * transferred to a duplicate the user had just made, and the real feature started
 * over as if it had never existed.
 */
describe('which duplicate is the clone — the one that ARRIVED', () => {
    const heading = (localId: string, fid: string | null, title: string) =>
        featureHeadingNode({ fid, localId, level: 0, retired: false, realized: true },
            textToInlineRuns(title));

    it('keeps the identity on the node that was already in the document', () => {
        const state = stateWithPlugin(makeDoc([
            heading('lid-1', 'f-orig', 'Original'),
            paragraphNode(textToInlineRuns('body')),
        ]));
        // Paste a copy of that heading ABOVE the original (position 0).
        const clone = PMNodeType.fromJSON(schema, heading('lid-1', 'f-orig', 'Original') as never);
        const next = state.apply(state.tr.insert(0, clone));

        const ids = headingLocalIds(next);
        const fids: (string | null)[] = [];
        next.doc.forEach(n => { if (n.type.name === 'featureHeading') fids.push(n.attrs.fid as string | null); });

        expect(ids[1]).toBe('lid-1');      // the original, now second, keeps its identity
        expect(fids[1]).toBe('f-orig');    // and its store id
        expect(ids[0]).not.toBe('lid-1');  // the pasted copy is the new node
        expect(fids[0]).toBeNull();
    });

    it('falls back to document order when the batch inserted both (a pasted subtree)', () => {
        const state = stateWithPlugin(makeDoc([heading('lid-keep', 'f-a', 'Kept')]));
        const a = PMNodeType.fromJSON(schema, heading('lid-2', null, 'One') as never);
        const b = PMNodeType.fromJSON(schema, heading('lid-2', null, 'Two') as never);
        const size = state.doc.content.size;
        const next = state.apply(state.tr.insert(size, [a, b]));

        const ids = headingLocalIds(next);
        expect(ids[1]).toBe('lid-2');       // first of the pasted pair keeps the id
        expect(ids[2]).not.toBe('lid-2');   // the second is re-minted
        expect(new Set(ids).size).toBe(ids.length);
    });
});
