/**
 * harness.ts — a real editor to drive in jsdom, and what to check afterwards.
 *
 * `mount` runs `mountEditor` over a small projection and returns the view, the
 * shadows, and everything the editor submitted. The gestures here are what a
 * browser delivers: `key` dispatches a keydown through ProseMirror's own
 * handlers, `typeText` dispatches the transaction `readDOMChange` would, and
 * `paste` goes through `handlePaste`. `shadowFaults` is the invariant every
 * test ends on: each block's shadow says what the page says.
 *
 * A test file that imports this needs `@vitest-environment jsdom`.
 */
import { Fragment, Slice, type Node as PMNode } from '@tiptap/pm/model';
import { TextSelection } from '@tiptap/pm/state';
import type { EditorView } from '@tiptap/pm/view';
import { vi } from 'vitest';

import { kindOf, pathOf, type Projection, type ProjectedBlock, type Submission } from '../../../web/calc/projection';
import type { Shadow } from '../../../web/calc/shadow';
import { mountEditor, type EditorHandle } from '../../../web/editor/editor';

// `mountEditor` keeps its shadows to itself. Capture the map as `docFrom` builds it.
let captured: Map<string, Shadow> | null = null;
vi.mock('../../../web/calc/adopt', async (importOriginal) => {
    const actual = await importOriginal<typeof import('../../../web/calc/adopt')>();
    return {
        ...actual,
        docFrom: (...args: Parameters<typeof actual.docFrom>) => {
            const r = actual.docFrom(...args);
            captured = r.shadows;
            return r;
        },
    };
});

// What jsdom does not implement and ProseMirror calls.
if (!Range.prototype.getClientRects) {
    Range.prototype.getClientRects = () => ({ length: 0, item: () => null, [Symbol.iterator]: function* () {} } as unknown as DOMRectList);
    Range.prototype.getBoundingClientRect = () => ({ top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0 } as DOMRect);
}
HTMLElement.prototype.scrollIntoView = () => undefined;
// ProseMirror's mousedown asks which element is under the pointer. No layout
// here, so there is none, and it treats that as a click outside the text.
document.elementFromPoint = () => null;

// ── fixtures ────────────────────────────────────────────────────────────────
let opn = 0;
/** A projected block of any heading, prose or source kind, its text minted by one op. */
export function block(id: string, kind: string, text: string, level = 1, head = 1, extra: Record<string, unknown> = {}): ProjectedBlock {
    const n = Array.from(text).length;
    return { id, kind, level, rank: id, head,
             fields: { [pathOf(kind)]: { text, runs: n ? [{ op: `s:${id}${opn++}`, k0: 0, start: 0, end: n, actor: 'human:a', at: '1', plan: null }] : [],
                                         spans: [], suggest_del: [] }, ...extra } };
}

/** A projected paragraph built from runs, so a projection can keep the
 *  identity of characters the page already has. */
export function para(id: string, head: number, parts: Array<[string, { op: string; actor: string; k0?: number; plan?: string | null }]>): ProjectedBlock {
    let at = 0;
    const runs = parts.map(([text, r]) => {
        const start = at;
        at += Array.from(text).length;
        return { op: r.op, k0: r.k0 ?? 0, start, end: at, actor: r.actor, at: '1', plan: r.plan ?? null };
    });
    return { id, kind: 'paragraph', level: 0, rank: id, head,
             fields: { text: { text: parts.map(x => x[0]).join(''), runs, spans: [], suggest_del: [] } } };
}

/** A plugin block, which the editor shows read-only. */
export function kindBlock(id: string, kind = 'image'): ProjectedBlock {
    return { id, kind, level: 0, rank: id, head: 1, fields: { asset: 'a.png', caption: { text: '', runs: [], spans: [], suggest_del: [] } } };
}

export const proj = (blocks: ProjectedBlock[], extra: Partial<Projection> = {}): Projection =>
    ({ head: 9, frontier: {}, blocks, phases: {}, plans: [], bindings: { by_feature: {}, by_file: {} }, claims: {}, ...extra });

export interface Mounted {
    handle: EditorHandle;
    view: EditorView;
    shadows: Map<string, Shadow>;
    /** Every submission the editor has sent, in order. */
    submitted: Submission[];
    /** Every refusal the editor has reported. */
    conflicts: string[];
    proj: Projection;
    container: HTMLElement;
}

export function mountProj(p: Projection): Mounted {
    captured = null;
    const container = document.createElement('div');
    document.body.append(container);
    const submitted: Submission[] = [];
    const conflicts: string[] = [];
    const handle = mountEditor(container, p, { submit: s => submitted.push(...s), onConflict: (_b, c) => conflicts.push(c) });
    if (!captured) throw new Error('the docFrom mock did not capture the shadows');
    return { handle, view: handle.view, shadows: captured, submitted, conflicts, proj: p, container };
}

export const mount = (blocks: ProjectedBlock[], extra: Partial<Projection> = {}): Mounted => mountProj(proj(blocks, extra));

// ── reading the page ────────────────────────────────────────────────────────
export const ids = (doc: PMNode): string[] => { const out: string[] = []; doc.forEach(x => out.push(`${x.type.name}:${x.attrs.bid}`)); return out; };
export const texts = (doc: PMNode): string[] => { const out: string[] = []; doc.forEach(x => out.push(x.textBetween(0, x.content.size, '\n'))); return out; };
export const kinds = (subs: Submission[]): string[] => subs.map(s => s.kind);

/** The document position `off` UTF-16 units into block `bid`. */
export function posIn(view: EditorView, bid: string, off = 0): number {
    let p = -1;
    view.state.doc.forEach((n, pos) => { if (n.attrs.bid === bid) p = pos + 1 + off; });
    if (p < 0) throw new Error(`no block ${bid}`);
    return p;
}

/** Each way a block's shadow disagrees with the page. Empty when they agree. */
export function shadowFaults(m: Mounted): string[] {
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

// ── gestures ────────────────────────────────────────────────────────────────
export const select = (view: EditorView, from: number, to = from): void =>
    view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, from, to)));

/** A keydown on the editor. Returns whether a handler took it. */
export function key(view: EditorView, name: string, code: number, init: KeyboardEventInit = {}): boolean {
    const ev = new window.KeyboardEvent('keydown', { key: name, keyCode: code, bubbles: true, cancelable: true, ...init } as KeyboardEventInit);
    view.dom.dispatchEvent(ev);
    return ev.defaultPrevented;
}

/** Typed text over the selection, as `readDOMChange` dispatches it. */
export const typeText = (view: EditorView, text: string): void => view.dispatch(view.state.tr.insertText(text));

/** Paste one block per string. Returns whether the editor's `handlePaste`
 *  took it; when it did not, ProseMirror's default paste runs. */
export function paste(m: Mounted, paras: string[], headingFirst = false): boolean {
    const schema = m.view.state.schema;
    const nodes = paras.map((t, i) => (headingFirst && i === 0 ? schema.nodes.heading : schema.nodes.paragraph)
        .create(null, t ? schema.text(t) : undefined));
    return pasteSlice(m, new Slice(Fragment.from(nodes), 1, 1));
}

/** A paste whose clipboard holds `plain` (and `html`), with the slice
 *  ProseMirror parses from plain text: one paragraph per line. */
export function pasteText(m: Mounted, plain: string, html = ''): boolean {
    const schema = m.view.state.schema;
    const nodes = plain.split(/\r?\n+/).map(l => schema.nodes.paragraph.create(null, l ? schema.text(l) : undefined));
    const data = { getData: (t: string) => (t === 'text/plain' ? plain : t === 'text/html' ? html : '') };
    return pasteSlice(m, new Slice(Fragment.from(nodes), 1, 1), Object.assign(new window.Event('paste'), { clipboardData: data }));
}

export function pasteSlice(m: Mounted, slice: Slice, event: Event = new window.Event('paste')): boolean {
    const handled = m.view.someProp('handlePaste', f => f(m.view, event as ClipboardEvent, slice)) ?? false;
    if (!handled) m.view.dispatch(m.view.state.tr.replaceSelection(slice));
    return !!handled;
}

/** Type `marker` at the start of block `bid`, then a space through the input
 *  rules, as the keystroke would. Returns whether a rule took the space. */
export function typeRule(view: EditorView, bid: string, marker: string): boolean {
    select(view, posIn(view, bid, 0));
    typeText(view, marker);
    const at = posIn(view, bid, marker.length);
    return view.someProp('handleTextInput', f => f(view, at, at, ' ', () => view.state.tr.insertText(' ', at, at))) ?? false;
}

/** The submissions as op bodies, without client and batch ids. */
export const bodies = (subs: Submission[]) => subs.map(({ kind, target, payload }) => ({ kind, target, payload }));

/** Each block as its store kind, with `:level` for a heading or a list item. */
export const shape = (doc: PMNode): string[] => {
    const out: string[] = [];
    doc.forEach(n => out.push(n.attrs.level ? `${kindOf(n)}:${n.attrs.level}` : kindOf(n)));
    return out;
};
