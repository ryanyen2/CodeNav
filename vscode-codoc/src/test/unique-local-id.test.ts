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
