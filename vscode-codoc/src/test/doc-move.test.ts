/**
 * doc-move.test.ts — U2b: reparenting a feature inside the authored doc.
 *
 * In the single-writer model a tree-pane drag is a pure doc transform (the host
 * persists tree.doc.json, not tree.codoc). moveFeatureInDoc must move the dragged
 * feature + its subtree under the new parent, shift levels to stay tree-valid, and
 * refuse no-ops / cycles. Loop B then derives the MOVE from parse_doc_file.
 */
import { describe, it, expect } from 'vitest';
import { moveFeatureInDoc } from '../state/doc-move';
import { makeDoc, featureHeadingNode, paragraphNode, textNode, textToInlineRuns, type PMNode } from '../state/pm-doc';

function h(fid: string, title: string, level: number): PMNode {
    return featureHeadingNode({ fid, level, retired: false, realized: true }, textToInlineRuns(title));
}

/** [fid, level] pairs in document order — the structural shape we assert on. */
function shape(doc: PMNode): [string, number][] {
    return (doc.content ?? [])
        .filter(b => b.type === 'featureHeading')
        .map(b => [(b.attrs as { fid: string }).fid, (b.attrs as { level: number }).level]);
}

function tree(): PMNode {
    // A: 0   ├─ A1: 1   B: 0
    return makeDoc([
        h('f-a', 'A', 0), paragraphNode(textToInlineRuns('desc A')),
        h('f-a1', 'A1', 1), paragraphNode(textToInlineRuns('desc A1')),
        h('f-b', 'B', 0), paragraphNode(textToInlineRuns('desc B')),
    ]);
}

describe('moveFeatureInDoc', () => {
    it('moves a feature under a new parent, shifting its level', () => {
        const out = moveFeatureInDoc(tree(), 'f-b', 'f-a')!;
        expect(out).not.toBeNull();
        // B nests under A (after A's existing subtree A1).
        expect(shape(out)).toEqual([['f-a', 0], ['f-a1', 1], ['f-b', 1]]);
    });

    it('carries the whole subtree (and re-levels it) when moving a parent', () => {
        const out = moveFeatureInDoc(tree(), 'f-a', 'f-b')!;
        // A + A1 move under B; A→1, A1→2.
        expect(shape(out)).toEqual([['f-b', 0], ['f-a', 1], ['f-a1', 2]]);
    });

    it('moves a nested feature out to root', () => {
        const out = moveFeatureInDoc(tree(), 'f-a1', null)!;
        expect(shape(out)).toEqual([['f-a', 0], ['f-b', 0], ['f-a1', 0]]);
    });

    it('keeps the description paragraphs with their heading', () => {
        const out = moveFeatureInDoc(tree(), 'f-b', 'f-a')!;
        const types = (out.content ?? []).map(b => b.type);
        // …, f-b heading, then its paragraph (not orphaned).
        const bIdx = (out.content ?? []).findIndex(b => (b.attrs as { fid?: string })?.fid === 'f-b');
        expect(types[bIdx + 1]).toBe('paragraph');
    });

    it('refuses a cycle (moving a feature under its own descendant)', () => {
        expect(moveFeatureInDoc(tree(), 'f-a', 'f-a1')).toBeNull();
    });

    it('refuses moving a feature under itself', () => {
        expect(moveFeatureInDoc(tree(), 'f-a', 'f-a')).toBeNull();
    });

    it('is a no-op (null) when already directly under the target parent', () => {
        expect(moveFeatureInDoc(tree(), 'f-a1', 'f-a')).toBeNull(); // A1 already under A
    });

    it('returns null for an unknown source or parent', () => {
        expect(moveFeatureInDoc(tree(), 'f-missing', null)).toBeNull();
        expect(moveFeatureInDoc(tree(), 'f-b', 'f-missing')).toBeNull();
    });
});
