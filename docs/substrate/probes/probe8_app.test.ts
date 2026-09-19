/**
 * Review-8 probes over the real `app.ts` message shell (18855c0), driven as the
 * host drives it: `window.postMessage`-shaped MessageEvents in, the fake
 * `acquireVsCodeApi().postMessage` capturing what the page sends back.
 *
 *   cd <worktree>/vscode-codoc && npx vitest run --config <scratchpad>/vitest.probe8.config.mjs -t 'probe8 app'
 *
 * One module instance for the whole file (app.ts has module-level state), so
 * the scenarios run in order on one page.
 */
import * as fs from 'node:fs';
import { describe, expect, it } from 'vitest';

import type { Projection, ProjectedBlock, Submission } from '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/calc/projection';
import type { HistoryWindow } from '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/calc/window';

const OUT = '/private/tmp/claude-501/-Users-r4yen-Desktop-Research-CoDoc-repos-CodeNav/d28f4a40-c80f-4f4e-b0c9-17b9d836ed30/scratchpad';

if (!Range.prototype.getClientRects) {
    Range.prototype.getClientRects = () => ({ length: 0, item: () => null, [Symbol.iterator]: function* () {} } as unknown as DOMRectList);
    Range.prototype.getBoundingClientRect = () => ({ top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0 } as DOMRect);
}
HTMLElement.prototype.scrollIntoView = () => undefined;

let opn = 0;
function block(id: string, kind: 'heading' | 'paragraph', text: string, level = 1, head = 1): ProjectedBlock {
    const n = Array.from(text).length;
    return { id, kind, level, rank: id, head,
             fields: { text: { text, runs: n ? [{ op: `s:${id}${opn++}`, k0: 0, start: 0, end: n, actor: 'human:a', at: '1', plan: null }] : [],
                               spans: [], suggest_del: [] } } };
}
const proj = (blocks: ProjectedBlock[], extra: Partial<Projection> = {}): Projection =>
    ({ head: 9, frontier: {}, blocks, phases: {}, plans: [], bindings: { by_feature: {}, by_file: {} }, claims: {}, ...extra });

const sent: Array<Record<string, unknown>> = [];
(globalThis as unknown as { acquireVsCodeApi: () => unknown }).acquireVsCodeApi = () => ({ postMessage: (m: unknown) => sent.push(m as Record<string, unknown>), getState: () => undefined, setState: () => undefined });
const app = document.createElement('div');
app.id = 'app';
document.body.append(app);
await import('/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/editor/app');

const post = (data: unknown, origin = window.location.origin) => window.dispatchEvent(new window.MessageEvent('message', { data, origin } as MessageEventInit));
const textOf = (bid: string) => (document.querySelector(`.codoc-page [data-bid="${bid}"]`) as HTMLElement | null)?.textContent ?? null;
const toolbarButtons = () => Array.from(document.querySelectorAll('.codoc-toolbar .codoc-btn')).map(b => b.textContent ?? '');
const keydown = (k: string, init: KeyboardEventInit = {}) => document.dispatchEvent(new window.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, metaKey: true, ctrlKey: true, ...init }));
const med = (xs: number[]) => xs.slice().sort((a, b) => a - b)[Math.floor(xs.length / 2)];

describe('probe8 app — the message shell', () => {
    it('S1: an older projection arriving after a newer one (two async file reads finishing out of order) does not move the page back', () => {
        const base = [block('h1', 'heading', 'Client'), block('p1', 'paragraph', 'Retries once.')];
        post({ kind: 'projection', proj: proj(base, { head: 9 }) });
        expect(textOf('p1')).toBe('Retries once.');
        const newer = proj([base[0], block('p1', 'paragraph', 'Retries twice, then gives up.', 0, 20)], { head: 20 });
        const older = proj([base[0], block('p1', 'paragraph', 'Retries once.', 0, 1)], { head: 9 });
        post({ kind: 'projection', proj: newer });
        expect(textOf('p1')).toBe('Retries twice, then gives up.');
        post({ kind: 'projection', proj: older });                              // its readFile resolved second
        expect(textOf('p1')).toBe('Retries twice, then gives up.');
    });

    it('S2 (confirm): a message from another origin that is not the host frame is ignored', () => {
        const before = textOf('p1');
        post({ kind: 'projection', proj: proj([block('h1', 'heading', 'Client'), block('p1', 'paragraph', 'INJECTED', 0, 99)], { head: 99 }) }, 'https://evil.example');
        expect(textOf('p1')).toBe(before);
    });

    it('S3 (confirm): a `suggest` role hides Hand off, Accept all and Build now; owner shows them', () => {
        const p = proj([block('h1', 'heading', 'Client'), block('p1', 'paragraph', 'Retries twice, then gives up.', 0, 20)],
                       { head: 21, phases: { h1: 'edited' }, plans: [{ id: 'pl1', actor: 'agent:x', kind: 'edit', target: 'p1', note: '', status: 'open' }] });
        post({ kind: 'projection', proj: p });
        post({ kind: 'status', status: { state: 'synced', role: 'owner', actor: 'human:local' } });
        expect(toolbarButtons().some(b => b.startsWith('Hand off'))).toBe(true);
        expect(toolbarButtons().some(b => b.startsWith('Accept all'))).toBe(true);
        post({ kind: 'status', status: { state: 'synced', role: 'suggest', actor: 'human:gh:alice' } });
        expect(toolbarButtons().some(b => b.startsWith('Hand off'))).toBe(false);
        expect(toolbarButtons().some(b => b.startsWith('Accept all'))).toBe(false);
        expect(toolbarButtons().some(b => b === 'Build now')).toBe(false);
        post({ kind: 'status', status: { state: 'synced', role: 'owner', actor: 'human:local' } });
    });

    it('S4 (confirm): ⌘Z waits for the acks of what the page sent, then submits the inverse of the newest batch', () => {
        sent.length = 0;
        // Accept all → one submit whose acks are outstanding.
        (Array.from(document.querySelectorAll('.codoc-toolbar .codoc-btn')).find(b => b.textContent?.startsWith('Accept all')) as HTMLButtonElement).click();
        const sub = sent.find(m => m.kind === 'submit') as { subs: Submission[] };
        expect(sub.subs.map(s => s.kind)).toEqual(['plan.accept']);
        sent.length = 0;
        keydown('z');
        expect(sent.map(m => m.kind)).toEqual(['history']);                     // asked for the window
        const w: HistoryWindow = { start: 0, head: 3, before: {}, ops: [
            { pos: 0, id: 'h:1', at: '2026-09-18T00:00:00', actor: 'human:local', kind: 'text.insert', batch: 't-older', plan: null, caused_by: null, blocks: { p1: 'x' },
              inverse: [{ kind: 'text.delete', target: { bid: 'p1', path: 'text' }, payload: { segments: [['h:1', 0, 1]] } }] },
        ] };
        sent.length = 0;
        post({ kind: 'history', history: w });                                  // the accept's ack has not come: wait
        expect(sent.filter(m => m.kind === 'submit')).toEqual([]);
        post({ kind: 'acks', acks: sub.subs.map(s => ({ cid: s.cid, op: 'h:2', conflict: null })) });
        expect(sent.map(m => m.kind)).toContain('history');                     // asked again once the acks landed
        sent.length = 0;
        const w2: HistoryWindow = { ...w, head: 4, ops: [...w.ops, { pos: 1, id: 'h:2', at: '2026-09-18T00:00:01', actor: 'human:local', kind: 'plan.accept', batch: sub.subs[0].batch, plan: null, caused_by: null, blocks: {},
            inverse: [{ kind: 'plan.reopen', target: { pid: 'pl1' }, payload: { from: 'accepted' } }] }] };
        post({ kind: 'history', history: w2 });
        const undo = sent.find(m => m.kind === 'submit') as { subs: Submission[] } | undefined;
        expect(undo?.subs.map(s => [s.kind, s.batch])).toEqual([['plan.reopen', `u-${sub.subs[0].batch}`]]);
    });

    it('S5 (confirm): one projection that renames, reorders and removes headings keeps every row once, in order, and nothing throws', () => {
        const errors: string[] = [];
        const onErr = (e: Event) => { errors.push(String((e as ErrorEvent).message)); e.preventDefault(); };
        window.addEventListener('error', onErr);
        const abc = [block('a', 'heading', 'A'), block('pa', 'paragraph', 'a'), block('b', 'heading', 'B'), block('pb', 'paragraph', 'b'), block('c', 'heading', 'C'), block('pc', 'paragraph', 'c')];
        post({ kind: 'projection', proj: proj(abc, { head: 30 }) });
        const rows = () => Array.from(document.querySelectorAll('.codoc-row')).map(r => `${(r as HTMLElement).dataset.fid}:${r.querySelector('.codoc-row-title')?.textContent}`);
        expect(rows()).toEqual(['a:A', 'b:B', 'c:C']);
        // c first and renamed, b gone, a renamed
        post({ kind: 'projection', proj: proj([block('c', 'heading', 'C2', 1, 31), abc[5], block('a', 'heading', 'A2', 1, 31), abc[1]], { head: 31 }) });
        expect(rows()).toEqual(['c:C2', 'a:A2']);
        // then a moves first while c is renamed again and a fourth appears between them
        post({ kind: 'projection', proj: proj([block('a', 'heading', 'A2', 1, 31), abc[1], block('d', 'heading', 'D', 1, 32), block('c', 'heading', 'C3', 1, 32), abc[5]], { head: 32 }) });
        expect(rows()).toEqual(['a:A2', 'd:D', 'c:C3']);
        window.removeEventListener('error', onErr);
        expect(errors).toEqual([]);
    });

    it('G3: the shell on a 1,500-feature projection (mount, a later projection with one block changed, a phase change)', () => {
        const blocks: ProjectedBlock[] = [];
        for (let i = 0; i < 1500; i++) { blocks.push(block(`h${i}`, 'heading', `Feature ${i}`)); blocks.push(block(`p${i}`, 'paragraph', `Paragraph ${i}: it retries once before giving up, then reports the failure to the caller.`)); }
        let t = performance.now();
        post({ kind: 'projection', proj: proj(blocks, { head: 100 }) });
        const first = performance.now() - t;
        const xs: number[] = [];
        for (let i = 0; i < 3; i++) {
            const again = proj(blocks.map(b => b.id === 'p900' ? block('p900', 'paragraph', `Paragraph 900 rewritten ${i}.`, 1, 101 + i) : b), { head: 101 + i });
            t = performance.now(); post({ kind: 'projection', proj: again }); xs.push(performance.now() - t);
        }
        const phased = proj(blocks, { head: 110, phases: { h900: 'edited' } });
        t = performance.now(); post({ kind: 'projection', proj: phased }); const ph = performance.now() - t;
        const line = `app.ts: projection at 1,500 features (adopt into an existing page + tree pane) ${first.toFixed(0)} ms; a later projection with one block changed, median of 3: ${med(xs).toFixed(0)} ms; one feature's phase changed: ${ph.toFixed(0)} ms; rows: ${document.querySelectorAll('.codoc-row').length}`;
        fs.appendFileSync(`${OUT}/probe8_cost.txt`, line + '\n');
        console.log('\n' + line);
        expect(document.querySelectorAll('.codoc-row').length).toBe(1500);
    });
});
