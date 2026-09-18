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

import type { OpBody, Projection, ProjectedBlock, Submission } from '../calc/projection';
import type { Shadow } from '../calc/shadow';
import { nextUndo, textAt, type HistoryWindow } from '../calc/window';
import { mountEditor, type EditorHandle } from '../editor/editor';
import { Timeline } from '../editor/timeline';
import { anchorsOf, threadsOf } from '../editor/comments';


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

interface Mounted { handle: EditorHandle; view: EditorView; shadows: Map<string, Shadow>; submitted: Submission[]; conflicts: string[]; proj: Projection }

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
    return { handle, view: handle.view, shadows: captured, submitted, conflicts, proj: p };
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
function paste(m: Mounted, paras: string[]): boolean {
    const schema = m.view.state.schema;
    const nodes = paras.map(t => schema.nodes.paragraph.create(null, t ? schema.text(t) : undefined));
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

// ── R. round trip: the real editor's ops → replayed by the real applier ─────
// Driven by `tests/loops/test_roundtrip.py`, which writes the base projection,
// sets CODOC_ROUNDTRIP and replays what this writes through the daemon. It is
// the one check that says the page and the store agree about the same edits;
// on its own, vitest skips it.
describe.runIf(process.env.CODOC_ROUNDTRIP)('R. the round trip', () => {
    const dir = process.env.CODOC_ROUNDTRIP ?? '';
    const SEEDS = 24, STEPS = 14;
    it(`R1: ${SEEDS} seeds × ${STEPS} gestures through the real view keep every shadow honest, and their ops are written out for the applier`, () => {
        const p0 = JSON.parse(fs.readFileSync(`${dir}/projection.json`, 'utf8')) as Projection;
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
            fs.writeFileSync(`${dir}/seed${String(seed).padStart(2, '0')}.json`, JSON.stringify({ seed, log, subs: m.submitted, expect: expect_ }));
        }
        expect(failures).toEqual([]);
    });
});

