/**
 * block-boundary.test.ts — a deletion never merges content across a heading
 * (T1.7 / T1.8).
 *
 * `featureHeading` and `paragraph` both hold `inline*`, so ProseMirror's default
 * `joinBackward` will happily append a description's first paragraph onto the
 * feature's TITLE — one Backspace, at the most common caret position there is.
 * The settle that follows reads as a deliberate rename plus a vanished
 * paragraph. The forward direction is the same bug approached from below.
 *
 * The first block below pins the default behaviour so these tests can never
 * quietly stop testing anything.
 */
import { describe, it, expect } from 'vitest';
import { Node as PMNodeType } from '@tiptap/pm/model';
import { EditorState, TextSelection } from '@tiptap/pm/state';
import { joinBackward } from '@tiptap/pm/commands';
import { codocSchema } from '../webview/tiptap/schema';
import { backspaceVerdict, deleteForwardVerdict, verdictTransaction } from '../webview/tiptap/block-boundary';
import type { PMNode } from '../state/pm-doc';

const schema = codocSchema();

const heading = (title: string, fid = 'f-a') => ({
    type: 'featureHeading',
    attrs: { fid, localId: null, level: 0, retired: false, realized: true },
    content: title ? [{ type: 'text', text: title }] : [],
});
const para = (text: string, ownerId: string | null = 'f-a') => ({
    type: 'paragraph',
    attrs: { ownerId },
    content: text ? [{ type: 'text', text }] : [],
});

function stateAt(blocks: unknown[], caret: number): EditorState {
    const doc = PMNodeType.fromJSON(schema, { type: 'doc', content: blocks } as never);
    const state = EditorState.create({ schema, doc });
    return state.apply(state.tr.setSelection(TextSelection.near(state.doc.resolve(caret))));
}
/** Title of the first heading — the thing that must never absorb prose. */
function titleOf(state: EditorState): string {
    let title = '';
    state.doc.forEach(n => { if (!title && n.type.name === 'featureHeading') title = n.textContent; });
    return title;
}

// doc = [heading "Auth" @0 (size 6), paragraph "Body text" @6 (content starts 7)]
const DESCRIPTION_START = 7;

describe('the bug being defended against (anti-vacuity floor)', () => {
    it('ProseMirror\'s default joinBackward appends the description onto the title', () => {
        const state = stateAt([heading('Auth'), para('Body text')], DESCRIPTION_START);
        let after: EditorState = state;
        joinBackward(state, tr => { after = state.apply(tr); });

        expect(titleOf(after)).toBe('AuthBody text');   // the feature just got renamed
    });
});

describe('backspaceVerdict — at the start of a block', () => {
    it('moves the caret to the end of the title instead of merging into it', () => {
        const state = stateAt([heading('Auth'), para('Body text')], DESCRIPTION_START);
        const verdict = backspaceVerdict(state);

        expect(verdict).toEqual({ kind: 'move', pos: 5 });   // end of "Auth"
        const tr = verdictTransaction(state, verdict);
        expect(tr).not.toBeNull();
        const after = state.apply(tr!);
        expect(titleOf(after)).toBe('Auth');                 // untouched
        expect(after.doc.childCount).toBe(2);                // paragraph still its own block
        expect(after.selection.from).toBe(5);                // caret went where the user was going
    });

    it('allows an ordinary paragraph-into-paragraph merge (a real gesture)', () => {
        // [heading@0 size6, para "one"@6 size5, para "two"@11 content starts 12]
        const state = stateAt([heading('Auth'), para('one'), para('two')], 12);
        expect(backspaceVerdict(state)).toEqual({ kind: 'allow' });
    });

    it('allows removing an EMPTY block — nothing merges, nothing is at risk', () => {
        const state = stateAt([heading('Auth'), para('')], DESCRIPTION_START);
        expect(backspaceVerdict(state)).toEqual({ kind: 'allow' });
    });

    it('protects a non-empty heading from being pulled into the block above', () => {
        // [heading@0 size6, para "one"@6 size5, heading "Next"@11 content starts 12]
        const state = stateAt([heading('Auth'), para('one'), heading('Next', 'f-b')], 12);
        expect(backspaceVerdict(state)).toEqual({ kind: 'move', pos: 10 });
    });

    it('allows a deletion in the middle of a block', () => {
        const state = stateAt([heading('Auth'), para('Body text')], DESCRIPTION_START + 3);
        expect(backspaceVerdict(state)).toEqual({ kind: 'allow' });
    });

    it('allows a selection delete — an explicit range is a stated intent', () => {
        const doc = PMNodeType.fromJSON(schema, { type: 'doc', content: [heading('Auth'), para('Body text')] } as never);
        const base = EditorState.create({ schema, doc });
        const state = base.apply(base.tr.setSelection(TextSelection.create(base.doc, 3, DESCRIPTION_START + 4)));
        expect(backspaceVerdict(state)).toEqual({ kind: 'allow' });
    });

    it('allows Backspace in the very first block (nothing above to merge into)', () => {
        const state = stateAt([heading('Auth'), para('Body text')], 1);
        expect(backspaceVerdict(state)).toEqual({ kind: 'allow' });
    });
});

describe('deleteForwardVerdict — at the end of a block', () => {
    it('protects the next heading from being swallowed by the paragraph above', () => {
        // caret at end of para "one" (@6, content 7..10) → pos 10; next block is a heading
        const state = stateAt([heading('Auth'), para('one'), heading('Next', 'f-b')], 10);
        expect(deleteForwardVerdict(state)).toEqual({ kind: 'move', pos: 12 });
    });

    it('allows a forward merge between two paragraphs', () => {
        const state = stateAt([heading('Auth'), para('one'), para('two')], 10);
        expect(deleteForwardVerdict(state)).toEqual({ kind: 'allow' });
    });

    it('allows forward-delete in the last block', () => {
        const state = stateAt([heading('Auth'), para('Body text')], 16);
        expect(deleteForwardVerdict(state)).toEqual({ kind: 'allow' });
    });
});

describe('verdictTransaction', () => {
    it('returns null for allow, so the default deletion runs untouched', () => {
        const state = stateAt([heading('Auth'), para('Body text')], DESCRIPTION_START + 3);
        expect(verdictTransaction(state, { kind: 'allow' })).toBeNull();
    });

    it('never addresses past the end of the document', () => {
        const state = stateAt([heading('Auth'), para('Body text')], DESCRIPTION_START);
        const tr = verdictTransaction(state, { kind: 'move', pos: 9_999 });
        expect(tr!.selection.from).toBeLessThanOrEqual(state.doc.content.size);
    });

    it('keeps the caret move out of the undo history', () => {
        const state = stateAt([heading('Auth'), para('Body text')], DESCRIPTION_START);
        const tr = verdictTransaction(state, backspaceVerdict(state));
        expect(tr!.getMeta('addToHistory')).toBe(false);
    });
});
