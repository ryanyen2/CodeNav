// @vitest-environment jsdom
import { beforeAll, describe, expect, it } from 'vitest';

import type { Submission } from '../../../web/calc/projection';
import type { HistoryWindow } from '../../../web/calc/window';
import { block, proj } from './harness';

// app.ts runs when it is imported and keeps its state in the module, so the
// page is loaded once and the tests below drive the same page in order.
describe('the page', () => {
    const posted: Array<Record<string, unknown>> = [];
    const post = (data: unknown, origin = window.location.origin) => window.dispatchEvent(new window.MessageEvent('message', { data, origin } as MessageEventInit));
    const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));
    const statusLine = () => document.querySelector('.codoc-status')?.textContent ?? '';

    beforeAll(async () => {
        (globalThis as unknown as { acquireVsCodeApi: () => unknown }).acquireVsCodeApi = () => ({ postMessage: (m: Record<string, unknown>) => posted.push(m) });
        const app = document.createElement('div');
        app.id = 'app';
        document.body.append(app);
        await import('../../../web/editor/app');
        post({ kind: 'projection', proj: proj([block('h', 'heading', 'Title'), block('p', 'paragraph', 'one two')]) });
        post({ kind: 'status', status: { state: 'synced', actor: 'human:local' } });
    });

    // On the hub the page is an ordinary tab, and an opener can post into it.
    it('ignores a message from another origin', () => {
        const before = statusLine();
        post({ kind: 'status', status: { state: 'PWNED', detail: 'PWNED', actor: 'human:gh:eve' } }, 'https://evil.example');
        expect(statusLine()).toBe(before);
    });

    it('says when an undo is refused, and the next undo moves past that batch', () => {
        const mine = (pos: number, batch: string, kind: string): HistoryWindow['ops'][number] => ({ pos, id: `s:${pos}`, at: String(pos),
            actor: 'human:local', kind, batch, batch_first: pos, plan: null, caused_by: null, blocks: {},
            inverse: [{ kind: 'plan.reopen', target: { pid: 'x' }, payload: {} }] });
        const w: HistoryWindow = { start: 0, head: 2, before: {}, ops: [mine(0, 'b-old', 'text.insert'), mine(1, 'b-verdict', 'plan.accept')] };
        const undoSubs = () => posted.filter(m => m.kind === 'submit').flatMap(m => (m as { subs: Submission[] }).subs);
        const cmdZ = () => document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true, cancelable: true }));
        posted.length = 0;
        cmdZ();
        post({ kind: 'history', history: w });
        const first = undoSubs();
        expect(first.map(s => s.batch)).toEqual(['u-b-verdict']);
        post({ kind: 'acks', acks: first.map(s => ({ cid: s.cid, op: null, conflict: 'verdict_folded' })) });
        expect(statusLine()).toBe('Could not undo accepting the proposal: it is in a snapshot now.');
        posted.length = 0;
        cmdZ();
        post({ kind: 'history', history: w });                          // the refused undo is not in the window
        expect(undoSubs().map(s => s.batch)).toEqual(['u-b-old']);
        post({ kind: 'acks', acks: undoSubs().map(s => ({ cid: s.cid, op: 's:9', conflict: null })) });
        // A snapshot refusal is final: someone else writing does not make it possible.
        posted.length = 0;
        cmdZ();
        post({ kind: 'history', history: { ...w, head: 3, ops: [...w.ops, { ...mine(2, 'b-theirs', 'text.insert'), actor: 'human:bob' }] } });
        expect(undoSubs().map(s => s.batch)).toEqual(['u-b-old']);
        post({ kind: 'acks', acks: undoSubs().map(s => ({ cid: s.cid, op: 's:10', conflict: null })) });
    });

    it('offers a refused undo again only once someone else changed a block it touches', () => {
        const op = (pos: number, batch: string, actor: string, bid: string): HistoryWindow['ops'][number] => ({ pos, id: `t:${pos}`,
            at: String(pos), actor, kind: 'text.insert', batch, batch_first: pos, plan: null, caused_by: null,
            blocks: { [bid]: { text: 'x' } }, inverse: [{ kind: 'text.delete', target: { bid, path: 'text' }, payload: {} }] });
        const undoSubs = () => posted.filter(m => m.kind === 'submit').flatMap(m => (m as { subs: Submission[] }).subs);
        const cmdZ = () => document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true, cancelable: true }));
        const base = [op(0, 'e-old', 'human:local', 'q'), op(1, 'e-mine', 'human:local', 'p')];
        posted.length = 0;
        cmdZ();
        post({ kind: 'history', history: { start: 0, head: 2, before: {}, ops: base } });
        const first = undoSubs();
        expect(first.map(s => s.batch)).toEqual(['u-e-mine']);
        post({ kind: 'acks', acks: first.map(s => ({ cid: s.cid, op: null, conflict: 'dead_block' })) });
        // Bob writes in another block: nothing changed for this undo, so it is still skipped.
        posted.length = 0;
        cmdZ();
        post({ kind: 'history', history: { start: 0, head: 3, before: {}, ops: [...base, op(2, 'b1', 'human:bob', 'r')] } });
        expect(undoSubs().map(s => s.batch)).toEqual(['u-e-old']);
        post({ kind: 'acks', acks: undoSubs().map(s => ({ cid: s.cid, op: 't:9', conflict: null })) });
        // Bob brings back the block it touches: the undo may apply now, so it is offered again.
        posted.length = 0;
        cmdZ();
        post({ kind: 'history', history: { start: 0, head: 4, before: {}, ops: [...base, op(2, 'b1', 'human:bob', 'r'), op(3, 'b2', 'human:bob', 'p')] } });
        expect(undoSubs().map(s => s.batch)).toEqual(['u-e-mine']);
        post({ kind: 'acks', acks: undoSubs().map(s => ({ cid: s.cid, op: 't:10', conflict: null })) });
    });

    it('offers a refused undo of a kind change again once someone else changed the structure', () => {
        const op = (pos: number, batch: string, actor: string, kind: string, bid: string): HistoryWindow['ops'][number] => ({ pos, id: `k:${pos}`,
            at: String(pos), actor, kind, batch, batch_first: pos, plan: null, caused_by: null, blocks: {},
            inverse: [{ kind: kind === 'block.set' ? 'block.set' : 'block.delete', target: { bid }, payload: {} }] });
        const undoSubs = () => posted.filter(m => m.kind === 'submit').flatMap(m => (m as { subs: Submission[] }).subs);
        const cmdZ = () => document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true, cancelable: true }));
        const base = [op(0, 'k-old', 'human:local', 'block.add', 'q'), op(1, 'k-mine', 'human:local', 'block.set', 'p')];
        posted.length = 0;
        cmdZ();
        post({ kind: 'history', history: { start: 0, head: 2, before: {}, ops: base } });
        post({ kind: 'acks', acks: undoSubs().map(s => ({ cid: s.cid, op: null, conflict: 'dead_block' })) });
        // Bob restores a block (block.set's history entry names no blocks): the kind change may apply now.
        posted.length = 0;
        cmdZ();
        post({ kind: 'history', history: { start: 0, head: 3, before: {}, ops: [...base, op(2, 'k-bob', 'human:bob', 'block.restore', 'p')] } });
        expect(undoSubs().map(s => s.batch)).toEqual(['u-k-mine']);
        post({ kind: 'acks', acks: undoSubs().map(s => ({ cid: s.cid, op: 'k:9', conflict: null })) });
    });

    it('answers an undo even when the batch it sent never appears in the history window', async () => {
        posted.length = 0;
        const pm = document.querySelector('#app .ProseMirror') as HTMLElement;
        expect(pm).toBeTruthy();
        // A gesture: Enter, which is one recorded batch.
        pm.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', keyCode: 13, bubbles: true, cancelable: true }));
        await sleep(450);                                                  // the settle timer flushes
        const submit = posted.find(m => m.kind === 'submit') as { subs: Submission[] } | undefined;
        expect(submit, 'the Enter gesture was submitted').toBeTruthy();
        const cids = submit!.subs.map(s => s.cid);
        // Undo: the page asks for the history window.
        document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true, cancelable: true }));
        expect(posted.some(m => m.kind === 'history')).toBe(true);
        // The daemon refused the whole batch, so the window carries only an
        // earlier batch of this actor's, and the acks say so.
        post({ kind: 'acks', acks: cids.map(cid => ({ cid, op: null, conflict: 'authority' })) });
        const w: HistoryWindow = { start: 0, head: 1, before: {}, ops: [
            { pos: 0, id: 's:1', at: '1', actor: 'human:local', kind: 'text.insert', batch: 't-earlier', batch_first: 0, plan: null, caused_by: null,
              blocks: { p: { text: 'one two' } }, inverse: [{ kind: 'text.delete', target: { bid: 'p', path: 'text' }, payload: {} }] },
        ] };
        posted.length = 0;
        post({ kind: 'history', history: w });
        post({ kind: 'history', history: w });
        const undoSent = posted.some(m => m.kind === 'submit' && (m as { subs: Submission[] }).subs.some(s => s.batch.startsWith('u-')));
        expect(undoSent || /undo/i.test(statusLine()), 'either the undo went out or the person was told why not').toBe(true);
    });

    it('accepting a plan hands off the features it names, in the same batch', () => {
        post({ kind: 'projection', proj: proj([block('h', 'heading', 'Title'), block('p', 'paragraph', 'one two')], { head: 30,
            plans: [{ id: 'p-1', actor: 'agent:x', kind: 'edit', target: 'p', note: '', status: 'open', handoff: ['h'] },
                    { id: 'p-2', actor: 'loop', kind: 'edit', target: 'p', note: '', status: 'open', handoff: [] }] }) });
        posted.length = 0;
        const accept = Array.from(document.querySelectorAll('.codoc-btn')).find(b => b.textContent?.startsWith('Accept all')) as HTMLButtonElement;
        accept.click();
        const subs = posted.filter(m => m.kind === 'submit').flatMap(m => (m as { subs: Submission[] }).subs)
            .filter(s => s.kind === 'plan.accept' || s.kind === 'feature.handoff');
        expect(subs.map(s => [s.kind, s.target, s.payload])).toEqual([
            ['plan.accept', { pid: 'p-1' }, {}], ['plan.accept', { pid: 'p-2' }, {}],
            ['feature.handoff', { fid: 'h' }, { asked: 'Build the plan p-1.' }]]);
        expect(new Set(subs.map(s => s.batch)).size).toBe(1);
    });
});
