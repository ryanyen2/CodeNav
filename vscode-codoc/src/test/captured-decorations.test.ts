/**
 * captured-decorations.test.ts — U3 guard for the "recorded, not sent" phase.
 *
 * Pure helpers only (the vitest node env has no DOM/editor): the feature-block projection,
 * the captured-set partition, and the add/del span positioning. The visual rail/dot/caret
 * and its place in the lifecycle ramp are the EDH gate. Load-bearing rules pinned here:
 *   - EVERY changed feature is captured (no size/code-implying threshold).
 *   - a held draft stays captured even after the daemon renders its prose back.
 *   - a handed-off (staged & sent) feature is NEVER captured — pending takes over.
 *   - whitespace-only edits don't register (ftKey matches the renderer's normalization).
 *   - additions map to an underline range; deletions map to a caret AT the gap (no text).
 */
import { describe, it, expect } from 'vitest';
import {
    featureBlocks, ftKey, capturedFids, blockDiffSpans, type FeatureText,
} from '../webview/tiptap/captured-decorations';
import { makeDoc, featureHeadingNode, paragraphNode, textToInlineRuns, type PMNode } from '../state/pm-doc';

function feat(fid: string, title: string, desc: string): PMNode[] {
    return [
        featureHeadingNode({ fid, level: 0, retired: false, realized: true }, textToInlineRuns(title)),
        paragraphNode(textToInlineRuns(desc)),
    ];
}
function doc(): PMNode {
    return makeDoc([...feat('f-a', 'Auth', 'Login and sessions.'), ...feat('f-b', 'Data', 'Persistence.')]);
}
const ft = (title: string, ...paras: string[]): FeatureText => ({ title, paras });

describe('U3: featureBlocks', () => {
    it('splits each feature into title + paragraph blocks', () => {
        const m = featureBlocks(doc());
        expect(m.get('f-a')).toEqual({ title: 'Auth', paras: ['Login and sessions.'] });
        expect(m.get('f-b')).toEqual({ title: 'Data', paras: ['Persistence.'] });
    });
});

describe('U3: ftKey (round-trip-invariant change key)', () => {
    it('ignores whitespace the renderer would strip (no false capture)', () => {
        expect(ftKey(ft('Auth ', 'Login and sessions.   '))).toBe(ftKey(ft('Auth', 'Login and sessions.')));
    });
    it('distinguishes a real word change', () => {
        expect(ftKey(ft('Auth', 'Login.'))).not.toBe(ftKey(ft('Auth', 'Login and OAuth.')));
    });
});

describe('U3: capturedFids', () => {
    const base = featureBlocks(doc());
    const empty = new Set<string>();

    it('captures a feature whose text changed vs baseline (any size, no threshold)', () => {
        const cur = new Map(base);
        cur.set('f-a', ft('Auth', 'Login and sessions, plus OAuth.'));
        expect(capturedFids(base, cur, empty, empty)).toEqual(new Set(['f-a']));
    });

    it('captures a pure DELETION (the reported bug: deletions must count)', () => {
        const cur = new Map(base);
        cur.set('f-a', ft('Auth', 'Login.')); // deleted " and sessions"
        expect([...capturedFids(base, cur, empty, empty)]).toContain('f-a');
    });

    it('does NOT capture a whitespace-only edit', () => {
        const cur = new Map(base);
        cur.set('f-a', ft('Auth', 'Login and sessions.   '));
        expect(capturedFids(base, cur, empty, empty).size).toBe(0);
    });

    it('captures a held draft even when its text matches the baseline (rendered back)', () => {
        expect(capturedFids(base, new Map(base), new Set(['f-a']), empty)).toEqual(new Set(['f-a']));
    });

    it('does NOT capture a handed-off feature — pending takes over (no double mark)', () => {
        const cur = new Map(base);
        cur.set('f-a', ft('Auth', 'Login and sessions, plus OAuth.'));
        expect(capturedFids(base, cur, new Set(['f-a']), new Set(['f-a'])).size).toBe(0);
    });

    it('captures a brand-new authored node absent from the baseline (U4: recorded immediately)', () => {
        // A new heading is keyed by its localId (no fid yet); it has no baseline entry,
        // so it now reads as recorded — the author sees the "recorded" dot at once
        // instead of silent nothing. (Supersedes the old "left to the ADD flow" behavior.)
        const cur = new Map(base);
        cur.set('lid-new', ft('Brand new', 'fresh node'));
        expect([...capturedFids(base, cur, empty, empty)]).toContain('lid-new');
    });
});

describe('U3: blockDiffSpans (add underline range + deletion caret position)', () => {
    // contentStart = 1 (first inline position inside a textblock)
    it('an addition yields an underline range over the inserted words', () => {
        const spans = blockDiffSpans('Login.', 'Login and OAuth.', 1);
        const add = spans.find(s => s.kind === 'add');
        expect(add).toBeTruthy();
        // "Login" (5) stays same; the insertion range covers the added " and OAuth" tail
        expect(add).toMatchObject({ kind: 'add' });
        if (add && add.kind === 'add') expect(add.to).toBeGreaterThan(add.from);
    });

    it('a deletion yields a caret AT the gap, not an underline ("I don\'t think" → "I think")', () => {
        const spans = blockDiffSpans('I don\'t think', 'I think', 1);
        const del = spans.find(s => s.kind === 'del');
        expect(del).toBeTruthy();
        if (del && del.kind === 'del') {
            expect(del.text).toContain("don't");
            // caret sits after "I " (offset 2) + contentStart(1) = position 3, i.e. "I |think"
            expect(del.at).toBe(3);
        }
        expect(spans.some(s => s.kind === 'add')).toBe(false); // pure deletion → no underline
    });

    it('a word REPLACEMENT (select-delete-retype) is editing → underline only, NO caret', () => {
        // "the cat" → "the dog": del("cat") is adjacent to ins("dog") ⇒ replacement.
        const spans = blockDiffSpans('the cat', 'the dog', 1);
        expect(spans.some(s => s.kind === 'add')).toBe(true);  // the new word is underlined
        expect(spans.some(s => s.kind === 'del')).toBe(false); // no deletion caret
    });

    it('a pure deletion alongside a SEPARATE addition still carets the deletion', () => {
        // "alpha beta gamma" → "alpha gamma delta": "beta" removed (pure), "delta" added.
        const spans = blockDiffSpans('alpha beta gamma', 'alpha gamma delta', 1);
        expect(spans.some(s => s.kind === 'del')).toBe(true);  // "beta" deletion → caret
        expect(spans.some(s => s.kind === 'add')).toBe(true);  // "delta" addition → underline
    });

    it('no change → no spans', () => {
        expect(blockDiffSpans('same', 'same', 1)).toEqual([]);
    });
});
