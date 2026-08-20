/**
 * edit-baseline.test.ts — what the author's own edits are measured against, and what
 * they look like once measured.
 *
 * The baseline bookkeeping half (`state/edit-baseline.ts`) is what survived the
 * settlement redesign intact: deciding WHICH text a human diff runs against was never
 * about decorations. The marks themselves moved to `state/settlement.ts`, and the
 * span-positioning tests moved with them — they are at the bottom of this file, now
 * asked of `claimsFor`, because the rules they pin are the same rules.
 *
 * Load-bearing rules pinned here:
 *   - EVERY changed feature is captured (no size/code-implying threshold).
 *   - a held draft stays captured even after the daemon renders its prose back.
 *   - a handed-off (staged & sent) feature is NEVER captured — pending takes over.
 *   - whitespace-only edits don't register (ftKey matches the renderer's normalization).
 *   - a baseline moves only for a feature that ADOPTED the projection.
 */
import { describe, it, expect } from 'vitest';
import { EditProvenance } from '../state/edit-provenance';
import type { FeatureUnit } from '../state/commands-from-doc';
import {
    featureBlocks, ftKey, capturedFids, rebaseCaptured, settledPendingFids,
    type FeatureText,
} from '../state/edit-baseline';
import { claimsFor } from '../state/settlement';
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

describe('the human channel: what your own editing looks like', () => {
    // These moved here with the marks themselves. `blockDiffSpans` computed them for a
    // decoration layer that no longer exists; `claimsFor` computes them for all three
    // channels at once, so the rules below are stated once instead of per-layer.
    //
    // The human channel is INK ONLY — no diff view. The claims below still carry
    // removals, because a deletion-only edit has no added words to ink and the margin
    // marker has to know the feature is unsettled; the DRAWING drops them, in one place
    // (`settlement-decorations`), which is where a rendering decision belongs.
    const ft = (title: string, ...paras: string[]) => ({ title, paras });
    const spans = (base: string, cur: string) =>
        claimsFor({ projected: ft('T', base), live: ft('T', cur) })
            .filter(c => c.channel === 'human');

    it('an addition covers the words you inserted', () => {
        const cur = 'Login and OAuth.';
        const add = spans('Login.', cur).find(c => c.edit === 'add')!;
        expect(add.end).toBeGreaterThan(add.start);
        expect(cur.slice(add.start, add.end)).toContain('OAuth');
    });

    it('a deletion-only edit still produces a claim, so the marker can see it', () => {
        // Nothing is drawn for it, but a feature you have only deleted from is not
        // settled — and with no claim the margin would say it was.
        const out = spans("I don't think", 'I think');
        expect(out.length).toBeGreaterThan(0);
        expect(out.some(c => c.edit === 'add')).toBe(false);
    });

    it('the SAME shape from the code channel keeps the removed words', () => {
        // There it means somebody else replaced your sentence, and what they took out
        // is the whole point — so the code channel does draw its ghost.
        const projected = ft('T', 'the dog');
        const claims = claimsFor({
            code: { layerId: 'e-1', prev: ft('T', 'the cat') }, projected, live: projected,
        });
        expect(claims.some(c => c.channel === 'code' && c.removed?.includes('cat'))).toBe(true);
    });

    it('no change → nothing', () => {
        expect(spans('same', 'same')).toEqual([]);
    });
});

/**
 * The baseline a change is measured against belongs to ONE feature. It used to be
 * replaced wholesale on any real reload, so a daemon write to an unrelated feature
 * erased the change marks under the user's cursor mid-sentence.
 */
describe('rebaseCaptured — a projection only re-baselines what it was adopted for', () => {
    it('a held DRAFT keeps its baseline through the settle echo', () => {
        // The flicker: the settle commits, the daemon echoes the committed text
        // back, the version gate ADOPTS it (it is newer — it is the echo), and
        // the baseline moves to the settled text. Diff empty, underline gone,
        // for the second or two until the sidecar's directive baseline arrives.
        // A fid the author holds as a draft is mid-lifecycle: its baseline
        // belongs to the edit, not to the echo, so adoption must not move it.
        const prev = new Map([['f-a', ft('Auth', 'Login and sessions.')]]);
        const next = new Map([['f-a', ft('Auth', 'Login and sessions. And remove this.')]]);
        const out = rebaseCaptured(prev, next, new Set(['f-a']), new Set(['f-a']));
        expect(out.get('f-a')).toEqual(ft('Auth', 'Login and sessions.'));
    });

    it('a non-draft adoption still re-baselines as before', () => {
        const prev = new Map([['f-a', ft('Auth', 'Old.')]]);
        const next = new Map([['f-a', ft('Auth', 'New from the daemon.')]]);
        const out = rebaseCaptured(prev, next, new Set(['f-a']), new Set());
        expect(out.get('f-a')).toEqual(ft('Auth', 'New from the daemon.'));
    });

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

describe('the ink survives a settle, and stops at CJK boundaries', () => {
    it('a Chinese description does not light up whole around an English insertion', async () => {
        // The pilot bug: five English words typed into a Chinese description marked the
        // ENTIRE node, because word-snapping grew outward to the previous space and a
        // script that writes without spaces has none. It used to be `changedRange`'s job
        // (hold-decorations); the settlement model answers it with script-aware word
        // tokens, so the guarantee is pinned against the diff that draws the ink now.
        const { wordDiff } = await import('../state/doc-diff');
        const base = '将提取的文本流转换为带编号的页面和行，因为下游策略模块需要自行判断；空白页会被保留。';
        const cur = '将提取的文本流转换为带编号的页面和行，因为下游策略模块需要自行判断, but how to determine this；空白页会被保留。';
        const inked = wordDiff(base, cur).filter(r => r.t === 'ins').map(r => r.s).join('');
        expect(inked).toContain('but how to determine this');
        expect(inked).not.toContain('将提取的文本流');
        expect(inked).not.toContain('空白页会被保留');
    });

    it('Latin still inks whole words', async () => {
        const { wordDiff } = await import('../state/doc-diff');
        const inked = wordDiff('the quick brown fox', 'the quicker brown fox')
            .filter(r => r.t === 'ins').map(r => r.s).join('');
        expect(inked).toBe('quicker');
    });

    it('a held draft keeps its diff after the local baseline adopts', () => {
        // The vanishing underline: settle → the daemon renders the new text back → the
        // feature adopts → the local baseline equals the current text → the diff is
        // empty, and the mark saying "this is yours, the code has not caught up" clears
        // at the exact moment it starts being true.
        //
        // The DIRECTIVE still knows what the edit displaced, and the settlement model
        // takes that as `humanBase` — see settlement.ts, which is where this rule now
        // lives. Held here too because it is a property of the BASELINE choice, which is
        // this module's subject.
        const current = {
            title: 'Summary total rounding',
            paras: ['Rounds once at the summary so I would like to remove this feature.'],
        };
        // Adopted: `projected` already equals what the author typed.
        const withoutFallback = claimsFor({ projected: current, live: current });
        expect(withoutFallback).toEqual([]);

        // …but the directive kept the pre-edit text, so the mark survives.
        const withFallback = claimsFor({
            projected: current, live: current, committed: true,
            humanBase: { title: current.title, paras: ['Rounds once at the summary'] },
        });
        expect(withFallback.some(c => c.channel === 'human' && c.edit === 'add')).toBe(true);
    });
});

// ── Deleting a heading, across two settles ───────────────────────────────────
//
// Absence retires, but not on the settle that first notices it. A heading is
// missing for a moment in the middle of edits that were never about deleting it
// — between a cut and its paste, inside an undo storm — and acting on the first
// frame of one of those destroys a feature and detaches its bindings, which is
// the class the 2026-08-01 robustness plan was written for. The deletion has to
// hold. EditProvenance is where "hold" is remembered, so it is tested here rather
// than through the pure diff.
describe('a deletion retires when it holds, and not before', () => {
    const unit = (fid: string, title: string): FeatureUnit => ({
        fid, localId: null, title, description: '', parentId: null,
        retired: false, realized: true,
    });
    const TREE = [unit('f-1', 'Alpha feature one'), unit('f-2', 'Beta feature two'),
                  unit('f-3', 'Gamma feature three')];

    const retires = (cmds: readonly { kind: string; feature_id?: string }[]): string[] =>
        cmds.filter(c => c.kind === 'retire').map(c => c.feature_id!);

    it('says nothing the first time a heading is missing', () => {
        const p = new EditProvenance('s');
        p.observe(TREE, 1);
        expect(retires(p.settle(TREE.slice(0, 2), 1, 't1'))).toEqual([]);
    });

    it('retires it when the next settle still misses it', () => {
        const p = new EditProvenance('s');
        p.observe(TREE, 1);
        p.settle(TREE.slice(0, 2), 1, 't1');                      // first absence
        expect(retires(p.settle(TREE.slice(0, 2), 1, 't2'))).toEqual(['f-3']);
    });

    it('a node that comes back has to earn its retire again', () => {
        // The paste landed, or the undo was undone. Two absences that were not
        // consecutive are not a deletion held for two settles.
        const p = new EditProvenance('s');
        p.observe(TREE, 1);
        p.settle(TREE.slice(0, 2), 1, 't1');                      // gone
        expect(retires(p.settle(TREE, 1, 't2'))).toEqual([]);      // back
        expect(retires(p.settle(TREE.slice(0, 2), 1, 't3'))).toEqual([]);  // gone again — first
        expect(retires(p.settle(TREE.slice(0, 2), 1, 't4'))).toEqual(['f-3']);
    });

    it('never retires the whole tree, however long it is missing', () => {
        const p = new EditProvenance('s');
        p.observe(TREE, 1);
        p.settle([], 1, 't1');
        expect(retires(p.settle([], 1, 't2'))).toEqual([]);
    });
});
