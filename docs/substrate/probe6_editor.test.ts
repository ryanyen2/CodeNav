/**
 * Review-6 probes for the editor surface. Real `mountEditor` views under jsdom,
 * driven the way the browser drives them. Every test asserts the CORRECT
 * behaviour, so a failing test is a confirmed fault.
 *
 *   cd <worktree>/vscode-codoc && npx vitest run --config <scratchpad>/vitest.probe6.config.mjs
 *
 * Run `probe6_hub.py` first: §T reads the timeline fixtures it writes and §R
 * reads the round-trip base; §R then writes `probe6_fixtures/roundtrip/out/*.json`
 * for `probe6_hub.py::test_R1` to drain through the real applier.
 */
import { Fragment, Slice, type Node as PMNode } from '@tiptap/pm/model';
import { EditorState, Plugin, TextSelection } from '@tiptap/pm/state';
import { Decoration, DecorationSet, EditorView } from '@tiptap/pm/view';
import * as fs from 'node:fs';
import { describe, expect, it, vi } from 'vitest';

const SRC = '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src';
const FIX = '/private/tmp/claude-501/-Users-r4yen-Desktop-Research-CoDoc-repos-CodeNav/d28f4a40-c80f-4f4e-b0c9-17b9d836ed30/scratchpad/probe6_fixtures';
const OUT = '/private/tmp/claude-501/-Users-r4yen-Desktop-Research-CoDoc-repos-CodeNav/d28f4a40-c80f-4f4e-b0c9-17b9d836ed30/scratchpad';

// ── instrumentation (transparent for correctness; timed for §G) ──────────────
let captured: Map<string, Shadow> | null = null;
const cost = { paints: 0, paintsCalls: 0, decos: 0, decosCalls: 0, drag: 0, dragCalls: 0 };
const toggles = { noDrag: false };

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
    };
});
vi.mock('/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/editor/drag', async (importOriginal) => {
    const actual = await importOriginal<Record<string, unknown>>();
    return {
        ...actual,
        dragPlugin: (move: unknown) => {
            if (toggles.noDrag) return new Plugin({ props: { decorations: (s: { doc: PMNode }) => DecorationSet.create(s.doc, []) } });
            const p = (actual.dragPlugin as (m: unknown) => Plugin)(move);
            const inner = p.props.decorations as (s: unknown) => unknown;
            (p.props as { decorations: unknown }).decorations = function (this: unknown, s: unknown) { const t = performance.now(); const r = inner.call(this, s); cost.drag += performance.now() - t; cost.dragCalls++; return r; };
            return p;
        },
    };
});

import type { Paint } from '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/calc/claims';
import type { Projection, ProjectedBlock, Submission } from '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/calc/projection';
import type { Shadow } from '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/calc/shadow';
import { shapeAt, type HistoryWindow } from '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/calc/window';
import { mountEditor, type EditorHandle } from '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/editor/editor';
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

interface Mounted { handle: EditorHandle; view: EditorView; shadows: Map<string, Shadow>; submitted: Submission[]; conflicts: string[]; proj: Projection }

function mountProj(p: Projection): Mounted {
    captured = null;
    const container = document.createElement('div');
    document.body.append(container);
    const submitted: Submission[] = [];
    const conflicts: string[] = [];
    const handle = mountEditor(container, p, { submit: s => submitted.push(...s), onConflict: (_b, c) => conflicts.push(c) });
    if (!captured) throw new Error('docFrom mock did not capture the shadows');
    return { handle, view: handle.view, shadows: captured, submitted, conflicts, proj: p };
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
function paste(m: Mounted, paras: string[]): boolean {
    const schema = m.view.state.schema;
    const nodes = paras.map(t => schema.nodes.paragraph.create(null, t ? schema.text(t) : undefined));
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
const kinds = (subs: Submission[]) => subs.map(s => s.kind);
const rng = (seed: number) => { let s = seed >>> 0; return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 2 ** 32; }; };

// ── A. adoption holds what it inserts and removes ────────────────────────────
describe('A. adoption', () => {
    it('A1: a block the projection inserted can be changed by the next projection', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one')]);
        m.handle.adopt(proj([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one'), block('p2', 'paragraph', 'two', 1, 3)]));
        expect(texts(m.view.state.doc)).toEqual(['T', 'one', 'two']);
        // Nothing local was typed into p2; another actor (or the agent) edits it.
        m.handle.adopt(proj([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one'), block('p2', 'paragraph', 'two more', 1, 7)]));
        expect(texts(m.view.state.doc)).toEqual(['T', 'one', 'two more']);
        expect(shadowFaults(m)).toEqual([]);
        expect(m.submitted).toEqual([]);                    // adoption never submits
    });

    it('A2: a block the projection removed comes back when a projection restores it (an undo elsewhere)', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one'), block('p2', 'paragraph', 'two')]);
        m.handle.adopt(proj([block('h', 'heading', 'T'), block('p2', 'paragraph', 'two')]));
        expect(ids(m.view.state.doc)).toEqual(['heading:h', 'paragraph:p2']);
        m.handle.adopt(proj([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one', 1, 9), block('p2', 'paragraph', 'two')]));
        expect(ids(m.view.state.doc)).toEqual(['heading:h', 'paragraph:p1', 'paragraph:p2']);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('A3: a plan paragraph the agent added loses its gray once the projection says the plan was accepted', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one')]);
        const planned: ProjectedBlock = { id: 'p2', kind: 'paragraph', level: 1, rank: 'p2', head: 3, fields: { text: { text: 'agent words', spans: [], suggest_del: [],
            runs: [{ op: 'a:1', k0: 0, start: 0, end: 11, actor: 'agent:claude', at: '2', plan: 'pl-1' }] } } };
        m.handle.adopt(proj([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one'), planned], { plans: [{ id: 'pl-1', actor: 'agent:claude', kind: 'edit', target: 'p1', note: '', status: 'open' }] }));
        const hasPlanMark = () => { let found = false; m.view.state.doc.descendants(n => { if (n.marks.some(k => k.type.name === 'plan')) found = true; }); return found; };
        expect(hasPlanMark()).toBe(true);
        const accepted: ProjectedBlock = { ...planned, head: 8, fields: { text: { ...(planned.fields.text as object), runs: [{ op: 'a:1', k0: 0, start: 0, end: 11, actor: 'agent:claude', at: '2', plan: null }] } } };
        m.handle.adopt(proj([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one'), accepted], { plans: [{ id: 'pl-1', actor: 'agent:claude', kind: 'edit', target: 'p1', note: '', status: 'accepted' }] }));
        expect(hasPlanMark()).toBe(false);
    });

    it('A4: after a split is acked, a projection that renames nothing still leaves both halves editable by others', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'hello world')]);
        select(m.view, posIn(m.view, 'p', 5));
        key(m.view, 'Enter', 13);
        m.handle.flush();
        const nid = ids(m.view.state.doc)[2].split(':')[1];
        m.handle.ack(m.submitted.map(s => ({ cid: s.cid, op: `o:${s.cid}`, conflict: null })));
        m.handle.adopt(proj([block('h', 'heading', 'T'), block('p', 'paragraph', 'hello', 1, 4), block(nid, 'paragraph', ' world', 1, 4)]));
        m.handle.adopt(proj([block('h', 'heading', 'T'), block('p', 'paragraph', 'hello', 1, 4), block(nid, 'paragraph', ' world!', 1, 6)]));
        expect(texts(m.view.state.doc)).toEqual(['T', 'hello', ' world!']);
    });
});

// ── B. NFC ──────────────────────────────────────────────────────────────────
describe('B. NFC composition', () => {
    it('B1: a decomposed paste in one block does not recompose text the store holds decomposed in another', () => {
        // The store records NFC per INSERT: "e" then U+0301 as two inserts is five
        // scalars in the store, the projection and the shadow. `composeNFC` scans
        // the whole document when any transaction carries a decomposed run.
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'cafe\u0301 ok'), block('p2', 'paragraph', 'x')]);
        expect(shadowFaults(m)).toEqual([]);
        select(m.view, posIn(m.view, 'p2', 1));
        typeText(m.view, 'e\u0301');
        m.handle.flush();
        expect(shadowFaults(m)).toEqual([]);
        // and the next keystroke at the end of p1 anchors after the accent, where the caret is
        select(m.view, posIn(m.view, 'p1', 5));
        typeText(m.view, '!');
        m.handle.flush();
        expect(shadowFaults(m)).toEqual([]);
    });
});

// ── T. the timeline fold against a prefix replay ─────────────────────────────
interface TFixture { history: HistoryWindow; projection: Projection; pages: Array<Array<[string, string, number, string]>>; now: Array<[string, string, number, string]> }

function pastPage(tl: Timeline): Array<[string, string]> {
    return Array.from(tl.past.children).map(el => {
        const kind = el.classList.contains('codoc-heading') ? 'heading' : 'paragraph';
        const text = Array.from(el.childNodes).filter(n => (n as HTMLElement).tagName !== 'DEL').map(n => n.textContent ?? '').join('');
        return [kind, text];
    });
}

describe('T. the History scrubber renders each past page as the store had it', () => {
    const files = fs.existsSync(`${FIX}/timeline`) ? fs.readdirSync(`${FIX}/timeline`).filter(f => f.endsWith('.json')) : [];
    for (const f of files) {
        it(`T-${f}: every position's blocks, kinds, words and order match a prefix replay`, () => {
            const fx = JSON.parse(fs.readFileSync(`${FIX}/timeline/${f}`, 'utf8')) as TFixture;
            const parent = document.createElement('div');
            const edom = document.createElement('div');
            parent.append(edom);
            document.body.append(parent);
            const tl = new Timeline(parent, () => edom);
            tl.setWindow(fx.history, fx.projection);
            tl.toggle(true);
            const range = tl.bar.querySelector('input') as HTMLInputElement;
            const orderFaults: string[] = [];
            const contentFaults: string[] = [];
            // Is the order fault exactly `localeCompare` on base-62 ranks? Re-sort the
            // rendered page by the fold's own ranks with code-point order (the store's) and compare.
            let cpOrderFaults = 0;
            const cpDetail: string[] = [];
            fx.pages.forEach((want, p) => {
                range.value = String(p);
                range.dispatchEvent(new window.Event('input'));
                const got = pastPage(tl);
                const wantKT = want.map(([, kind, , text]) => [kind, text] as [string, string]);
                const sortKey = (x: [string, string]) => `${x[0]}|${x[1]}`;
                const gotSet = got.map(sortKey).sort(), wantSet = wantKT.map(sortKey).sort();
                if (JSON.stringify(gotSet) !== JSON.stringify(wantSet)) contentFaults.push(`p=${p} (${fx.history.ops[p].kind}): got ${JSON.stringify(got)} want ${JSON.stringify(wantKT)}`);
                else if (JSON.stringify(got) !== JSON.stringify(wantKT)) {
                    orderFaults.push(`p=${p}: order got ${JSON.stringify(got.map(x => x[1].slice(0, 12)))} want ${JSON.stringify(wantKT.map(x => x[1].slice(0, 12)))}`);
                    const shape = shapeAt(fx.history, p);
                    const live = new Map(fx.projection.blocks.map(b => [b.id, b]));
                    const rankOf = (id: string) => shape.rank.get(id) ?? live.get(id)?.rank ?? id;
                    const wantIds = want.map(([bid]) => bid);
                    const cp = [...wantIds].sort((a, b) => rankOf(a) < rankOf(b) ? -1 : rankOf(a) > rankOf(b) ? 1 : 0);
                    if (JSON.stringify(cp) !== JSON.stringify(wantIds)) { cpOrderFaults++; if (cpDetail.length < 2) cpDetail.push(`p=${p} cp=${JSON.stringify(cp.map(i => [i, rankOf(i)]))} want=${JSON.stringify(wantIds)}`); }
                }
            });
            expect(contentFaults, 'blocks/kinds/words').toEqual([]);
            expect(cpDetail, 'order faults that a code-point sort of the fold\'s own ranks would NOT fix').toEqual([]);
            expect(orderFaults, 'order').toEqual([]);
        });
    }
});

// ── R. round trip: the real editor's ops → written for the real applier ───────
describe('R. the round trip', () => {
    const base = `${FIX}/roundtrip/projection.json`;
    const SEEDS = 24, STEPS = 14;
    it(`R1: ${SEEDS} seeds × ${STEPS} gestures through the real view keep every shadow honest, and their ops are written out for the applier`, () => {
        if (!fs.existsSync(base)) throw new Error('run probe6_hub.py first');
        const p0 = JSON.parse(fs.readFileSync(base, 'utf8')) as Projection;
        fs.mkdirSync(`${FIX}/roundtrip/out`, { recursive: true });
        const failures: string[] = [];
        for (let seed = 1; seed <= SEEDS; seed++) {
            const r = rng(seed);
            const m = mountProj(JSON.parse(JSON.stringify(p0)));
            const log: string[] = [];
            let ok = true;
            for (let step = 0; step < STEPS && ok; step++) {
                const doc = m.view.state.doc;
                const blocks: Array<{ bid: string; kind: string; start: number; len: number; index: number }> = [];
                doc.forEach((n, pos, i) => blocks.push({ bid: n.attrs.bid, kind: n.type.name, start: pos + 1, len: n.content.size, index: i }));
                const a = blocks[Math.floor(r() * blocks.length)], b = blocks[Math.floor(r() * blocks.length)];
                const pa = a.start + Math.floor(r() * (a.len + 1)), pb = b.start + Math.floor(r() * (b.len + 1));
                const from = Math.min(pa, pb), to = Math.max(pa, pb);
                const heads = blocks.filter(x => x.kind === 'heading');
                const g = Math.floor(r() * 11);
                let name = '';
                try {
                    if (g === 0) { select(m.view, from); typeText(m.view, `x${step}`); name = `type@${from}`; }
                    else if (g === 1) { select(m.view, from, to); typeText(m.view, 'y'); name = `type over ${from}-${to}`; }
                    else if (g === 2) { select(m.view, from, to); key(m.view, 'Backspace', 8); name = `bs ${from}-${to}`; }
                    else if (g === 3) { select(m.view, from, to); key(m.view, 'Delete', 46); name = `del ${from}-${to}`; }
                    else if (g === 4) { select(m.view, r() < 0.5 ? from : to, to); key(m.view, 'Enter', 13); name = `enter ${from}-${to}`; }
                    else if (g === 5) { select(m.view, from, r() < 0.5 ? from : to); paste(m, [`P${step}`, `Q${step}`]); name = `paste ${from}-${to}`; }
                    else if (g === 6 && heads.length > 1) { const h = heads[Math.floor(r() * heads.length)]; m.handle.deleteFeature(h.bid); name = `deleteFeature ${h.bid}`; }
                    else if (g === 7 && heads.length > 1) { const h = heads[Math.floor(r() * heads.length)]; const after = r() < 0.3 ? 'START' : blocks[Math.floor(r() * blocks.length)].bid; m.handle.moveBlock(h.bid, after as 'START'); name = `move ${h.bid} after ${after}`; }
                    else if (g === 8 && heads.length) { const h = heads[Math.floor(r() * heads.length)]; select(m.view, h.start); key(m.view, 'Tab', 9, { shiftKey: r() < 0.5 }); name = `relevel ${h.bid}`; }
                    else if (g === 9) { const paras = blocks.filter(x => x.kind === 'paragraph'); if (!paras.length) continue; const pp = paras[Math.floor(r() * paras.length)]; select(m.view, pp.start); typeText(m.view, '##'); const at = posIn(m.view, pp.bid, 2); m.view.someProp('handleTextInput', f => f(m.view, at, at, ' ', () => m.view.state.tr)); name = `## ${pp.bid}`; }
                    else { select(m.view, from); paste(m, [`S${step}`]); name = `paste1@${from}`; }
                } catch (e) { name += ` THREW ${(e as Error).message}`; ok = false; }
                m.handle.flush();
                log.push(name);
                const faults = shadowFaults(m);
                if (faults.length) { failures.push(`seed ${seed} step ${step}: ${log.join(' → ')}\n    ${faults.join('\n    ')}`); ok = false; }
                if (m.conflicts.length) { m.conflicts.length = 0; }
            }
            const expect_: Array<[string, string, number, string]> = [];
            m.view.state.doc.forEach(n => expect_.push([n.attrs.bid, n.type.name, n.type.name === 'heading' ? n.attrs.level : 0, n.textBetween(0, n.content.size, '\n')]));
            fs.writeFileSync(`${FIX}/roundtrip/out/seed${String(seed).padStart(2, '0')}.json`, JSON.stringify({ seed, log, subs: m.submitted, expect: expect_ }));
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
    const reset = () => { cost.paints = cost.decos = cost.drag = 0; cost.paintsCalls = cost.decosCalls = cost.dragCalls = 0; };
    const med = (xs: number[]) => xs.slice().sort((a, b) => a - b)[Math.floor(xs.length / 2)];
    const fmt = (label: string, total: number) => `${label}: ${total.toFixed(1)} ms  [paintsFor ${cost.paints.toFixed(1)} ms/${cost.paintsCalls} calls, buildDecorations ${cost.decos.toFixed(1)} ms/${cost.decosCalls}, drag decorations ${cost.drag.toFixed(1)} ms/${cost.dragCalls}]`;

    it('G1: mount, keystroke, adopt, ack — with where the time goes', () => {
        const lines: string[] = [];
        const blocks = bigTree();
        reset();
        let t = performance.now();
        const m = mount(blocks);
        lines.push(fmt('mount (3,000 blocks)', performance.now() - t));
        // keystroke
        const ks: number[] = [];
        reset();
        for (let i = 0; i < 6; i++) {
            select(m.view, posIn(m.view, 'p700', 5));
            t = performance.now();
            typeText(m.view, 'x');
            ks.push(performance.now() - t);
        }
        lines.push(fmt(`keystroke median of 6 (${ks.map(x => x.toFixed(0)).join(',')})`, med(ks)) + ' (accumulators are the sum over 6)');
        m.handle.flush();
        // adopt: nothing changed
        const stale = proj(blocks);
        reset(); t = performance.now();
        m.handle.adopt(stale);
        lines.push(fmt('adopt, nothing changed', performance.now() - t));
        // adopt: one block changed by another actor
        const changed = proj(blocks.map(b => b.id === 'p900' ? block('p900', 'paragraph', 'Paragraph 900 rewritten by someone else entirely.', 1, 77) : b));
        reset(); t = performance.now();
        m.handle.adopt(changed);
        lines.push(fmt('adopt, one block changed', performance.now() - t));
        // adopt: one block inserted
        const inserted = proj([...blocks.slice(0, 1000), block('pnew', 'paragraph', 'A new paragraph from the loop.', 1, 5), ...blocks.slice(1000)]);
        reset(); t = performance.now();
        m.handle.adopt(inserted);
        lines.push(fmt('adopt, one block inserted', performance.now() - t));
        // ack of one cid
        const cid = m.submitted[0]?.cid ?? 'none';
        reset(); t = performance.now();
        m.handle.ack([{ cid, op: 'o:1', conflict: null }]);
        lines.push(fmt('ack (one cid)', performance.now() - t));
        // keystroke without the drag plugin's per-transaction decoration rebuild
        toggles.noDrag = true;
        const m2 = mount(blocks);
        const ks2: number[] = [];
        reset();
        for (let i = 0; i < 6; i++) {
            select(m2.view, posIn(m2.view, 'p700', 5));
            t = performance.now();
            typeText(m2.view, 'x');
            ks2.push(performance.now() - t);
        }
        toggles.noDrag = false;
        lines.push(fmt(`keystroke WITHOUT drag decorations, median of 6 (${ks2.map(x => x.toFixed(0)).join(',')})`, med(ks2)));
        // a bare ProseMirror insertText on the same doc, no plugins: the floor
        const bare = new EditorView(document.createElement('div'), { state: EditorState.create({ doc: m.view.state.doc }) });
        const ks3: number[] = [];
        for (let i = 0; i < 6; i++) {
            const pos = posIn(bare, 'p700', 5);
            bare.dispatch(bare.state.tr.setSelection(TextSelection.create(bare.state.doc, pos)));
            t = performance.now();
            bare.dispatch(bare.state.tr.insertText('x'));
            ks3.push(performance.now() - t);
        }
        lines.push(`bare ProseMirror keystroke on the same 3,000-block doc, no plugins: ${med(ks3).toFixed(1)} ms`);
        fs.writeFileSync(`${OUT}/probe6_cost.txt`, lines.join('\n') + '\n');
        console.log('\n' + lines.join('\n'));
        expect(lines.length).toBeGreaterThan(0);
    });
});

describe('G2. the two decoration rebuilds, in isolation', () => {
    it('G2: buildDecorations and the drag widgets over a 3,000-block doc', async () => {
        const decorations = await vi.importActual<typeof import('/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/editor/decorations')>('/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/editor/decorations');
        const drag = await vi.importActual<typeof import('/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/editor/drag')>('/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/editor/drag');
        const adopt = await vi.importActual<typeof import('/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/calc/adopt')>('/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/calc/adopt');
        const { codocSchema } = await import('/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/editor/schema');
        const blocks: ProjectedBlock[] = [];
        for (let i = 0; i < 1500; i++) { blocks.push(block(`h${i}`, 'heading', `Feature ${i}`)); blocks.push(block(`p${i}`, 'paragraph', `Paragraph ${i}: it retries once before giving up, then reports the failure to the caller.`)); }
        const { doc } = adopt.docFrom(codocSchema(), proj(blocks));
        const med = (xs: number[]) => xs.slice().sort((a, b) => a - b)[Math.floor(xs.length / 2)];
        const time = (f: () => unknown, n = 7) => { const xs: number[] = []; for (let i = 0; i < n; i++) { const t = performance.now(); f(); xs.push(performance.now() - t); } return med(xs); };
        const empty = new Map<string, Paint[]>();
        const oneInkEach = new Map<string, Paint[]>();
        doc.forEach(b => oneInkEach.set(b.attrs.bid, [{ bid: b.attrs.bid, start: 0, end: 5, cls: 'ink-unsent', title: 'yours' }]));
        const lines = [
            `buildDecorations, no paints, 3,000 blocks: ${time(() => decorations.buildDecorations(doc, empty, {}, new Set())).toFixed(1)} ms`,
            `buildDecorations, one ink paint per block: ${time(() => decorations.buildDecorations(doc, oneInkEach, {}, new Set())).toFixed(1)} ms`,
            `drag plugin decorations prop (3,000 widgets): ${time(() => (drag.dragPlugin(() => undefined).props.decorations as (s: unknown) => unknown)({ doc })).toFixed(1)} ms`,
            `DecorationSet.create with 3,000 inline decorations, precomputed positions: ${time(() => { const ds: Decoration[] = []; doc.forEach((b, pos) => ds.push(Decoration.inline(pos + 1, pos + 6, { class: 'x' }))); return DecorationSet.create(doc, ds); }).toFixed(1)} ms`,
        ];
        fs.appendFileSync(`${OUT}/probe6_cost.txt`, lines.join('\n') + '\n');
        console.log('\n' + lines.join('\n'));
        expect(lines.length).toBe(4);
    });
});

describe('G3. the app shell on a projection at 1,500 features', () => {
    it('G3: a second projection costs adopt + renderTree + renderToolbar', async () => {
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
        const line = `app.ts: first projection (mount + tree pane) ${first.toFixed(0)} ms; a later projection with one block changed (adopt + renderTree + renderToolbar + margin) median ${xs.slice().sort((a, b) => a - b)[1].toFixed(0)} ms; tree rows rebuilt: ${document.querySelectorAll('.codoc-row').length}`;
        fs.appendFileSync(`${OUT}/probe6_cost.txt`, line + '\n');
        console.log('\n' + line);
        expect(document.querySelectorAll('.codoc-row').length).toBe(1500);
    });
});

// ── F. confirmed fine, re-probed ─────────────────────────────────────────────
describe('F. still correct', () => {
    it('F1: an ack for a cid this window never sent changes nothing', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'one')]);
        select(m.view, posIn(m.view, 'p', 3));
        typeText(m.view, 'x');
        m.handle.ack([{ cid: 'other-window-1', op: 'o:9', conflict: null }, { cid: 'other-window-2', op: null, conflict: 'authority' }]);
        expect(texts(m.view.state.doc)).toEqual(['T', 'onex']);
        expect(m.conflicts).toEqual([]);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('F2: a comment anchor on words not yet acked names them provisionally, by the cid that was submitted', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'one ')]);
        select(m.view, posIn(m.view, 'p', 4));
        typeText(m.view, 'two');
        m.handle.flush();
        select(m.view, posIn(m.view, 'p', 4), posIn(m.view, 'p', 7));
        const a = m.handle.selectionAnchor()!;
        expect(a.text).toBe('two');
        expect((a.start as string[])[0]).toBe('$cid');
        expect(m.submitted.map(s => s.cid)).toContain((a.start as string[])[1]);
    });

    it('F3: a refused op re-adopts only its block; the other block\'s unsent words stay', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one'), block('p2', 'paragraph', 'two')]);
        select(m.view, posIn(m.view, 'p1', 3)); typeText(m.view, 'A');
        select(m.view, posIn(m.view, 'p2', 3)); typeText(m.view, 'B');
        m.handle.flush();
        const bad = m.submitted.find(s => (s.target as { bid: string }).bid === 'p1')!;
        m.handle.ack([{ cid: bad.cid, op: null, conflict: 'unknown_anchor' }]);
        expect(texts(m.view.state.doc)).toEqual(['T', 'one', 'twoB']);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('F4: Enter with a cross-paragraph selection is the delete, the merge and the split in one batch', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one two'), block('p2', 'paragraph', 'three four')]);
        select(m.view, posIn(m.view, 'p1', 4), posIn(m.view, 'p2', 6));
        key(m.view, 'Enter', 13);
        m.handle.flush();
        expect(texts(m.view.state.doc)).toEqual(['T', 'one ', 'four']);
        expect(new Set(m.submitted.map(s => s.batch)).size).toBe(1);
        expect(kinds(m.submitted)).toEqual(['text.delete', 'text.delete', 'block.merge', 'block.split']);
        expect(shadowFaults(m)).toEqual([]);
    });
});
