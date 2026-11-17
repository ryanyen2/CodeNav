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
              blocks: { p: 'one two' }, inverse: [{ kind: 'text.delete', target: { bid: 'p', path: 'text' }, payload: {} }] },
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
