/**
 * Review-8 probes for the editor surface, at 18855c0. Real `mountEditor` views
 * under jsdom, driven the way the browser drives them (keydown through
 * ProseMirror's handlers, `view.dispatch` of what `readDOMChange` would
 * dispatch, `handlePaste` through `someProp`, DOM drag events through
 * `handleDOMEvents`). Every test asserts the CORRECT behaviour: a failing test
 * is a confirmed fault, a passing one a confirmed property.
 *
 *   cd <worktree>/vscode-codoc && npx vitest run --config <scratchpad>/vitest.probe8.config.mjs -t 'probe8 editor'
 *
 * Sections: A the ack/adopt seam (the frontier rule and what escapes it);
 * B the paints after a gesture; C the CSS drag handle under ProseMirror's own
 * drag handlers; D the History scrubber; E the margin's authority rule;
 * G cost (writes probe8_cost.txt).
 */
import { type Node as PMNode } from '@tiptap/pm/model';
import { EditorState, Plugin, TextSelection } from '@tiptap/pm/state';
import { EditorView } from '@tiptap/pm/view';
import * as fs from 'node:fs';
import { describe, expect, it, vi } from 'vitest';

const SRC = '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src';
const OUT = '/private/tmp/claude-501/-Users-r4yen-Desktop-Research-CoDoc-repos-CodeNav/d28f4a40-c80f-4f4e-b0c9-17b9d836ed30/scratchpad';

// ── instrumentation (transparent for correctness; timed for §G) ──────────────
let captured: Map<string, Shadow> | null = null;
const cost = { paints: 0, paintsCalls: 0, decos: 0, decosCalls: 0, decosFor: 0, decosForCalls: 0 };

vi.mock('/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/calc/adopt', async (importOriginal) => {
    const actual = await importOriginal<Record<string, unknown>>();
    return {
        ...actual,
        docFrom: (schema: unknown, proj: unknown) => {
            const r = (actual.docFrom as (s: unknown, p: unknown) => { doc: PMNode; shadows: Map<string, Shadow> })(schema, proj);
            captured = r.shadows;
            return r;
        },
    };
});
vi.mock('/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/calc/claims', async (importOriginal) => {
    const actual = await importOriginal<Record<string, unknown>>();
    return {
        ...actual,
        paintsFor: (...a: unknown[]) => { const t = performance.now(); const r = (actual.paintsFor as (...x: unknown[]) => unknown)(...a); cost.paints += performance.now() - t; cost.paintsCalls++; return r; },
    };
});
vi.mock('/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/editor/decorations', async (importOriginal) => {
    const actual = await importOriginal<Record<string, unknown>>();
    return {
        ...actual,
        buildDecorations: (...a: unknown[]) => { const t = performance.now(); const r = (actual.buildDecorations as (...x: unknown[]) => unknown)(...a); cost.decos += performance.now() - t; cost.decosCalls++; return r; },
        decorationsFor: (...a: unknown[]) => { const t = performance.now(); const r = (actual.decorationsFor as (...x: unknown[]) => unknown)(...a); cost.decosFor += performance.now() - t; cost.decosForCalls++; return r; },
    };
});

import type { OpBody, Projection, ProjectedBlock, Submission, TextField, Run } from '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/calc/projection';
import type { Shadow } from '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/calc/shadow';
import type { HistoryWindow } from '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/calc/window';
import { mountEditor, type EditorHandle } from '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/editor/editor';
import { Margin } from '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/editor/comments';
import { Timeline } from '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/editor/timeline';
import { RECORDED } from '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/editor/commands';

if (!Range.prototype.getClientRects) {
    Range.prototype.getClientRects = () => ({ length: 0, item: () => null, [Symbol.iterator]: function* () {} } as unknown as DOMRectList);
    Range.prototype.getBoundingClientRect = () => ({ top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0 } as DOMRect);
}
HTMLElement.prototype.scrollIntoView = () => undefined;
// jsdom has no hit testing. ProseMirror's own mousedown/dragstart/drop call
// `posAtCoords`, which returns null when nothing is under the point — the
// same as a pointer outside the text, which is where a handle is.
(document as unknown as { elementFromPoint: () => Element | null }).elementFromPoint = () => null;

// ── fixtures / harness (as in src/test/editor-dom.test.ts) ───────────────────
let opn = 0;
function block(id: string, kind: 'heading' | 'paragraph', text: string, level = 1, head = 1, actor = 'human:a'): ProjectedBlock {
    const n = Array.from(text).length;
    return { id, kind, level, rank: id, head,
             fields: { text: { text, runs: n ? [{ op: `s:${opn++}`, k0: 0, start: 0, end: n, actor, at: '1', plan: null }] : [],
                               spans: [], suggest_del: [] } } };
}
const proj = (blocks: ProjectedBlock[], extra: Partial<Projection> = {}): Projection =>
    ({ head: 9, frontier: {}, blocks, phases: {}, plans: [], bindings: { by_feature: {}, by_file: {} }, claims: {}, ...extra });

interface Mounted { handle: EditorHandle; view: EditorView; shadows: Map<string, Shadow>; submitted: Submission[]; conflicts: string[]; proj: Projection; container: HTMLElement }

function mountProj(p: Projection): Mounted {
    captured = null;
    const container = document.createElement('div');
    document.body.append(container);
    const submitted: Submission[] = [];
    const conflicts: string[] = [];
    const handle = mountEditor(container, p, { submit: s => submitted.push(...s), onConflict: (_b, c) => conflicts.push(c) });
    if (!captured) throw new Error('docFrom mock did not capture the shadows');
    return { handle, view: handle.view, shadows: captured, submitted, conflicts, proj: p, container };
}
const mount = (blocks: ProjectedBlock[], extra: Partial<Projection> = {}) => mountProj(proj(blocks, extra));

const ids = (doc: PMNode) => { const out: string[] = []; doc.forEach(x => out.push(`${x.type.name}:${x.attrs.bid}`)); return out; };
const texts = (doc: PMNode) => { const out: string[] = []; doc.forEach(x => out.push(x.textBetween(0, x.content.size, '\n'))); return out; };
function posIn(view: EditorView, bid: string, off = 0): number {
    let p = -1;
    view.state.doc.forEach((n, pos) => { if (n.attrs.bid === bid) p = pos + 1 + off; });
    if (p < 0) throw new Error(`no block ${bid}`);
    return p;
}
const select = (view: EditorView, from: number, to = from) => view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, from, to)));
function key(view: EditorView, name: string, code: number, init: KeyboardEventInit = {}): boolean {
    const ev = new window.KeyboardEvent('keydown', { key: name, keyCode: code, bubbles: true, cancelable: true, ...init } as KeyboardEventInit);
    view.dom.dispatchEvent(ev);
    return ev.defaultPrevented;
}
const typeText = (view: EditorView, text: string) => view.dispatch(view.state.tr.insertText(text));
function shadowFaults(m: Mounted): string[] {
    const faults: string[] = [];
    const seen = new Set<string>();
    m.view.state.doc.forEach(n => {
        const bid = n.attrs.bid as string | null;
        if (!bid) { faults.push(`${n.type.name} without bid`); return; }
        if (seen.has(bid)) faults.push(`duplicate bid ${bid}`);
        seen.add(bid);
        if (n.type.name === 'kindBlock') return;
        const sh = m.shadows.get(bid);
        const doc = n.textBetween(0, n.content.size, '\n');
        if (!sh) { faults.push(`${bid}: no shadow (doc ${JSON.stringify(doc)})`); return; }
        if (sh.text() !== doc) faults.push(`${bid}: shadow ${JSON.stringify(sh.text())} != doc ${JSON.stringify(doc)}`);
    });
    for (const bid of m.shadows.keys()) if (!seen.has(bid)) faults.push(`shadow for ${bid} which is not on the page`);
    return faults;
}
const el = (m: Mounted, bid: string) => m.view.dom.querySelector(`[data-bid="${bid}"]`) as HTMLElement;
const paintsIn = (m: Mounted, bid: string) => Array.from(el(m, bid).querySelectorAll('.codoc-paint')).map(e => Array.from(e.classList).filter(c => c !== 'codoc-paint').join(' ')).sort();

/** The run of `b`'s fixture text, so a two-actor projection can keep "my" characters' identity. */
const runOf = (p: Projection, bid: string): Run => (p.blocks.find(b => b.id === bid)!.fields.text as TextField).runs[0];
/** A projected paragraph made of runs, for two-actor projections. */
function para(id: string, head: number, parts: Array<[text: string, run: Partial<Run> & { op: string; actor: string }]>): ProjectedBlock {
    let at = 0;
    const runs: Run[] = [];
    let text = '';
    for (const [t, r] of parts) {
        const n = Array.from(t).length;
        runs.push({ k0: 0, at: '1', plan: null, ...r, start: at, end: at + n } as Run);
        at += n; text += t;
    }
    return { id, kind: 'paragraph', level: 0, rank: id, head, fields: { text: { text, runs, spans: [], suggest_del: [] } } };
}

// ── A. the ack/adopt seam ────────────────────────────────────────────────────
describe('probe8 editor — A. a released block and the projection it may not read', () => {
    it('A1: a conflict in one block does not let a co-acked block read a projection that predates its op', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one'), block('p2', 'paragraph', 'two')]);
        select(m.view, posIn(m.view, 'p1', 3)); typeText(m.view, 'x'); m.handle.flush();
        select(m.view, posIn(m.view, 'p2', 3)); typeText(m.view, 'y'); m.handle.flush();
        const [cidA, cidB] = m.submitted.map(s => s.cid);
        // Bob prefixed p2 before either of my ops landed; the daemon published that (frontier: bob only) and it
        // arrived while I held both blocks.
        const bob = proj([block('h', 'heading', 'T'), m.proj.blocks[1],
                          para('p2', 9, [['B', { op: 'bob:1', actor: 'human:gh:bob' }], ['two', { ...runOf(m.proj, 'p2') }]])],
                         { frontier: { bob: 1 } });
        m.handle.adopt(bob);
        expect(texts(m.view.state.doc)).toEqual(['T', 'onex', 'twoy']);          // both held: right
        // Then one message carries both acks: mine on p1 was refused, mine on p2 landed as h:6.
        m.handle.ack([{ cid: cidA, op: 'h:5', conflict: 'unknown_anchor' }, { cid: cidB, op: 'h:6', conflict: null }]);
        expect(texts(m.view.state.doc)[1]).toBe('one');                          // p1 re-read: right (the op did nothing)
        // p2: the projection's frontier does NOT cover h:6, so §6 says it must not be re-read — my 'y' is in the store.
        expect(texts(m.view.state.doc)[2]).toBe('twoy');
        expect(shadowFaults(m)).toEqual([]);
    });

    it('A2: the same projection delivered twice (a doubled file-watcher event) does not undo typing it does not contain', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one')]);
        select(m.view, posIn(m.view, 'p1', 3)); typeText(m.view, 'x'); m.handle.flush();
        const cid = m.submitted[0].cid;
        const bob = proj([block('h', 'heading', 'T'),
                          para('p1', 9, [['B', { op: 'bob:1', actor: 'human:gh:bob' }], ['one', { ...runOf(m.proj, 'p1') }]])],
                         { frontier: { bob: 1 } });
        m.handle.adopt(bob);
        expect(texts(m.view.state.doc)[1]).toBe('onex');                         // held
        m.handle.ack([{ cid, op: 'h:5', conflict: null }]);
        expect(texts(m.view.state.doc)[1]).toBe('onex');                         // released, projection older than h:5: kept — right
        m.handle.adopt(bob);                                                     // VS Code's watcher fires twice per atomic write
        expect(texts(m.view.state.doc)[1]).toBe('onex');
        expect(shadowFaults(m)).toEqual([]);
    });

    it('A3: two acks in one message, the projection covering one op and not the other: only the covered block is re-read', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one'), block('p2', 'paragraph', 'two')]);
        select(m.view, posIn(m.view, 'p1', 3)); typeText(m.view, 'x'); m.handle.flush();
        select(m.view, posIn(m.view, 'p2', 3)); typeText(m.view, 'y'); m.handle.flush();
        const [cidA, cidB] = m.submitted.map(s => s.cid);
        // Published after my p1 op (h:5) and Bob's p2 prefix, before my p2 op (h:6).
        const p = proj([block('h', 'heading', 'T'),
                        para('p1', 5, [['one', { ...runOf(m.proj, 'p1') }], ['x', { op: 'h:5', actor: 'human:local' }]]),
                        para('p2', 9, [['B', { op: 'bob:1', actor: 'human:gh:bob' }], ['two', { ...runOf(m.proj, 'p2') }]])],
                       { frontier: { h: 5, bob: 1 } });
        m.handle.adopt(p);
        m.handle.ack([{ cid: cidA, op: 'h:5', conflict: null }, { cid: cidB, op: 'h:6', conflict: null }]);
        expect(texts(m.view.state.doc)[1]).toBe('onex');                         // covered: re-read (same words) — right
        expect(texts(m.view.state.doc)[2]).toBe('twoy');                         // not covered: the page keeps its word
        expect(shadowFaults(m)).toEqual([]);
    });

    it('A4: a projection older than the one already adopted is not adopted (the IDE reads two files asynchronously)', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one')]);
        const newer = proj([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one, then two', 0, 20)], { head: 20 });
        const older = proj([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one', 0, 1)], { head: 9 });
        m.handle.adopt(newer);
        expect(texts(m.view.state.doc)[1]).toBe('one, then two');
        m.handle.adopt(older);                                                   // completed its read second
        expect(texts(m.view.state.doc)[1]).toBe('one, then two');
        expect(m.handle.projection().head).toBe(20);
    });

    it('A5 (confirm): a block born by Enter is re-read from the first projection that covers the split', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one two')]);
        select(m.view, posIn(m.view, 'p1', 3));
        key(m.view, 'Enter', 13);
        m.handle.flush();
        const nid = ids(m.view.state.doc)[2].split(':')[1];
        const cid = m.submitted[0].cid;
        const mine = runOf(m.proj, 'p1');
        const p = proj([block('h', 'heading', 'T'),
                        para('p1', 5, [['one', { ...mine }]]),
                        para(nid, 5, [[' two', { ...mine, k0: 3 }]])],
                       { frontier: { h: 5 } });
        m.handle.adopt(p);                                                       // both held: skipped
        m.handle.ack([{ cid, op: 'h:5', conflict: null }]);
        expect(texts(m.view.state.doc)).toEqual(['T', 'one', ' two']);
        let heads: number[] = [];
        m.view.state.doc.forEach(n => heads.push(n.attrs.head as number));
        expect(heads).toEqual([1, 5, 5]);                                        // both re-read
        expect(shadowFaults(m)).toEqual([]);
        expect(m.view.state.selection.from).toBe(posIn(m.view, nid, 0));       // the caret stayed where Enter put it
    });

    it('A6 (confirm): a batch acked across two messages is re-read once the last ack lands', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one')]);
        select(m.view, posIn(m.view, 'p1', 3)); typeText(m.view, 'x'); typeText(m.view, 'y'); m.handle.flush();
        expect(m.submitted.length).toBe(2);
        const [c1, c2] = m.submitted.map(s => s.cid);
        const p = proj([block('h', 'heading', 'T'),
                        para('p1', 6, [['B', { op: 'bob:1', actor: 'human:gh:bob' }], ['one', { ...runOf(m.proj, 'p1') }],
                                       ['x', { op: 'h:5', actor: 'human:local' }], ['y', { op: 'h:6', actor: 'human:local' }]])],
                       { frontier: { h: 6, bob: 1 } });
        m.handle.adopt(p);
        m.handle.ack([{ cid: c1, op: 'h:5', conflict: null }]);
        expect(texts(m.view.state.doc)[1]).toBe('onexy');                        // still held by c2
        m.handle.ack([{ cid: c2, op: 'h:6', conflict: null }]);
        expect(texts(m.view.state.doc)[1]).toBe('Bonexy');
        expect(shadowFaults(m)).toEqual([]);
    });
});

// ── B. the paints after a gesture ────────────────────────────────────────────
describe('probe8 editor — B. paints', () => {
    it('B1: the paragraph Enter creates is inked once the projection carries its words', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one two')]);
        expect(paintsIn(m, 'p1')).toEqual(['codoc-ink-unsent']);                 // a human run, nothing realized
        select(m.view, posIn(m.view, 'p1', 3));
        key(m.view, 'Enter', 13);
        m.handle.flush();
        const nid = ids(m.view.state.doc)[2].split(':')[1];
        const cid = m.submitted[0].cid;
        const mine = runOf(m.proj, 'p1');
        const p = proj([block('h', 'heading', 'T'), para('p1', 5, [['one', { ...mine }]]), para(nid, 5, [[' two', { ...mine, k0: 3 }]])],
                       { frontier: { h: 5 } });
        m.handle.adopt(p);
        m.handle.ack([{ cid, op: 'h:5', conflict: null }]);
        expect(texts(m.view.state.doc)).toEqual(['T', 'one', ' two']);
        expect(paintsIn(m, 'p1')).toEqual(['codoc-ink-unsent']);
        expect(paintsIn(m, nid)).toEqual(['codoc-ink-unsent']);
    });

    it("B2: a paragraph dragged under another feature is painted as that feature's (its hand-off watermark), at once and after the projection", () => {
        const m = mount([block('h1', 'heading', 'A'), block('p1', 'paragraph', 'a-body'), block('h2', 'heading', 'B'), block('p2', 'paragraph', 'b-body')],
                        { watermarks: { h1: { handoff: { s: 9999 }, realized: {} } } });
        expect(paintsIn(m, 'p1')).toEqual(['codoc-ink-sent']);                   // A was handed off
        expect(paintsIn(m, 'p2')).toEqual(['codoc-ink-unsent']);                 // B was not
        m.handle.moveBlock('p1', 'p2');
        expect(ids(m.view.state.doc)).toEqual(['heading:h1', 'heading:h2', 'paragraph:p2', 'paragraph:p1']);
        expect(paintsIn(m, 'p1')).toEqual(['codoc-ink-unsent']);                 // now B's: not handed off
        m.handle.flush();
        const cid = m.submitted[0].cid;
        const p = proj([block('h1', 'heading', 'A'), block('h2', 'heading', 'B'), m.proj.blocks[3], { ...m.proj.blocks[1], head: 5, rank: 'z' }],
                       { frontier: { h: 5 }, watermarks: m.proj.watermarks });
        m.handle.adopt(p);
        m.handle.ack([{ cid, op: 'h:5', conflict: null }]);
        expect(paintsIn(m, 'p1')).toEqual(['codoc-ink-unsent']);
    });

    it("B3 (contract): a projection in which only `plans` changed (no block head, no phase) re-paints the plan's words — reachable only if the applier ever stops touching the block on a verdict (probe8_hub H10 says it touches it today)", () => {
        const run = { op: 's:x1', actor: 'human:a' };
        const p1 = para('p1', 1, [['one', run], [' agent', { op: 'ag:1', actor: 'agent:x', plan: 'pl1' }]]);
        const plans = (open: string[]) => open.map(id => ({ id, actor: 'agent:x', kind: 'edit', target: 'p1', note: '', status: 'open' }));
        const m = mount([block('h', 'heading', 'T'), p1], { plans: plans(['pl1', 'pl2']), phases: { h: 'proposed' } });
        expect(paintsIn(m, 'p1')).toEqual(['codoc-ink-unsent', 'codoc-plan-open']);
        m.handle.adopt(proj([block('h', 'heading', 'T'), p1], { plans: plans(['pl2']), phases: { h: 'proposed' }, head: 10 }));
        expect(paintsIn(m, 'p1')).toEqual(['codoc-ink-unsent', 'codoc-plan-accepted']);
    });

    it("B4 (confirm): another actor's heading arriving above a paragraph re-paints it as the new feature's", () => {
        const m = mount([block('h1', 'heading', 'A'), block('p1', 'paragraph', 'a-body')],
                        { watermarks: { h1: { handoff: { s: 9999 }, realized: {} } } });
        expect(paintsIn(m, 'p1')).toEqual(['codoc-ink-sent']);
        m.handle.adopt(proj([block('h1', 'heading', 'A'), block('h9', 'heading', 'New', 1, 7), m.proj.blocks[1]],
                            { watermarks: m.proj.watermarks, head: 12 }));
        expect(paintsIn(m, 'p1')).toEqual(['codoc-ink-unsent']);
    });
});

// ── C. the CSS drag handle under ProseMirror's own drag handlers ─────────────
class DT {
    private d = new Map<string, string>();
    files = { length: 0 } as unknown as FileList;
    effectAllowed = 'uninitialized';
    dropEffect = 'none';
    get types(): string[] { return [...this.d.keys()]; }
    setData(t: string, v: string): void { this.d.set(t, v); }
    getData(t: string): string { return this.d.get(t) ?? ''; }
    clearData(t?: string): void { if (t) this.d.delete(t); else this.d.clear(); }
}
function dragEvent(type: string, target: Element, dt: DT, clientY = 10): Event {
    const ev = new window.Event(type, { bubbles: true, cancelable: true });
    Object.defineProperty(ev, 'dataTransfer', { value: dt });
    Object.defineProperty(ev, 'clientX', { value: 5 });
    Object.defineProperty(ev, 'clientY', { value: clientY });
    target.dispatchEvent(ev);
    return ev;
}
/** A drag as Chrome delivers it: mousedown in the handle strip, dragstart on the block, dragover and drop on the target. */
function dragBlock(m: Mounted, from: string, to: string): { dt: DT; drop: Event } {
    const src = el(m, from);
    src.dispatchEvent(new window.MouseEvent('mousedown', { bubbles: true, cancelable: true, button: 0, clientX: -30, clientY: 5 }));
    const dt = new DT();
    dragEvent('dragstart', src, dt);
    const tgt = el(m, to);
    dragEvent('dragover', tgt, dt);
    const drop = dragEvent('drop', tgt, dt);
    dragEvent('dragend', src, dt);
    return { dt, drop };
}

describe('probe8 editor — C. the CSS drag handle', () => {
    it('C1: dragging a block by its handle moves it (the payload survives dragstart)', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one'), block('p2', 'paragraph', 'two')]);
        const { dt } = dragBlock(m, 'p1', 'p2');
        expect(dt.getData('application/x-codoc-bid')).toBe('p1');                // what drag.ts set at dragstart
        expect(ids(m.view.state.doc)).toEqual(['heading:h', 'paragraph:p2', 'paragraph:p1']);
        expect(m.view.dragging).toBeNull();                                      // ProseMirror did not take the drag as its own
        m.handle.flush();
        expect(m.submitted.map(s => s.kind)).toEqual(['block.move']);
    });

    it('C2: with words selected elsewhere, a handle drag is still a block move, not a move of the selected words', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one'), block('p2', 'paragraph', 'two')]);
        select(m.view, posIn(m.view, 'p2', 0), posIn(m.view, 'p2', 3));         // "two" selected
        const { dt } = dragBlock(m, 'p1', 'p2');
        expect(dt.getData('application/x-codoc-bid')).toBe('p1');
        expect(m.view.dragging).toBeNull();                                      // else ProseMirror's drop moves "two" to the pointer
        expect(ids(m.view.state.doc)).toEqual(['heading:h', 'paragraph:p2', 'paragraph:p1']);
    });

    it('C3 (confirm): a mousedown in the text, not the handle strip, arms nothing and starts no block drag', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one'), block('p2', 'paragraph', 'two')]);
        const src = el(m, 'p1');
        src.dispatchEvent(new window.MouseEvent('mousedown', { bubbles: true, cancelable: true, button: 0, clientX: 40, clientY: 5 }));
        expect(src.draggable).toBe(false);
        const dt = new DT();
        dragEvent('dragstart', src, dt);
        expect(dt.getData('application/x-codoc-bid')).toBe('');
        expect(ids(m.view.state.doc)).toEqual(['heading:h', 'paragraph:p1', 'paragraph:p2']);
    });
});

// ── D. the History scrubber ──────────────────────────────────────────────────
const entry = (pos: number, id = `s:${pos}`): HistoryWindow['ops'][number] =>
    ({ pos, id, at: `2026-09-18T00:00:${String(pos).padStart(2, '0')}`, actor: 'human:gh:bob', kind: 'text.insert', batch: `b${pos}`, plan: null, caused_by: null, blocks: { p1: `one${pos}` }, inverse: [] });

describe('probe8 editor — D. the scrubber', () => {
    it('D1: parked on a moment in the past, the page stays on that moment when the window slides (it is the newest 200 ops)', () => {
        const parent = document.createElement('div');
        document.body.append(parent);
        const dom = document.createElement('div');
        parent.append(dom);
        const tl = new Timeline(parent, () => dom);
        const p = proj([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one')]);
        tl.setWindow({ start: 0, head: 3, before: { p1: 'one' }, ops: [entry(0), entry(1), entry(2)] }, p);
        tl.toggle(true);
        const range = parent.querySelector('input[type=range]') as HTMLInputElement;
        range.value = '1';
        range.dispatchEvent(new window.Event('input'));
        const caption = parent.querySelector('.codoc-timeline-caption') as HTMLElement;
        expect(caption.textContent).toContain('00:00:01');                       // the moment after op s:1
        // Someone else edits: the window is the newest three ops now, so s:0 has left it.
        tl.setWindow({ start: 1, head: 4, before: { p1: 'one0' }, ops: [entry(1), entry(2), entry(3)] }, p);
        expect(tl.past.hidden).toBe(false);
        expect(caption.textContent).toContain('00:00:01');
    });

    it('D2 (confirm): parked at now, the page stays at now when the window grows', () => {
        const parent = document.createElement('div');
        document.body.append(parent);
        const dom = document.createElement('div');
        parent.append(dom);
        const tl = new Timeline(parent, () => dom);
        const p = proj([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one')]);
        tl.setWindow({ start: 0, head: 2, before: { p1: 'one' }, ops: [entry(0), entry(1)] }, p);
        tl.toggle(true);
        tl.setWindow({ start: 0, head: 3, before: { p1: 'one' }, ops: [entry(0), entry(1), entry(2)] }, p);
        expect(tl.past.hidden).toBe(true);
        expect(dom.hidden).toBe(false);
    });
});

// ── E. the margin's authority rule ───────────────────────────────────────────
function commentBlock(id: string, thread: string, anchor: string, actor: string, body: string, status = 'open'): ProjectedBlock {
    return { id, kind: 'comment', level: 0, rank: `z${id}`, head: 1, fields: { thread, anchor, anchor_text: 'one', scope: 'code', status,
             body: { text: body, runs: [{ op: `c:${id}`, k0: 0, start: 0, end: Array.from(body).length, actor, at: '1', plan: null }], spans: [], suggest_del: [] } } };
}
function marginFor(m: Mounted, me: { actor: string; decides: boolean }): { margin: Margin; page: HTMLElement; posted: OpBody[] } {
    const page = document.createElement('div');
    document.body.append(page);
    const posted: OpBody[] = [];
    const margin = new Margin(page, () => m.view, { submit: ops => posted.push(...ops), build: () => undefined, newBid: () => 'c-new', featureOf: () => 'h', me: () => me });
    return { margin, page, posted };
}
const buttons = (card: Element) => Array.from(card.querySelectorAll('button')).map(b => b.textContent);

describe('probe8 editor — E. the margin', () => {
    it('E1 (confirm): a contributor sees Resolve on a thread they started, even after a maintainer replied, and not on one they did not', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one two')]);
        const { margin, page } = marginFor(m, { actor: 'human:gh:alice', decides: false });
        const p = proj([...m.proj.blocks,
                        commentBlock('c1', 'c1', 'p1', 'human:gh:alice', 'split this'), commentBlock('c2', 'c1', 'p1', 'human:gh:bob', 'agreed'),
                        commentBlock('c3', 'c3', 'p1', 'human:gh:bob', 'rename it')]);
        (p.blocks[1].fields.text as TextField).spans.push({ kind: 'comment', attrs: { block: 'c1' }, start: 0, end: 3 }, { kind: 'comment', attrs: { block: 'c3' }, start: 4, end: 7 });
        margin.update(p);
        const cards = page.querySelectorAll('.codoc-card');
        expect(cards.length).toBe(2);
        expect(buttons(cards[0])).toEqual(['Reply', 'Resolve', 'Build it']);    // alice's thread, bob replied
        expect(buttons(cards[1])).toEqual(['Reply', 'Build it']);               // bob's thread
    });

    it("E2 (confirm): Resolve on a thread with replies sets status on every block of the thread, all judged by the thread's starter", () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one two')]);
        const { margin, page, posted } = marginFor(m, { actor: 'human:gh:alice', decides: false });
        const p = proj([...m.proj.blocks, commentBlock('c1', 'c1', 'p1', 'human:gh:alice', 'split this'), commentBlock('c2', 'c1', 'p1', 'human:gh:bob', 'agreed')]);
        (p.blocks[1].fields.text as TextField).spans.push({ kind: 'comment', attrs: { block: 'c1' }, start: 0, end: 3 });
        margin.update(p);
        (Array.from(page.querySelectorAll('button')).find(b => b.textContent === 'Resolve') as HTMLButtonElement).click();
        expect(posted.map(o => [o.kind, o.target.bid, o.payload.value])).toEqual([['block.set', 'c1', 'resolved'], ['block.set', 'c2', 'resolved']]);
    });
});

// ── G. cost at 1,500 features ────────────────────────────────────────────────
describe('probe8 editor — G. cost', () => {
    function bigTree(n = 1500): ProjectedBlock[] {
        const out: ProjectedBlock[] = [];
        for (let i = 0; i < n; i++) {
            out.push(block(`h${i}`, 'heading', `Feature number ${i} of the tree`, 1));
            out.push(block(`p${i}`, 'paragraph', `Paragraph ${i}: it retries once before giving up, then reports the failure to the caller.`, 1));
        }
        return out;
    }
    const reset = () => { for (const k of Object.keys(cost) as Array<keyof typeof cost>) cost[k] = 0; };
    const med = (xs: number[]) => xs.slice().sort((a, b) => a - b)[Math.floor(xs.length / 2)];
    const fmt = (label: string, total: number) => `${label}: ${total.toFixed(1)} ms  [paintsFor ${cost.paints.toFixed(1)}/${cost.paintsCalls}, buildDecorations ${cost.decos.toFixed(1)}/${cost.decosCalls}, decorationsFor ${cost.decosFor.toFixed(1)}/${cost.decosForCalls}]`;

    it('G1: mount, keystroke, adopt, ack, and what a per-block adoption would cost', () => {
        const lines: string[] = [];
        const blocks = bigTree();
        reset();
        let t = performance.now();
        const m = mount(blocks);
        lines.push(fmt('mount (3,000 blocks)', performance.now() - t));
        const ks: number[] = [];
        reset();
        for (let i = 0; i < 8; i++) {
            select(m.view, posIn(m.view, 'p700', 5));
            t = performance.now();
            typeText(m.view, 'x');
            ks.push(performance.now() - t);
        }
        lines.push(fmt(`keystroke median of 8 (${ks.map(x => x.toFixed(1)).join(',')})`, med(ks)) + ' (accumulators: sum over 8)');
        const tr = m.view.state.tr.insertText('z', posIn(m.view, 'p700', 5));
        t = performance.now(); const next = m.view.state.apply(tr); const applyMs = performance.now() - t;
        t = performance.now(); m.view.updateState(next); const updMs = performance.now() - t;
        lines.push(`  keystroke split: EditorState.apply (all plugins) ${applyMs.toFixed(1)} ms; view.updateState (jsdom DOM) ${updMs.toFixed(1)} ms`);
        m.handle.flush();
        const stale = proj(blocks);
        reset(); t = performance.now();
        m.handle.adopt(stale);
        lines.push(fmt('adopt, nothing changed', performance.now() - t));
        const changedBlocks = blocks.map(b => b.id === 'p900' ? block('p900', 'paragraph', 'Paragraph 900 rewritten by someone else entirely.', 1, 77) : b);
        const changed = proj(changedBlocks, { head: 10 });
        reset(); t = performance.now();
        m.handle.adopt(changed);
        const adoptOne = performance.now() - t;
        lines.push(fmt('adopt, one block changed', adoptOne));
        // What a PER-BLOCK adoption would cost on this same view: replace the one block's node in a
        // transaction that keeps the structure (the plugin maps its kept set instead of rebuilding).
        const times: number[] = [];
        for (let i = 0; i < 5; i++) {
            const n = m.view.state.doc.child(1801);           // p900
            let from = 0; m.view.state.doc.forEach((b, p, idx) => { if (idx === 1801) from = p; });
            const node = n.type.create(n.attrs, m.view.state.schema.text(`Paragraph 900 rewritten again ${i}.`));
            reset(); t = performance.now();
            m.view.dispatch(m.view.state.tr.replaceWith(from, from + n.nodeSize, node).setMeta(RECORDED, []));
            times.push(performance.now() - t);
        }
        lines.push(fmt('  one block replaced in place (a per-block adoption\'s transaction), median of 5', med(times)));
        const inserted = proj([...changedBlocks.slice(0, 1000), block('pnew', 'paragraph', 'A new paragraph from the loop.', 1, 5), ...changedBlocks.slice(1000)], { head: 11 });
        reset(); t = performance.now();
        m.handle.adopt(inserted);
        lines.push(fmt('adopt, one block inserted', performance.now() - t));
        select(m.view, posIn(m.view, 'p700', 5)); typeText(m.view, 'q');
        const again = proj(inserted.blocks.map(b => b.id === 'p901' ? block('p901', 'paragraph', 'Paragraph 901 rewritten.', 1, 78) : b), { head: 12 });
        reset(); t = performance.now();
        m.handle.adopt(again);
        lines.push(fmt('adopt, one block changed, one held', performance.now() - t));
        m.handle.flush();
        const cid = m.submitted[m.submitted.length - 1]?.cid ?? 'none';
        reset(); t = performance.now();
        m.handle.ack([{ cid, op: 'o:1', conflict: null }]);
        lines.push(fmt('ack (one cid; one block repainted in the kept set)', performance.now() - t));
        select(m.view, posIn(m.view, 'p700', 5)); typeText(m.view, 'w'); m.handle.flush();
        const cid2 = m.submitted[m.submitted.length - 1]?.cid ?? 'none';
        reset(); t = performance.now();
        m.handle.ack([{ cid: cid2, op: null, conflict: 'unknown_anchor' }]);
        lines.push(fmt('ack (one refusal → re-adopt one block)', performance.now() - t));
        // plans-only projection: the adoptDirty fast path
        const plansOnly = proj(again.blocks, { head: 13, plans: [{ id: 'plX', actor: 'agent:x', kind: 'edit', target: 'p5', note: 'n', status: 'open' }] });
        reset(); t = performance.now();
        m.handle.adopt(plansOnly);
        lines.push(fmt('adopt, only `plans` changed (no head, no phase)', performance.now() - t));
        reset(); t = performance.now();
        m.handle.setHistory(true);
        lines.push(fmt('History on (author paints for every block)', performance.now() - t));
        m.handle.setHistory(false);
        const sel: number[] = [];
        for (let i = 0; i < 6; i++) { t = performance.now(); select(m.view, posIn(m.view, `p${100 + i}`, 3)); sel.push(performance.now() - t); }
        lines.push(`selection move (no doc change), median of 6: ${med(sel).toFixed(2)} ms`);
        // The structural guard: `structure(doc)` (editor.ts:648) joins 3,000 `type:bid` strings. It runs twice in
        // `filterTransaction` and twice in the paints plugin's `apply`, on every doc-changing transaction.
        const structure = (d: PMNode) => { const out: string[] = []; d.forEach(b => out.push(`${b.type.name}:${b.attrs.bid ?? '?'}`)); return out.join('|'); };
        const st: number[] = [];
        for (let i = 0; i < 10; i++) { t = performance.now(); structure(m.view.state.doc); st.push(performance.now() - t); }
        lines.push(`  structure(doc) once: ${med(st).toFixed(2)} ms (×4 per keystroke = ${(4 * med(st)).toFixed(1)} ms of EditorState.apply)`);
        fs.writeFileSync(`${OUT}/probe8_cost.txt`, lines.join('\n') + '\n');
        console.log('\n' + lines.join('\n'));
        expect(lines.length).toBeGreaterThan(0);
    });
});
