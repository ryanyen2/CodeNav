import { EditorState, TextSelection } from '@tiptap/pm/state';
import { describe, expect, it } from 'vitest';

import { docFrom } from '../../../web/calc/adopt';
import { START, type Projection, type ProjectedBlock } from '../../../web/calc/projection';
import { deleteFeature, indent, mergeBackward, moveBlock, setKind, splitBlock } from '../../../web/editor/commands';
import { codocSchema } from '../../../web/editor/schema';

const schema = codocSchema();

function block(id: string, kind: 'heading' | 'paragraph', text: string, level = 1, actor = 'human:a', op?: string): ProjectedBlock {
    const n = Array.from(text).length;
    return { id, kind, level, rank: id, head: 1,
             fields: { text: { text, runs: n ? [{ op: op ?? `s:${id}`, k0: 0, start: 0, end: n, actor, at: '1', plan: null }] : [],
                               spans: [] } } };
}

function proj(blocks: ProjectedBlock[]): Projection {
    return { head: 9, frontier: {}, blocks, phases: {}, plans: [], bindings: { by_feature: {} }, claims: {} };
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
        const g = setKind(state, env(shadows), 'heading', 2, 3)!;
        expect(g.ops.map(o => o.kind)).toEqual(['text.delete', 'block.set']);
        expect(g.ops[1].payload).toEqual({ value: 'heading', level: 2 });
        expect(g.tr.doc.child(1).type.name).toBe('heading');
        expect(g.tr.doc.child(1).textContent).toBe('New');
        expect(shadows.get('p')!.text()).toBe('New');
    });

    it('Tab moves a heading in place one level deeper with its range; drag moves a heading with everything it dominates', () => {
        const { doc } = docFrom(schema, proj([block('a', 'heading', 'A'), block('p', 'paragraph', 'x'),
                                                        block('b', 'heading', 'B', 2), block('q', 'paragraph', 'y'), block('c', 'heading', 'C')]));
        const state = at(EditorState.create({ schema, doc }), 1);
        const r = indent(state, 1)!;
        expect(r.ops).toEqual([{ kind: 'block.move', target: { bid: 'a' }, payload: { after: 'START', level_delta: 1 } }]);
        const levels: number[] = [];
        r.tr.doc.forEach(n => { if (n.type.name === 'heading') levels.push(n.attrs.level); });
        expect(levels).toEqual([2, 3, 1]);
        const m = moveBlock(state, 'a', 'c')!;
        expect(m.ops).toEqual([{ kind: 'block.move', target: { bid: 'a' }, payload: { after: 'c', level_delta: 0 } }]);
        expect(ids(m.tr.doc)).toEqual(['heading:c', 'heading:a', 'paragraph:p', 'heading:b', 'paragraph:q']);
        expect(moveBlock(state, 'a', 'b')).toBeNull();   // cannot move under itself
    });

    it('deleting a feature removes its own body and promotes children; the applier detaches', () => {
        const { doc, shadows } = docFrom(schema, proj([block('a', 'heading', 'A'), block('p', 'paragraph', 'x'),
                                                        block('b', 'heading', 'B', 2), block('c', 'heading', 'C')]));
        const state = EditorState.create({ schema, doc });
        const g = deleteFeature(state, env(shadows), 'a')!;
        // No bind.detach here: block.delete detaches and records, so an undo
        // re-attaches; sending our own would conflict as not_bound.
        expect(g.ops.map(o => o.kind)).toEqual(['block.delete', 'block.set']);
        expect(ids(g.tr.doc)).toEqual(['heading:b', 'heading:c']);
        expect(g.tr.doc.child(0).attrs.level).toBe(1);
    });

    it('the `## ` rule drops the hashes before the caret and keeps the words after them', () => {
        const { doc, shadows } = docFrom(schema, proj([block('p', 'paragraph', '##abc')]));
        const state = at(EditorState.create({ schema, doc }), 3);     // caret after "##"
        const g = setKind(state, env(shadows), 'heading', 2, 2)!;
        expect(g.tr.doc.child(0).textContent).toBe('abc');
        expect(g.tr.doc.child(0).type.name).toBe('heading');
        expect(shadows.get('p')!.text()).toBe('abc');
        expect(g.ops[0].payload).toEqual({ segments: [['s:p', 0, 2]] });
    });
});
