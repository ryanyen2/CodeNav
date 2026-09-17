import { EditorState, TextSelection } from '@tiptap/pm/state';
import { describe, expect, it } from 'vitest';

import { docFrom } from '../../calc/adopt';
import { displayRuns, inkPaints, planPaints } from '../../calc/claims';
import { START, type Projection, type ProjectedBlock, type TextField } from '../../calc/projection';
import { deleteFeature, makeHeading, mergeBackward, moveBlock, relevel, splitBlock } from '../../editor/commands';
import { codocSchema } from '../../editor/schema';

const schema = codocSchema();

function block(id: string, kind: 'heading' | 'paragraph', text: string, level = 1, actor = 'human:a', op?: string): ProjectedBlock {
    const n = Array.from(text).length;
    return { id, kind, level, rank: id, head: 1,
             fields: { text: { text, runs: n ? [{ op: op ?? `s:${id}`, k0: 0, start: 0, end: n, actor, at: '1', plan: null }] : [],
                               spans: [], suggest_del: [] } } };
}

function proj(blocks: ProjectedBlock[]): Projection {
    return { head: 9, frontier: {}, blocks, phases: {}, plans: [], bindings: { by_feature: {}, by_file: {} }, claims: {} };
}

let n = 0;
const env = (shadows: Map<string, any>) => ({ shadows, nextBid: () => `n${n++}`, nextCid: () => `c${n++}` });
const at = (state: EditorState, pos: number) => state.apply(state.tr.setSelection(TextSelection.create(state.doc, pos)));
const ids = (doc: any) => { const out: string[] = []; doc.forEach((x: any) => out.push(`${x.type.name}:${x.attrs.bid}`)); return out; };

describe('structural commands', () => {
    it('Enter mid-paragraph splits; the id stays with the text-bearing first half', () => {
        const { doc, shadows } = docFrom(schema, proj([block('h', 'heading', 'T'), block('p', 'paragraph', 'hello world')]));
        let state = at(EditorState.create({ schema, doc }), 4 + 5);     // after "hello"
        const g = splitBlock(state, env(shadows))!;
        expect(g.ops).toEqual([{ kind: 'block.split', target: { bid: 'p', path: 'text' }, payload: { at: ['s:p', 4], new: 'n0' } }]);
        expect(ids(g.tr.doc)).toEqual(['heading:h', 'paragraph:p', 'paragraph:n0']);
        expect(shadows.get('p')!.text()).toBe('hello');
        expect(shadows.get('n0')!.text()).toBe(' world');
        // Enter at the START of a paragraph: the empty new block comes first and gets the new id.
        state = at(EditorState.create({ schema, doc }), 4);
        const g2 = splitBlock(state, env(shadows))!;
        expect(g2.ops[0].payload).toEqual({ at: START, new: 'n1' });
        expect(ids(g2.tr.doc)).toEqual(['heading:h', 'paragraph:n1', 'paragraph:p']);
    });

    it('Enter at the end of a heading yields heading + paragraph', () => {
        const { doc, shadows } = docFrom(schema, proj([block('h', 'heading', 'Title')]));
        const state = at(EditorState.create({ schema, doc }), 6);
        const g = splitBlock(state, env(shadows))!;
        expect(ids(g.tr.doc)).toEqual(['heading:h', 'paragraph:n2']);
        expect(g.tr.doc.child(0).textContent).toBe('Title');
    });

    it('Backspace at a paragraph start merges into the previous paragraph, never a heading', () => {
        const { doc, shadows } = docFrom(schema, proj([block('h', 'heading', 'T'), block('a', 'paragraph', 'one'), block('b', 'paragraph', 'two')]));
        const bStart = 3 + 5 + 1;
        const state = at(EditorState.create({ schema, doc }), bStart);
        const g = mergeBackward(state, env(shadows))!;
        expect(g.ops).toEqual([{ kind: 'block.merge', target: { into: 'a', bid: 'b', path: 'text' }, payload: {} }]);
        expect(ids(g.tr.doc)).toEqual(['heading:h', 'paragraph:a']);
        expect(shadows.get('a')!.text()).toBe('onetwo');
        expect(shadows.has('b')).toBe(false);
        const state2 = at(EditorState.create({ schema, doc }), 4);       // start of "one", after the heading
        expect(mergeBackward(state2, env(new Map(shadows)))).toBeNull();
    });

    it('## turns a paragraph into a heading and drops the marker characters', () => {
        const { doc, shadows } = docFrom(schema, proj([block('h', 'heading', 'T'), block('p', 'paragraph', '## New')]));
        const state = at(EditorState.create({ schema, doc }), 4 + 3);
        const g = makeHeading(state, env(shadows), 2, 3)!;
        expect(g.ops.map(o => o.kind)).toEqual(['text.delete', 'block.set']);
        expect(g.ops[1].payload).toEqual({ value: 'heading', level: 2 });
        expect(g.tr.doc.child(1).type.name).toBe('heading');
        expect(g.tr.doc.child(1).textContent).toBe('New');
        expect(shadows.get('p')!.text()).toBe('New');
    });

    it('Tab relevels; drag moves a heading with everything it dominates', () => {
        const { doc, shadows } = docFrom(schema, proj([block('a', 'heading', 'A'), block('p', 'paragraph', 'x'),
                                                        block('b', 'heading', 'B', 2), block('q', 'paragraph', 'y'), block('c', 'heading', 'C')]));
        const state = at(EditorState.create({ schema, doc }), 1);
        const r = relevel(state, env(shadows), 1)!;
        expect(r.ops).toEqual([{ kind: 'block.set', target: { bid: 'a', path: 'level' }, payload: { value: 2 } }]);
        const m = moveBlock(state, env(shadows), 'a', 'c')!;
        expect(m.ops).toEqual([{ kind: 'block.move', target: { bid: 'a' }, payload: { after: 'c', level_delta: 0 } }]);
        expect(ids(m.tr.doc)).toEqual(['heading:c', 'heading:a', 'paragraph:p', 'heading:b', 'paragraph:q']);
        expect(moveBlock(state, env(shadows), 'a', 'b')).toBeNull();   // cannot move under itself
    });

    it('deleting a feature removes its own body, promotes children and detaches', () => {
        const { doc, shadows } = docFrom(schema, proj([block('a', 'heading', 'A'), block('p', 'paragraph', 'x'),
                                                        block('b', 'heading', 'B', 2), block('c', 'heading', 'C')]));
        const state = EditorState.create({ schema, doc });
        const g = deleteFeature(state, env(shadows), 'a', [{ file: 'f.py', symbol: 'f.py::g' }])!;
        expect(g.ops.map(o => o.kind)).toEqual(['block.delete', 'block.set', 'bind.detach']);
        expect(ids(g.tr.doc)).toEqual(['heading:b', 'heading:c']);
        expect(g.tr.doc.child(0).attrs.level).toBe(1);
    });
});

describe('claims', () => {
    it('ink is unsent until the hand-off frontier and gone past the realized one', () => {
        const b = block('p', 'paragraph', 'abcdef');
        const f = b.fields.text as TextField;
        f.runs = [{ op: 's:1', k0: 0, start: 0, end: 2, actor: 'human:a', at: '1', plan: null },
                  { op: 's:5', k0: 0, start: 2, end: 4, actor: 'human:a', at: '1', plan: null },
                  { op: 's:9', k0: 0, start: 4, end: 6, actor: 'loop', at: '1', plan: null }];
        const paints = inkPaints(b, f, { s: 1 }, { s: 5 }, new Set());
        expect(paints).toEqual([{ bid: 'p', start: 2, end: 4, cls: 'ink-sent', title: 'yours, handed off' }]);
        const paints2 = inkPaints(b, f, {}, {}, new Set(['s:5']));
        expect(paints2.map(p => [p.start, p.end, p.cls])).toEqual([[0, 4, 'ink-unsent']]);
    });

    it('plan runs are gray and struck spans are cuts; display runs snap to words', () => {
        const b = block('p', 'paragraph', 'keep this maybe');
        const f = b.fields.text as TextField;
        f.runs = [{ op: 's:1', k0: 0, start: 0, end: 10, actor: 'human:a', at: '1', plan: null },
                  { op: 'a:1', k0: 0, start: 10, end: 15, actor: 'agent:x', at: '2', plan: 'pl' }];
        f.suggest_del = [{ plan: 'pl', start: 5, end: 9 }];
        const p = planPaints(b, f, [{ id: 'pl', actor: 'agent:x', kind: 'edit', target: 'p', note: 'n', status: 'open' }]);
        expect(p.map(x => x.cls)).toEqual(['plan-open', 'plan-cut']);
        f.runs = [{ op: 's:1', k0: 0, start: 0, end: 7, actor: 'human:a', at: '1', plan: null },
                  { op: 'l:1', k0: 0, start: 7, end: 15, actor: 'loop', at: '1', plan: null }];
        expect(displayRuns(f)).toEqual([{ start: 0, end: 9, actor: 'human:a' }, { start: 9, end: 15, actor: 'loop' }]);
    });
});
