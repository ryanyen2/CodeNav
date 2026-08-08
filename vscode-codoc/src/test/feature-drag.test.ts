/**
 * feature-drag.test.ts — moving a feature is moving its whole slice.
 *
 * The gesture deliberately has no persistence of its own: it edits the document,
 * and `reorderTargets` turns the changed order into one `move` command. So what
 * has to be right here is purely geometric — which blocks belong to the feature,
 * where it may land, and that a move never loses or duplicates anything.
 */
import { describe, it, expect } from 'vitest';
import { Node as PMNodeType } from '@tiptap/pm/model';
import { EditorState } from '@tiptap/pm/state';
import { codocSchema } from '../webview/tiptap/schema';
import {
    featureSlices, sliceAt, dropPositions, nearestDrop, moveSlice, nudgeTarget,
} from '../webview/tiptap/feature-drag';
import { featureUnits } from '../state/commands-from-doc';
import { isUserInput } from '../webview/tiptap/edit-origin';
import type { PMNode } from '../state/pm-doc';

const schema = codocSchema();

/** `['A', 0, 'B', 1]` → heading A at level 0, heading B at level 1, each with prose. */
function build(spec: Array<[string, number]>): PMNode {
    const content: unknown[] = [];
    for (const [name, level] of spec) {
        content.push({
            type: 'featureHeading',
            attrs: { fid: `f-${name}`, localId: `l-${name}`, level },
            content: [{ type: 'text', text: name }],
        });
        content.push({
            type: 'paragraph', attrs: { ownerId: `f-${name}` },
            content: [{ type: 'text', text: `prose of ${name}` }],
        });
    }
    return { type: 'doc', content } as unknown as PMNode;
}

const state = (spec: Array<[string, number]>) =>
    EditorState.create({ schema, doc: PMNodeType.fromJSON(schema, build(spec) as never) });

const titles = (s: EditorState) =>
    featureUnits(s.doc.toJSON() as PMNode).map(u => u.title);

describe('what a feature slice covers', () => {
    it('takes the heading and its prose', () => {
        const s = state([['A', 0], ['B', 0]]);
        const [a] = featureSlices(s.doc);
        const covered = s.doc.slice(a.from, a.to).content;
        expect(covered.childCount).toBe(2);          // heading + paragraph
    });

    it('takes nested features too — dragging a parent takes its children', () => {
        const s = state([['A', 0], ['A1', 1], ['A2', 1], ['B', 0]]);
        const [a] = featureSlices(s.doc);
        const text = s.doc.slice(a.from, a.to).content.textBetween(0, a.to - a.from, ' ');
        expect(text).toContain('A1');
        expect(text).toContain('A2');
        expect(text).not.toContain('B');
    });

    it('uses the CLAMPED depth, so the slice matches the tree the reader sees', () => {
        // A level-3 heading following a level-0 one renders as depth 1, not 3.
        // Slicing by the raw attribute would disagree with parentage everywhere
        // else, and a drag would grab a different subtree than the indentation.
        const s = state([['A', 0], ['B', 3], ['C', 1]]);
        const slices = featureSlices(s.doc);
        expect(slices.map(x => x.depth)).toEqual([0, 1, 1]);
        const text = s.doc.slice(slices[0].from, slices[0].to).content
            .textBetween(0, slices[0].to - slices[0].from, ' ');
        expect(text).toContain('B');   // B is A's child, so A's slice contains it
        expect(text).toContain('C');
    });

    it('ends the last slice at the end of the document', () => {
        const s = state([['A', 0], ['B', 0]]);
        const slices = featureSlices(s.doc);
        expect(slices[slices.length - 1].to).toBe(s.doc.content.size);
    });
});

describe('where a feature may be dropped', () => {
    it('excludes its own interior — a node cannot land inside itself', () => {
        const s = state([['A', 0], ['A1', 1], ['B', 0]]);
        const a = sliceAt(s.doc, featureSlices(s.doc)[0].from)!;
        for (const p of dropPositions(s.doc, a)) {
            expect(p > a.from && p < a.to).toBe(false);
        }
    });

    it('excludes the position it already occupies', () => {
        const s = state([['A', 0], ['B', 0]]);
        const a = featureSlices(s.doc)[0];
        expect(dropPositions(s.doc, a)).not.toContain(a.from);
    });

    it('offers the end of the document, so a feature can be moved last', () => {
        const s = state([['A', 0], ['B', 0]]);
        const a = featureSlices(s.doc)[0];
        expect(dropPositions(s.doc, a)).toContain(s.doc.content.size);
    });

    it('snaps to the nearest offered position', () => {
        expect(nearestDrop([0, 10, 30], 12)).toBe(10);
        expect(nearestDrop([0, 10, 30], 24)).toBe(30);
    });
});

describe('moving a slice', () => {
    it('reorders the tree the user reads', () => {
        const s = state([['A', 0], ['B', 0], ['C', 0]]);
        const c = featureSlices(s.doc)[2];
        const tr = moveSlice(s, c, 0)!;
        expect(titles(s.apply(tr))).toEqual(['C', 'A', 'B']);
    });

    it('carries children with the parent', () => {
        const s = state([['A', 0], ['A1', 1], ['B', 0]]);
        const a = featureSlices(s.doc)[0];
        const tr = moveSlice(s, a, s.doc.content.size)!;
        expect(titles(s.apply(tr))).toEqual(['B', 'A', 'A1']);
    });

    it('refuses to drop a feature inside itself', () => {
        const s = state([['A', 0], ['A1', 1], ['B', 0]]);
        const a = featureSlices(s.doc)[0];
        expect(moveSlice(s, a, a.from + 2)).toBeNull();
    });

    it('refuses a move that changes nothing', () => {
        // A no-op transaction would still mark the document dirty and settle,
        // producing a command for a gesture that did not move anything.
        const s = state([['A', 0], ['B', 0]]);
        const a = featureSlices(s.doc)[0];
        expect(moveSlice(s, a, a.from)).toBeNull();
    });

    it('is ONE transaction, so undo restores the feature in one step', () => {
        const s = state([['A', 0], ['B', 0], ['C', 0]]);
        const c = featureSlices(s.doc)[2];
        const tr = moveSlice(s, c, 0)!;
        expect(tr.steps.length).toBeGreaterThan(0);
        const back = s.apply(tr);
        expect(titles(back)).toEqual(['C', 'A', 'B']);
    });

    it('never loses or duplicates a feature, however much it is dragged', () => {
        let s = state([['A', 0], ['B', 0], ['C', 0], ['D', 0]]);
        const expected = new Set(['A', 'B', 'C', 'D']);
        for (let i = 0; i < 40; i++) {
            const slices = featureSlices(s.doc);
            const pick = slices[i % slices.length];
            const spots = dropPositions(s.doc, pick);
            const tr = moveSlice(s, pick, spots[(i * 3) % spots.length]);
            if (tr) s = s.apply(tr);
            const seen = titles(s);
            expect(new Set(seen)).toEqual(expected);
            expect(seen.length).toBe(4);
        }
    });
});

describe('the keyboard equivalent', () => {
    it('steps over a whole sibling, not into its prose', () => {
        const s = state([['A', 0], ['B', 0], ['C', 0]]);
        const a = featureSlices(s.doc)[0];
        const tr = moveSlice(s, a, nudgeTarget(s.doc, a, 1)!)!;
        expect(titles(s.apply(tr))).toEqual(['B', 'A', 'C']);
    });

    it('steps up past the previous sibling', () => {
        const s = state([['A', 0], ['B', 0], ['C', 0]]);
        const c = featureSlices(s.doc)[2];
        const tr = moveSlice(s, c, nudgeTarget(s.doc, c, -1)!)!;
        expect(titles(s.apply(tr))).toEqual(['A', 'C', 'B']);
    });

    it('steps over a sibling that has children of its own', () => {
        const s = state([['A', 0], ['B', 0], ['B1', 1], ['C', 0]]);
        const a = featureSlices(s.doc)[0];
        const tr = moveSlice(s, a, nudgeTarget(s.doc, a, 1)!)!;
        expect(titles(s.apply(tr))).toEqual(['B', 'B1', 'A', 'C']);
    });

    it('does nothing at either end of the sibling list', () => {
        const s = state([['A', 0], ['B', 0]]);
        const slices = featureSlices(s.doc);
        expect(nudgeTarget(s.doc, slices[0], -1)).toBeNull();
        expect(nudgeTarget(s.doc, slices[1], 1)).toBeNull();
    });

    it('moves among SIBLINGS, ignoring features at other depths', () => {
        const s = state([['A', 0], ['A1', 1], ['B', 0]]);
        const a1 = featureSlices(s.doc)[1];
        expect(nudgeTarget(s.doc, a1, 1)).toBeNull();   // A1 is an only child
    });
});

describe('nudge stays inside the parent', () => {
    it('does not step from the last child of A onto the first child of B', () => {
        // Same depth is not same parent: stepping A1 "down" onto B1 would silently
        // reparent it under B, while every surface promises "among its siblings".
        const s = state([['A', 0], ['A1', 1], ['B', 0], ['B1', 1]]);
        const slices = featureSlices(s.doc);
        expect(nudgeTarget(s.doc, slices[1], 1)).toBeNull();   // A1 down → would land in B
        expect(nudgeTarget(s.doc, slices[3], -1)).toBeNull();  // B1 up → would land in A
    });

    it('still steps across a sibling whose own children sit in between', () => {
        const s = state([['A', 0], ['A1', 1], ['A1a', 2], ['A2', 1]]);
        const a1 = featureSlices(s.doc)[1];
        const tr = moveSlice(s, a1, nudgeTarget(s.doc, a1, 1)!)!;
        expect(titles(s.apply(tr))).toEqual(['A', 'A2', 'A1', 'A1a']);
    });
});

describe('a move is structural, not typing', () => {
    it('tags its transaction so author-stamp and mark-hygiene skip the slice', () => {
        // Without the tag, dragging a feature that carries agent proposal marks
        // strips them into plain "human-typed" text — a silent proposal
        // resolution neither Accept nor Reject produced.
        const s = state([['A', 0], ['B', 0]]);
        const a = featureSlices(s.doc)[0];
        const tr = moveSlice(s, a, nudgeTarget(s.doc, a, 1)!)!;
        expect(isUserInput(tr)).toBe(false);
    });
});
