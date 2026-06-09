/**
 * doc-diff.test.ts — R1 word-diff guard.
 */
import { describe, it, expect } from 'vitest';
import { wordDiff, changed, compactRuns } from '../state/doc-diff';

/** Reconstruct old/new from the diff runs to assert correctness independent of merging. */
function reconstruct(runs: ReturnType<typeof wordDiff>): { old: string; neu: string } {
    let old = '';
    let neu = '';
    for (const r of runs) {
        if (r.t === 'same') { old += r.s; neu += r.s; }
        else if (r.t === 'del') old += r.s;
        else neu += r.s;
    }
    return { old, neu };
}

describe('R1: wordDiff', () => {
    it('returns a single same run for identical strings', () => {
        const runs = wordDiff('the quick fox', 'the quick fox');
        expect(runs).toEqual([{ t: 'same', s: 'the quick fox' }]);
    });

    it('marks a replaced word as del + ins, keeping context same', () => {
        const runs = wordDiff('the quick fox', 'the slow fox');
        expect(runs.map(r => r.t)).toEqual(['same', 'del', 'ins', 'same']);
        expect(runs.find(r => r.t === 'del')?.s.trim()).toBe('quick');
        expect(runs.find(r => r.t === 'ins')?.s.trim()).toBe('slow');
    });

    it('handles pure insertion at the end', () => {
        const runs = wordDiff('hello', 'hello world');
        const { old, neu } = reconstruct(runs);
        expect(old).toBe('hello');
        expect(neu).toBe('hello world');
        expect(runs.some(r => r.t === 'ins')).toBe(true);
    });

    it('handles pure deletion', () => {
        const runs = wordDiff('hello big world', 'hello world');
        expect(reconstruct(runs)).toEqual({ old: 'hello big world', neu: 'hello world' });
    });

    it('reconstructs old and new exactly for an arbitrary change', () => {
        const old = 'Login and sessions, plus OAuth via Google.';
        const neu = 'Login, sessions, and OAuth via Google or GitHub.';
        expect(reconstruct(wordDiff(old, neu))).toEqual({ old, neu });
    });

    it('merges a run of consecutive deletions into one run', () => {
        // trailing tail removed: " big world" deletes as one merged run
        const runs = wordDiff('hello big world', 'hello');
        const dels = runs.filter(r => r.t === 'del');
        expect(dels).toHaveLength(1);
        expect(dels[0].s).toBe(' big world');
        expect(reconstruct(runs)).toEqual({ old: 'hello big world', neu: 'hello' });
    });

    it('keeps whitespace as common tokens (does not over-merge across spaces)', () => {
        const runs = wordDiff('a b c', 'x y z');
        // spaces stay 'same', so each changed word is its own del/ins pair
        expect(runs.filter(r => r.t === 'same').every(r => r.s.trim() === '')).toBe(true);
        expect(reconstruct(runs)).toEqual({ old: 'a b c', neu: 'x y z' });
    });

    it('changed() detects difference', () => {
        expect(changed('a', 'a')).toBe(false);
        expect(changed('a', 'b')).toBe(true);
    });
});

describe('compactRuns (in-situ suggestion strip)', () => {
    const text = (runs: ReturnType<typeof compactRuns>): string => runs.map(r => r.s).join('');

    it('collapses long unchanged context around a change to an ellipsis', () => {
        // a long description with a single word swapped in the middle
        const old = 'one two three four five six seven and eight nine ten eleven twelve';
        const neu = 'one two three four five six seven OR eight nine ten eleven twelve';
        const compact = compactRuns(wordDiff(old, neu));
        const s = text(compact);
        expect(s).toContain('…');                 // long unchanged ends are trimmed
        expect(s).toContain('and');               // the deletion is kept
        expect(s).toContain('OR');                // the insertion is kept
        expect(s.length).toBeLessThan(old.length); // shorter than restating the whole text
    });

    it('leaves a fully-unchanged (or short) run intact', () => {
        const runs = compactRuns(wordDiff('short text', 'short text'));
        expect(text(runs)).toBe('short text');
        expect(runs.every(r => r.t === 'same')).toBe(true);
    });

    it('passes del/ins runs through untouched', () => {
        const runs = compactRuns(wordDiff('alpha', 'beta'));
        expect(runs.some(r => r.t === 'del' && r.s.includes('alpha'))).toBe(true);
        expect(runs.some(r => r.t === 'ins' && r.s.includes('beta'))).toBe(true);
    });
});
