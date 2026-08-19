import { describe, it, expect } from 'vitest';
import {
    claimsFor, forwardMap, droppedLayers, rebase, LOCAL_EDIT_LAYER,
    type FeatureLayers, type FeatureText, type Claim,
} from '../state/settlement';
import { wordDiff, sentenceDiff } from '../state/doc-diff';

const ft = (title: string, ...paras: string[]): FeatureText => ({ title, paras });

/** Claims of one channel, in document order, as terse tuples for readable assertions. */
function of(claims: Claim[], channel: Claim['channel']): [string, number, number, string?][] {
    return claims.filter(c => c.channel === channel)
        .map(c => [c.edit, c.start, c.end, c.removed] as [string, number, number, string?]);
}

describe('forwardMap — carrying a span across an edit that happened after it', () => {
    it('shifts an offset by the insertions before it', () => {
        const map = forwardMap(wordDiff('a c', 'a b c'));
        // "c" starts at 2 in the old text, at 4 in the new.
        expect(map(2)).toBe(4);
    });

    it('collapses an offset inside deleted text to where the deletion happened', () => {
        const map = forwardMap(wordDiff('a b c', 'a c'));
        const at = map(2); // inside the deleted "b "
        expect(at).toBe(map(3));
        expect(at).toBeLessThanOrEqual(2);
    });

    it('pins the ends', () => {
        const map = forwardMap(wordDiff('one two', 'one two three'));
        expect(map(0)).toBe(0);
        expect(map('one two'.length)).toBe('one two three'.length);
    });

    it('is identity when nothing changed', () => {
        const map = forwardMap(wordDiff('same text', 'same text'));
        for (const i of [0, 3, 5, 9]) expect(map(i)).toBe(i);
    });
});

describe('claimsFor — the human channel', () => {
    it('says nothing about a feature nobody touched', () => {
        expect(claimsFor({ projected: ft('T', 'p'), live: ft('T', 'p') })).toEqual([]);
    });

    it('marks typed words open until they are handed off', () => {
        const base = { projected: ft('T', 'the retry is capped'), live: ft('T', 'the retry is capped at five') };
        expect(claimsFor(base).every(c => c.stage === 'open')).toBe(true);
        expect(claimsFor({ ...base, committed: true }).every(c => c.stage === 'committed')).toBe(true);
    });

    it('carries a deletion as a point plus the words it removed', () => {
        const claims = claimsFor({ projected: ft('T', 'I do not think so'), live: ft('T', 'I think so') });
        const dels = of(claims, 'human').filter(c => c[0] === 'del');
        expect(dels.length).toBe(1);
        expect(dels[0][1]).toBe(dels[0][2]);          // zero width — nothing left to cover
        expect(dels[0][3]).toContain('do not');
    });

    it('diffs a title by word and a paragraph by sentence', () => {
        const titleClaims = claimsFor({ projected: ft('Retry policy'), live: ft('Retry budget') });
        expect(of(titleClaims, 'human').some(c => c[0] === 'add')).toBe(true);
        // A one-word fix inside a two-sentence paragraph still strikes the sentence,
        // because the sentence is the unit of the claim being made.
        const p = claimsFor({
            projected: ft('T', 'It retries twice. It then gives up.'),
            live: ft('T', 'It retries five times. It then gives up.'),
        });
        const adds = of(p, 'human').filter(c => c[0] === 'add');
        expect(adds.length).toBe(1);
    });
});

describe('claimsFor — the code channel', () => {
    const code = { layerId: 'e-1', prev: ft('T', 'The uploader retries twice.') };

    it('reports what the loop rewrote, against the text it displaced', () => {
        const claims = claimsFor({
            code, projected: ft('T', 'The uploader retries five times.'),
            live: ft('T', 'The uploader retries five times.'),
        });
        const c = of(claims, 'code');
        expect(c.some(x => x[0] === 'add')).toBe(true);
        expect(c.some(x => x[0] === 'del' && x[3]?.includes('twice'))).toBe(true);
        expect(claims.filter(x => x.channel === 'code').every(x => x.stage === 'landed')).toBe(true);
    });

    it('lands its spans on the words as they now read, after the author typed above them', () => {
        const projected = ft('T', 'Intro.', 'The uploader retries five times.');
        const live = ft('T', 'Intro, expanded a lot.', 'The uploader retries five times.');
        const claims = claimsFor({
            code: { layerId: 'e-1', prev: ft('T', 'Intro.', 'The uploader retries twice.') },
            projected, live,
        });
        // The rewritten sentence is in paragraph 1, whose text the author did not touch,
        // so its offsets must be unchanged by the edit to paragraph 0.
        const inPara1 = claims.filter(c => c.channel === 'code' && c.block.kind === 'para' && c.block.index === 1);
        expect(inPara1.length).toBeGreaterThan(0);
        const add = inPara1.find(c => c.edit === 'add')!;
        expect(live.paras[1].slice(add.start, add.end)).toContain('five times');
    });

    it('yields the span to the author when they overwrite it — those words are theirs now', () => {
        const claims = claimsFor({
            code: { layerId: 'e-1', prev: ft('T', 'The uploader retries twice.') },
            projected: ft('T', 'The uploader retries five times.'),
            live: ft('T', 'The uploader gives up immediately.'),
        });
        // Nothing of the loop's sentence survives, so it holds no live range.
        expect(of(claims, 'code').some(c => c[0] === 'add')).toBe(false);
        expect(of(claims, 'human').length).toBeGreaterThan(0);
    });
});

describe('claimsFor — the plan channel', () => {
    const runs = (block: 'title' | number, o: string, n: string) => ({
        block: block === 'title' ? { kind: 'title' as const } : { kind: 'para' as const, index: block },
        runs: block === 'title' ? wordDiff(o, n) : sentenceDiff(o, n),
    });

    it('marks the materialized proposal, carrying its stage', () => {
        const projected = ft('T', 'It retries twice.');
        const planned = ft('T', 'It retries twice. It then backs off.');
        const layers: FeatureLayers = {
            projected, planned, live: planned,
            plan: { layerId: 'e-9', stage: 'proposed', runs: [runs(0, 'It retries twice.', 'It retries twice. It then backs off.')] },
        };
        const claims = claimsFor(layers);
        expect(claims.filter(c => c.channel === 'plan').every(c => c.stage === 'proposed')).toBe(true);
        const add = claims.find(c => c.channel === 'plan' && c.edit === 'add')!;
        expect(planned.paras[0].slice(add.start, add.end)).toContain('backs off');
    });

    it('keeps the author\'s typing separate from the plan text it was typed around', () => {
        const projected = ft('T', 'It retries twice.');
        const planned = ft('T', 'It retries twice. It then backs off.');
        const live = ft('T', 'It retries twice. It then backs off. We should measure this.');
        const claims = claimsFor({
            projected, planned, live,
            plan: { layerId: 'e-9', stage: 'proposed', runs: [runs(0, projected.paras[0], planned.paras[0])] },
        });
        const human = claims.filter(c => c.channel === 'human');
        expect(human.length).toBe(1);
        expect(live.paras[0].slice(human[0].start, human[0].end)).toContain('measure');
        // …and the plan's own span still covers only the plan's sentence.
        const plan = claims.find(c => c.channel === 'plan' && c.edit === 'add')!;
        expect(live.paras[0].slice(plan.start, plan.end)).toContain('backs off');
        expect(live.paras[0].slice(plan.start, plan.end)).not.toContain('measure');
    });
});

describe('claimsFor — the three channels on one paragraph', () => {
    it('stacks them without any of them standing down', () => {
        // The loop rewrote the sentence; an agent then proposed another; the author
        // typed a third. All three are true at once and all three are marked.
        const projected = ft('T', 'It retries five times.');
        const planned = ft('T', 'It retries five times. It then backs off.');
        const live = ft('T', 'It retries five times. It then backs off. Measure it.');
        const claims = claimsFor({
            code: { layerId: 'e-1', prev: ft('T', 'It retries twice.') },
            plan: { layerId: 'e-9', stage: 'accepted', runs: [{ block: { kind: 'para', index: 0 }, runs: sentenceDiff(projected.paras[0], planned.paras[0]) }] },
            projected, planned, live,
        });
        expect(new Set(claims.map(c => c.channel))).toEqual(new Set(['code', 'plan', 'human']));
        // …and they are handed over in stacking order: background, then opacity, then ink.
        const order = claims.map(c => c.channel);
        expect(order.indexOf('code')).toBeLessThan(order.indexOf('plan'));
        expect(order.indexOf('plan')).toBeLessThan(order.indexOf('human'));
    });

    it('shows a plan sentence the build then cut back as plan ink over a code deletion', () => {
        // Planned two sentences; the build kept one and dropped the other.
        const prev = ft('T', 'It retries. It then backs off.');
        const projected = ft('T', 'It retries.');
        const claims = claimsFor({
            code: { layerId: 'e-1', prev }, projected, live: projected,
        });
        const del = claims.find(c => c.channel === 'code' && c.edit === 'del')!;
        expect(del.removed).toContain('backs off');
    });
});

describe('the drop rules', () => {
    it('retires a layer the daemon has stopped offering', () => {
        expect(droppedLayers(new Set(['e-1', 'e-2']), new Set(['e-2'])))
            .toEqual(new Set(['e-1']));
    });

    it('never retires the local edit layer — it is not the daemon\'s to withdraw', () => {
        expect(droppedLayers(new Set([LOCAL_EDIT_LAYER]), new Set())).toEqual(new Set());
    });

    it('rebases onto what is on screen, so an unanswered claim reads as accepted', () => {
        const live = ft('T', 'planned words the author never answered');
        const next = rebase(live);
        expect(claimsFor({ projected: next, live })).toEqual([]);
    });
});
