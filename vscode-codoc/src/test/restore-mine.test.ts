/**
 * "Restore mine" — the verdict that put the reader's words back and showed nothing.
 *
 * The host emitted a `set_description` and left the document alone, so the store kept
 * the loop's sentence, every projection re-rendered it, and the button visibly did
 * nothing. The restore is an edit of the DOCUMENT now, which reaches the store by the
 * ordinary settle. These pin the pure half; the wiring is asserted at the bottom.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import {
    descriptionBlocks, withFeatureDescription, featureHeadingNode, paragraphNode,
    textToInlineRuns, type PMNode,
} from '../state/pm-doc';
import { featureUnits } from '../state/commands-from-doc';

const heading = (fid: string, title: string): PMNode => featureHeadingNode(
    { fid, level: 0, retired: false, realized: true, localId: null }, textToInlineRuns(title));
const doc = (content: PMNode[]): PMNode => ({ type: 'doc', content });

describe('descriptionBlocks', () => {
    it('splits on blank lines, the inverse of the join featureUnits does', () => {
        const text = 'First paragraph.\n\nSecond paragraph.';
        const blocks = descriptionBlocks(text, 'f-1');
        expect(blocks).toHaveLength(2);
        const units = featureUnits(doc([heading('f-1', 'T'), ...blocks]));
        expect(units[0].description).toBe(text);
    });

    it('owns every paragraph, so none is captured by the heading above it', () => {
        for (const b of descriptionBlocks('a\n\nb', 'f-7')) {
            expect((b.attrs as { ownerId?: string }).ownerId).toBe('f-7');
        }
    });

    it('empty text leaves a feature with no description rather than a blank paragraph', () => {
        expect(descriptionBlocks('', 'f-1')).toEqual([]);
        expect(descriptionBlocks('   \n\n  ', 'f-1')).toEqual([]);
    });
});

describe('withFeatureDescription', () => {
    const before = doc([
        heading('f-1', 'One'), paragraphNode(textToInlineRuns('one prose'), 'f-1'),
        heading('f-2', 'Two'),
        paragraphNode(textToInlineRuns('the loop rewrote this'), 'f-2'),
        paragraphNode(textToInlineRuns('and this'), 'f-2'),
        heading('f-3', 'Three'), paragraphNode(textToInlineRuns('three prose'), 'f-3'),
    ]);

    it('replaces one feature and leaves its neighbours untouched', () => {
        const after = featureUnits(withFeatureDescription(before, 'f-2', 'what I wrote'));
        expect(after.map(u => [u.fid, u.description])).toEqual([
            ['f-1', 'one prose'],
            ['f-2', 'what I wrote'],
            ['f-3', 'three prose'],
        ]);
    });

    it('replaces ALL of the old prose, however many paragraphs it ran to', () => {
        const after = withFeatureDescription(before, 'f-2', 'one line now');
        const text = JSON.stringify(after);
        expect(text).not.toContain('the loop rewrote this');
        expect(text).not.toContain('and this');
    });

    it('keeps the heading itself — a restore is about the prose', () => {
        const after = featureUnits(withFeatureDescription(before, 'f-2', 'x'));
        expect(after[1].title).toBe('Two');
    });

    it('a fid that is not there changes nothing, so a stale verdict is a no-op', () => {
        expect(withFeatureDescription(before, 'f-nope', 'x')).toEqual(before);
    });

    it('round-trips: restoring the text a feature already has is identity', () => {
        const units = featureUnits(before);
        const same = withFeatureDescription(before, 'f-2', units[1].description);
        expect(featureUnits(same).map(u => u.description))
            .toEqual(units.map(u => u.description));
    });
});

describe('the wiring, so the restore cannot go back to being silent', () => {
    const at = (p: string): string => readFileSync(resolve(__dirname, p), 'utf8');

    it('the revert verdict edits the document', () => {
        expect(at('../webview/tiptap/whole-doc-editor.ts'))
            .toMatch(/restoreFeatureDescription\(editor, fid, prev\)/);
    });

    it('and the host no longer emits a set_description of its own for it', () => {
        // The duplicate is the bug: a host-side command changed the store without the
        // prose moving, which is exactly what made the button look dead.
        const host = at('../providers/tree-editor.ts');
        const fn = host.slice(host.indexOf('private async resolveAutoEdit'),
                              host.indexOf('private async resolveAutoEdit') + 1600);
        expect(fn).not.toMatch(/kind: 'set_description'/);
        expect(fn).toMatch(/markAutoEditSeen/);
    });
});
