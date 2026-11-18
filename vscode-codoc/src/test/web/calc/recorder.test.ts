import { EditorState } from '@tiptap/pm/state';
import { describe, expect, it } from 'vitest';

import { docFrom } from '../../../web/calc/adopt';
import { Recorder, reconcile, scalarOffset } from '../../../web/calc/recorder';
import type { Projection, ProjectedBlock, Span } from '../../../web/calc/projection';
import { codocSchema } from '../../../web/editor/schema';

const schema = codocSchema();

function block(id: string, kind: 'heading' | 'paragraph', text: string, runs: Array<[string, number, number, string?, string?]>,
               spans: Span[] = [], level = 1, head = 1): ProjectedBlock {
    return {
        id, kind, level, rank: id, head,
        fields: { text: { text, runs: runs.map(([op, s, e, actor, plan]) => ({ op, k0: 0, start: s, end: e, actor: actor ?? 'human:a', at: '1', plan: plan ?? null })),
                          spans, suggest_del: [] } },
    };
}

function proj(blocks: ProjectedBlock[]): Projection {
    return { head: 9, frontier: {}, blocks, phases: {}, plans: [], bindings: { by_feature: {} }, claims: {} };
}

let n = 0;
const cid = () => `c${n++}`;

describe('Recorder', () => {
    it('turns typing inside a paragraph into delete + insert on identities', () => {
        const p = proj([block('h', 'heading', 'Title', [['s:1', 0, 5]]), block('p', 'paragraph', 'hello world', [['s:2', 0, 11]])]);
        const { doc, shadows } = docFrom(schema, p);
        let state = EditorState.create({ schema, doc });
        // Replace "world" with "there": the paragraph node sits after the heading
        // (nodeSize 7) at pos 7; its content starts at 8.
        const pStart = doc.child(0).nodeSize + 1;
        let tr = state.tr.replaceWith(pStart + 6, pStart + 11, schema.text('there'));
        const rec = new Recorder(shadows, cid);
        const got = rec.record(tr.steps, tr.docs);
        expect(got.unhandled).toEqual([]);
        expect(got.ops.map(o => o.kind)).toEqual(['text.delete', 'text.insert']);
        expect(got.ops[0].payload).toEqual({ segments: [['s:2', 6, 5]] });
        expect(got.ops[1].payload).toEqual({ after: ['s:2', 5], text: 'there' });
        expect(shadows.get('p')!.text()).toBe('hello there');
        expect(shadows.get('p')!.ref(6)).toEqual(['$cid', 'c0', 0]);
        // A second keystroke anchors to the provisional character.
        state = state.apply(tr);
        tr = state.tr.insertText('!', pStart + 11);
        const got2 = rec.record(tr.steps, tr.docs);
        expect(got2.ops[0].payload).toEqual({ after: ['$cid', 'c0', 4], text: '!' });
    });

    it('deletes by segments: exactly the characters on the page, the unacked ones by client id', () => {
        // "ab" by s:1 then "cd" by s:2: the store may hold another actor's
        // words between them that this page has not seen. A range from "b" to
        // "c" would kill those too.
        const p = proj([block('p', 'paragraph', 'abcd', [['s:1', 0, 2], ['s:2', 2, 4]])]);
        (p.blocks[0].fields.text as { runs: Array<{ k0: number }> }).runs[1].k0 = 0;
        const { doc, shadows } = docFrom(schema, p);
        let state = EditorState.create({ schema, doc });
        const rec = new Recorder(shadows, cid);
        let tr = state.tr.insertText('XY', 5);                         // "abcdXY", XY unacked
        const c = rec.record(tr.steps, tr.docs).cids[0];
        state = state.apply(tr);
        tr = state.tr.delete(2, 6);                                     // "a|bcdX|Y"
        expect(rec.record(tr.steps, tr.docs).ops).toEqual([{ kind: 'text.delete', target: { bid: 'p', path: 'text' },
            payload: { segments: [['s:1', 1, 1], ['s:2', 0, 2], ['$cid', c, 0, 1]] } }]);
    });

    it('counts scalars, not UTF-16 units, and records marks on inserted text', () => {
        const p = proj([block('p', 'paragraph', 'a😀b', [['s:1', 0, 3]])]);
        const { doc, shadows } = docFrom(schema, p);
        const state = EditorState.create({ schema, doc });
        // Insert bold "X" after the emoji (UTF-16 offset 3 inside the paragraph).
        const tr = state.tr.insert(1 + 3, schema.text('X', [schema.marks.bold.create()]));
        const got = new Recorder(shadows, cid).record(tr.steps, tr.docs);
        expect(got.ops[0].payload).toEqual({ after: ['s:1', 1], text: 'X' });
        expect(got.ops[1].kind).toBe('text.mark');
        expect(got.ops[1].payload.start).toEqual(['$cid', got.cids[0], 0]);
        expect(scalarOffset(doc.child(0), 3)).toBe(2);
    });

    it('records mark toggles and ignores painted marks', () => {
        const p = proj([block('p', 'paragraph', 'abcd', [['s:1', 0, 4]])]);
        const { doc, shadows } = docFrom(schema, p);
        const state = EditorState.create({ schema, doc });
        const tr = state.tr.addMark(2, 4, schema.marks.cite.create({ symbol: 'f.py::g' }))
            .addMark(1, 3, schema.marks.plan.create({ pid: 'p1' }));
        const got = new Recorder(shadows, cid).record(tr.steps, tr.docs);
        expect(got.unhandled).toEqual([]);
        expect(got.ops).toEqual([{ kind: 'text.mark', target: { bid: 'p', path: 'text' },
            payload: { kind: 'cite', attrs: { symbol: 'f.py::g' }, start: ['s:1', 1], end: ['s:1', 2], on: true } }]);
    });

    it('leaves cross-block steps unhandled and the fallback reconciles one block', () => {
        const p = proj([block('a', 'paragraph', 'one', [['s:1', 0, 3]]), block('b', 'paragraph', 'two', [['s:2', 0, 3]])]);
        const { doc, shadows } = docFrom(schema, p);
        const state = EditorState.create({ schema, doc });
        const tr = state.tr.delete(3, 7);   // "on|e" .. "t|wo" across the boundary
        const got = new Recorder(shadows, cid).record(tr.steps, tr.docs);
        expect(got.unhandled.length).toBe(1);
        // Fallback on block "a" whose text became "onwo" after the join.
        const after = tr.doc.child(0);
        const r = reconcile(after, shadows.get('a')!, cid);
        expect(r.ops.map(o => o.kind)).toEqual(['text.delete', 'text.insert']);
        expect(r.ops[0].payload).toEqual({ segments: [['s:1', 2, 1]] });
        expect(r.ops[1].payload).toEqual({ after: ['s:1', 1], text: 'wo' });
    });
});
