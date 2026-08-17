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
    featureBlocks, ftKey, capturedFids, blockDiffSpans, rebaseCaptured, settledPendingFids,
    type FeatureText,
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

/**
 * The baseline a change is measured against belongs to ONE feature. It used to be
 * replaced wholesale on any real reload, so a daemon write to an unrelated feature
 * erased the change marks under the user's cursor mid-sentence.
 */
describe('rebaseCaptured — a projection only re-baselines what it was adopted for', () => {
    const ft = (title: string, ...paras: string[]): FeatureText => ({ title, paras });

    it('keeps the baseline of a feature the gate kept LOCAL', () => {
        const prev = new Map([['f-a', ft('A', 'as committed')]]);
        // The merged doc carries the user's in-flight text for f-a (it was not adopted).
        const next = new Map([['f-a', ft('A', 'as the user is typing it')]]);

        const out = rebaseCaptured(prev, next, new Set());
        expect(out.get('f-a')!.paras).toEqual(['as committed']);
    });

    it('moves the baseline of a feature that DID adopt the projection', () => {
        const prev = new Map([['f-a', ft('A', 'old')]]);
        const next = new Map([['f-a', ft('A', 'from the daemon')]]);

        const out = rebaseCaptured(prev, next, new Set(['f-a']));
        expect(out.get('f-a')!.paras).toEqual(['from the daemon']);
    });

    it('an unrelated feature updating cannot disturb the one being edited', () => {
        const prev = new Map([['f-a', ft('A', 'as committed')], ['f-b', ft('B', 'old b')]]);
        const next = new Map([['f-a', ft('A', 'mid-edit')], ['f-b', ft('B', 'new b')]]);

        const out = rebaseCaptured(prev, next, new Set(['f-b']));   // only B adopted
        expect(out.get('f-a')!.paras).toEqual(['as committed']);    // A's marks survive
        expect(out.get('f-b')!.paras).toEqual(['new b']);
    });

    it('drops features gone from the document and leaves brand-new ones baseline-free', () => {
        const prev = new Map([['f-gone', ft('G', 'x')]]);
        const next = new Map([['lid-new', ft('N', 'fresh')]]);

        const out = rebaseCaptured(prev, next, new Set());
        expect(out.has('f-gone')).toBe(false);
        // Present with the projection's own text; absence-from-prev is what marks it new.
        expect(out.get('lid-new')!.paras).toEqual(['fresh']);
    });
});

/**
 * "Pending" protects an un-acked local edit from a returning projection. It was
 * cleared only by adopting a NEWER projection — so typing and undoing left the
 * feature pending against text identical to the daemon's, and since the daemon
 * never advanced that feature's version, the gate refused every later update to
 * it for the life of the window.
 */
describe('settledPendingFids — an edit undone stops being pending', () => {
    const ft = (title: string, ...paras: string[]): FeatureText => ({ title, paras });

    it('drops a feature whose text is back to what it last adopted', () => {
        const current = new Map([['f-a', ft('A', 'original')]]);
        const adopted = new Map([['f-a', ftKey(ft('A', 'original'))]]);

        expect(settledPendingFids(new Set(['f-a']), current, adopted).has('f-a')).toBe(false);
    });

    it('keeps a feature that still differs', () => {
        const current = new Map([['f-a', ft('A', 'edited')]]);
        const adopted = new Map([['f-a', ftKey(ft('A', 'original'))]]);

        expect(settledPendingFids(new Set(['f-a']), current, adopted).has('f-a')).toBe(true);
    });

    it('keeps a feature that has never adopted anything (nothing to compare)', () => {
        const current = new Map([['lid-new', ft('N', 'fresh')]]);
        expect(settledPendingFids(new Set(['lid-new']), current, new Map()).has('lid-new')).toBe(true);
    });

    it('ignores whitespace-only differences, like every other captured comparison', () => {
        const current = new Map([['f-a', ft('A', 'original  ')]]);
        const adopted = new Map([['f-a', ftKey(ft('A', 'original'))]]);

        expect(settledPendingFids(new Set(['f-a']), current, adopted).has('f-a')).toBe(false);
    });
});

describe('the underline survives a settle, and stops at CJK boundaries', () => {
    it('changedRange does not swallow a Chinese paragraph around an insertion', async () => {
        const { changedRange } = await import('../webview/tiptap/hold-decorations');
        const base = '将提取的文本流转换为带编号的页面和行，因为下游策略模块需要自行判断；空白页会被保留。';
        const cur = '将提取的文本流转换为带编号的页面和行，因为下游策略模块需要自行判断, but how to determine this；空白页会被保留。';
        const r = changedRange(base, cur);
        expect(r).not.toBeNull();
        const marked = cur.slice(r!.start, r!.end);
        // The insertion, snapped to at most a couple of neighbouring characters —
        // NOT the whole paragraph, which is what whitespace-only word-snapping
        // produced for a script that writes without spaces.
        expect(marked).toContain('but how to determine this');
        expect(marked).not.toContain('将提取的文本流');
        expect(marked).not.toContain('空白页会被保留');
    });

    it('Latin snapping still grows to whole words', async () => {
        const { changedRange } = await import('../webview/tiptap/hold-decorations');
        const r = changedRange('the quick brown fox', 'the quicker brown fox');
        expect('the quicker brown fox'.slice(r!.start, r!.end)).toBe('quicker');
    });

    it('a held draft keeps its word diff after the local baseline adopts', async () => {
        // The vanishing underline: settle → daemon renders the new text back →
        // the feature adopts → the local baseline equals the current text → diff
        // empty. The DIRECTIVE kept the pre-edit text, and the captured build now
        // falls back to it, so the author's change stays underlined until they
        // press Commit & send.
        const { buildCapturedDecorations } = await import('../webview/tiptap/captured-decorations');
        const { Schema } = await import('@tiptap/pm/model');
        const schema = new Schema({
            nodes: {
                doc: { content: 'block+' },
                featureHeading: {
                    group: 'block', content: 'inline*',
                    attrs: { fid: { default: null }, localId: { default: null } },
                },
                paragraph: { group: 'block', content: 'inline*' },
                text: { group: 'inline' },
            },
        });
        const doc = schema.node('doc', null, [
            schema.node('featureHeading', { fid: 'f-1' }, [schema.text('Summary total rounding')]),
            schema.node('paragraph', null, [schema.text('Rounds once at the summary so I would like to remove this feature.')]),
        ]);
        // Local baseline has ADOPTED the settled text (equal to current) …
        const baseline = new Map([['f-1', {
            title: 'Summary total rounding',
            paras: ['Rounds once at the summary so I would like to remove this feature.'],
        }]]);
        // … but the directive still knows what the edit displaced.
        const detail = { 'f-1': {
            kind: 'amend', intent: 'update the summary rounding',
            baseline: 'Rounds once at the summary',
        } };
        const withFallback = buildCapturedDecorations(doc, new Set(['f-1']), baseline, detail);
        const without = buildCapturedDecorations(doc, new Set(['f-1']), baseline, {});
        const adds = (set: import('@tiptap/pm/view').DecorationSet): number =>
            set.find().filter(d => (d as unknown as { type: { attrs: { class?: string } } })
                .type.attrs?.class === 'ce-captured-add').length;
        expect(adds(without)).toBe(0);
        expect(adds(withFallback)).toBeGreaterThan(0);
    });
});
