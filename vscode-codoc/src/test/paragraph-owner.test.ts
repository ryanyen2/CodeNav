/**
 * paragraph-owner.test.ts — invariant I2 at the plugin level: prose is anchored to its
 * feature by identity (`ownerId`), and that anchor survives real ProseMirror transforms.
 *
 * Two layers:
 *   • the pure `paragraphOwnerFills` logic (state/pm-doc) — what owner an un-owned
 *     paragraph should adopt, over plain JSON;
 *   • the live `keepParagraphOwnerPlugin` against a real EditorState — filling nulls,
 *     preserving owners across a split, and (the anti-steal invariant) NEVER re-owning a
 *     paragraph that is already owned, so a heading inserted above owned prose can't take
 *     it. Headless, mirroring unique-local-id.test.ts.
 */
import { describe, it, expect } from 'vitest';
import { Node as PMNodeType } from '@tiptap/pm/model';
import { EditorState } from '@tiptap/pm/state';
import { codocSchema } from '../webview/tiptap/schema';
import { keepParagraphOwnerPlugin } from '../webview/tiptap/paragraph-owner';
import {
    makeDoc, featureHeadingNode, paragraphNode, textToInlineRuns, paragraphOwnerFills, PMNode,
} from '../state/pm-doc';

const schema = codocSchema();
const head = (fid: string, level = 0) =>
    featureHeadingNode({ fid, localId: null, level, retired: false, realized: true }, textToInlineRuns(fid));

function stateWith(json: PMNode): EditorState {
    const doc = PMNodeType.fromJSON(schema, json as never);
    return EditorState.create({ schema, doc, plugins: [keepParagraphOwnerPlugin()] });
}
/** ownerId of every paragraph, in doc order. */
function paraOwners(state: EditorState): (string | null)[] {
    const out: (string | null)[] = [];
    state.doc.forEach(n => { if (n.type.name === 'paragraph') out.push((n.attrs.ownerId as string | null) ?? null); });
    return out;
}

describe('paragraphOwnerFills — pure attribution logic', () => {
    it('fills an un-owned paragraph with the nearest heading identity', () => {
        const doc = makeDoc([head('f-a'), paragraphNode(textToInlineRuns('body'))]);
        expect(paragraphOwnerFills(doc)).toEqual(new Map([[1, 'f-a']]));
    });
    it('leaves an already-owned paragraph alone', () => {
        const doc = makeDoc([head('f-a'), paragraphNode(textToInlineRuns('body'), 'f-other')]);
        expect(paragraphOwnerFills(doc).size).toBe(0);
    });
    it('does not fill prose before the first heading (unattributable)', () => {
        const doc = makeDoc([paragraphNode(textToInlineRuns('orphan')), head('f-a')]);
        expect(paragraphOwnerFills(doc).size).toBe(0);
    });
});

describe('keepParagraphOwnerPlugin — crystallize + preserve across transforms', () => {
    it('stamps a null-owner paragraph with its heading on the next transaction', () => {
        const state = stateWith(makeDoc([head('f-a'), paragraphNode(textToInlineRuns('body'))]));
        const next = state.apply(state.tr.insertText('!', 1));  // any docChanged tr triggers the sweep
        expect(paraOwners(next)).toEqual(['f-a']);
    });

    it('NEVER re-owns an owned paragraph — a heading above it cannot steal it (anti-steal)', () => {
        // The paragraph sits positionally under B but is owned by A (the exact shape a
        // "type under A, then insert heading B above" edit produces once A's prose is
        // anchored). The sweep must leave ownerId = f-a, not rewrite it to f-b.
        const state = stateWith(makeDoc([
            head('f-a'), head('f-b'), paragraphNode(textToInlineRuns('A prose'), 'f-a'),
        ]));
        const next = state.apply(state.tr.insertText('x', 1));
        expect(paraOwners(next)).toEqual(['f-a']);
    });

    it('preserves the owner across a paragraph split (Enter mid-prose)', () => {
        const state = stateWith(makeDoc([head('f-a'), paragraphNode(textToInlineRuns('hello world'), 'f-a')]));
        // Find the paragraph and split inside its text (ProseMirror copies attrs to both halves).
        let paraStart = -1;
        state.doc.forEach((n, pos) => { if (n.type.name === 'paragraph') paraStart = pos; });
        const splitPos = paraStart + 1 + 'hello'.length;  // +1 into the paragraph, after "hello"
        const next = state.apply(state.tr.split(splitPos));
        expect(paraOwners(next)).toEqual(['f-a', 'f-a']);  // both halves stay owned by A
    });

    it('is convergent — a second sweep over an all-owned doc is a no-op (no loop)', () => {
        const state = stateWith(makeDoc([head('f-a'), paragraphNode(textToInlineRuns('body'))]));
        const once = state.apply(state.tr.insertText('!', 1));   // fills the null
        const twice = once.apply(once.tr.insertText('?', 1));    // nothing left to fill
        expect(paraOwners(twice)).toEqual(['f-a']);
    });
});
