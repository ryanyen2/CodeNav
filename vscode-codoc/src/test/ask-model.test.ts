/**
 * ask-model.test.ts — the pure logic behind the /codoc:ask walkthrough overlay:
 * tolerant parsing, group-run detection, and the whitespace-tolerant quote match
 * that has to agree with the store's normalized prose while searching the live
 * editor text.
 */
import { describe, it, expect } from 'vitest';
import {
    parseAsk, stepsByFid, groupOpeners, stepIndex, findQuoteRange,
} from '../state/ask-model';

describe('parseAsk', () => {
    it('returns null for anything with no steps', () => {
        expect(parseAsk(null)).toBeNull();
        expect(parseAsk({})).toBeNull();
        expect(parseAsk({ steps: [] })).toBeNull();
        expect(parseAsk({ steps: [{ note: 'no fid' }] })).toBeNull();
    });
    it('keeps only the first step per feature', () => {
        const walk = parseAsk({
            question: 'q', steps: [
                { label: '1', feature_id: 'f1', note: 'first' },
                { label: '2', feature_id: 'f1', note: 'dup' },
                { label: '3', feature_id: 'f2' },
            ],
        })!;
        expect(walk.steps.map(s => s.feature_id)).toEqual(['f1', 'f2']);
        expect(walk.steps[0].note).toBe('first');
    });
    it('carries the fields a step renders', () => {
        const walk = parseAsk({
            question: 'q', answer: 'a', id: 'ask-1',
            steps: [{ label: '1a', feature_id: 'f1', group: 'g', quote: 'x', file: 'a.py', line: 4 }],
        })!;
        expect(walk).toMatchObject({ question: 'q', answer: 'a', id: 'ask-1' });
        expect(walk.steps[0]).toMatchObject({ group: 'g', quote: 'x', file: 'a.py', line: 4 });
    });
});

describe('groupOpeners', () => {
    const walk = (groups: string[]) => parseAsk({
        question: 'q', steps: groups.map((g, i) => ({ feature_id: `f${i}`, group: g })),
    });
    it('marks the first feature of each run', () => {
        const w = walk(['parse', 'parse', 'convert'])!;
        const openers = groupOpeners(w);
        expect(openers.get('f0')).toBe('parse');
        expect(openers.has('f1')).toBe(false);
        expect(openers.get('f2')).toBe('convert');
    });
    it('opens a fresh run when a group recurs', () => {
        const w = walk(['strip', 'convert', 'strip'])!;
        const openers = groupOpeners(w);
        expect(openers.get('f2')).toBe('strip'); // the return visit names the stage again
    });
});

describe('stepsByFid / stepIndex', () => {
    const w = parseAsk({ question: 'q', steps: [{ feature_id: 'a' }, { feature_id: 'b' }] })!;
    it('indexes by feature id', () => {
        expect(stepsByFid(w).get('b')).toBeDefined();
        expect(stepIndex(w, 'b')).toBe(1);
        expect(stepIndex(w, 'z')).toBe(-1);
    });
});

describe('findQuoteRange', () => {
    it('locates an exact span', () => {
        expect(findQuoteRange('the running header is gone', 'running header')).toEqual([4, 18]);
    });
    it('tolerates re-wrapped whitespace, returning original offsets', () => {
        // The store verified the quote against "so the header is gone"; the editor
        // holds it broken across lines. The words agree; the breaks do not.
        const hay = 'Runs first,\nso the header\nis gone.';
        const range = findQuoteRange(hay, 'so the header is gone')!;
        expect(hay.slice(range[0], range[1])).toBe('so the header\nis gone');
    });
    it('returns null when the prose has genuinely moved on', () => {
        expect(findQuoteRange('the header is gone', 'words nobody wrote')).toBeNull();
    });
    it('returns null for an empty needle', () => {
        expect(findQuoteRange('anything', '   ')).toBeNull();
    });
});
