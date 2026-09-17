import { EditorState, TextSelection } from '@tiptap/pm/state';
import { describe, expect, it } from 'vitest';

import { adoptionPlan, blockNode, docFrom } from '../../calc/adopt';
import { Recorder, reconcile, scalarOffset } from '../../calc/recorder';
import { type Projection, type ProjectedBlock, START } from '../../calc/projection';
import { codocSchema } from '../../editor/schema';

const schema = codocSchema();

function block(id: string, kind: 'heading' | 'paragraph', text: string, runs: Array<[string, number, number, string?, string?]>,
               spans: any[] = [], level = 1, head = 1): ProjectedBlock {
    return {
        id, kind, level, rank: id, head,
        fields: { text: { text, runs: runs.map(([op, s, e, actor, plan]) => ({ op, k0: 0, start: s, end: e, actor: actor ?? 'human:a', at: '1', plan: plan ?? null })),
                          spans, suggest_del: [] } },
    };
}

function proj(blocks: ProjectedBlock[]): Projection {
    return { head: 9, frontier: {}, blocks, phases: {}, plans: [], bindings: { by_feature: {}, by_file: {} }, claims: {} };
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
        expect(got.ops[0].payload).toEqual({ from: ['s:2', 6], to: ['s:2', 10] });
        expect(got.ops[1].payload).toEqual({ after: ['s:2', 5], text: 'there' });
        expect(shadows.get('p')!.text()).toBe('hello there');
        expect(shadows.get('p')!.ref(6)).toEqual(['$cid', 'c0', 0]);
        // A second keystroke anchors to the provisional character.
        state = state.apply(tr);
        tr = state.tr.insertText('!', pStart + 11);
        const got2 = rec.record(tr.steps, tr.docs);
        expect(got2.ops[0].payload).toEqual({ after: ['$cid', 'c0', 4], text: '!' });
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
        const tr = state.tr.addMark(2, 4, schema.marks.cite.create({ file: 'f.py', symbol: 'g' }))
            .addMark(1, 3, schema.marks.plan.create({ pid: 'p1' }));
        const got = new Recorder(shadows, cid).record(tr.steps, tr.docs);
        expect(got.unhandled).toEqual([]);
        expect(got.ops).toEqual([{ kind: 'text.mark', target: { bid: 'p', path: 'text' },
            payload: { kind: 'cite', attrs: { file: 'f.py', symbol: 'g' }, start: ['s:1', 1], end: ['s:1', 2], on: true } }]);
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
        expect(r.ops[0].payload).toEqual({ from: ['s:1', 2], to: ['s:1', 2] });
        expect(r.ops[1].payload).toEqual({ after: ['s:1', 1], text: 'wo' });
    });
});

describe('adopt', () => {
    it('paints spans, plan runs and suggested deletions as marks', () => {
        const b = block('p', 'paragraph', 'keep this maybe', [['s:1', 0, 10], ['a:1', 10, 15, 'agent:x', 'pl']],
            [{ kind: 'bold', attrs: {}, start: 0, end: 4 }]);
        (b.fields.text as any).suggest_del = [{ plan: 'pl', start: 5, end: 9 }];
        const { node, shadow } = blockNode(schema, b);
        const marksAt = (i: number) => node.resolve(i + 1).marks().map(m => m.type.name).sort();
        expect(node.textContent).toBe('keep this maybe');
        expect(marksAt(0)).toEqual(['bold']);
        expect(marksAt(6)).toEqual(['suggest_del']);
        expect(marksAt(12)).toEqual(['plan']);
        expect(shadow!.ref(11)).toEqual(['a:1', 1]);
    });

    it('plans replacements only for blocks whose head moved and are not held', () => {
        const p = proj([block('h', 'heading', 'T', [['s:1', 0, 1]], [], 1, 5), block('p', 'paragraph', 'x', [['s:2', 0, 1]], [], 0, 7),
                        block('q', 'paragraph', 'y', [['s:3', 0, 1]], [], 0, 8)]);
        const steps = adoptionPlan([{ bid: 'h', head: 5 }, { bid: 'p', head: 6 }, { bid: 'z', head: 1 }], p, new Set(['p']));
        expect(steps).toEqual([
            { kind: 'remove', bid: 'z', index: 2 },
            { kind: 'insert', bid: 'q', index: 2 },
        ]);
        const steps2 = adoptionPlan([{ bid: 'h', head: 5 }, { bid: 'p', head: 6 }], p, new Set());
        expect(steps2).toEqual([{ kind: 'replace', bid: 'p', index: 1 }, { kind: 'insert', bid: 'q', index: 2 }]);
    });
});
