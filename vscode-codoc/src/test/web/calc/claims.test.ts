import { describe, expect, it } from 'vitest';

import { authorPaints, displayRuns, paintsFor } from '../../../web/calc/claims';
import type { Claim, Projection, ProjectedBlock, Run, TextField } from '../../../web/calc/projection';

const run = (op: string, start: number, end: number, actor: string, plan: string | null = null): Run =>
    ({ op, k0: 0, start, end, actor, at: '1', plan });

function paragraph(text: string, runs: Run[]): { b: ProjectedBlock; f: TextField } {
    const f: TextField = { text, runs, spans: [] };
    return { b: { id: 'p', kind: 'paragraph', level: 0, rank: 'a', head: 1, fields: { text: f } }, f };
}

const claim = (start: number, end: number, channel: Claim['channel'], stage: Claim['stage'], actor: string,
               text = '', plan?: string): Claim => ({ bid: 'p', path: 'text', start, end, channel, stage, actor, text, plan });

/** The paints for paragraph `p` of feature `h`, given the feature's claims. */
function paints(claims: Claim[], viewer = 'human:a', plans: Projection['plans'] = []) {
    const { b } = paragraph('keep this maybe', []);
    const proj: Projection = { head: 1, frontier: {}, blocks: [b], phases: {}, plans, bindings: { by_feature: {} }, claims: { h: claims } };
    return paintsFor(b, proj, false, viewer);
}

describe('ink', () => {
    it("is the viewer's own words only: a teammate's unsent words are plain", () => {
        const claims = [claim(0, 5, 'human', 'unsent', 'human:a@x.org'), claim(5, 11, 'human', 'sent', 'human:bob@x.org')];
        expect(paints(claims, 'human:a@x.org').map(p => [p.start, p.end, p.cls])).toEqual([[0, 5, 'ink-unsent']]);
        expect(paints(claims, 'human:bob@x.org').map(p => [p.start, p.end, p.cls, p.title]))
            .toEqual([[5, 11, 'ink-sent', 'yours, handed off']]);
    });

    it('wins over what the code did on the same words', () => {
        const claims = [claim(0, 4, 'code', 'added', 'loop', 'keep'), claim(2, 6, 'human', 'unsent', 'human:a'),
                        claim(9, 9, 'code', 'removed', 'loop', ' gone')];
        expect(paints(claims).map(p => [p.cls, p.start, p.end, p.text])).toEqual([
            ['code-removed', 9, 9, ' gone'], ['ink-unsent', 2, 6, undefined]]);
    });
});

describe('plan paints', () => {
    it("plan words are gray, cuts are struck, and both name the proposer and the plan's note", () => {
        const plans = [{ id: 'pl', actor: 'agent:x', kind: 'edit', target: 'p', note: 'n', status: 'open' }];
        const claims = [claim(10, 15, 'plan', 'open', 'agent:x', 'maybe', 'pl'), claim(5, 9, 'plan', 'removed', 'agent:x', 'this', 'pl')];
        expect(paints(claims, 'human:a', plans).map(p => [p.cls, p.title])).toEqual([
            ['plan-open', 'agent:x proposed this: n'], ['plan-cut', 'agent:x proposes removing this']]);
    });

    it('a proposed mark toggle is its own paint, not the mark itself', () => {
        const claims = [
            claim(8, 12, 'plan', 'unmarked', 'loop', 'bold:once', 'p1'),
            claim(0, 7, 'plan', 'marked', 'agent:x', 'bold:Retries', 'p2'),
        ];
        expect(paints(claims).map(p => [p.cls, p.start, p.end, p.text, p.title])).toEqual([
            ['plan-unmark', 8, 12, 'once', 'loop proposes unmarking this as bold'],
            ['plan-mark', 0, 7, 'Retries', 'agent:x proposes marking this as bold'],
        ]);
    });
});

describe('history paints', () => {
    it('display runs keep each character with its author, in any script', () => {
        // An agent changed one character of a person's Chinese sentence: 两 → 三.
        const zh = paragraph('重试三次后放弃', [run('s:1', 0, 2, 'human:a'), run('a:1', 2, 3, 'agent:x'), run('s:2', 3, 7, 'human:a')]);
        expect(displayRuns(zh.f)).toEqual([
            { start: 0, end: 2, actor: 'human:a' }, { start: 2, end: 3, actor: 'agent:x' }, { start: 3, end: 7, actor: 'human:a' },
        ]);
        // A boundary inside an English word stays where it is too.
        const en = paragraph('keep this maybe', [run('s:1', 0, 7, 'human:a'), run('l:1', 7, 15, 'loop')]);
        expect(displayRuns(en.f)).toEqual([{ start: 0, end: 7, actor: 'human:a' }, { start: 7, end: 15, actor: 'loop' }]);
    });

    it('adjacent display runs by one author merge', () => {
        const { f } = paragraph('keep this', [run('s:1', 0, 5, 'human:a'), run('s:2', 5, 9, 'human:a')]);
        expect(displayRuns(f)).toEqual([{ start: 0, end: 9, actor: 'human:a' }]);
    });

    it('one class per kind of actor', () => {
        const { b, f } = paragraph('ab cd', [run('x:1', 0, 3, 'human:a'), run('y:1', 3, 5, 'loop')]);
        expect(authorPaints(b, f).map(p => [p.cls, p.start, p.end])).toEqual([['author-human', 0, 3], ['author-loop', 3, 5]]);
    });
});
