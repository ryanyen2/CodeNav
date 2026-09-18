import { describe, expect, it } from 'vitest';
import { EditorState, TextSelection } from '@tiptap/pm/state';

import { authorPaints } from '../../calc/claims';
import { citeItems, triggerAt } from '../../editor/menus';
import { findMatches } from '../../editor/find';
import { codocSchema } from '../../editor/schema';

const schema = codocSchema();
const para = (bid: string, text: string) => schema.nodes.paragraph.create({ bid, head: 1 }, text ? schema.text(text) : undefined);

describe('menus', () => {
    it('opens / only at the start of a block and @ after whitespace', () => {
        const doc = schema.nodes.doc.create(null, [para('a', '/he'), para('b', 'see @cli'), para('c', 'x/y')]);
        const at = (pos: number) => triggerAt(EditorState.create({ schema, doc, selection: TextSelection.create(doc, pos) }));
        expect(at(4)).toEqual({ kind: '/', from: 1, query: 'he' });          // "/he|"
        expect(at(14)).toEqual({ kind: '@', from: 10, query: 'cli' });       // "see @cli|"
        expect(at(19)).toBeNull();                                           // "x/y|"
    });
    it('ranks cite candidates by leaf prefix, then substring, and dedups', () => {
        const bindings = { by_feature: { h1: [{ file: 'c.py', symbol: 'c.py::Client' }, { file: 'c.py', symbol: 'c.py::Client.send' }],
                                         h2: [{ file: 'c.py', symbol: 'c.py::Client' }, { file: 's.py', symbol: 's.py::save' }] }, by_file: {} };
        expect(citeItems(bindings, 'cl').map(i => i.label)).toEqual(['Client', 'Client.send']);
        expect(citeItems(bindings, '').length).toBe(3);
        expect(citeItems(bindings, 'zzz')).toEqual([]);
    });
});

describe('find', () => {
    it('matches inside blocks only, case-insensitively', () => {
        const doc = schema.nodes.doc.create(null, [para('a', 'Retry once'), para('b', 'once more, ONCE')]);
        expect(findMatches(doc, 'once')).toEqual([{ from: 7, to: 11 }, { from: 13, to: 17 }, { from: 24, to: 28 }]);
        expect(findMatches(doc, 'once more')).toEqual([{ from: 13, to: 22 }]);
        expect(findMatches(doc, '')).toEqual([]);
    });
});

describe('history paints', () => {
    it('one class per actor kind over display runs', () => {
        const f = { text: 'ab cd', spans: [], suggest_del: [], runs: [
            { start: 0, end: 3, actor: 'human:a', at: '1', plan: null, op: 'x:1', k0: 0 },
            { start: 3, end: 5, actor: 'loop', at: '2', plan: null, op: 'y:1', k0: 0 } ] };
        const b = { id: 'p', kind: 'paragraph', level: 0, rank: 'a', head: 1, fields: { text: f } };
        expect(authorPaints(b, f).map(p => [p.cls, p.start, p.end])).toEqual([['author-human', 0, 3], ['author-loop', 3, 5]]);
    });
});

describe('comments', () => {
    it('groups comment blocks into threads and builds the ops for a new one', async () => {
        const { threadsOf, newThreadOps, resolveOps } = await import('../../editor/comments');
        const body = (t: string, actor: string) => ({ text: t, spans: [], suggest_del: [], runs: [{ start: 0, end: t.length, actor, at: '1', plan: null, op: 'x:1', k0: 0 }] });
        const proj = { head: 0, frontier: {}, phases: {}, plans: [], bindings: { by_feature: {}, by_file: {} }, claims: {}, blocks: [
            { id: 'h1', kind: 'heading', level: 1, rank: 'a', head: 1, fields: {} },
            { id: 'p1', kind: 'paragraph', level: 0, rank: 'b', head: 1, fields: {} },
            { id: 'c1', kind: 'comment', level: 0, rank: 'c', head: 1, fields: { thread: 'c1', anchor: 'p1', anchor_text: 'once', scope: 'code', status: 'open', body: body('Twice?', 'human:a') } },
            { id: 'c2', kind: 'comment', level: 0, rank: 'd', head: 1, fields: { thread: 'c1', anchor: 'p1', anchor_text: 'once', scope: 'code', status: 'open', body: body('Done.', 'agent:x') } },
        ] };
        const ts = threadsOf(proj as never);
        expect(ts).toHaveLength(1);
        expect(ts[0].messages.map(m => [m.actor, m.body])).toEqual([['human:a', 'Twice?'], ['agent:x', 'Done.']]);
        expect(resolveOps(ts[0]).map(o => o.target.bid)).toEqual(['c1', 'c2']);
        const ops = newThreadOps('c9', 'p1', 'once', 'Why?', 'both', ['x:1', 0], ['x:1', 3]);
        expect(ops.map(o => o.kind)).toEqual(['block.add', 'text.insert', 'text.mark']);
        expect(ops[2].payload).toMatchObject({ kind: 'comment', attrs: { block: 'c9' }, on: true });
    });
});
