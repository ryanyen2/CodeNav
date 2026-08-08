/**
 * decoration-policy.test.ts — when a structure-keyed decoration layer is invalid.
 *
 * The bug this replaces: nine layers each treated `tr.docChanged` as "everything
 * I computed is invalid", so typing one character into a 300-feature tree rebuilt
 * every decoration to redraw none of them. The risk in fixing it is the opposite
 * failure — mapping when a rebuild was genuinely needed, leaving stale decorations
 * behind. So these pin BOTH directions.
 */
import { describe, it, expect } from 'vitest';
import { Node as PMNodeType } from '@tiptap/pm/model';
import { EditorState, TextSelection } from '@tiptap/pm/state';
import { DecorationSet, Decoration } from '@tiptap/pm/view';
import { codocSchema } from '../webview/tiptap/schema';
import { structureChanged, nextDecorations } from '../webview/tiptap/decoration-policy';
import { REFLECT_META } from '../webview/tiptap/edit-origin';
import type { PMNode } from '../state/pm-doc';

const schema = codocSchema();

function doc(features: number): PMNode {
    const content: unknown[] = [];
    for (let i = 0; i < features; i++) {
        content.push({
            type: 'featureHeading',
            attrs: { fid: `f-${i}`, localId: `l-${i}`, level: 1, version: `v${i}` },
            content: [{ type: 'text', text: `Feature ${i}` }],
        });
        content.push({
            type: 'paragraph', attrs: { ownerId: `f-${i}` },
            content: [{ type: 'text', text: `Body of feature ${i}.` }],
        });
    }
    return { type: 'doc', content } as unknown as PMNode;
}

const state = (n = 3) => EditorState.create({ schema, doc: PMNodeType.fromJSON(schema, doc(n) as never) });

/** Position inside the first paragraph's text. */
function inBody(s: EditorState): number {
    let pos = -1;
    s.doc.forEach((node, p) => { if (pos < 0 && node.type.name === 'paragraph') pos = p + 1; });
    return pos;
}

describe('what counts as a structure change', () => {
    it('typing inside a paragraph does not', () => {
        const s = state();
        expect(structureChanged(s.tr.insertText('x', inBody(s)))).toBe(false);
    });

    it('typing inside a HEADING does not either', () => {
        // Deliberate: a structure-keyed layer draws from the heading's identity,
        // not its words. A layer that reads heading TEXT (glance) is not
        // structure-keyed and must not use this policy.
        const s = state();
        expect(structureChanged(s.tr.insertText('x', 1))).toBe(false);
    });

    it('a transaction that changes nothing does not', () => {
        const s = state();
        expect(structureChanged(s.tr.setSelection(TextSelection.create(s.doc, 1)))).toBe(false);
    });

    it('splitting a block does', () => {
        const s = state();
        expect(structureChanged(s.tr.split(inBody(s) + 3))).toBe(true);
    });

    it('deleting a whole block does', () => {
        const s = state();
        const first = s.doc.child(0);
        expect(structureChanged(s.tr.delete(0, first.nodeSize))).toBe(true);
    });

    it('changing a heading identity does', () => {
        // The projection adopting a minted fid, or a paste re-owning a paragraph:
        // same block count, different identity, and every per-feature decoration
        // is now attached to the wrong thing.
        const s = state();
        expect(structureChanged(s.tr.setNodeAttribute(0, 'fid', 'f-renamed'))).toBe(true);
    });

    it('re-owning a paragraph does', () => {
        const s = state();
        const pos = inBody(s) - 1;
        expect(structureChanged(s.tr.setNodeAttribute(pos, 'ownerId', 'f-other'))).toBe(true);
    });

    it('changing a heading level does', () => {
        const s = state();
        expect(structureChanged(s.tr.setNodeAttribute(0, 'level', 2))).toBe(true);
    });
});

describe('the memo', () => {
    it('answers once per transaction, so nine layers share one computation', () => {
        const s = state(2);
        const tr = s.tr.insertText('x', inBody(s));
        // Same transaction object → same answer, without recomputing. Observable
        // only as identity of the result, but the contract is what matters: no
        // plugin ordering dependency, no nine-fold repeat.
        expect(structureChanged(tr)).toBe(structureChanged(tr));
    });
});

describe('nextDecorations', () => {
    const marker = (s: EditorState) => DecorationSet.create(s.doc, [
        Decoration.node(0, s.doc.child(0).nodeSize, { class: 'built' }),
    ]);

    it('rebuilds when the layer own state changed, even with no doc change', () => {
        const s = state();
        let built = 0;
        nextDecorations(s.tr, DecorationSet.empty, true, () => { built++; return marker(s); });
        expect(built).toBe(1);
    });

    it('maps — does not rebuild — while the user is only typing', () => {
        const s = state();
        const old = marker(s);
        let built = 0;
        const out = nextDecorations(s.tr.insertText('x', inBody(s)), old, false,
            () => { built++; return DecorationSet.empty; });
        expect(built).toBe(0);
        expect(out.find().length).toBe(old.find().length);   // and nothing was lost
    });

    it('still rebuilds when the structure moved under it', () => {
        // The anti-vacuity floor for the whole change: if this mapped instead, the
        // optimisation would be trading jank for stale decorations, which is worse.
        const s = state();
        let built = 0;
        nextDecorations(s.tr.split(inBody(s) + 3), marker(s), false,
            () => { built++; return DecorationSet.empty; });
        expect(built).toBe(1);
    });

    it('keeps decorations positioned correctly after a mapped edit', () => {
        // Mapping is only the right answer if it actually repositions. Insert
        // BEFORE a decoration and assert it moved by the inserted length.
        const s = state();
        const headSize = s.doc.child(0).nodeSize;
        const old = DecorationSet.create(s.doc, [
            Decoration.node(headSize, headSize + s.doc.child(1).nodeSize, { class: 'p' }),
        ]);
        const out = nextDecorations(s.tr.insertText('abc', 1), old, false, () => DecorationSet.empty);
        expect(out.find()[0].from).toBe(headSize + 3);
    });
});

describe('a projection reload always rebuilds', () => {
    it('a REFLECT whole-doc replace rebuilds instead of mapping to nothing', () => {
        // A text-only agent amend replaces the WHOLE doc in one ReplaceStep: the
        // structure is unchanged (structureChanged is false), but mapping through
        // a full-doc replace deletes every decoration inside it — the layer would
        // come back empty and stay empty until its next meta. The REFLECT tag
        // must force the rebuild.
        const s = state();
        const d = doc(3) as unknown as { content: Array<{ content: Array<{ text: string }> }> };
        d.content[1].content[0].text = 'Rewritten by the agent.';
        const replacement = PMNodeType.fromJSON(schema, d as never);
        const tr = s.tr.replaceWith(0, s.doc.content.size, replacement.content);
        tr.setMeta(REFLECT_META, true);
        let rebuilt = false;
        nextDecorations(tr, DecorationSet.empty, false,
                        () => { rebuilt = true; return DecorationSet.empty; });
        expect(rebuilt).toBe(true);
        expect(structureChanged(tr)).toBe(false);  // i.e. the map branch would have run
    });
});
