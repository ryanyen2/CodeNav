// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';

import type { Claim, TextField } from '../../../web/calc/projection';
import { moveBlock } from '../../../web/editor/commands';
import { block, ids, key, mount, para, perform, posIn, proj, select, texts, type Mounted } from './harness';

/** The paint classes drawn inside a block. */
const paintsIn = (m: Mounted, bid: string) =>
    Array.from((m.view.dom.querySelector(`[data-bid="${bid}"]`) as HTMLElement).querySelectorAll('.codoc-paint'))
        .map(e => Array.from(e.classList).filter(c => c !== 'codoc-paint').join(' ')).sort();

/** The daemon's ink claim on a block's first `end` characters, by the viewer. */
const ink = (bid: string, end: number, stage: 'unsent' | 'sent' = 'unsent'): Claim =>
    ({ bid, path: 'text', start: 0, end, channel: 'human', stage, actor: 'human:a', text: '' });

// A block's paints are its feature's claims, so a projection that moves a block
// to another feature has to redraw the blocks whose feature changed.
describe('paints after a structural gesture', () => {
    it('ink the paragraph an Enter created', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one two')], { claims: { h: [ink('p1', 7)] } });
        expect(paintsIn(m, 'p1')).toEqual(['codoc-ink-unsent']);
        select(m.view, posIn(m.view, 'p1', 3));
        key(m.view, 'Enter', 13);
        m.handle.flush();
        const nid = ids(m.view.state.doc)[2].split(':')[1];
        const cid = m.submitted[0].cid;
        const mine = (m.proj.blocks[1].fields.text as TextField).runs[0];
        m.handle.adopt(proj([block('h', 'heading', 'T'),
                             para('p1', 5, [['one', { op: mine.op, actor: mine.actor }]]),
                             para(nid, 5, [[' two', { op: mine.op, actor: mine.actor, k0: 3 }]])],
                            { frontier: { h: 5 }, head: 20, claims: { h: [ink('p1', 3), ink(nid, 4)] } }));
        m.handle.ack([{ cid, op: 'h:5', conflict: null }]);
        expect(texts(m.view.state.doc)).toEqual(['T', 'one', ' two']);
        expect(paintsIn(m, nid)).toEqual(['codoc-ink-unsent']);
    });

    it("paint a paragraph moved under another feature as that feature's", () => {
        const m = mount([block('h1', 'heading', 'A'),
                         para('p1', 1, [['a-body', { op: 'w:1', actor: 'human:a' }]]),
                         block('h2', 'heading', 'B'),
                         para('p2', 1, [['b-body', { op: 'w:2', actor: 'human:a' }]])],
                        { claims: { h1: [ink('p1', 6, 'sent')], h2: [ink('p2', 6)] } });
        expect(paintsIn(m, 'p1')).toEqual(['codoc-ink-sent']);       // A was handed off
        expect(paintsIn(m, 'p2')).toEqual(['codoc-ink-unsent']);     // B was not
        perform(m, s => moveBlock(s, 'p1', 'p2'));
        expect(ids(m.view.state.doc)).toEqual(['heading:h1', 'heading:h2', 'paragraph:p2', 'paragraph:p1']);
        // The projection that answers the move says what the paragraph's ink is in B.
        m.handle.adopt(proj([block('h1', 'heading', 'A'), block('h2', 'heading', 'B'),
                             para('p2', 1, [['b-body', { op: 'w:2', actor: 'human:a' }]]),
                             para('p1', 5, [['a-body', { op: 'w:1', actor: 'human:a' }]])],
                            { head: 20, claims: { h2: [ink('p2', 6), ink('p1', 6)] } }));
        expect(paintsIn(m, 'p1')).toEqual(['codoc-ink-unsent']);     // now B's
    });

    it('keep every phase marker after a move', () => {
        const m = mount([block('h1', 'heading', 'A'), block('p1', 'paragraph', 'a'),
                         block('h2', 'heading', 'B'), block('p2', 'paragraph', 'b')],
                        { phases: { h1: 'edited', h2: 'drifted' } });
        expect(m.view.dom.querySelectorAll('.codoc-marker').length).toBe(2);
        perform(m, s => moveBlock(s, 'h2', 'START'));
        expect(ids(m.view.state.doc)).toEqual(['heading:h2', 'paragraph:p2', 'heading:h1', 'paragraph:p1']);
        // Right after the gesture, before any ack or keystroke.
        expect(m.view.dom.querySelectorAll('.codoc-marker').length).toBe(2);
    });
});
