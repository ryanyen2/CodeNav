/**
 * captured-decorations.test.ts — U3 guard for the "recorded, not sent" phase.
 *
 * Pure helpers only (the vitest node env has no DOM/editor): the feature-text projection
 * and the captured-set partition. The visual rail/dot + its place in the lifecycle ramp
 * are the EDH gate. The load-bearing rules pinned here:
 *   - EVERY changed feature is captured (no size/code-implying threshold).
 *   - a held draft stays captured even after the daemon renders its prose back.
 *   - a handed-off (staged & sent) feature is NEVER captured — pending takes over.
 */
import { describe, it, expect } from 'vitest';
import { featureTextFromJson, capturedFids } from '../webview/tiptap/captured-decorations';
import { makeDoc, featureHeadingNode, paragraphNode, textToInlineRuns, type PMNode } from '../state/pm-doc';

function feat(fid: string, title: string, desc: string): PMNode[] {
    return [
        featureHeadingNode({ fid, level: 0, retired: false, realized: true }, textToInlineRuns(title)),
        paragraphNode(textToInlineRuns(desc)),
    ];
}

function doc(): PMNode {
    return makeDoc([
        featureHeadingNode({ fid: 'f-a', level: 0, retired: false, realized: true }, textToInlineRuns('Auth')),
        paragraphNode(textToInlineRuns('Login and sessions.')),
        featureHeadingNode({ fid: 'f-b', level: 0, retired: false, realized: true }, textToInlineRuns('Data')),
        paragraphNode(textToInlineRuns('Persistence.')),
    ]);
}

describe('U3: featureTextFromJson', () => {
    it('maps each fid to its title + description text', () => {
        const m = featureTextFromJson(doc());
        expect(m.get('f-a')).toBe('Auth\nLogin and sessions.');
        expect(m.get('f-b')).toBe('Data\nPersistence.');
    });

    it('is stable across an identical doc (baseline == current ⇒ no diff)', () => {
        const a = featureTextFromJson(doc());
        const b = featureTextFromJson(doc());
        for (const [fid, text] of a) expect(b.get(fid)).toBe(text);
    });

    it('normalizes whitespace so a round-tripped edit is not falsely captured (review Finding 1)', () => {
        // The daemon strips trailing/leading whitespace + blank-line runs on render. The
        // projection must apply the SAME normalization, else a feature whose only change was
        // whitespace stays "captured" forever after the round-trip.
        const withWs = makeDoc(feat('f-a', 'Auth ', 'Login and sessions.   '));
        const clean = makeDoc(feat('f-a', 'Auth', 'Login and sessions.'));
        expect(featureTextFromJson(withWs).get('f-a')).toBe(featureTextFromJson(clean).get('f-a'));
        // → capturedFids sees no diff once the daemon renders the whitespace away
        const empty = new Set<string>();
        expect(capturedFids(featureTextFromJson(clean), featureTextFromJson(withWs), empty, empty).size).toBe(0);
    });
});

describe('U3: capturedFids', () => {
    const base = featureTextFromJson(doc());
    const empty = new Set<string>();

    it('captures a feature whose text changed vs baseline (any size, no threshold)', () => {
        const cur = new Map(base);
        cur.set('f-a', 'Auth\nLogin and sessions, plus OAuth.'); // a normal-sized prose edit
        expect(capturedFids(base, cur, empty, empty)).toEqual(new Set(['f-a']));
    });

    it('captures even a one-character edit (no "is it big enough?" gate)', () => {
        const cur = new Map(base);
        cur.set('f-b', 'Data\nPersistence!'); // single char
        expect([...capturedFids(base, cur, empty, empty)]).toContain('f-b');
    });

    it('does NOT capture an unchanged feature', () => {
        expect(capturedFids(base, new Map(base), empty, empty).size).toBe(0);
    });

    it('captures a held draft even when its text now matches the baseline (rendered back)', () => {
        // a code-implying draft: daemon rendered the prose to tree.codoc, so current == baseline,
        // but it is still recorded & not sent → stays captured via the drafts union.
        const drafts = new Set(['f-a']);
        expect(capturedFids(base, new Map(base), drafts, empty)).toEqual(new Set(['f-a']));
    });

    it('does NOT capture a handed-off feature — pending takes over (no double mark)', () => {
        const cur = new Map(base);
        cur.set('f-a', 'Auth\nLogin and sessions, plus OAuth.'); // still differs from baseline
        const handedOff = new Set(['f-a']);
        expect(capturedFids(base, cur, new Set(['f-a']), handedOff).size).toBe(0);
    });

    it('does NOT capture a brand-new feature absent from the baseline (left to the ADD flow)', () => {
        const cur = new Map(base);
        cur.set('f-new', 'Brand new\nfresh node');
        expect([...capturedFids(base, cur, empty, empty)]).not.toContain('f-new');
    });
});
