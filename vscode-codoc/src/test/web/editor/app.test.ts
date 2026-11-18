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

    // The batches this page sent before a reload, as the webview's state kept them.
    // ⌘Z offers only these and the ones it sends from now on.
    let saved: unknown = { sent: ['b-page', 'b-old', 'b-verdict', 'e-old', 'e-mine', 'k-old', 'k-mine', 'j-old', 'j-split', 't-earlier'] };

    beforeAll(async () => {
        (globalThis as unknown as { acquireVsCodeApi: () => unknown }).acquireVsCodeApi = () => ({
            postMessage: (m: Record<string, unknown>) => posted.push(m), getState: () => saved, setState: (st: unknown) => { saved = st; } });
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

    it("offers only this page's batches: not a teammate's merged batch, not the person's own terminal", () => {
        const op = (pos: number, batch: string, actor: string): HistoryWindow['ops'][number] => ({ pos, id: `q:${pos}`, at: String(pos),
            actor, kind: 'text.insert', batch, batch_first: pos, plan: null, caused_by: null, blocks: {},
            inverse: [{ kind: 'text.delete', target: { bid: 'p', path: 'text' }, payload: {} }] });
        const w: HistoryWindow = { start: 0, head: 3, before: {}, ops: [
            op(0, 'b-page', 'human:local'), op(1, 'x-teammate', 'human:bob@x.org'), op(2, 'x-codoc-accept', 'human:local')] };
        posted.length = 0;
        document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true, cancelable: true }));
        post({ kind: 'history', history: w });
        const subs = posted.filter(m => m.kind === 'submit').flatMap(m => (m as { subs: Submission[] }).subs);
        expect(subs.map(s => s.batch)).toEqual(['u-b-page']);
        expect((saved as { sent: string[] }).sent).toContain('u-b-page');      // a reload still knows it
        post({ kind: 'acks', acks: subs.map(s => ({ cid: s.cid, op: 'q:9', conflict: null })) });
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
        // An agent adds a block elsewhere: nothing changed for this undo, so ⌘Z moves past it.
        const agent = op(2, 'k-agent', 'agent:x', 'block.add', 'z');
        posted.length = 0;
        cmdZ();
        post({ kind: 'history', history: { start: 0, head: 3, before: {}, ops: [...base, agent] } });
        expect(undoSubs().map(s => s.batch)).toEqual(['u-k-old']);
        post({ kind: 'acks', acks: undoSubs().map(s => ({ cid: s.cid, op: 'k:8', conflict: null })) });
        // Bob restores the block (block.set's history entry names no blocks; its inverse does): the kind change may apply now.
        posted.length = 0;
        cmdZ();
        post({ kind: 'history', history: { start: 0, head: 4, before: {}, ops: [...base, agent, op(3, 'k-bob', 'human:bob', 'block.restore', 'p')] } });
        expect(undoSubs().map(s => s.batch)).toEqual(['u-k-mine']);
        post({ kind: 'acks', acks: undoSubs().map(s => ({ cid: s.cid, op: 'k:9', conflict: null })) });
    });

    it('offers a split undo refused not_adjacent again once someone removed a block', () => {
        const op = (pos: number, batch: string, actor: string, kind: string, bid: string): HistoryWindow['ops'][number] => ({ pos, id: `j:${pos}`,
            at: String(pos), actor, kind, batch, batch_first: pos, plan: null, caused_by: null, blocks: {},
            inverse: [{ kind: kind === 'block.split' ? 'block.merge' : 'block.restore', target: kind === 'block.split' ? { into: 'p', bid: 'n' } : { bid }, payload: {} }] });
        const undoSubs = () => posted.filter(m => m.kind === 'submit').flatMap(m => (m as { subs: Submission[] }).subs);
        const cmdZ = () => document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true, cancelable: true }));
        const base = [op(0, 'j-old', 'human:local', 'block.add', 'q'), op(1, 'j-split', 'human:local', 'block.split', 'p')];
        posted.length = 0;
        cmdZ();
        post({ kind: 'history', history: { start: 0, head: 2, before: {}, ops: base } });
        expect(undoSubs().map(s => s.batch)).toEqual(['u-j-split']);
        post({ kind: 'acks', acks: undoSubs().map(s => ({ cid: s.cid, op: null, conflict: 'not_adjacent' })) });
        // Bob deletes y, the block between the halves; the delete names only y.
        posted.length = 0;
        cmdZ();
        post({ kind: 'history', history: { start: 0, head: 3, before: {}, ops: [...base, op(2, 'j-bob', 'human:bob', 'block.delete', 'y')] } });
        expect(undoSubs().map(s => s.batch)).toEqual(['u-j-split']);
        post({ kind: 'acks', acks: undoSubs().map(s => ({ cid: s.cid, op: 'j:9', conflict: null })) });
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

    // The daemon's lease is the one definition of realizing, and it reaches the
    // page as a phase. An older host also sent a `realizing` list; it changes nothing.
    it("draws each feature's phase as the projection says it", () => {
        post({ kind: 'projection', proj: proj([block('h', 'heading', 'Title'), block('p', 'paragraph', 'one two')],
            { head: 40, phases: { h: 'edited' } }), realizing: ['h'] });
        expect(Array.from(document.querySelectorAll('.codoc-marker'))
            .map(e => [...e.classList].find(c => c.startsWith('codoc-marker-')))).toEqual(['codoc-marker-edited']);
    });
});
