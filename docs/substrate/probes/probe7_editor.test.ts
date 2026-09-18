/**
 * Review-7 probes for the editor surface. Real `mountEditor` views under jsdom,
 * driven the way the browser drives them. Every test asserts the CORRECT
 * behaviour, so a failing test is a confirmed fault.
 *
 *   cd <worktree>/vscode-codoc && npx vitest run --config <scratchpad>/vitest.probe7.config.mjs
 *
 * §R (the round trips) is driven by `probe7_hub.py`, which sets CODOC_P7_DIR
 * and CODOC_P7_PHASE and drains what this file writes through the real applier;
 * on its own, vitest skips §R.
 */
import { Fragment, Slice, type Node as PMNode } from '@tiptap/pm/model';
import { EditorState, Plugin, TextSelection } from '@tiptap/pm/state';
import { DecorationSet, EditorView } from '@tiptap/pm/view';
import * as fs from 'node:fs';
import { describe, expect, it, vi } from 'vitest';

const SRC = '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src';
const OUT = '/private/tmp/claude-501/-Users-r4yen-Desktop-Research-CoDoc-repos-CodeNav/d28f4a40-c80f-4f4e-b0c9-17b9d836ed30/scratchpad';

// ── instrumentation (transparent for correctness; timed for §G) ──────────────
let captured: Map<string, Shadow> | null = null;
const cost = { paints: 0, paintsCalls: 0, decos: 0, decosCalls: 0, decosFor: 0, decosForCalls: 0, drag: 0, dragCalls: 0, handles: 0, handlesCalls: 0 };

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
vi.mock('/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/editor/drag', async (importOriginal) => {
    const actual = await importOriginal<Record<string, unknown>>();
    return {
        ...actual,
        dragPlugin: (move: unknown) => {
            const p = (actual.dragPlugin as (m: unknown) => Plugin)(move);
            const spec = p.spec as { state: { init: (...a: unknown[]) => unknown; apply: (...a: unknown[]) => unknown } };
            const init = spec.state.init, apply = spec.state.apply;
            spec.state.init = (...a: unknown[]) => { const t = performance.now(); const r = init(...a); cost.handles += performance.now() - t; cost.handlesCalls++; return r; };
            spec.state.apply = (...a: unknown[]) => { const t = performance.now(); const r = apply(...a); cost.drag += performance.now() - t; cost.dragCalls++; return r; };
            return new Plugin(p.spec);
        },
    };
});

import type { OpBody, Projection, ProjectedBlock, Submission, TextField } from '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/calc/projection';
import type { Shadow } from '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/calc/shadow';
import type { HistoryWindow } from '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/calc/window';
import { mountEditor, type EditorHandle } from '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/editor/editor';
import { Margin, newThreadOps } from '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/editor/comments';
import { Timeline } from '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/editor/timeline';

if (!Range.prototype.getClientRects) {
    Range.prototype.getClientRects = () => ({ length: 0, item: () => null, [Symbol.iterator]: function* () {} } as unknown as DOMRectList);
    Range.prototype.getBoundingClientRect = () => ({ top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0 } as DOMRect);
}
HTMLElement.prototype.scrollIntoView = () => undefined;

// ── fixtures / harness (as in src/test/editor-dom.test.ts) ───────────────────
let opn = 0;
function block(id: string, kind: 'heading' | 'paragraph', text: string, level = 1, head = 1): ProjectedBlock {
    const n = Array.from(text).length;
    return { id, kind, level, rank: id, head,
             fields: { text: { text, runs: n ? [{ op: `s:${id}${opn++}`, k0: 0, start: 0, end: n, actor: 'human:a', at: '1', plan: null }] : [],
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
function paste(m: Mounted, paras: string[], headingFirst = false): boolean {
    const schema = m.view.state.schema;
    const nodes = paras.map((t, i) => (headingFirst && i === 0 ? schema.nodes.heading : schema.nodes.paragraph).create(null, t ? schema.text(t) : undefined));
    const slice = new Slice(Fragment.from(nodes), 1, 1);
    const handled = m.view.someProp('handlePaste', f => f(m.view, new window.Event('paste') as ClipboardEvent, slice)) ?? false;
    if (!handled) m.view.dispatch(m.view.state.tr.replaceSelection(slice));
    return !!handled;
}
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
const rng = (seed: number) => { let s = seed >>> 0; return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 2 ** 32; }; };
const pageOf = (doc: PMNode): Array<[string, string, number, string]> => {
    const out: Array<[string, string, number, string]> = [];
    doc.forEach(n => out.push([n.attrs.bid, n.type.name, n.type.name === 'heading' ? n.attrs.level : 0, n.textBetween(0, n.content.size, '\n')]));
    return out;
};
/** Drive the Find widget's DOM: replace every match of `q` with `r`. */
function replaceAll(m: Mounted, q: string, r: string): void {
    m.handle.find(true);
    const inputs = m.container.querySelectorAll('.codoc-find .codoc-find-input') as NodeListOf<HTMLInputElement>;
    inputs[0].value = q;
    inputs[0].dispatchEvent(new window.Event('input', { bubbles: true }));
    inputs[1].value = r;
    const all = Array.from(m.container.querySelectorAll('.codoc-find .codoc-find-btn')).find(b => b.textContent === 'All') as HTMLButtonElement;
    all.click();
    const close = Array.from(m.container.querySelectorAll('.codoc-find .codoc-find-btn')).find(b => b.textContent === '×') as HTMLButtonElement;
    close.click();
}

// ── E. the seams round 6 left ────────────────────────────────────────────────
describe('E. adoption × acks × plans × comments × the timeline × the paints', () => {
    it('E1: a projection skipped while a block was held is taken up when the ack lifts the hold (the hub sends projection, then acks)', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one')]);
        select(m.view, posIn(m.view, 'p1', 3));
        typeText(m.view, 'x');
        m.handle.flush();
        const cid = m.submitted[0].cid;
        const mine = (m.proj.blocks[1].fields.text as TextField).runs[0];
        // Bob put a 'B' at the start; my 'x' landed as op h:5. The daemon published this, then acked me.
        const p1: ProjectedBlock = { id: 'p1', kind: 'paragraph', level: 0, rank: 'p1', head: 9, fields: { text: { text: 'Bonex', spans: [], suggest_del: [], runs: [
            { op: 'bob:1', k0: 0, start: 0, end: 1, actor: 'human:gh:bob', at: '2', plan: null },
            { op: mine.op, k0: 0, start: 1, end: 4, actor: 'human:a', at: '1', plan: null },
            { op: 'h:5', k0: 0, start: 4, end: 5, actor: 'human:local', at: '3', plan: null }] } } };
        m.handle.adopt(proj([block('h', 'heading', 'T'), p1], { frontier: { h: 5, bob: 1 } }));
        expect(texts(m.view.state.doc)[1]).toBe('onex');          // held: left alone, as §6 says
        m.handle.ack([{ cid, op: 'h:5', conflict: null }]);
        // The hold is gone and the page's word for p1 is now the shadow's; the projection that
        // knows Bob's letter was already delivered and nothing re-reads it.
        expect(texts(m.view.state.doc)[1]).toBe('Bonex');
        expect(shadowFaults(m)).toEqual([]);
    });

    it('E1b (control): the same two messages in the IDE order — ack, then projection — are fine', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one')]);
        select(m.view, posIn(m.view, 'p1', 3));
        typeText(m.view, 'x');
        m.handle.flush();
        const cid = m.submitted[0].cid;
        const mine = (m.proj.blocks[1].fields.text as TextField).runs[0];
        const p1: ProjectedBlock = { id: 'p1', kind: 'paragraph', level: 0, rank: 'p1', head: 9, fields: { text: { text: 'Bonex', spans: [], suggest_del: [], runs: [
            { op: 'bob:1', k0: 0, start: 0, end: 1, actor: 'human:gh:bob', at: '2', plan: null },
            { op: mine.op, k0: 0, start: 1, end: 4, actor: 'human:a', at: '1', plan: null },
            { op: 'h:5', k0: 0, start: 4, end: 5, actor: 'human:local', at: '3', plan: null }] } } };
        m.handle.ack([{ cid, op: 'h:5', conflict: null }]);
        m.handle.adopt(proj([block('h', 'heading', 'T'), p1], { frontier: { h: 5, bob: 1 } }));
        expect(texts(m.view.state.doc)[1]).toBe('Bonex');
        expect(shadowFaults(m)).toEqual([]);
    });

    it('E1c: an agent plan that arrived in the paragraph the person was typing in shows once their ack lands', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one')]);
        select(m.view, posIn(m.view, 'p1', 3));
        typeText(m.view, 'x');
        m.handle.flush();
        const cid = m.submitted[0].cid;
        const mine = (m.proj.blocks[1].fields.text as TextField).runs[0];
        const p1: ProjectedBlock = { id: 'p1', kind: 'paragraph', level: 0, rank: 'p1', head: 9, fields: { text: { text: 'onex more', spans: [], suggest_del: [], runs: [
            { op: mine.op, k0: 0, start: 0, end: 3, actor: 'human:a', at: '1', plan: null },
            { op: 'h:5', k0: 0, start: 3, end: 4, actor: 'human:local', at: '3', plan: null },
            { op: 'ag:1', k0: 0, start: 4, end: 9, actor: 'agent:x', at: '4', plan: 'pl1' }] } } };
        m.handle.adopt(proj([block('h', 'heading', 'T'), p1], { frontier: { h: 5, ag: 1 }, plans: [{ id: 'pl1', actor: 'agent:x', kind: 'edit', target: 'p1', note: '', status: 'open' }] }));
        m.handle.ack([{ cid, op: 'h:5', conflict: null }]);
        expect(texts(m.view.state.doc)[1]).toBe('onex more');
        expect(m.view.dom.querySelectorAll('[data-plan]').length).toBe(1);
    });

    it("E1d: a contributor's Backspace comes back as the plan's strike-through once acked, not as an absence", () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one two')]);
        select(m.view, posIn(m.view, 'p1', 4), posIn(m.view, 'p1', 7));
        key(m.view, 'Backspace', 8);
        m.handle.flush();
        expect(texts(m.view.state.doc)[1]).toBe('one ');
        const cid = m.submitted[0].cid;
        // The daemon turned the suggester's delete into suggest_del (workspace._as_proposal) and published; then acked.
        const p1: ProjectedBlock = { ...m.proj.blocks[1], head: 9, fields: { text: { ...(m.proj.blocks[1].fields.text as TextField), suggest_del: [{ plan: 'pl1', start: 4, end: 7 }] } } };
        m.handle.adopt(proj([block('h', 'heading', 'T'), p1], { frontier: { h: 6 }, plans: [{ id: 'pl1', actor: 'human:gh:alice', kind: 'edit', target: 'p1', note: '', status: 'open' }] }));
        m.handle.ack([{ cid, op: 'h:6', conflict: null }]);
        expect(texts(m.view.state.doc)[1]).toBe('one two');
        expect(m.view.dom.querySelectorAll('[data-suggest-del]').length).toBe(1);
    });

    it('E2: a multi-paragraph paste whose later paragraph is decomposed leaves the page NFC, like the shadow and the op', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'ab')]);
        select(m.view, posIn(m.view, 'p', 2));
        expect(paste(m, ['x', 'café ok'])).toBe(true);
        m.handle.flush();
        expect(shadowFaults(m)).toEqual([]);
        const sent = m.submitted.filter(s => s.kind === 'text.insert').map(s => (s.payload as { text: string }).text);
        expect(sent).toContain('café ok');                       // NFC on the wire, as projection.ts promises
        // …and the next keystroke in that block anchors on the right character.
        const nb = ids(m.view.state.doc)[2].split(':')[1];
        select(m.view, posIn(m.view, nb, 4));                    // after "café"
        typeText(m.view, '!');
        m.handle.flush();
        expect(shadowFaults(m)).toEqual([]);
        expect(texts(m.view.state.doc)[2]).toBe('café! ok');
    });

    it('E3: a comment the person is still writing survives a projection arriving (every settle publishes one)', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one two'), block('p2', 'paragraph', 'three')]);
        const page = document.createElement('div');
        document.body.append(page);
        const posted: OpBody[] = [];
        const margin = new Margin(page, () => m.view, { submit: ops => posted.push(...ops), build: () => undefined, newBid: () => 'c-new', featureOf: () => 'h' });
        // An open thread by someone else, so there is a reply box too.
        const p = proj([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one two'), block('p2', 'paragraph', 'three'),
            { id: 'c1', kind: 'comment', level: 0, rank: 'z', head: 1, fields: { thread: 'c1', anchor: 'p2', anchor_text: 'three', scope: 'code', status: 'open',
              body: { text: 'tighten this', runs: [{ op: 'o', k0: 0, start: 0, end: 12, actor: 'human:gh:bob', at: '1', plan: null }], spans: [], suggest_del: [] } } }]);
        (p.blocks[2].fields.text as TextField).spans.push({ kind: 'comment', attrs: { block: 'c1' }, start: 0, end: 5 });
        margin.update(p);
        margin.compose('p1', 'one', ['s:x', 0], ['s:x', 2]);
        const draft = page.querySelector('.codoc-card.is-draft textarea') as HTMLTextAreaElement;
        draft.value = 'please split this sentence';
        const reply = page.querySelector('.codoc-card:not(.is-draft) textarea') as HTMLTextAreaElement;
        reply.value = 'agreed, and rename it';
        margin.update(p);                                        // someone else typed elsewhere
        expect((page.querySelector('.codoc-card.is-draft textarea') as HTMLTextAreaElement | null)?.value).toBe('please split this sentence');
        expect((page.querySelector('.codoc-card:not(.is-draft) textarea') as HTMLTextAreaElement | null)?.value).toBe('agreed, and rename it');
        expect(posted).toEqual([]);
    });

    it('E4: sitting at "now" with History on, the page stays at now when the window grows (someone else edited)', () => {
        const parent = document.createElement('div');
        document.body.append(parent);
        const dom = document.createElement('div');
        parent.append(dom);
        const tl = new Timeline(parent, () => dom);
        const p = proj([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one')]);
        const entry = (pos: number): HistoryWindow['ops'][number] => ({ pos, id: `s:${pos}`, at: '2026-09-18T00:00:0' + pos, actor: 'human:gh:bob', kind: 'text.insert', batch: `b${pos}`, plan: null, caused_by: null, blocks: { p1: `one${pos}` }, inverse: [] });
        tl.setWindow({ start: 0, head: 2, before: { p1: 'one' }, ops: [entry(0), entry(1)] }, p);
        tl.toggle(true);
        expect(tl.past.hidden).toBe(true);                       // at "now"
        tl.setWindow({ start: 0, head: 3, before: { p1: 'one' }, ops: [entry(0), entry(1), entry(2)] }, p);
        expect(tl.past.hidden).toBe(true);                       // still now: nobody dragged the scrubber
        expect(dom.hidden).toBe(false);
    });

    it('E5: a drag keeps every margin marker on the page (the paints are patched before the gesture is recorded)', () => {
        const m = mount([block('h1', 'heading', 'A'), block('p1', 'paragraph', 'a'), block('h2', 'heading', 'B'), block('p2', 'paragraph', 'b')],
                        { phases: { h1: 'edited', h2: 'drifted' } });
        expect(m.view.dom.querySelectorAll('.codoc-marker').length).toBe(2);
        m.handle.moveBlock('h2', 'START');
        expect(ids(m.view.state.doc)).toEqual(['heading:h2', 'paragraph:p2', 'heading:h1', 'paragraph:p1']);
        // Right after the gesture — before any ack or further keystroke — both headings still carry their marker.
        expect(m.view.dom.querySelectorAll('.codoc-marker').length).toBe(2);
    });

    it('E5b: a split leaves the new paragraph painted like its neighbours, without waiting for the ack', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one two')], { phases: { h: 'edited' } });
        select(m.view, posIn(m.view, 'p1', 3));
        key(m.view, 'Enter', 13);
        expect(texts(m.view.state.doc)).toEqual(['T', 'one', ' two']);
        expect(m.view.dom.querySelectorAll('.codoc-marker').length).toBe(1);
        // Every heading node the page has carries its drag handle too.
        expect(m.view.dom.querySelectorAll('.codoc-handle').length).toBe(3);
    });
});

// ── R. round trips, driven by probe7_hub.py ──────────────────────────────────
const P7 = process.env.CODOC_P7_DIR ?? '';
const PHASE = process.env.CODOC_P7_PHASE ?? '';

/** The gesture generator shared by every round trip: review 6's fourteen plus
 *  comments, replace-all through the Find widget, a heading paste, Tab on a
 *  paragraph and Enter at a heading boundary. */
function gesture(m: Mounted, r: () => number, step: number, seed: number): string {
    const doc = m.view.state.doc;
    const blocks: Array<{ bid: string; kind: string; start: number; len: number; index: number }> = [];
    doc.forEach((n, pos, i) => blocks.push({ bid: n.attrs.bid, kind: n.type.name, start: pos + 1, len: n.content.size, index: i }));
    const a = blocks[Math.floor(r() * blocks.length)], b = blocks[Math.floor(r() * blocks.length)];
    const pa = a.start + Math.floor(r() * (a.len + 1)), pb = b.start + Math.floor(r() * (b.len + 1));
    const from = Math.min(pa, pb), to = Math.max(pa, pb);
    const heads = blocks.filter(x => x.kind === 'heading');
    const g = Math.floor(r() * 15);
    if (g === 0) { select(m.view, from); typeText(m.view, `x${step}`); return `type@${from}`; }
    if (g === 1) { select(m.view, from, to); typeText(m.view, 'y'); return `type over ${from}-${to}`; }
    if (g === 2) { select(m.view, from, to); key(m.view, 'Backspace', 8); return `bs ${from}-${to}`; }
    if (g === 3) { select(m.view, from, to); key(m.view, 'Delete', 46); return `del ${from}-${to}`; }
    if (g === 4) { select(m.view, r() < 0.5 ? from : to, to); key(m.view, 'Enter', 13); return `enter ${from}-${to}`; }
    if (g === 5) { select(m.view, from, r() < 0.5 ? from : to); paste(m, [`P${step}`, `Q${step}`]); return `paste ${from}-${to}`; }
    if (g === 6 && heads.length > 1) { const h = heads[Math.floor(r() * heads.length)]; m.handle.deleteFeature(h.bid); return `deleteFeature ${h.bid}`; }
    if (g === 7 && heads.length > 1) { const h = heads[Math.floor(r() * heads.length)]; const after = r() < 0.3 ? 'START' : blocks[Math.floor(r() * blocks.length)].bid; m.handle.moveBlock(h.bid, after as 'START'); return `move ${h.bid} after ${after}`; }
    if (g === 8 && heads.length) { const h = heads[Math.floor(r() * heads.length)]; select(m.view, h.start); key(m.view, 'Tab', 9, { shiftKey: r() < 0.5 }); return `relevel ${h.bid}`; }
    if (g === 9) { const paras = blocks.filter(x => x.kind === 'paragraph'); if (!paras.length) return 'noop'; const pp = paras[Math.floor(r() * paras.length)]; select(m.view, pp.start); typeText(m.view, '##'); const at = posIn(m.view, pp.bid, 2); m.view.someProp('handleTextInput', f => f(m.view, at, at, ' ', () => m.view.state.tr)); return `## ${pp.bid}`; }
    if (g === 10) {
        // A comment on a span inside one block: flushed first, as the toolbar's verdict path does.
        if (a.len < 2) return 'noop';
        const s = a.start + Math.floor(r() * (a.len - 1)), e = s + 1 + Math.floor(r() * (a.len - (s - a.start) - 1));
        select(m.view, s, e);
        const anchor = m.handle.selectionAnchor();
        if (!anchor) return 'noop';
        m.handle.flush();
        const bid = `c-${seed}-${step}`;
        const ops = newThreadOps(bid, anchor.bid, anchor.text, `note ${step}`, r() < 0.5 ? 'code' : 'both', anchor.start, anchor.end);
        m.submitted.push(...ops.map((op, i) => ({ cid: `cm-${seed}-${step}-${i}`, batch: `t-c${seed}-${step}`, ...op })));
        return `comment on ${anchor.bid}[${anchor.text}]`;
    }
    if (g === 11) { const q = ['e', 'a', 'o', 't'][Math.floor(r() * 4)]; replaceAll(m, q, q.toUpperCase()); return `replaceAll ${q}`; }
    if (g === 12) { select(m.view, from); paste(m, [`H${step}`, `body ${step}`], true); return `pasteHeading@${from}`; }
    if (g === 13) { const paras = blocks.filter(x => x.kind === 'paragraph'); if (!paras.length) return 'noop'; const pp = paras[Math.floor(r() * paras.length)]; select(m.view, pp.start); key(m.view, 'Tab', 9); return `tab on ${pp.bid}`; }
    select(m.view, from); paste(m, [`S${step}`]); return `paste1@${from}`;
}

/** Deterministic `Math.random` per seed so bids and batch ids replay. */
function seedRandom(seed: number): () => void {
    const orig = Math.random;
    const r = rng(seed * 7919 + 1);
    Math.random = r;
    return () => { Math.random = orig; };
}

describe.runIf(P7 && PHASE === 'R1')('R1. the single-actor round trip, more gestures', () => {
    const SEEDS = 24, STEPS = 18;
    it(`R1: ${SEEDS} seeds × ${STEPS} gestures incl. comments, replace-all and heading pastes; ops written for the applier`, () => {
        const p0 = JSON.parse(fs.readFileSync(`${P7}/projection.json`, 'utf8')) as Projection;
        const failures: string[] = [];
        for (let seed = 1; seed <= SEEDS; seed++) {
            const restore = seedRandom(seed);
            const r = rng(seed);
            const m = mountProj(JSON.parse(JSON.stringify(p0)));
            const log: string[] = [];
            let ok = true;
            for (let step = 0; step < STEPS && ok; step++) {
                let name = '';
                try { name = gesture(m, r, step, seed); } catch (e) { name += ` THREW ${(e as Error).message}`; ok = false; }
                m.handle.flush();
                log.push(name);
                const faults = shadowFaults(m);
                if (faults.length) { failures.push(`seed ${seed} step ${step}: ${log.join(' → ')}\n    ${faults.join('\n    ')}`); ok = false; }
                m.conflicts.length = 0;
            }
            restore();
            fs.writeFileSync(`${P7}/r1-seed${String(seed).padStart(2, '0')}.json`, JSON.stringify({ seed, log, subs: m.submitted, expect: pageOf(m.view.state.doc) }));
        }
        expect(failures).toEqual([]);
    });
});

describe.runIf(P7 && (PHASE === 'R2A' || PHASE === 'R2B'))('R2. two actors and a plan arriving mid-edit', () => {
    const SEEDS = 16, STEPS = 12;
    it(`R2${PHASE.slice(2)}: ${SEEDS} seeds × ${STEPS} gestures; phase A records, phase B replays, adopts Bob's projection mid-edit, acks, adopts the final one`, () => {
        const p1 = JSON.parse(fs.readFileSync(`${P7}/p1.json`, 'utf8')) as Projection;
        const p2 = PHASE === 'R2B' ? JSON.parse(fs.readFileSync(`${P7}/p2.json`, 'utf8')) as Projection : null;
        const failures: string[] = [];
        for (let seed = 1; seed <= SEEDS; seed++) {
            const restore = seedRandom(seed);
            const r = rng(seed);
            const m = mountProj(JSON.parse(JSON.stringify(p1)));
            const log: string[] = [];
            let ok = true;
            for (let step = 0; step < STEPS && ok; step++) {
                let name = '';
                try { name = gesture(m, r, step, seed); } catch (e) { name += ` THREW ${(e as Error).message}`; ok = false; }
                m.handle.flush();
                log.push(name);
                const faults = shadowFaults(m);
                if (faults.length) { failures.push(`seed ${seed} step ${step}: ${log.join(' → ')}\n    ${faults.join('\n    ')}`); ok = false; }
                m.conflicts.length = 0;
            }
            restore();
            if (PHASE === 'R2A') {
                fs.writeFileSync(`${P7}/r2a-seed${String(seed).padStart(2, '0')}.json`, JSON.stringify({ seed, log, subs: m.submitted, page: pageOf(m.view.state.doc) }));
                continue;
            }
            // Phase B: the same page, then Bob's projection arrives (it predates every one of these ops),
            // then the acks (positionally: same seed, same gestures, same op order), then the final projection.
            const rec = JSON.parse(fs.readFileSync(`${P7}/r2b-seed${String(seed).padStart(2, '0')}.json`, 'utf8')) as { acks: Array<{ cid: string; op: string; conflict: string | null }>; p3: Projection; subs: Submission[] };
            const kindsA = rec.subs.map(s => s.kind).join(','), kindsB = m.submitted.map(s => s.kind).join(',');
            if (kindsA !== kindsB) { failures.push(`seed ${seed}: replay diverged\n  A: ${kindsA}\n  B: ${kindsB}`); continue; }
            m.handle.adopt(p2!);
            const faultsP2 = shadowFaults(m);
            m.handle.ack(rec.acks.map((a, i) => ({ cid: m.submitted[i].cid, op: a.op, conflict: a.conflict })));
            const afterAck = pageOf(m.view.state.doc), faultsAck = shadowFaults(m);
            m.handle.adopt(rec.p3);
            const afterP3 = pageOf(m.view.state.doc), faultsP3 = shadowFaults(m);
            fs.writeFileSync(`${P7}/r2c-seed${String(seed).padStart(2, '0')}.json`, JSON.stringify({ seed, log, afterAck, afterP3, faultsP2, faultsAck, faultsP3, conflicts: m.conflicts }));
        }
        expect(failures).toEqual([]);
    });
});

// ── G. cost at 1,500 features ────────────────────────────────────────────────
describe('G. cost', () => {
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
    const fmt = (label: string, total: number) => `${label}: ${total.toFixed(1)} ms  [paintsFor ${cost.paints.toFixed(1)}/${cost.paintsCalls}, buildDecorations ${cost.decos.toFixed(1)}/${cost.decosCalls}, decorationsFor ${cost.decosFor.toFixed(1)}/${cost.decosForCalls}, drag.apply ${cost.drag.toFixed(1)}/${cost.dragCalls}, handles() ${cost.handles.toFixed(1)}/${cost.handlesCalls}]`;

    it('G1: mount, keystroke, adopt, ack, projection-while-held — with where the time goes', () => {
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
        // Where a keystroke goes: state.apply vs the view update, measured apart.
        const tr = m.view.state.tr.insertText('z', posIn(m.view, 'p700', 5));
        t = performance.now(); const next = m.view.state.apply(tr); const applyMs = performance.now() - t;
        t = performance.now(); m.view.updateState(next); const updMs = performance.now() - t;
        lines.push(`  keystroke split: EditorState.apply (all plugins) ${applyMs.toFixed(1)} ms; view.updateState (jsdom DOM) ${updMs.toFixed(1)} ms`);
        m.handle.flush();
        const stale = proj(blocks);
        reset(); t = performance.now();
        m.handle.adopt(stale);
        lines.push(fmt('adopt, nothing changed', performance.now() - t));
        const changed = proj(blocks.map(b => b.id === 'p900' ? block('p900', 'paragraph', 'Paragraph 900 rewritten by someone else entirely.', 1, 77) : b));
        reset(); t = performance.now();
        m.handle.adopt(changed);
        lines.push(fmt('adopt, one block changed', performance.now() - t));
        // Inside that adopt: the whole-document replace itself, measured on a bare view of the same doc.
        const bare = new EditorView(document.createElement('div'), { state: EditorState.create({ doc: m.view.state.doc }) });
        t = performance.now(); bare.dispatch(bare.state.tr.replaceWith(0, bare.state.doc.content.size, m.view.state.doc.content)); const replMs = performance.now() - t;
        lines.push(`  whole-doc replaceWith on a bare view (no plugins): ${replMs.toFixed(1)} ms`);
        const inserted = proj([...blocks.slice(0, 1000), block('pnew', 'paragraph', 'A new paragraph from the loop.', 1, 5), ...blocks.slice(1000)]);
        reset(); t = performance.now();
        m.handle.adopt(inserted);
        lines.push(fmt('adopt, one block inserted', performance.now() - t));
        // A projection arriving while the person holds a block (the common case while typing).
        select(m.view, posIn(m.view, 'p700', 5)); typeText(m.view, 'q');
        const again = proj(inserted.blocks.map(b => b.id === 'p901' ? block('p901', 'paragraph', 'Paragraph 901 rewritten.', 1, 78) : b));
        reset(); t = performance.now();
        m.handle.adopt(again);
        lines.push(fmt('adopt, one block changed, one held', performance.now() - t));
        m.handle.flush();
        const cid = m.submitted[m.submitted.length - 1]?.cid ?? 'none';
        reset(); t = performance.now();
        m.handle.ack([{ cid, op: 'o:1', conflict: null }]);
        lines.push(fmt('ack (one cid)', performance.now() - t));
        // ack of a conflict: a forced re-adopt of one block
        select(m.view, posIn(m.view, 'p700', 5)); typeText(m.view, 'w'); m.handle.flush();
        const cid2 = m.submitted[m.submitted.length - 1]?.cid ?? 'none';
        reset(); t = performance.now();
        m.handle.ack([{ cid: cid2, op: null, conflict: 'unknown_anchor' }]);
        lines.push(fmt('ack (one refusal → re-adopt one block)', performance.now() - t));
        reset(); t = performance.now();
        m.handle.setHistory(true);
        lines.push(fmt('History on (author paints for every block)', performance.now() - t));
        m.handle.setHistory(false);
        // selection move only
        const sel: number[] = [];
        for (let i = 0; i < 6; i++) { t = performance.now(); select(m.view, posIn(m.view, `p${100 + i}`, 3)); sel.push(performance.now() - t); }
        lines.push(`selection move (no doc change), median of 6: ${med(sel).toFixed(2)} ms`);
        fs.writeFileSync(`${OUT}/probe7_cost.txt`, lines.join('\n') + '\n');
        console.log('\n' + lines.join('\n'));
        expect(lines.length).toBeGreaterThan(0);
    });
});

describe('G3. the app shell on a projection at 1,500 features', () => {
    it('G3: a second projection costs adopt + renderTree + renderToolbar + margin', async () => {
        (globalThis as unknown as { acquireVsCodeApi: () => unknown }).acquireVsCodeApi = () => ({ postMessage: () => undefined, getState: () => undefined, setState: () => undefined });
        const app = document.createElement('div');
        app.id = 'app';
        document.body.append(app);
        await import('/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/editor/app');
        const blocks: ProjectedBlock[] = [];
        for (let i = 0; i < 1500; i++) { blocks.push(block(`h${i}`, 'heading', `Feature ${i}`)); blocks.push(block(`p${i}`, 'paragraph', `Paragraph ${i}: it retries once before giving up, then reports the failure to the caller.`)); }
        const post = (data: unknown) => window.dispatchEvent(new window.MessageEvent('message', { data, origin: window.location.origin } as MessageEventInit));
        let t = performance.now();
        post({ kind: 'projection', proj: proj(blocks) });
        const first = performance.now() - t;
        const again = proj(blocks.map(b => b.id === 'p900' ? block('p900', 'paragraph', 'Paragraph 900 rewritten.', 1, 77) : b));
        const xs: number[] = [];
        for (let i = 0; i < 3; i++) { t = performance.now(); post({ kind: 'projection', proj: again }); xs.push(performance.now() - t); }
        const phased = proj(blocks.map(b => b.id === 'h900' ? { ...b } : b), { phases: { h900: 'edited' } });
        t = performance.now(); post({ kind: 'projection', proj: phased }); const ph = performance.now() - t;
        const line = `app.ts: first projection (mount + tree pane) ${first.toFixed(0)} ms; a later projection with one block changed, median of 3: ${med3(xs).toFixed(0)} ms; one feature's phase changed: ${ph.toFixed(0)} ms; rows: ${document.querySelectorAll('.codoc-row').length}`;
        fs.appendFileSync(`${OUT}/probe7_cost.txt`, line + '\n');
        console.log('\n' + line);
        expect(xs.length).toBe(3);
    });

    it("E6: a projection that changes one heading's title (or phase) re-renders that row; nothing throws, and the toolbar still updates", () => {
        const blocks: ProjectedBlock[] = [];
        for (let i = 0; i < 1500; i++) { blocks.push(block(`h${i}`, 'heading', `Feature ${i}`)); blocks.push(block(`p${i}`, 'paragraph', `Paragraph ${i}.`)); }
        const post = (data: unknown) => window.dispatchEvent(new window.MessageEvent('message', { data, origin: window.location.origin } as MessageEventInit));
        const errors: string[] = [];
        const onErr = (e: Event) => { errors.push(String((e as ErrorEvent).message)); e.preventDefault(); };
        window.addEventListener('error', onErr);
        post({ kind: 'projection', proj: proj(blocks) });
        expect(document.querySelectorAll('.codoc-row').length).toBe(1500);
        // The person typed in a heading (every settle publishes), or a feature's phase moved.
        const renamed = proj(blocks.map(b => b.id === 'h5' ? block('h5', 'heading', 'Feature 5 renamed', 1, 66) : b), { phases: { h7: 'edited' } });
        post({ kind: 'projection', proj: renamed });
        window.removeEventListener('error', onErr);
        expect(errors).toEqual([]);
        expect(document.querySelectorAll('.codoc-row').length).toBe(1500);
        expect(document.querySelector('.codoc-row[data-fid="h5"] .codoc-row-title')?.textContent).toBe('Feature 5 renamed');
        expect(document.querySelector('.codoc-row[data-fid="h7"]')?.className).toContain('codoc-ch-human');
        // renderToolbar ran after renderTree: the Hand off button reflects the edited feature.
        expect(Array.from(document.querySelectorAll('.codoc-toolbar .codoc-btn')).some(b => b.textContent?.startsWith('Hand off'))).toBe(true);
    });
});

describe('G2. where a keystroke goes: ProseMirror\'s per-child decoration lookup', () => {
    it('G2: a bare view with one kept DecorationSet of N widgets — N = 750, 1500, 3000', async () => {
        const adopt = await vi.importActual<typeof import('/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/calc/adopt')>('/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/calc/adopt');
        const { codocSchema } = await import('/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/editor/schema');
        const { Decoration } = await import('@tiptap/pm/view');
        const lines: string[] = [];
        for (const n of [750, 1500, 3000]) {
            const blocks: ProjectedBlock[] = [];
            for (let i = 0; i < n / 2; i++) { blocks.push(block(`h${i}`, 'heading', `Feature ${i}`)); blocks.push(block(`p${i}`, 'paragraph', `Paragraph ${i}: it retries once before giving up.`)); }
            const { doc } = adopt.docFrom(codocSchema(), proj(blocks));
            const time = (plugins: Plugin[]) => {
                const view = new EditorView(document.createElement('div'), { state: EditorState.create({ doc, plugins }) });
                const xs: number[] = [];
                for (let i = 0; i < 6; i++) {
                    let pos = 0; view.state.doc.forEach((b, p, idx) => { if (idx === Math.floor(n / 2)) pos = p + 2; });
                    view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, pos)));
                    const t = performance.now(); view.dispatch(view.state.tr.insertText('x')); xs.push(performance.now() - t);
                }
                view.destroy();
                return xs.slice().sort((a, b) => a - b)[3];
            };
            const widgets = (d: PMNode) => { const ds: Decoration[] = []; d.forEach((b, p) => ds.push(Decoration.widget(p + 1, () => document.createElement('span'), { side: -2, key: `w${b.attrs.bid}` }))); return DecorationSet.create(d, ds); };
            const kept = new Plugin<DecorationSet>({ state: { init: (_c, s) => widgets(s.doc), apply: (tr, set) => set.map(tr.mapping, tr.doc) }, props: { decorations(s) { return this.getState(s) as DecorationSet; } } });
            const inline = (d: PMNode) => { const ds: Decoration[] = []; d.forEach((b, p) => ds.push(Decoration.inline(p + 1, p + 6, { class: 'x' }))); return DecorationSet.create(d, ds); };
            const keptInline = new Plugin<DecorationSet>({ state: { init: (_c, s) => inline(s.doc), apply: (tr, set) => set.map(tr.mapping, tr.doc) }, props: { decorations(s) { return this.getState(s) as DecorationSet; } } });
            lines.push(`${n} blocks: bare keystroke ${time([]).toFixed(2)} ms; + one kept set of ${n} widgets ${time([kept]).toFixed(2)} ms; + one kept set of ${n} inline paints ${time([keptInline]).toFixed(2)} ms; + both ${time([kept, keptInline]).toFixed(2)} ms`);
        }
        fs.appendFileSync(`${OUT}/probe7_cost.txt`, lines.join('\n') + '\n');
        console.log('\n' + lines.join('\n'));
        expect(lines.length).toBe(3);
    });
});
const med3 = (xs: number[]) => xs.slice().sort((a, b) => a - b)[Math.floor(xs.length / 2)];
