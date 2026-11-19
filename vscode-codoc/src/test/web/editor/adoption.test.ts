// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';

import type { ProjectedBlock, Projection, TextField } from '../../../web/calc/projection';
import { deleteFeature } from '../../../web/editor/commands';
import { block, ids, key, mount, para, perform, posIn, proj, select, shadowFaults, texts, typeText } from './harness';

describe('a projection that predates a gesture', () => {
    it('does not bring back a paragraph that was merged away', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one'), block('p2', 'paragraph', 'two')]);
        select(m.view, posIn(m.view, 'p2', 0));
        key(m.view, 'Backspace', 8);
        expect(texts(m.view.state.doc)).toEqual(['T', 'onetwo']);
        m.handle.adopt(m.proj);                 // the projection the page was built from, delivered again
        expect(texts(m.view.state.doc)).toEqual(['T', 'onetwo']);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('does not drop a paragraph that was split off', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'hello world')]);
        select(m.view, posIn(m.view, 'p', 5));
        key(m.view, 'Enter', 13);
        expect(texts(m.view.state.doc)).toEqual(['T', 'hello', ' world']);
        m.handle.adopt(m.proj);
        expect(texts(m.view.state.doc)).toEqual(['T', 'hello', ' world']);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('does not bring back a deleted feature', () => {
        const m = mount([block('h1', 'heading', 'A'), block('p1', 'paragraph', 'a'), block('h2', 'heading', 'B'), block('p2', 'paragraph', 'b')]);
        perform(m, (s, env) => deleteFeature(s, env, 'h2'));
        expect(ids(m.view.state.doc)).toEqual(['heading:h1', 'paragraph:p1']);
        m.handle.adopt(m.proj);
        expect(ids(m.view.state.doc)).toEqual(['heading:h1', 'paragraph:p1']);
    });
});

describe('a projection arriving while the person types', () => {
    it('leaves a held block the projection dropped in its place', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one'), block('p2', 'paragraph', 'two'), block('p3', 'paragraph', 'three')]);
        select(m.view, posIn(m.view, 'p2', 3));
        typeText(m.view, 'X');                  // p2 is now held
        // Someone else edited p3, so the projection has something to adopt.
        m.handle.adopt(proj([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one'), block('p3', 'paragraph', 'three more', 1, 7)]));
        expect(ids(m.view.state.doc)).toEqual(['heading:h', 'paragraph:p1', 'paragraph:p2', 'paragraph:p3']);
        expect(texts(m.view.state.doc)[2]).toBe('twoX');
    });

    it('keeps the caret after the character it followed', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'abc def')]);
        select(m.view, posIn(m.view, 'p', 7));   // after "f"
        // Another actor inserted "ZZ " at the start of the paragraph.
        const mine = (m.proj.blocks[1].fields.text as TextField).runs[0];
        m.handle.adopt(proj([block('h', 'heading', 'T'),
                             para('p', 2, [['ZZ ', { op: 'other:1', actor: 'human:gh:bob' }], ['abc def', { op: mine.op, actor: 'human:a' }]])]));
        expect(texts(m.view.state.doc)[1]).toBe('ZZ abc def');
        // By identity the caret is still after "f", at offset 10. By offset it would be at 7.
        expect(m.view.state.selection.from - posIn(m.view, 'p', 0)).toBe(10);
    });

    it('re-renders a block whose kind changed', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'was prose')]);
        m.handle.adopt(proj([block('h', 'heading', 'T'), block('p', 'heading', 'was prose', 2, 5)]));
        expect(ids(m.view.state.doc)).toEqual(['heading:h', 'heading:p']);
        expect(shadowFaults(m)).toEqual([]);
    });
});

describe('a projection skipped for a block with an op in flight', () => {
    /** The page after Bob's letter arrives while the person's own 'x' is unacked. */
    function twoActors(extra: Partial<Projection> = {}, text = 'Bonex', runs?: TextField['runs']) {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one')]);
        select(m.view, posIn(m.view, 'p1', 3));
        typeText(m.view, 'x');
        m.handle.flush();
        const cid = m.submitted[0].cid;
        const mine = (m.proj.blocks[1].fields.text as TextField).runs[0];
        const p1: ProjectedBlock = { id: 'p1', kind: 'paragraph', level: 0, rank: 'p1', head: 9,
            fields: { text: { text, spans: [], runs: runs ?? [
                { op: 'bob:1', k0: 0, start: 0, end: 1, actor: 'human:gh:bob', at: '2', plan: null },
                { op: mine.op, k0: 0, start: 1, end: 4, actor: 'human:a', at: '1', plan: null },
                { op: 'h:5', k0: 0, start: 4, end: 5, actor: 'human:local', at: '3', plan: null }] } } };
        return { m, cid, p1: proj([block('h', 'heading', 'T'), p1], { frontier: { h: 5, bob: 1, ag: 1 }, ...extra }) };
    }

    it("is taken up when the ack lifts the hold, so another actor's word appears", () => {
        const { m, cid, p1 } = twoActors();
        m.handle.adopt(p1);
        expect(texts(m.view.state.doc)[1]).toBe('onex');          // held, so left alone
        m.handle.ack([{ cid, op: 'h:5', conflict: null }]);
        expect(texts(m.view.state.doc)[1]).toBe('Bonex');
        expect(shadowFaults(m)).toEqual([]);
    });

    it('is not taken up when its frontier does not cover the acked op', () => {
        const { m, cid, p1 } = twoActors();
        // Published before the op landed: its word for p1 is older than the
        // page's, and adopting it would undo the person's typing.
        m.handle.adopt({ ...p1, frontier: { bob: 1 } });
        m.handle.ack([{ cid, op: 'h:5', conflict: null }]);
        expect(texts(m.view.state.doc)[1]).toBe('onex');
        expect(shadowFaults(m)).toEqual([]);
    });

    it("shows an agent's plan that arrived in the paragraph the person was typing in", () => {
        const { m, cid, p1 } = twoActors(
            { plans: [{ id: 'pl1', actor: 'agent:x', kind: 'edit', target: 'p1', note: '', status: 'open' }],
              claims: { h: [{ bid: 'p1', path: 'text', start: 4, end: 9, channel: 'plan', stage: 'open', actor: 'agent:x',
                              text: ' more', plan: 'pl1' }] } },
            'onex more',
            [{ op: 's:p10', k0: 0, start: 0, end: 3, actor: 'human:a', at: '1', plan: null },
             { op: 'h:5', k0: 0, start: 3, end: 4, actor: 'human:local', at: '3', plan: null },
             { op: 'ag:1', k0: 0, start: 4, end: 9, actor: 'agent:x', at: '4', plan: 'pl1' }]);
        m.handle.adopt(p1);
        m.handle.ack([{ cid, op: 'h:5', conflict: null }]);
        expect(texts(m.view.state.doc)[1]).toBe('onex more');
        expect(m.view.dom.querySelectorAll('.codoc-plan-open').length).toBe(1);
    });

    it("shows a contributor's own Backspace as the plan's strike-through, not as an absence", () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one two')]);
        select(m.view, posIn(m.view, 'p1', 4), posIn(m.view, 'p1', 7));
        key(m.view, 'Backspace', 8);
        m.handle.flush();
        expect(texts(m.view.state.doc)[1]).toBe('one ');
        const cid = m.submitted[0].cid;
        // The daemon turned the contributor's delete into their plan's
        // suggested deletion, published, then acked.
        const p1: ProjectedBlock = { ...m.proj.blocks[1], head: 9 };
        m.handle.adopt(proj([block('h', 'heading', 'T'), p1],
            { frontier: { h: 6 }, plans: [{ id: 'pl1', actor: 'human:gh:alice', kind: 'edit', target: 'p1', note: '', status: 'open' }],
              claims: { h: [{ bid: 'p1', path: 'text', start: 4, end: 7, channel: 'plan', stage: 'removed',
                              actor: 'human:gh:alice', text: 'two', plan: 'pl1' }] } }));
        m.handle.ack([{ cid, op: 'h:6', conflict: null }]);
        expect(texts(m.view.state.doc)[1]).toBe('one two');
        expect(m.view.dom.querySelectorAll('.codoc-plan-cut').length).toBe(1);
    });
});

describe('a block is held while the projection predates its last op', () => {
    /** Two blocks, each with one op in flight, and Bob's projection, whose
     *  frontier covers neither. */
    function twoInFlight() {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one'), block('p2', 'paragraph', 'two')]);
        select(m.view, posIn(m.view, 'p1', 3));
        typeText(m.view, 'x');
        m.handle.flush();
        select(m.view, posIn(m.view, 'p2', 3));
        typeText(m.view, 'y');
        m.handle.flush();
        const [cidA, cidB] = m.submitted.map(s => s.cid);
        const bob = proj([block('h', 'heading', 'T'), block('p1', 'paragraph', 'Bone', 0, 9),
                          block('p2', 'paragraph', 'Btwo', 0, 9)], { frontier: { bob: 1 }, head: 20 });
        return { m, cidA, cidB, bob };
    }

    it('holds a clean block acked alongside a refused one', () => {
        const { m, cidA, cidB, bob } = twoInFlight();
        m.handle.adopt(bob);
        m.handle.ack([{ cid: cidA, op: 'h:5', conflict: 'unknown_anchor' },
                      { cid: cidB, op: 'h:6', conflict: null }]);
        expect(texts(m.view.state.doc)[2]).toBe('twoy');     // p2's own word, not Bob's older one
        expect(shadowFaults(m)).toEqual([]);
    });

    it('holds a released block when the same projection arrives twice', () => {
        const { m, cidA, bob } = twoInFlight();
        m.handle.adopt(bob);
        m.handle.ack([{ cid: cidA, op: 'h:5', conflict: null }]);
        m.handle.adopt(bob);                                  // a watcher firing twice for one write
        expect(texts(m.view.state.doc)[1]).toBe('onex');
        expect(shadowFaults(m)).toEqual([]);
    });

    it('holds one block and re-reads the other when one ack is covered and one is not', () => {
        const { m, cidA, cidB } = twoInFlight();
        const covering = proj([block('h', 'heading', 'T'), block('p1', 'paragraph', 'Bonex', 0, 9),
                               block('p2', 'paragraph', 'Btwo', 0, 9)], { frontier: { bob: 1, h: 5 }, head: 21 });
        m.handle.adopt(covering);
        m.handle.ack([{ cid: cidA, op: 'h:5', conflict: null }, { cid: cidB, op: 'h:6', conflict: null }]);
        expect(texts(m.view.state.doc)[1]).toBe('Bonex');     // covered: the projection's word is newer
        expect(texts(m.view.state.doc)[2]).toBe('twoy');      // not covered: the page keeps its own
        expect(shadowFaults(m)).toEqual([]);
    });

    it('does not adopt a projection older than the one it has', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one')]);
        m.handle.adopt(proj([block('h', 'heading', 'T'), block('p1', 'paragraph', 'newer', 0, 9)], { head: 20 }));
        expect(texts(m.view.state.doc)[1]).toBe('newer');
        m.handle.adopt(proj([block('h', 'heading', 'T'), block('p1', 'paragraph', 'older', 0, 5)], { head: 11 }));
        expect(texts(m.view.state.doc)[1]).toBe('newer');
    });
});

describe('a projection that changed only words', () => {
    it('replaces the blocks it changed and leaves the other nodes untouched', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one'), block('p2', 'paragraph', 'two')]);
        const before = m.view.state.doc;
        m.handle.adopt(proj([block('h', 'heading', 'T'), block('p1', 'paragraph', 'ONE', 0, 9),
                             block('p2', 'paragraph', 'two')], { head: 20 }));
        expect(texts(m.view.state.doc)).toEqual(['T', 'ONE', 'two']);
        expect(m.view.state.doc.child(0)).toBe(before.child(0));
        expect(m.view.state.doc.child(2)).toBe(before.child(2));
        expect(shadowFaults(m)).toEqual([]);
    });

    it('keeps the caret on the character it sat after', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one two')]);
        select(m.view, posIn(m.view, 'p1', 7));                       // after "two"
        const run = (m.proj.blocks[1].fields.text as TextField).runs[0];
        m.handle.adopt(proj([block('h', 'heading', 'T'),
                             para('p1', 9, [['X', { op: 'other:1', actor: 'human:b' }],
                                            ['one two', { op: run.op, actor: run.actor }]])], { head: 20 }));
        expect(texts(m.view.state.doc)[1]).toBe('Xone two');
        expect(m.view.state.selection.from).toBe(posIn(m.view, 'p1', 8));   // still after "two"
    });

    it('keeps a selection, so typing still replaces it', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'Retries once.'), block('p2', 'paragraph', 'Other.')]);
        const from = posIn(m.view, 'p1', 8), to = posIn(m.view, 'p1', 12);   // "once"
        select(m.view, from, to);
        m.handle.adopt(proj([block('h', 'heading', 'T'), m.proj.blocks[1], block('p2', 'paragraph', 'Other, by the agent.', 1, 7)]));
        expect(texts(m.view.state.doc)[2]).toBe('Other, by the agent.');
        expect([m.view.state.selection.from, m.view.state.selection.to]).toEqual([from, to]);
        typeText(m.view, 'twice');
        expect(texts(m.view.state.doc)[1]).toBe('Retries twice.');
    });

    it('rebuilds the document when a block arrived, left or moved', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one')]);
        m.handle.adopt(proj([block('h', 'heading', 'T'), block('p2', 'paragraph', 'new'),
                             block('p1', 'paragraph', 'one')], { head: 20 }));
        expect(ids(m.view.state.doc)).toEqual(['heading:h', 'paragraph:p2', 'paragraph:p1']);
        expect(shadowFaults(m)).toEqual([]);
    });
});

describe('a refused op', () => {
    it('leaves the block held while its other ops are in flight, then re-reads it', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one')]);
        const one = (m.proj.blocks[1].fields.text as TextField).runs[0].op;
        select(m.view, posIn(m.view, 'p1', 3));
        typeText(m.view, 'x');
        m.handle.flush();
        typeText(m.view, 'y');
        m.handle.flush();
        const [cidA, cidB] = m.submitted.map(s => s.cid);
        m.handle.ack([{ cid: cidA, op: null, conflict: 'unknown_anchor' }]);
        expect(m.conflicts).toEqual(['unknown_anchor']);
        expect(texts(m.view.state.doc)[1]).toBe('onexy');            // y is still on its way
        expect(shadowFaults(m)).toEqual([]);
        m.handle.ack([{ cid: cidB, op: 'h:6', conflict: null }]);
        expect(texts(m.view.state.doc)[1]).toBe('onexy');            // the projection in hand lacks h:6
        m.handle.adopt(proj([block('h', 'heading', 'T'),
                             para('p1', 1, [['one', { op: one, actor: 'human:a' }], ['y', { op: 'h:6', actor: 'human:local' }]])],
                            { head: 10, frontier: { h: 6 } }));
        expect(texts(m.view.state.doc)[1]).toBe('oney');              // re-read, though its head did not move
        expect(shadowFaults(m)).toEqual([]);
    });
});

describe('a projection of a rebuilt store', () => {
    it('keeps words still in flight, and the next keystroke anchors on them once acked', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'abc')], { head: 9, store: 'a' });
        select(m.view, posIn(m.view, 'p', 3));
        typeText(m.view, 'XYZ');
        m.handle.flush();
        m.handle.adopt(proj([block('h', 'heading', 'T'), block('p', 'paragraph', 'abc')], { head: 9, store: 'b' }));
        expect(texts(m.view.state.doc)).toEqual(['T', 'abcXYZ']);
        m.handle.ack([{ cid: m.submitted[0].cid, op: 's2:1', conflict: null }]);
        select(m.view, posIn(m.view, 'p', 6));
        typeText(m.view, '!');
        m.handle.flush();
        expect(m.submitted.at(-1)!.payload).toEqual({ after: ['s2:1', 2], text: '!' });
        expect(shadowFaults(m)).toEqual([]);
    });

    it('is adopted whole though its head is lower, and forgets what the old store applied', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'mine')], { head: 40, frontier: { s1: 39 }, store: 'a' });
        select(m.view, posIn(m.view, 'p', 4));
        typeText(m.view, '!');
        m.handle.flush();
        m.handle.ack([{ cid: m.submitted[0].cid, op: 's1:40', conflict: null }]);   // held: the projection lacks s1:40
        m.handle.adopt(proj([block('h', 'heading', 'T'), block('p', 'paragraph', 'older text', 0, 3)], { head: 12, store: 'b' }));
        expect(texts(m.view.state.doc)).toEqual(['T', 'older text']);
        m.handle.adopt(proj([block('h', 'heading', 'T'), block('p', 'paragraph', 'their text', 0, 7)],
                            { head: 500, frontier: { s9: 499 }, store: 'b' }));
        expect(texts(m.view.state.doc)).toEqual(['T', 'their text']);
        m.handle.adopt(proj([block('h', 'heading', 'T'), block('p', 'paragraph', 'stale', 0, 6)], { head: 300, store: 'b' }));
        expect(texts(m.view.state.doc)).toEqual(['T', 'their text']);  // within one store, head still only grows
        expect(shadowFaults(m)).toEqual([]);
    });
});
