/**
 * mark-hygiene.test.ts — the user's words are never the agent's words (T5.5).
 *
 * The failure this pins: an agent amend arrives as `insertion`-marked text; the
 * user puts the caret inside that span and types; ProseMirror hands the new text
 * the enclosing marks; `inlineRunsToText` then drops every insertion-marked run
 * when projecting to `tree.codoc`. The person's sentence is silently deleted on
 * save, counts as no change, and dies outright if the proposal is rejected.
 *
 * Driven through a real EditorState with the real plugin stack, because the bug
 * lives in ProseMirror's mark inheritance — a JSON-level test would not see it.
 */
import { describe, it, expect } from 'vitest';
import { Node as PMNodeType } from '@tiptap/pm/model';
import { EditorState } from '@tiptap/pm/state';
import { codocSchema } from '../webview/tiptap/schema';
import { markHygienePlugin } from '../webview/tiptap/mark-hygiene';
import { REFLECT_META } from '../webview/tiptap/edit-origin';
import { inlineRunsToText } from '../state/pm-doc';
import type { PMNode } from '../state/pm-doc';

const schema = codocSchema();

/**
 * A description paragraph that is half committed text and half a pending agent
 * insertion — the shape `applyAgentProposals` puts in the payload doc.
 */
function docWithProposal(): PMNode {
    return {
        type: 'doc',
        content: [
            {
                type: 'featureHeading',
                attrs: { fid: 'f-a', localId: null, level: 0, retired: false, realized: true },
                content: [{ type: 'text', text: 'Auth' }],
            },
            {
                type: 'paragraph',
                attrs: { ownerId: 'f-a' },
                content: [
                    { type: 'text', text: 'Committed. ' },
                    { type: 'text', text: 'Agent proposed this.', marks: [{ type: 'insertion' }] },
                ],
            },
        ],
    } as unknown as PMNode;
}

function stateOf(json: PMNode): EditorState {
    const doc = PMNodeType.fromJSON(schema, json as never);
    return EditorState.create({ schema, doc, plugins: [markHygienePlugin()] });
}

/** Marks on the text node covering `pos`, by name. */
function marksAt(state: EditorState, pos: number): string[] {
    const { nodeAfter } = state.doc.resolve(pos);
    return (nodeAfter?.marks ?? []).map(m => m.type.name);
}

/** The paragraph's runs as `inlineRunsToText` will project them to tree.codoc. */
function projectedParagraph(state: EditorState): string {
    const json = state.doc.toJSON() as unknown as { content: PMNode[] };
    const para = json.content.find(b => b.type === 'paragraph');
    return inlineRunsToText(para?.content);
}

describe('the bug being defended against (anti-vacuity floor)', () => {
    it('without hygiene, typing inside an agent span is swallowed on save', () => {
        const doc = PMNodeType.fromJSON(schema, docWithProposal() as never);
        const bare = EditorState.create({ schema, doc });   // no hygiene plugin
        const insideAgentSpan = bare.doc.content.size - 5;
        const next = bare.apply(bare.tr.insertText('HUMAN', insideAgentSpan));

        expect(marksAt(next, insideAgentSpan)).toContain('insertion');
        expect(projectedParagraph(next)).not.toContain('HUMAN');
    });
});

describe('mark hygiene — engine marks never cover user input', () => {
    it('strips the insertion mark from text typed INSIDE an agent span', () => {
        const state = stateOf(docWithProposal());
        // "Committed. " is 11 chars from content start (pos 1 is the heading).
        // The agent run starts right after it; type in its middle.
        const insideAgentSpan = state.doc.content.size - 5;
        const next = state.apply(state.tr.insertText('HUMAN', insideAgentSpan));

        expect(marksAt(next, insideAgentSpan)).not.toContain('insertion');
    });

    it('keeps that text in the tree.codoc projection instead of dropping it', () => {
        const state = stateOf(docWithProposal());
        const insideAgentSpan = state.doc.content.size - 5;
        const next = state.apply(state.tr.insertText('HUMAN', insideAgentSpan));

        // The load-bearing assertion: without hygiene, inlineRunsToText excludes the
        // whole insertion-marked run and the person's word never reaches the store.
        expect(projectedParagraph(next)).toContain('HUMAN');
    });

    it('splits the agent span rather than clearing it — the proposal survives', () => {
        const state = stateOf(docWithProposal());
        const insideAgentSpan = state.doc.content.size - 5;
        const next = state.apply(state.tr.insertText('HUMAN', insideAgentSpan));

        const names: string[] = [];
        next.doc.descendants(n => { if (n.isText) names.push(...n.marks.map(m => m.type.name)); });
        expect(names).toContain('insertion');   // the agent's remaining words keep their mark
    });

    it('leaves marks alone on a system (projection) transaction', () => {
        const state = stateOf(docWithProposal());
        // How agent marks legitimately ENTER the doc: a reflect-tagged load.
        const doc = PMNodeType.fromJSON(schema, docWithProposal() as never);
        const tr = state.tr
            .replaceWith(0, state.doc.content.size, doc.content)
            .setMeta(REFLECT_META, true);
        const next = state.apply(tr);

        const names: string[] = [];
        next.doc.descendants(n => { if (n.isText) names.push(...n.marks.map(m => m.type.name)); });
        expect(names).toContain('insertion');
    });

    it('is a no-op for ordinary typing outside any agent span (no churn)', () => {
        const state = stateOf(docWithProposal());
        const before = JSON.stringify(state.doc.toJSON());
        const next = state.apply(state.tr.insertText('x', 3));   // inside the heading title
        expect(JSON.stringify(next.doc.toJSON())).not.toBe(before);
        expect(marksAt(next, 3)).not.toContain('insertion');
    });

    it('converges — a second transaction produces no further cleanup', () => {
        const state = stateOf(docWithProposal());
        const insideAgentSpan = state.doc.content.size - 5;
        const once = state.apply(state.tr.insertText('A', insideAgentSpan));
        const twice = once.apply(once.tr.insertText('B', insideAgentSpan));
        // No plugin loop: the sweep settles, and both characters are plain.
        expect(marksAt(twice, insideAgentSpan)).not.toContain('insertion');
    });
});
