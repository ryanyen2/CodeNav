import { describe, expect, it } from 'vitest';

import type { ProjectedBlock, Projection, TextField } from '../../../web/calc/projection';
import { anchorsOf, newThreadOps, resolveOps, threadsOf } from '../../../web/editor/comments';

const body = (text: string, actor: string): TextField =>
    ({ text, spans: [], suggest_del: [], runs: [{ start: 0, end: text.length, actor, at: '1', plan: null, op: 'x:1', k0: 0 }] });

const comment = (id: string, anchor: string, anchorText: string, text: string, actor = 'human:a'): ProjectedBlock =>
    ({ id, kind: 'comment', level: 0, rank: id, head: 1,
       fields: { thread: 'c1', anchor, anchor_text: anchorText, scope: 'code', status: 'open', body: body(text, actor) } });

const proj = (blocks: ProjectedBlock[]): Projection =>
    ({ head: 0, frontier: {}, phases: {}, plans: [], bindings: { by_feature: {}, by_file: {} }, claims: {}, blocks });

describe('threads', () => {
    it('group comment blocks by thread, in order', () => {
        const ts = threadsOf(proj([
            { id: 'h1', kind: 'heading', level: 1, rank: 'a', head: 1, fields: {} },
            { id: 'p1', kind: 'paragraph', level: 0, rank: 'b', head: 1, fields: {} },
            comment('c1', 'p1', 'once', 'Twice?'),
            comment('c2', 'p1', 'once', 'Done.', 'agent:x'),
        ]));
        expect(ts).toHaveLength(1);
        expect(ts[0].messages.map(m => [m.actor, m.body])).toEqual([['human:a', 'Twice?'], ['agent:x', 'Done.']]);
        expect(resolveOps(ts[0]).map(o => o.target.bid)).toEqual(['c1', 'c2']);
    });

    it('are anchored where their mark is, not where the thread was written', () => {
        // p2 was merged into p1. The commented words and their mark are in p1
        // now, and the thread still names p2.
        const p1: ProjectedBlock = { id: 'p1', kind: 'paragraph', level: 0, rank: 'a', head: 2, fields: { text: {
            text: 'firstsecond', suggest_del: [], runs: [], spans: [{ kind: 'comment', attrs: { block: 'c1' }, start: 5, end: 11 }] } } };
        const p = proj([p1, comment('c1', 'p2', 'second', 'Why?')]);
        expect(anchorsOf(p).get('c1')).toBe('p1');
        expect(threadsOf(p)[0].anchor).toBe('p1');
    });
});

describe('a new thread', () => {
    it('is a comment block, its body, and a mark on the words it is about', () => {
        const ops = newThreadOps('c9', 'p1', 'once', 'Why?', 'both', ['x:1', 0], ['x:1', 3]);
        expect(ops.map(o => o.kind)).toEqual(['block.add', 'text.insert', 'text.mark']);
        expect(ops[2].payload).toMatchObject({ kind: 'comment', attrs: { block: 'c9' }, on: true });
    });
});
