import { describe, expect, it } from 'vitest';

import { nextUndo, textAt, wordDiff, type HistoryWindow, type WindowEntry } from '../../calc/window';

const e = (pos: number, actor: string, batch: string, blocks: Record<string, string>, inverse: unknown[] = [{ kind: 'x', target: {}, payload: { pos } }], kind = 'text.insert'): WindowEntry =>
    ({ pos, id: `s:${pos}`, at: String(pos), actor, kind, batch, plan: null, caused_by: null, blocks, inverse: inverse as never });

describe('undo over the window', () => {
    it('picks the newest of my batches, skips undone ones, and redo targets undo batches', () => {
        const w: HistoryWindow = { start: 0, head: 5, before: {}, ops: [
            e(0, 'human:local', 'b1', { p: 'a' }), e(1, 'human:local', 'b1', { p: 'ab' }),
            e(2, 'loop', 'b2', { p: 'abc' }), e(3, 'human:local', 'b3', { p: 'abcd' }),
        ] };
        const u = nextUndo(w, 'human:local')!;
        expect(u.batch).toBe('u-b3');
        expect(u.ops).toEqual([{ kind: 'x', target: {}, payload: { pos: 3 } }]);
        w.ops.push(e(4, 'human:local', 'u-b3', { p: 'abc' }));
        expect(nextUndo(w, 'human:local')!.batch).toBe('u-b1');            // b3 is undone; b1 next, inverses newest first
        expect(nextUndo(w, 'human:local')!.ops.map(o => (o.payload as { pos: number }).pos)).toEqual([1, 0]);
        expect(nextUndo(w, 'human:local', true)!.batch).toBe('u-u-b3');    // redo
        expect(nextUndo(w, 'loop')!.batch).toBe('u-b2');
        expect(nextUndo(w, 'agent:x')).toBeNull();
    });
    it('will not undo a batch that may reach past the window', () => {
        const w: HistoryWindow = { start: 10, head: 12, before: {}, ops: [e(10, 'human:local', 'b1', { p: 'a' }), e(11, 'human:local', 'b1', { p: 'ab' })] };
        expect(nextUndo(w, 'human:local')).toBeNull();
    });
});

describe('text at a position', () => {
    it('reads the latest snapshot, then before, then live; a later add is absent', () => {
        const w: HistoryWindow = { start: 3, head: 6, before: { p: 'old' }, ops: [
            e(3, 'h', 'b', { p: 'new' }), e(4, 'h', 'b', { q: 'q!' }, [], 'block.add'), e(5, 'h', 'b', { q: 'q!!' }),
        ] };
        expect(textAt(w, 'p', -1, 'live')).toBe('old');
        expect(textAt(w, 'p', 0, 'live')).toBe('new');
        expect(textAt(w, 'q', 0, 'q live')).toBeNull();
        expect(textAt(w, 'q', 1, 'q live')).toBe('q!');
        expect(textAt(w, 'r', 0, 'untouched')).toBe('untouched');
    });
    it('word diff for display', () => {
        expect(wordDiff('a b c', 'a x c')).toEqual([['eq', 'a '], ['del', 'b'], ['ins', 'x'], ['eq', ' c']]);
    });
});

describe('proposed mark toggles', () => {
    it('are painted as dashed plan marks, not as the mark itself', async () => {
        const { markPaints } = await import('../../calc/claims');
        const claims = [
            { bid: 'p', path: 'text', start: 8, end: 12, channel: 'plan', stage: 'unmarked', actor: 'loop', text: 'bold:once' },
            { bid: 'p', path: 'text', start: 0, end: 7, channel: 'plan', stage: 'marked', actor: 'agent:x', text: 'bold:Retries' },
            { bid: 'p', path: 'text', start: 0, end: 3, channel: 'plan', stage: 'open', actor: 'loop', text: 'new' },
            { bid: 'p', path: 'text', start: 0, end: 3, channel: 'code', stage: 'added', actor: 'loop', text: 'x' },
        ];
        expect(markPaints(claims as never).map(p => [p.cls, p.start, p.end, p.text, p.title])).toEqual([
            ['plan-unmark', 8, 12, 'once', 'loop proposes unmarking this as bold'],
            ['plan-mark', 0, 7, 'Retries', 'agent:x proposes marking this as bold'],
        ]);
        expect(markPaints(undefined)).toEqual([]);
    });
});
