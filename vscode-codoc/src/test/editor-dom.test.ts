/**
 * @vitest-environment jsdom
 *
 * The editor as the browser drives it: real `mountEditor` views, keydown events
 * through ProseMirror's own handlers, `view.dispatch` of what `readDOMChange`
 * would dispatch, `handlePaste` through `someProp`. The rest of the suite tests
 * the editor's pure modules; nothing else mounts the editor, which is why
 * review 5 found five faults here that no test could see — the recorder
 * speaking for a refused gesture, adoption undoing a gesture against a stale
 * projection, a paste landing inside its host block, a decomposed keystroke
 * throwing inside `dispatch`, and deleting the only feature emptying the tree.
 */
import { Fragment, Slice, type Node as PMNode } from '@tiptap/pm/model';
import { AllSelection, TextSelection } from '@tiptap/pm/state';
import type { EditorView } from '@tiptap/pm/view';
import * as fs from 'node:fs';
import { describe, expect, it, vi } from 'vitest';


// Capture the shadows map `mountEditor` builds (it is not on the handle).
let captured: Map<string, Shadow> | null = null;
vi.mock('../calc/adopt', async (importOriginal) => {
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

import type { OpBody, Projection, ProjectedBlock, Submission, TextField } from '../calc/projection';
import type { Shadow } from '../calc/shadow';
import { nextUndo, textAt, type HistoryWindow } from '../calc/window';
import { mountEditor, type EditorHandle } from '../editor/editor';
import { Timeline } from '../editor/timeline';
import { Margin, anchorsOf, newThreadOps, threadsOf } from '../editor/comments';


if (!Range.prototype.getClientRects) {
    Range.prototype.getClientRects = () => ({ length: 0, item: () => null, [Symbol.iterator]: function* () {} } as unknown as DOMRectList);
    Range.prototype.getBoundingClientRect = () => ({ top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0 } as DOMRect);
}
HTMLElement.prototype.scrollIntoView = () => undefined;

// ── fixtures ────────────────────────────────────────────────────────────────
let opn = 0;
function block(id: string, kind: 'heading' | 'paragraph', text: string, level = 1, head = 1): ProjectedBlock {
    const n = Array.from(text).length;
    return { id, kind, level, rank: id, head,
             fields: { text: { text, runs: n ? [{ op: `s:${id}${opn++}`, k0: 0, start: 0, end: n, actor: 'human:a', at: '1', plan: null }] : [],
                               spans: [], suggest_del: [] } } };
}
function kindBlock(id: string, kind = 'diagram'): ProjectedBlock {
    return { id, kind, level: 0, rank: id, head: 1, fields: { source: { text: 'a->b', runs: [], spans: [], suggest_del: [] } } };
}
const proj = (blocks: ProjectedBlock[], extra: Partial<Projection> = {}): Projection =>
    ({ head: 9, frontier: {}, blocks, phases: {}, plans: [], bindings: { by_feature: {}, by_file: {} }, claims: {}, ...extra });

interface Mounted { handle: EditorHandle; view: EditorView; shadows: Map<string, Shadow>; submitted: Submission[]; conflicts: string[]; proj: Projection; container: HTMLElement }

const mount = (blocks: ProjectedBlock[], extra: Partial<Projection> = {}) => mountProj(proj(blocks, extra));

/** A deterministic pseudo-random source, so a failing seed is reproducible. */
function rng(seed: number): () => number {
    let x = seed * 1103515245 + 12345;
    return () => { x = (x * 1103515245 + 12345) & 0x7fffffff; return x / 0x7fffffff; };
}

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
/** What `readDOMChange` dispatches for typed text over the selection. */
const typeText = (view: EditorView, text: string) => view.dispatch(view.state.tr.insertText(text));
function paste(m: Mounted, paras: string[], headingFirst = false): boolean {
    const schema = m.view.state.schema;
    const nodes = paras.map((t, i) => (headingFirst && i === 0 ? schema.nodes.heading : schema.nodes.paragraph)
        .create(null, t ? schema.text(t) : undefined));
    const slice = new Slice(Fragment.from(nodes), 1, 1);
    const handled = m.view.someProp('handlePaste', f => f(m.view, new window.Event('paste') as ClipboardEvent, slice)) ?? false;
    if (!handled) m.view.dispatch(m.view.state.tr.replaceSelection(slice));   // ProseMirror's default paste
    return !!handled;
}

/** The invariant under test everywhere: each block's shadow says what the page says. */
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
const pageOf = (doc: PMNode): Array<[string, string, number, string]> => {
    const out: Array<[string, string, number, string]> = [];
    doc.forEach(n => out.push([n.attrs.bid, n.type.name, n.type.name === 'heading' ? n.attrs.level : 0,
                               n.textBetween(0, n.content.size, '\n')]));
    return out;
};

/** Drive the Find widget's own DOM: replace every match of `q` with `r`. One
 *  transaction over many blocks, which nothing else in the fuzz produces. */
function replaceAll(m: Mounted, q: string, r: string): void {
    m.handle.find(true);
    const inputs = m.container.querySelectorAll('.codoc-find .codoc-find-input') as NodeListOf<HTMLInputElement>;
    inputs[0].value = q;
    inputs[0].dispatchEvent(new window.Event('input', { bubbles: true }));
    inputs[1].value = r;
    const btn = (label: string) => Array.from(m.container.querySelectorAll('.codoc-find .codoc-find-btn'))
        .find(b => b.textContent === label) as HTMLButtonElement;
    btn('All').click();
    btn('\u00d7').click();
}

/** Deterministic `Math.random` for a seed, so the bids and batch ids a replay
 *  mints are the ones the recording minted. */
function seedRandom(seed: number): () => void {
    const orig = Math.random;
    Math.random = rng(seed * 7919 + 1);
    return () => { Math.random = orig; };
}

/** One random gesture. Review 6's eleven plus a comment thread on a span, a
 *  replace-all, a pasted heading and Tab on a paragraph. */
function gesture(m: Mounted, r: () => number, step: number, seed: number): string {
    const doc = m.view.state.doc;
    const blocks: Array<{ bid: string; kind: string; start: number; len: number }> = [];
    doc.forEach((n, pos) => blocks.push({ bid: n.attrs.bid, kind: n.type.name, start: pos + 1, len: n.content.size }));
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
        if (a.len < 2) return 'noop';
        const s0 = a.start + Math.floor(r() * (a.len - 1));
        const e0 = s0 + 1 + Math.floor(r() * (a.len - (s0 - a.start) - 1));
        select(m.view, s0, e0);
        const anchor = m.handle.selectionAnchor();
        if (!anchor) return 'noop';
        m.handle.flush();                        // the anchor refs must not be `$cid` refs
        const ops = newThreadOps(`c-${seed}-${step}`, anchor.bid, anchor.text, `note ${step}`,
                                 r() < 0.5 ? 'code' : 'both', anchor.start, anchor.end);
        m.submitted.push(...ops.map((op, i) => ({ cid: `cm-${seed}-${step}-${i}`, batch: `t-c${seed}-${step}`, ...op })));
        return `comment on ${anchor.bid}[${anchor.text}]`;
    }
    if (g === 11) { const q = ['e', 'a', 'o', 't'][Math.floor(r() * 4)]; replaceAll(m, q, q.toUpperCase()); return `replaceAll ${q}`; }
    if (g === 12) { select(m.view, from); paste(m, [`H${step}`, `body ${step}`], true); return `pasteHeading@${from}`; }
    if (g === 13) { const paras = blocks.filter(x => x.kind === 'paragraph'); if (!paras.length) return 'noop'; const pp = paras[Math.floor(r() * paras.length)]; select(m.view, pp.start); key(m.view, 'Tab', 9); return `tab on ${pp.bid}`; }
    select(m.view, from); paste(m, [`S${step}`]); return `paste1@${from}`;
}

/** Fuzz one seed and return what the page ended as. */
function fuzz(p0: Projection, seed: number, steps: number, failures: string[]): { m: Mounted; log: string[] } {
    const restore = seedRandom(seed);
    const r = rng(seed);
    const m = mountProj(JSON.parse(JSON.stringify(p0)) as Projection);
    const log: string[] = [];
    let ok = true;
    for (let step = 0; step < steps && ok; step++) {
        let name = '';
        try { name = gesture(m, r, step, seed); } catch (e) { name = `${name} THREW ${(e as Error).message}`; ok = false; }
        m.handle.flush();
        log.push(name);
        const faults = shadowFaults(m);
        if (faults.length) { failures.push(`seed ${seed} step ${step}: ${log.join(' \u2192 ')}\n    ${faults.join('\n    ')}`); ok = false; }
        m.conflicts.length = 0;
    }
    restore();
    return { m, log };
}

// ── A. gesture ownership ─────────────────────────────────────────────────────
describe('A. the structural guard and the recorder', () => {
    it('E1: typing over a selection that spans two paragraphs changes nothing it does not record', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one two'), block('p2', 'paragraph', 'three four')]);
        select(m.view, posIn(m.view, 'p1', 4), posIn(m.view, 'p2', 5));
        typeText(m.view, 'X');
        m.handle.flush();
        // Whatever the page shows, the store must be told the same thing.
        expect(shadowFaults(m)).toEqual([]);
        if (texts(m.view.state.doc)[1] === 'one two') expect(m.submitted).toEqual([]);   // refused → nothing recorded
        else expect(texts(m.view.state.doc)).toEqual(['T', 'one X four']);
    });

    it('E2: Enter with a selection inside one paragraph splits it as ops (spec §4 table, last row)', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'hello world')]);
        select(m.view, posIn(m.view, 'p', 2), posIn(m.view, 'p', 5));      // "he|llo| world"
        key(m.view, 'Enter', 13);
        m.handle.flush();
        expect(shadowFaults(m)).toEqual([]);
        expect(texts(m.view.state.doc)).toEqual(['T', 'he', ' world']);
        expect(kinds(m.submitted)).toEqual(expect.arrayContaining(['text.delete', 'block.split']));
    });

    it('E3: Backspace over a whole selected paragraph deletes its words', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'hello world'), block('q', 'paragraph', 'tail')]);
        select(m.view, posIn(m.view, 'p', 0), posIn(m.view, 'p', 11));
        key(m.view, 'Backspace', 8);
        m.handle.flush();
        expect(shadowFaults(m)).toEqual([]);
        expect(texts(m.view.state.doc)[1]).toBe('');
        expect(kinds(m.submitted)).toContain('text.delete');
    });

    it('E3b: typing over a whole selected paragraph replaces its words', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'hello world'), block('q', 'paragraph', 'tail')]);
        select(m.view, posIn(m.view, 'p', 0), posIn(m.view, 'p', 11));
        typeText(m.view, 'X');
        m.handle.flush();
        expect(shadowFaults(m)).toEqual([]);
        expect(texts(m.view.state.doc)[1]).toBe('X');
    });

    it('E3c: select-all + Backspace over a tree with headings is refused with a word, and the shadows stand', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'hello world')]);
        m.view.dispatch(m.view.state.tr.setSelection(new AllSelection(m.view.state.doc)));
        key(m.view, 'Backspace', 8);
        m.handle.flush();
        expect(shadowFaults(m)).toEqual([]);
        expect(m.conflicts.length).toBeGreaterThan(0);
        expect(m.submitted).toEqual([]);
        expect(texts(m.view.state.doc)).toEqual(['T', 'hello world']);
    });

    it('E3d: Cut (Mod-x) of a cross-paragraph selection removes the words or leaves everything consistent', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one two'), block('p2', 'paragraph', 'three four')]);
        select(m.view, posIn(m.view, 'p1', 4), posIn(m.view, 'p2', 5));
        // ProseMirror's cut handler: tr.deleteSelection() tagged uiEvent=cut.
        m.view.dispatch(m.view.state.tr.deleteSelection().setMeta('uiEvent', 'cut'));
        m.handle.flush();
        expect(shadowFaults(m)).toEqual([]);
        if (texts(m.view.state.doc)[1] === 'one two') expect(m.submitted).toEqual([]);
        else expect(texts(m.view.state.doc)).toEqual(['T', 'one  four']);
    });

    it('E4: a multi-paragraph paste over a selection keeps one bid per block and the shadows in step', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'hello world')]);
        select(m.view, posIn(m.view, 'p', 3), posIn(m.view, 'p', 7));      // "hel|lo w|orld"
        expect(paste(m, ['first', 'second'])).toBe(true);
        m.handle.flush();
        expect(shadowFaults(m)).toEqual([]);
        expect(texts(m.view.state.doc)).toEqual(['T', 'helfirstorld', 'second']);
    });

    it('E5: after a multi-paragraph paste every provisional ref names a cid that was actually submitted', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'head tail')]);
        select(m.view, posIn(m.view, 'p', 5));
        paste(m, ['first', 'second']);
        m.handle.flush();
        const sent = new Set(m.submitted.filter(s => s.kind === 'text.insert').map(s => s.cid));
        const dangling: string[] = [];
        for (const [bid, sh] of m.shadows) for (const r of sh.refs) if (r.length === 3 && !sent.has(r[1])) dangling.push(`${bid}:${r[1]}`);
        expect(dangling).toEqual([]);
        // …and the daemon can therefore resolve the next keystroke's anchor. Type right after "first":
        select(m.view, posIn(m.view, 'p', 10));
        typeText(m.view, '!');
        m.handle.flush();
        const anchor = (m.submitted[m.submitted.length - 1].payload as { after: unknown }).after as [string, string, number];
        expect(anchor[0] === '$cid' ? sent.has(anchor[1]) : true).toBe(true);
    });

    it('E5b: paste into an empty paragraph, and a paste whose slice is open at one end', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', ''), block('q', 'paragraph', 'after')]);
        select(m.view, posIn(m.view, 'p', 0));
        paste(m, ['first', 'second']);
        m.handle.flush();
        expect(shadowFaults(m)).toEqual([]);
        expect(texts(m.view.state.doc)).toEqual(['T', 'first', 'second', 'after']);
        // open-ended slice (openStart 0: begins with a whole paragraph)
        const schema = m.view.state.schema;
        select(m.view, posIn(m.view, 'q', 5));
        const slice = new Slice(Fragment.from([schema.nodes.paragraph.create(null, schema.text('A')), schema.nodes.paragraph.create(null, schema.text('B'))]), 0, 1);
        const handled = m.view.someProp('handlePaste', f => f(m.view, new window.Event('paste') as ClipboardEvent, slice)) ?? false;
        if (!handled) m.view.dispatch(m.view.state.tr.replaceSelection(slice));
        m.handle.flush();
        expect(shadowFaults(m)).toEqual([]);
    });

    it('E6: a Backspace over a selection that crosses a kind block leaves the shadows honest when it refuses', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'aaa bbb'), kindBlock('k'), block('p2', 'paragraph', 'ccc ddd')]);
        select(m.view, posIn(m.view, 'p1', 4), posIn(m.view, 'p2', 3));
        key(m.view, 'Backspace', 8);
        m.handle.flush();
        expect(shadowFaults(m)).toEqual([]);
    });

    it('E6b: Enter at the very start and the very end of a heading, with and without a selection', () => {
        const m = mount([block('h', 'heading', 'Title'), block('p', 'paragraph', 'body')]);
        select(m.view, posIn(m.view, 'h', 5));
        key(m.view, 'Enter', 13);
        expect(shadowFaults(m)).toEqual([]);
        expect(ids(m.view.state.doc).length).toBe(3);
        select(m.view, posIn(m.view, 'h', 0));
        key(m.view, 'Enter', 13);
        expect(shadowFaults(m)).toEqual([]);
        expect(ids(m.view.state.doc).length).toBe(4);
        expect(texts(m.view.state.doc)).toEqual(['', 'Title', '', 'body']);
        // Selection inside the heading, Enter: refused or exact, never desynced.
        select(m.view, posIn(m.view, 'h', 1), posIn(m.view, 'h', 3));
        key(m.view, 'Enter', 13);
        m.handle.flush();
        expect(shadowFaults(m)).toEqual([]);
    });

    it('E6c: drop (drag handle) moves the block with what it dominates, once', () => {
        const m = mount([block('h1', 'heading', 'A'), block('p1', 'paragraph', 'a-body'), block('h2', 'heading', 'B'), block('p2', 'paragraph', 'b-body')]);
        m.handle.moveBlock('h2', 'START');
        m.handle.flush();
        expect(ids(m.view.state.doc)).toEqual(['heading:h2', 'paragraph:p2', 'heading:h1', 'paragraph:p1']);
        expect(kinds(m.submitted)).toEqual(['block.move']);
        expect(shadowFaults(m)).toEqual([]);
    });
});

// ── B. adoption ──────────────────────────────────────────────────────────────
describe('B. adopting a projection while the person holds blocks', () => {
    it('E7: a merge followed by a projection that predates it does not bring the merged paragraph back', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one'), block('p2', 'paragraph', 'two')]);
        select(m.view, posIn(m.view, 'p2', 0));
        key(m.view, 'Backspace', 8);
        expect(texts(m.view.state.doc)).toEqual(['T', 'onetwo']);
        m.handle.adopt(m.proj);                 // the same projection: what a conflict re-adopt or an unrelated publish delivers
        expect(texts(m.view.state.doc)).toEqual(['T', 'onetwo']);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('E8: a split followed by a projection that predates it does not drop the new paragraph', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'hello world')]);
        select(m.view, posIn(m.view, 'p', 5));
        key(m.view, 'Enter', 13);
        expect(texts(m.view.state.doc)).toEqual(['T', 'hello', ' world']);
        m.handle.adopt(m.proj);
        expect(texts(m.view.state.doc)).toEqual(['T', 'hello', ' world']);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('E8b: a deleted feature followed by a stale projection stays deleted', () => {
        const m = mount([block('h1', 'heading', 'A'), block('p1', 'paragraph', 'a'), block('h2', 'heading', 'B'), block('p2', 'paragraph', 'b')]);
        m.handle.deleteFeature('h2');
        expect(ids(m.view.state.doc)).toEqual(['heading:h1', 'paragraph:p1']);
        m.handle.adopt(m.proj);
        expect(ids(m.view.state.doc)).toEqual(['heading:h1', 'paragraph:p1']);
    });

    it('E9: a held block the projection dropped keeps its place, not the top of the page', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one'), block('p2', 'paragraph', 'two'), block('p3', 'paragraph', 'three')]);
        select(m.view, posIn(m.view, 'p2', 3));
        typeText(m.view, 'X');                  // p2 is now held (unacked)
        // …and someone else edited p3, so the projection is not a no-op.
        m.handle.adopt(proj([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one'), block('p3', 'paragraph', 'three more', 1, 7)]));
        expect(ids(m.view.state.doc)).toEqual(['heading:h', 'paragraph:p1', 'paragraph:p2', 'paragraph:p3']);
        expect(texts(m.view.state.doc)[2]).toBe('twoX');
    });

    it('E9b: a projection arriving mid-typing keeps the caret after the character it followed', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'abc def')]);
        select(m.view, posIn(m.view, 'p', 7));   // after "f"
        // Another actor inserted "ZZ " at the start of the paragraph; heads move.
        const old = m.proj.blocks[1].fields.text as { runs: Array<{ op: string }> };
        const nb: ProjectedBlock = { id: 'p', kind: 'paragraph', level: 1, rank: 'p', head: 2, fields: { text: { text: 'ZZ abc def', spans: [], suggest_del: [],
            runs: [{ op: 'other:1', k0: 0, start: 0, end: 3, actor: 'human:gh:bob', at: '2', plan: null },
                   { op: old.runs[0].op, k0: 0, start: 3, end: 10, actor: 'human:a', at: '1', plan: null }] } } };
        m.handle.adopt(proj([block('h', 'heading', 'T'), nb]));
        expect(texts(m.view.state.doc)[1]).toBe('ZZ abc def');
        // by character identity the caret should still sit after "f", i.e. at the end (offset 10); by offset it would be 7
        const caretOff = m.view.state.selection.from - posIn(m.view, 'p', 0);
        expect(caretOff).toBe(10);
    });

    it('E9c: a block whose kind changed in the projection is re-rendered with its new kind', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'was prose')]);
        m.handle.adopt(proj([block('h', 'heading', 'T'), block('p', 'heading', 'was prose', 2, 5)]));
        expect(ids(m.view.state.doc)).toEqual(['heading:h', 'heading:p']);
        expect(shadowFaults(m)).toEqual([]);
    });
});

// ── C. property: random gestures never desync a shadow ───────────────────────
describe('C. shadow integrity under random gestures', () => {
    function rng(seed: number) { let s = seed >>> 0; return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 2 ** 32; }; }

    it('P1: 40 seeds × 25 gestures through the real view; shadow.text() === node text for every block after each', () => {
        const failures = new Map<string, string>();
        for (let seed = 1; seed <= 40; seed++) {
            const r = rng(seed);
            const m = mount([block('h', 'heading', 'Title'), block('p1', 'paragraph', 'one two three'), block('p2', 'paragraph', 'four five'), block('p3', 'paragraph', 'six')]);
            const log: string[] = [];
            for (let step = 0; step < 25; step++) {
                const doc = m.view.state.doc;
                const blocks: Array<{ bid: string; start: number; len: number }> = [];
                doc.forEach((n, pos) => { if (n.type.name !== 'kindBlock') blocks.push({ bid: n.attrs.bid, start: pos + 1, len: n.content.size }); });
                const a = blocks[Math.floor(r() * blocks.length)], b = blocks[Math.floor(r() * blocks.length)];
                const pa = a.start + Math.floor(r() * (a.len + 1)), pb = b.start + Math.floor(r() * (b.len + 1));
                const from = Math.min(pa, pb), to = Math.max(pa, pb);
                const gesture = Math.floor(r() * 7);
                let name = '';
                try {
                    if (gesture === 0) { select(m.view, from); typeText(m.view, 'x'); name = `type@${from}`; }
                    else if (gesture === 1) { select(m.view, from, to); typeText(m.view, 'y'); name = `type over ${from}-${to}`; }
                    else if (gesture === 2) { select(m.view, from, to); key(m.view, 'Backspace', 8); name = `bs ${from}-${to}`; }
                    else if (gesture === 3) { select(m.view, from, to); key(m.view, 'Delete', 46); name = `del ${from}-${to}`; }
                    else if (gesture === 4) { select(m.view, from, to); key(m.view, 'Enter', 13); name = `enter ${from}-${to}`; }
                    else if (gesture === 5) { select(m.view, from, to); paste(m, ['P', 'Q']); name = `paste ${from}-${to}`; }
                    else { select(m.view, from); paste(m, ['single']); name = `paste1@${from}`; }
                } catch (e) { name += ` THREW ${(e as Error).message}`; }
                log.push(name);
                const faults = shadowFaults(m);
                if (faults.length) {
                    const sig = faults[0].replace(/"[^"]*"/g, '"…"').replace(/b-[0-9a-f]+/g, 'b-*');
                    if (!failures.has(sig)) failures.set(sig, `seed ${seed} step ${step}: ${log.join(' → ')}\n    ${faults.join('\n    ')}`);
                    break;
                }
            }
        }
        expect([...failures.values()]).toEqual([]);
    });
});

// ── E. the app shell: undo gating and message trust ──────────────────────────
describe('E. app.ts: the undo gate and the message listener', () => {
    const posted: Array<Record<string, unknown>> = [];
    const post = (data: unknown, origin = window.location.origin) => window.dispatchEvent(new window.MessageEvent('message', { data, origin } as MessageEventInit));
    const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));

    it('E10: the page ignores a `status` message from another origin (an opener can postMessage into the hub tab)', async () => {
        (globalThis as unknown as { acquireVsCodeApi: () => unknown }).acquireVsCodeApi = () => ({ postMessage: (m: Record<string, unknown>) => posted.push(m), getState: () => undefined, setState: () => undefined });
        const app = document.createElement('div');
        app.id = 'app';
        document.body.append(app);
        await import('../editor/app');
        const p = proj([block('h', 'heading', 'Title'), block('p', 'paragraph', 'one two')]);
        post({ kind: 'projection', proj: p });
        post({ kind: 'status', status: { state: 'synced', actor: 'human:local' } });
        const bar = document.querySelector('.codoc-status')!;
        const before = bar.textContent;
        post({ kind: 'status', status: { state: 'PWNED', detail: 'PWNED', actor: 'human:gh:eve' } }, 'https://evil.example');
        expect(bar.textContent).toBe(before);
    });

    it('E11: undo after a batch the window will never carry (refused, or aged out) still answers the person', async () => {
        posted.length = 0;
        const pm = document.querySelector('#app .ProseMirror') as HTMLElement;
        expect(pm).toBeTruthy();
        // A gesture: Enter at the end of the paragraph → one recorded batch.
        const view = (pm as unknown as { pmViewDesc?: { view?: EditorView } }).pmViewDesc?.view
            ?? ((pm as unknown as { __view?: EditorView }).__view as EditorView | undefined);
        // No handle to the view from outside app.ts: drive the DOM. Click into the paragraph, press Enter.
        pm.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', keyCode: 13, bubbles: true, cancelable: true }));
        await sleep(450);                                                  // the settle timer flushes
        const submit = posted.find(m => m.kind === 'submit') as { subs: Submission[] } | undefined;
        expect(submit, 'the Enter gesture was submitted').toBeTruthy();
        const cids = submit!.subs.map(s => s.cid);
        void view;
        // ⌘Z → the app asks for history.
        document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true, cancelable: true }));
        expect(posted.some(m => m.kind === 'history')).toBe(true);
        // The daemon refused the whole batch (or it has aged out of the 200-op window):
        // the window carries only other batches of ours, and the acks say so.
        post({ kind: 'acks', acks: cids.map(cid => ({ cid, op: null, conflict: 'authority' })) });
        const w: HistoryWindow = { start: 0, head: 1, before: {}, ops: [
            { pos: 0, id: 's:1', at: '1', actor: 'human:local', kind: 'text.insert', batch: 't-earlier', batch_first: 0, plan: null, caused_by: null,
              blocks: { p: 'one two' }, inverse: [{ kind: 'text.delete', target: { bid: 'p', path: 'text' }, payload: {} }] },
        ] };
        posted.length = 0;
        post({ kind: 'history', history: w });
        post({ kind: 'history', history: w });
        const undoSent = posted.some(m => m.kind === 'submit' && (m as { subs: Submission[] }).subs.some(s => s.batch.startsWith('u-')));
        const noticed = /undo/i.test(document.querySelector('.codoc-status')?.textContent ?? '');
        expect(undoSent || noticed, 'either the undo went out or the person was told why not').toBe(true);
    });
});

// ── F. comments ──────────────────────────────────────────────────────────────
describe('F. comment anchors (review-12 C2)', () => {
    it('F1: a thread hangs where its mark lives, not where its anchor field says', () => {
        const p = proj([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one two'), block('p2', 'paragraph', 'three four')]);
        (p.blocks[2].fields.text as { spans: unknown[] }).spans.push({ kind: 'comment', attrs: { block: 'c1' }, start: 0, end: 5 });
        p.blocks.push({ id: 'c1', kind: 'comment', level: 0, rank: 'z', head: 1, fields: { thread: 'c1', anchor: 'p1', anchor_text: 'three', scope: 'code', status: 'open',
                        body: { text: 'fix it', runs: [{ op: 'o', k0: 0, start: 0, end: 6, actor: 'human:gh:eve', at: '1', plan: null }], spans: [], suggest_del: [] } } });
        expect(anchorsOf(p).get('c1')).toBe('p2');
        expect(threadsOf(p)[0].anchor).toBe('p2');
    });
});

// ── H. review-12 fixes re-verified ────────────────────────────────────────────
describe('H. review-12 fixes', () => {
    it('R1 (A2): the `## ` rule drops exactly the hashes, at the start of a populated paragraph', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', '##abc')]);
        select(m.view, posIn(m.view, 'p', 2));
        const handled = m.view.someProp('handleTextInput', f => f(m.view, posIn(m.view, 'p', 2), posIn(m.view, 'p', 2), ' ', () => m.view.state.tr)) ?? false;
        expect(handled).toBe(true);
        m.handle.flush();
        expect(ids(m.view.state.doc)).toEqual(['heading:h', 'heading:p']);
        expect(texts(m.view.state.doc)).toEqual(['T', 'abc']);
        expect(shadowFaults(m)).toEqual([]);
        expect(kinds(m.submitted)).toEqual(['text.delete', 'block.set']);
    });

    it('R2 (A15): deleteFeature that would empty the tree bails out and keeps the shadows', () => {
        const m = mount([block('h', 'heading', 'Only'), block('p', 'paragraph', 'body')]);
        m.handle.deleteFeature('h');
        expect(ids(m.view.state.doc)).toEqual(['heading:h', 'paragraph:p']);
        expect(shadowFaults(m)).toEqual([]);
        select(m.view, posIn(m.view, 'p', 4));
        typeText(m.view, '!');
        m.handle.flush();
        expect(kinds(m.submitted)).toEqual(['text.insert']);
    });

    it('R3 (A8): a decomposed keystroke is one scalar to the shadow and anchors the next one correctly', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'caf')]);
        select(m.view, posIn(m.view, 'p', 3));
        typeText(m.view, 'e\u0301');
        typeText(m.view, 's');
        m.handle.flush();
        expect(m.shadows.get('p')!.length).toBe(5);
        const last = m.submitted[m.submitted.length - 1].payload as { after: [string, string, number] };
        expect(last.after).toEqual(['$cid', m.submitted[m.submitted.length - 2].cid, 0]);
    });
});

// ── R. round trips: the real editor's ops → replayed by the real applier ────
// Driven by `tests/loops/test_roundtrip.py`, which writes the projections, sets
// CODOC_ROUNDTRIP (and, for R2, CODOC_ROUNDTRIP_PHASE) and replays what these
// write through the daemon. They are the one check that says the page and the
// store agree about the same edits; on their own, vitest skips them.
const TRIP = process.env.CODOC_ROUNDTRIP ?? '';
const PHASE = process.env.CODOC_ROUNDTRIP_PHASE ?? 'R1';

describe.runIf(TRIP && PHASE === 'R1')('R1. one actor, every gesture', () => {
    const SEEDS = 24, STEPS = 18;
    it(`${SEEDS} seeds × ${STEPS} gestures keep every shadow honest, and their ops are written out for the applier`, () => {
        const p0 = JSON.parse(fs.readFileSync(`${TRIP}/projection.json`, 'utf8')) as Projection;
        const failures: string[] = [];
        for (let seed = 1; seed <= SEEDS; seed++) {
            const { m, log } = fuzz(p0, seed, STEPS, failures);
            fs.writeFileSync(`${TRIP}/seed${String(seed).padStart(2, '0')}.json`,
                             JSON.stringify({ seed, log, subs: m.submitted, expect: pageOf(m.view.state.doc) }));
        }
        expect(failures).toEqual([]);
    });
});

// Two actors and a plan arriving mid-edit: the case one actor cannot show,
// because with one actor the shadow and the store never disagree about
// anything the projection carries. Phase A records the gestures and their
// submissions; the Python half drains them into a store Bob and an agent have
// already moved, and publishes. Phase B replays the SAME gestures (seeded
// `Math.random`, so the bids and batches match), adopts Bob's projection while
// every block it touched is still held, acks with the daemon's real acks, then
// adopts the final projection — and the page must be the store's page.
describe.runIf(TRIP && (PHASE === 'R2A' || PHASE === 'R2B'))('R2. two actors and a plan', () => {
    const SEEDS = 16, STEPS = 12;
    it(`${SEEDS} seeds × ${STEPS} gestures under another actor's edits and an agent's plan`, () => {
        const p1 = JSON.parse(fs.readFileSync(`${TRIP}/p1.json`, 'utf8')) as Projection;
        const p2 = PHASE === 'R2B' ? JSON.parse(fs.readFileSync(`${TRIP}/p2.json`, 'utf8')) as Projection : null;
        const failures: string[] = [];
        for (let seed = 1; seed <= SEEDS; seed++) {
            const pad = String(seed).padStart(2, '0');
            const { m, log } = fuzz(p1, seed, STEPS, failures);
            if (PHASE === 'R2A') {
                fs.writeFileSync(`${TRIP}/r2a-seed${pad}.json`, JSON.stringify({ seed, log, subs: m.submitted }));
                continue;
            }
            const rec = JSON.parse(fs.readFileSync(`${TRIP}/r2b-seed${pad}.json`, 'utf8')) as
                { acks: Array<{ cid: string; op: string; conflict: string | null }>; p3: Projection; subs: Submission[] };
            const a = rec.subs.map(x => x.kind).join(','), b = m.submitted.map(x => x.kind).join(',');
            if (a !== b) { failures.push(`seed ${seed}: the replay diverged\n  A: ${a}\n  B: ${b}`); continue; }
            m.handle.adopt(p2!);                          // Bob's page, while every touched block is held
            const faultsP2 = shadowFaults(m);
            m.handle.ack(rec.acks.map((x, i) => ({ cid: m.submitted[i].cid, op: x.op, conflict: x.conflict })));
            const faultsAck = shadowFaults(m);
            m.handle.adopt(rec.p3);
            fs.writeFileSync(`${TRIP}/r2c-seed${pad}.json`, JSON.stringify(
                { seed, log, afterP3: pageOf(m.view.state.doc), faultsP2, faultsAck, faultsP3: shadowFaults(m) }));
        }
        expect(failures).toEqual([]);
    });
});


// ── H. held blocks, acks and the projection ──────────────────────────────────
//
// The seam review 7 found: `adopt` leaves a held block alone, and nothing read
// the projection again when the ack lifted the hold. None of it is visible with
// one actor, which is why the fuzz above never saw it.
describe('H. a projection skipped for a held block', () => {
    /** The page after Bob's letter arrives while the person's own 'x' is unacked. */
    function twoActors(extra: Partial<Projection> = {}, text = 'Bonex', runs?: TextField['runs']) {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one')]);
        select(m.view, posIn(m.view, 'p1', 3));
        typeText(m.view, 'x');
        m.handle.flush();
        const cid = m.submitted[0].cid;
        const mine = (m.proj.blocks[1].fields.text as TextField).runs[0];
        const p1: ProjectedBlock = { id: 'p1', kind: 'paragraph', level: 0, rank: 'p1', head: 9,
            fields: { text: { text, spans: [], suggest_del: [], runs: runs ?? [
                { op: 'bob:1', k0: 0, start: 0, end: 1, actor: 'human:gh:bob', at: '2', plan: null },
                { op: mine.op, k0: 0, start: 1, end: 4, actor: 'human:a', at: '1', plan: null },
                { op: 'h:5', k0: 0, start: 4, end: 5, actor: 'human:local', at: '3', plan: null }] } } };
        return { m, cid, p1: proj([block('h', 'heading', 'T'), p1], { frontier: { h: 5, bob: 1, ag: 1 }, ...extra }) };
    }

    it("is taken up when the ack lifts the hold, so another actor's word appears", () => {
        const { m, cid, p1 } = twoActors();
        m.handle.adopt(p1);
        expect(texts(m.view.state.doc)[1]).toBe('onex');          // held: left alone, as §6 says
        m.handle.ack([{ cid, op: 'h:5', conflict: null }]);
        expect(texts(m.view.state.doc)[1]).toBe('Bonex');
        expect(shadowFaults(m)).toEqual([]);
    });

    it('is NOT taken up when the projection predates the gesture', () => {
        const { m, cid, p1 } = twoActors();
        // The same projection, but its frontier does not carry the acked op: it
        // was published before that op landed, so its word for p1 is older than
        // the page's and adopting it would undo the person's typing.
        m.handle.adopt({ ...p1, frontier: { bob: 1 } });
        m.handle.ack([{ cid, op: 'h:5', conflict: null }]);
        expect(texts(m.view.state.doc)[1]).toBe('onex');
        expect(shadowFaults(m)).toEqual([]);
    });

    it("shows an agent's plan that arrived in the paragraph the person was typing in", () => {
        const { m, cid, p1 } = twoActors(
            { plans: [{ id: 'pl1', actor: 'agent:x', kind: 'edit', target: 'p1', note: '', status: 'open' }] },
            'onex more',
            [{ op: 's:p10', k0: 0, start: 0, end: 3, actor: 'human:a', at: '1', plan: null },
             { op: 'h:5', k0: 0, start: 3, end: 4, actor: 'human:local', at: '3', plan: null },
             { op: 'ag:1', k0: 0, start: 4, end: 9, actor: 'agent:x', at: '4', plan: 'pl1' }]);
        m.handle.adopt(p1);
        m.handle.ack([{ cid, op: 'h:5', conflict: null }]);
        expect(texts(m.view.state.doc)[1]).toBe('onex more');
        expect(m.view.dom.querySelectorAll('[data-plan]').length).toBe(1);
    });

    it("shows a contributor's own Backspace as the plan's strike-through, not as an absence", () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one two')]);
        select(m.view, posIn(m.view, 'p1', 4), posIn(m.view, 'p1', 7));
        key(m.view, 'Backspace', 8);
        m.handle.flush();
        expect(texts(m.view.state.doc)[1]).toBe('one ');
        const cid = m.submitted[0].cid;
        // The daemon turned the suggester's delete into the plan's suggest_del
        // (`workspace._as_proposal`) and published, then acked.
        const p1: ProjectedBlock = { ...m.proj.blocks[1], head: 9,
            fields: { text: { ...(m.proj.blocks[1].fields.text as TextField), suggest_del: [{ plan: 'pl1', start: 4, end: 7 }] } } };
        m.handle.adopt(proj([block('h', 'heading', 'T'), p1],
            { frontier: { h: 6 }, plans: [{ id: 'pl1', actor: 'human:gh:alice', kind: 'edit', target: 'p1', note: '', status: 'open' }] }));
        m.handle.ack([{ cid, op: 'h:6', conflict: null }]);
        expect(texts(m.view.state.doc)[1]).toBe('one two');
        expect(m.view.dom.querySelectorAll('[data-suggest-del]').length).toBe(1);
    });
});

describe('I. what a page must not lose to an arriving projection', () => {
    it('a multi-paragraph paste leaves the page NFC, like the shadow and the op', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'ab')]);
        select(m.view, posIn(m.view, 'p', 2));
        expect(paste(m, ['x', 'café ok'])).toBe(true);      // decomposed é, as a macOS paste gives it
        m.handle.flush();
        expect(shadowFaults(m)).toEqual([]);
        expect(m.submitted.filter(s => s.kind === 'text.insert').map(s => (s.payload as { text: string }).text))
            .toContain('café ok');
        const nb = ids(m.view.state.doc)[2].split(':')[1];
        select(m.view, posIn(m.view, nb, 4));                     // after "café"
        typeText(m.view, '!');
        m.handle.flush();
        expect(shadowFaults(m)).toEqual([]);
        expect(texts(m.view.state.doc)[2]).toBe('café! ok');
    });

    it('a comment being written survives a projection (every settle anywhere publishes one)', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one two'), block('p2', 'paragraph', 'three')]);
        const page = document.createElement('div');
        document.body.append(page);
        const posted: OpBody[] = [];
        const margin = new Margin(page, () => m.view,
            { submit: ops => posted.push(...ops), build: () => undefined, newBid: () => 'c-new', featureOf: () => 'h' });
        const p = proj([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one two'), block('p2', 'paragraph', 'three'),
            { id: 'c1', kind: 'comment', level: 0, rank: 'z', head: 1, fields: { thread: 'c1', anchor: 'p2', anchor_text: 'three',
              scope: 'code', status: 'open', body: { text: 'tighten this', spans: [], suggest_del: [],
              runs: [{ op: 'o', k0: 0, start: 0, end: 12, actor: 'human:gh:bob', at: '1', plan: null }] } } }]);
        (p.blocks[2].fields.text as TextField).spans.push({ kind: 'comment', attrs: { block: 'c1' }, start: 0, end: 5 });
        margin.update(p);
        margin.compose('p1', 'one', ['s:x', 0], ['s:x', 2]);
        (page.querySelector('.codoc-card.is-draft textarea') as HTMLTextAreaElement).value = 'please split this sentence';
        (page.querySelector('.codoc-card:not(.is-draft) textarea') as HTMLTextAreaElement).value = 'agreed, and rename it';
        margin.update(p);                                          // someone else typed elsewhere
        expect((page.querySelector('.codoc-card.is-draft textarea') as HTMLTextAreaElement | null)?.value).toBe('please split this sentence');
        expect((page.querySelector('.codoc-card:not(.is-draft) textarea') as HTMLTextAreaElement | null)?.value).toBe('agreed, and rename it');
        expect(posted).toEqual([]);
    });

    it('the History scrubber sitting at now stays at now when the window grows', () => {
        const parent = document.createElement('div');
        document.body.append(parent);
        const dom = document.createElement('div');
        parent.append(dom);
        const tl = new Timeline(parent, () => dom);
        const p = proj([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one')]);
        const entry = (pos: number): HistoryWindow['ops'][number] => ({ pos, id: `s:${pos}`, at: `2026-09-18T00:00:0${pos}`,
            actor: 'human:gh:bob', kind: 'text.insert', batch: `b${pos}`, plan: null, caused_by: null, blocks: { p1: `one${pos}` }, inverse: [] });
        tl.setWindow({ start: 0, head: 2, before: { p1: 'one' }, ops: [entry(0), entry(1)] }, p);
        tl.toggle(true);
        expect(tl.past.hidden).toBe(true);                         // at "now"
        tl.setWindow({ start: 0, head: 3, before: { p1: 'one' }, ops: [entry(0), entry(1), entry(2)] }, p);
        expect(tl.past.hidden).toBe(true);                         // still now: nobody dragged the scrubber
        expect(dom.hidden).toBe(false);
    });

    it('a drag keeps every margin marker on the page', () => {
        const m = mount([block('h1', 'heading', 'A'), block('p1', 'paragraph', 'a'),
                         block('h2', 'heading', 'B'), block('p2', 'paragraph', 'b')],
                        { phases: { h1: 'edited', h2: 'drifted' } });
        expect(m.view.dom.querySelectorAll('.codoc-marker').length).toBe(2);
        m.handle.moveBlock('h2', 'START');
        expect(ids(m.view.state.doc)).toEqual(['heading:h2', 'paragraph:p2', 'heading:h1', 'paragraph:p1']);
        // Right after the gesture, before any ack or keystroke, both keep theirs.
        expect(m.view.dom.querySelectorAll('.codoc-marker').length).toBe(2);
    });
});

// ── J. the drag handle as a browser delivers it ──────────────────────────────
//
// The handle is a CSS `::before` on the block, so a drag is four DOM events and
// no decoration. Nothing tested that path — the other drag tests call
// `handle.moveBlock`, which is the API *under* the drop — and what shipped in
// round 7 did nothing at all in a browser: ProseMirror's own `dragstart` runs
// after a custom handler that returns false, calls `dataTransfer.clearData()`,
// and takes the drag for the selection.
class FakeDataTransfer {
    private d = new Map<string, string>();
    effectAllowed = 'uninitialized';
    dropEffect = 'none';
    get types(): string[] { return [...this.d.keys()]; }
    setData(t: string, v: string): void { this.d.set(t, v); }
    getData(t: string): string { return this.d.get(t) ?? ''; }
    clearData(t?: string): void { if (t) this.d.delete(t); else this.d.clear(); }
}

function domOf(m: Mounted, bid: string): Element {
    const el = m.view.dom.querySelector(`[data-bid="${bid}"]`);
    if (!el) throw new Error(`no DOM for ${bid}`);
    return el;
}

function dragEvent(type: string, target: Element, dt: FakeDataTransfer, clientY = 10): Event {
    const ev = new window.Event(type, { bubbles: true, cancelable: true });
    Object.defineProperty(ev, 'dataTransfer', { value: dt });
    Object.defineProperty(ev, 'clientX', { value: 5 });
    Object.defineProperty(ev, 'clientY', { value: clientY });
    target.dispatchEvent(ev);
    return ev;
}

/** A drag as Chrome delivers it: mousedown in the handle strip (x is negative,
 *  which is where the `::before` sits), then dragstart on the block, dragover
 *  and drop on the target, dragend on the source. */
function dragBlock(m: Mounted, from: string, to: string): FakeDataTransfer {
    const src = domOf(m, from);
    src.dispatchEvent(new window.MouseEvent('mousedown',
        { bubbles: true, cancelable: true, button: 0, clientX: -30, clientY: 5 }));
    const dt = new FakeDataTransfer();
    dragEvent('dragstart', src, dt);
    const tgt = domOf(m, to);
    dragEvent('dragover', tgt, dt);
    dragEvent('drop', tgt, dt);
    dragEvent('dragend', src, dt);
    return dt;
}

describe('J. the drag handle', () => {
    it('moves the block, and the payload survives ProseMirror\'s own dragstart', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one'), block('p2', 'paragraph', 'two')]);
        const dt = dragBlock(m, 'p1', 'p2');
        expect(dt.getData('application/x-codoc-bid')).toBe('p1');
        expect(ids(m.view.state.doc)).toEqual(['heading:h', 'paragraph:p2', 'paragraph:p1']);
        expect(m.view.dragging).toBeNull();               // ProseMirror did not take the drag as its own
        m.handle.flush();
        expect(kinds(m.submitted)).toEqual(['block.move']);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('with words selected elsewhere, is still a block move and not a move of those words', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one'), block('p2', 'paragraph', 'two')]);
        select(m.view, posIn(m.view, 'p2', 0), posIn(m.view, 'p2', 3));        // "two" selected
        dragBlock(m, 'p1', 'p2');
        expect(texts(m.view.state.doc)).toEqual(['T', 'two', 'one']);
        expect(m.view.dragging).toBeNull();
        m.handle.flush();
        expect(kinds(m.submitted)).toEqual(['block.move']);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('does nothing when the pointer went down in the text rather than the handle', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one'), block('p2', 'paragraph', 'two')]);
        const src = domOf(m, 'p1');
        src.dispatchEvent(new window.MouseEvent('mousedown',
            { bubbles: true, cancelable: true, button: 0, clientX: 20, clientY: 5 }));
        const dt = new FakeDataTransfer();
        dragEvent('dragstart', src, dt);
        expect(dt.getData('application/x-codoc-bid')).toBe('');
        expect((src as HTMLElement).draggable).toBe(false);
    });
});

// ── K. the hold predicate ────────────────────────────────────────────────────
//
// Round 7 put the frontier rule in `ack()`, which decides which released blocks
// to re-read — but the re-reading is `adopt()`, whose hold set was "unacked"
// alone. So any block just released whose op the projection does not cover was
// replaced by that older projection the next time `adopt()` ran for any reason.
describe('K. a block is held while the projection predates its last op', () => {
    /** Two blocks, each with one op in flight, and Bob's projection which
     *  predates both (its frontier covers neither). */
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
        m.handle.adopt(bob);                                  // the watcher fires twice for one write
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

// ── L. the paint map follows the structure ───────────────────────────────────
//
// `paintFid` is the block→feature index the paints read, and it was rebuilt
// only on a full repaint — so a block born by Enter had no entry and was never
// painted, and a dragged one kept its old feature's watermark.
const paintsIn = (m: Mounted, bid: string) =>
    Array.from((m.view.dom.querySelector(`[data-bid="${bid}"]`) as HTMLElement).querySelectorAll('.codoc-paint'))
        .map(e => Array.from(e.classList).filter(c => c !== 'codoc-paint').join(' ')).sort();

/** A projected paragraph built from runs, so a projection can keep the
 *  identity of characters the page already has. */
function para(id: string, head: number, parts: Array<[string, { op: string; actor: string; k0?: number; plan?: string | null }]>): ProjectedBlock {
    let at = 0;
    const runs = parts.map(([text, r]) => {
        const start = at;
        at += Array.from(text).length;
        return { op: r.op, k0: r.k0 ?? 0, start, end: at, actor: r.actor, at: '1', plan: r.plan ?? null };
    });
    return { id, kind: 'paragraph', level: 0, rank: id, head,
             fields: { text: { text: parts.map(x => x[0]).join(''), runs, spans: [], suggest_del: [] } } };
}

describe('L. which feature a block belongs to, after a gesture', () => {
    it('inks the paragraph an Enter created', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one two')]);
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
                            { frontier: { h: 5 }, head: 20 }));
        m.handle.ack([{ cid, op: 'h:5', conflict: null }]);
        expect(texts(m.view.state.doc)).toEqual(['T', 'one', ' two']);
        expect(paintsIn(m, nid)).toEqual(['codoc-ink-unsent']);
    });

    it("re-paints a paragraph dragged under another feature as that feature's", () => {
        const m = mount([block('h1', 'heading', 'A'),
                         para('p1', 1, [['a-body', { op: 'w:1', actor: 'human:a' }]]),
                         block('h2', 'heading', 'B'),
                         para('p2', 1, [['b-body', { op: 'w:2', actor: 'human:a' }]])],
                        { watermarks: { h1: { handoff: { w: 1 }, realized: {} } } });
        expect(paintsIn(m, 'p1')).toEqual(['codoc-ink-sent']);       // A was handed off
        expect(paintsIn(m, 'p2')).toEqual(['codoc-ink-unsent']);     // B was not
        m.handle.moveBlock('p1', 'p2');
        expect(ids(m.view.state.doc)).toEqual(['heading:h1', 'heading:h2', 'paragraph:p2', 'paragraph:p1']);
        expect(paintsIn(m, 'p1')).toEqual(['codoc-ink-unsent']);     // now B's
    });
});
