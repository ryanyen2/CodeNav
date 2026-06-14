/**
 * doc-reconcile.test.ts — U4 marks-survival guard.
 *
 * Structure must always come fresh from `tree.codoc`; authorship marks must be
 * preserved by fid when the description text is unchanged, and dropped when it
 * changed underneath (loop / external edit).
 */
import { describe, it, expect } from 'vitest';
import { reconcileDoc, replaceFeatureBlocks, groupByHeading } from '../state/doc-reconcile';
import { parseTreeToDoc } from '../state/doc-deserialize';
import {
    makeDoc,
    featureHeadingNode,
    paragraphNode,
    textNode,
    textToInlineRuns,
    blocksToDescriptionText,
    PMNode,
} from '../state/pm-doc';

const TREE = '- Auth  ⟨f-aaaa0001⟩\n    Login and sessions.\n\n- Data  ⟨f-bbbb0002⟩\n    Storage layer.\n';

/** A saved doc where Auth's description carries a pencil author mark. */
function savedWithMark(descText: string): PMNode {
    return makeDoc([
        featureHeadingNode({ fid: 'f-aaaa0001', level: 0, retired: false, realized: true }, textToInlineRuns('Auth')),
        paragraphNode([textNode(descText, [{ type: 'author', attrs: { role: 'claude-code', mode: 'pencil', ts: 1 } }])]),
        featureHeadingNode({ fid: 'f-bbbb0002', level: 0, retired: false, realized: true }, textToInlineRuns('Data')),
        paragraphNode(textToInlineRuns('Storage layer.')),
    ]);
}

describe('U4: reconcileDoc preserves marks when text is unchanged', () => {
    it('keeps the saved author mark when the description text matches', () => {
        const saved = savedWithMark('Login and sessions.');
        const doc = reconcileDoc(TREE, saved);
        const authGroup = groupByHeading(doc)[0];
        const mark = (authGroup.blocks[0].content?.[0].marks ?? []).find(m => m.type === 'author');
        expect(mark?.attrs).toMatchObject({ role: 'claude-code', mode: 'pencil' });
        // structure unchanged
        expect(groupByHeading(doc).map(g => (g.heading.attrs as { fid: string }).fid)).toEqual(['f-aaaa0001', 'f-bbbb0002']);
    });

    it('drops stale marks when the description text changed underneath', () => {
        const saved = savedWithMark('OLD stale description.');
        const doc = reconcileDoc(TREE, saved);
        const authGroup = groupByHeading(doc)[0];
        const marks = authGroup.blocks[0].content?.[0].marks ?? [];
        expect(marks.find(m => m.type === 'author')).toBeUndefined();
        expect(blocksToDescriptionText(authGroup.blocks)).toBe('Login and sessions.');
    });

    it('falls back to fresh when there is no saved doc', () => {
        const doc = reconcileDoc(TREE, null);
        expect(doc).toEqual(parseTreeToDoc(TREE));
    });

    it('picks up a newly added feature from text (not in saved)', () => {
        const saved = savedWithMark('Login and sessions.');
        const grown = TREE + '\n- Search  ⟨f-cccc0003⟩\n    Full text search.\n';
        const doc = reconcileDoc(grown, saved);
        const fids = groupByHeading(doc).map(g => (g.heading.attrs as { fid: string }).fid);
        expect(fids).toEqual(['f-aaaa0001', 'f-bbbb0002', 'f-cccc0003']);
    });
});

describe('U4: replaceFeatureBlocks swaps one feature, leaves the rest', () => {
    it('replaces only the target feature description blocks', () => {
        const saved = savedWithMark('Login and sessions.');
        const next = replaceFeatureBlocks(saved, 'f-aaaa0001', [paragraphNode(textToInlineRuns('Rewritten auth.'))]);
        const groups = groupByHeading(next);
        expect(blocksToDescriptionText(groups[0].blocks)).toBe('Rewritten auth.');
        expect(blocksToDescriptionText(groups[1].blocks)).toBe('Storage layer.'); // untouched
    });
});
