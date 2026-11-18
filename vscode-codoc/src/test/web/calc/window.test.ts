import { describe, expect, it } from 'vitest';

import { nextUndo, textAt, wordDiff, type HistoryWindow, type WindowEntry } from '../../../web/calc/window';

/** `{bid: text}` as the window writes it: `{bid: {text}}`, one entry per Text field. */
const text = (o: Record<string, string>) => Object.fromEntries(Object.entries(o).map(([b, t]) => [b, { text: t }]));
const e = (pos: number, actor: string, batch: string, blocks: Record<string, string>, inverse: unknown[] = [{ kind: 'x', target: {}, payload: { pos } }], kind = 'text.insert'): WindowEntry =>
    ({ pos, id: `s:${pos}`, at: String(pos), actor, kind, batch, plan: null, caused_by: null, blocks: text(blocks), inverse: inverse as never });

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
    it('skips a snapshot fold, a batch with no inverse, and a batch whose undo was refused', () => {
        const w: HistoryWindow = { start: 0, head: 4, before: {}, ops: [
            e(0, 'human:local', 'b1', { p: 'a' }), e(1, 'human:local', 'b2', { p: 'ab' }),
            e(2, 'human:local', 'n1', { p: 'ab' }, []), e(3, 'human:local', 'snap-1', { p: 'ab' }),
        ] };
        expect(nextUndo(w, 'human:local')!.batch).toBe('u-b2');
        expect(nextUndo(w, 'human:local', false, new Set(['u-b2']))!.batch).toBe('u-b1');
    });
});

describe('text at a position', () => {
    it('reads the latest snapshot, then before, then live; a later add is absent', () => {
        const w: HistoryWindow = { start: 3, head: 6, before: text({ p: 'old' }), ops: [
            e(3, 'h', 'b', { p: 'new' }), e(4, 'h', 'b', { q: 'q!' }, [], 'block.add'), e(5, 'h', 'b', { q: 'q!!' }),
        ] };
        expect(textAt(w, 'p', 'text', -1, 'live')).toBe('old');
        expect(textAt(w, 'p', 'text', 0, 'live')).toBe('new');
        expect(textAt(w, 'q', 'text', 0, 'q live')).toBeNull();
        expect(textAt(w, 'q', 'text', 1, 'q live')).toBe('q!');
        expect(textAt(w, 'r', 'text', 0, 'untouched')).toBe('untouched');
    });
    it('reads the field asked for: a plugin block has one text per Text field', () => {
        const w: HistoryWindow = { start: 0, head: 2, before: { img: { alt: 'a', caption: 'old' } }, ops: [
            { ...e(0, 'h', 'b', {}), blocks: { img: { alt: 'a', caption: 'new' } } },
        ] };
        expect(textAt(w, 'img', 'caption', -1, 'live')).toBe('old');
        expect(textAt(w, 'img', 'caption', 0, 'live')).toBe('new');
        expect(textAt(w, 'img', 'alt', 0, 'live')).toBe('a');
    });
    it('word diff for display', () => {
        expect(wordDiff('a b c', 'a x c')).toEqual([['eq', 'a '], ['del', 'b'], ['ins', 'x'], ['eq', ' c']]);
    });
});

describe('the window reader after the daemon publishes births and batch starts', () => {
    const w: HistoryWindow = { start: 10, head: 14, before: text({ p1: 'old' }), born: ['p2'], ranks: { p1: 'a', p2: 'b' }, ops: [
        { pos: 10, id: 's:10', at: 'a', actor: 'loop', kind: 'text.insert', batch: 'L', batch_first: 10, plan: null, caused_by: null, blocks: text({ p1: 'old!' }), inverse: [{ kind: 'x', target: {}, payload: {} }] },
        { pos: 11, id: 's:11', at: 'b', actor: 'human:local', kind: 'block.split', batch: 'b1', batch_first: 9, plan: null, caused_by: null, blocks: text({ p1: 'old', p2: '!' }), inverse: [{ kind: 'x', target: {}, payload: {} }] },
        { pos: 12, id: 's:12', at: 'c', actor: 'human:local', kind: 'text.insert', batch: 'b1', batch_first: 9, plan: null, caused_by: null, blocks: text({ p2: '!?' }), inverse: [{ kind: 'x', target: {}, payload: {} }] },
    ] };
    it('will not half-undo a batch that began before the window', () => {
        expect(nextUndo(w, 'human:local')).toBeNull();          // b1 started at 9, the window starts at 10
        expect(nextUndo(w, 'loop')!.batch).toBe('u-L');
    });
    it('shows nothing for a block born inside the window, and `before` for one that is not', () => {
        expect(textAt(w, 'p2', 'text', -1, '!?')).toBeNull();
        expect(textAt(w, 'p2', 'text', 0, '!?')).toBeNull();
        expect(textAt(w, 'p2', 'text', 1, '!?')).toBe('!');
        expect(textAt(w, 'p1', 'text', -1, 'old')).toBe('old');
    });
});
