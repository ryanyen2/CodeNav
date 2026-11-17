import { describe, expect, it } from 'vitest';

import { authorPaints, displayRuns, inkPaints, markPaints, planPaints } from '../../../web/calc/claims';
import type { Claim, ProjectedBlock, Run, TextField } from '../../../web/calc/projection';

const run = (op: string, start: number, end: number, actor: string, plan: string | null = null): Run =>
    ({ op, k0: 0, start, end, actor, at: '1', plan });

function paragraph(text: string, runs: Run[]): { b: ProjectedBlock; f: TextField } {
    const f: TextField = { text, runs, spans: [], suggest_del: [] };
    return { b: { id: 'p', kind: 'paragraph', level: 0, rank: 'a', head: 1, fields: { text: f } }, f };
}

describe('ink', () => {
    it('is unsent until the hand-off frontier and gone past the realized one', () => {
        const { b, f } = paragraph('abcdef', [run('s:1', 0, 2, 'human:a'), run('s:5', 2, 4, 'human:a'), run('s:9', 4, 6, 'loop')]);
        expect(inkPaints(b, f, { s: 1 }, { s: 5 })).toEqual([{ bid: 'p', start: 2, end: 4, cls: 'ink-sent', title: 'yours, handed off' }]);
        // Nothing handed off: the person's two runs are unsent and join into
        // one paint. The loop's run is nobody's ink.
        expect(inkPaints(b, f, {}, {}).map(p => [p.start, p.end, p.cls])).toEqual([[0, 4, 'ink-unsent']]);
    });
});

describe('plan paints', () => {
    it('plan runs are gray and suggested deletions are cuts', () => {
        const { b, f } = paragraph('keep this maybe', [run('s:1', 0, 10, 'human:a'), run('a:1', 10, 15, 'agent:x', 'pl')]);
        f.suggest_del = [{ plan: 'pl', start: 5, end: 9 }];
        const paints = planPaints(b, f, [{ id: 'pl', actor: 'agent:x', kind: 'edit', target: 'p', note: 'n', status: 'open' }]);
        expect(paints.map(x => x.cls)).toEqual(['plan-open', 'plan-cut']);
    });

    it('a proposed mark toggle is its own paint, not the mark itself', () => {
        const claim = (start: number, end: number, channel: Claim['channel'], stage: Claim['stage'], actor: string, text: string): Claim =>
            ({ bid: 'p', path: 'text', start, end, channel, stage, actor, text });
        const claims = [
            claim(8, 12, 'plan', 'unmarked', 'loop', 'bold:once'),
            claim(0, 7, 'plan', 'marked', 'agent:x', 'bold:Retries'),
            claim(0, 3, 'plan', 'open', 'loop', 'new'),
            claim(0, 3, 'code', 'added', 'loop', 'x'),
        ];
        expect(markPaints(claims).map(p => [p.cls, p.start, p.end, p.text, p.title])).toEqual([
            ['plan-unmark', 8, 12, 'once', 'loop proposes unmarking this as bold'],
            ['plan-mark', 0, 7, 'Retries', 'agent:x proposes marking this as bold'],
        ]);
        expect(markPaints(undefined)).toEqual([]);
    });
});

describe('history paints', () => {
    it('display runs snap to word boundaries', () => {
        const { f } = paragraph('keep this maybe', [run('s:1', 0, 7, 'human:a'), run('l:1', 7, 15, 'loop')]);
        expect(displayRuns(f)).toEqual([{ start: 0, end: 9, actor: 'human:a' }, { start: 9, end: 15, actor: 'loop' }]);
    });

    it('one class per kind of actor', () => {
        const { b, f } = paragraph('ab cd', [run('x:1', 0, 3, 'human:a'), run('y:1', 3, 5, 'loop')]);
        expect(authorPaints(b, f).map(p => [p.cls, p.start, p.end])).toEqual([['author-human', 0, 3], ['author-loop', 3, 5]]);
    });
});
