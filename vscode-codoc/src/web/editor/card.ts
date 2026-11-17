/**
 * card.ts: a block of a kind this editor has no node type for.
 *
 * The card shows the kind's name, one text area per Text field the block's
 * layout names, and every other field read-only as a short JSON summary. A
 * kind codoc does not ship also gets a note saying why the block looks
 * generic. An edit in a text area is compared with that field's shadow
 * (`reconcileText`) and becomes text ops on the field's path, recorded like
 * any gesture, so the person is the author of the words.
 *
 * The rows are built once. A new projection updates them in place, so a text
 * area keeps its focus, and its caret stays after the character it sat after,
 * found by identity. While an IME composition is open the block is held (the
 * outbox reads `composing`), so no projection rewrites the field under it;
 * the composed text is compared with the shadow when the composition ends.
 *
 * The shadows live in `CardFields`, keyed by block, outside the view: a view
 * ProseMirror draws again keeps the person's unacked words, and the outbox
 * resolves their provisional refs when the daemon acks.
 */
import type { Node as PMNode } from '@tiptap/pm/model';
import type { EditorView, NodeView } from '@tiptap/pm/view';

import { refsOf, scalars, type Layout, type TextField } from '../calc/projection';
import { reconcileText } from '../calc/recorder';
import { Shadow } from '../calc/shadow';
import { GESTURE_CIDS, RECORDED } from './commands';

/** Per card block: the projected fields its shadows were built from, one
 *  shadow per Text field, and whether an IME composition is open in it. */
export type CardFields = Map<string, { from: unknown; shadows: Map<string, Shadow>; composing?: boolean }>;

/** Kinds codoc ships that have no node type here. Any other kind gets the note. */
const SHIPPED = new Set(['image', 'url', 'pdf', 'screenshot']);
export const UNKNOWN_KIND_NOTE = 'made by a newer codoc or a plugin this editor does not have';

const el = (tag: string, cls: string, text = ''): HTMLElement => {
    const e = document.createElement(tag);
    e.className = cls;
    e.textContent = text;
    return e;
};

const summary = (v: unknown): string => {
    const s = typeof v === 'string' ? v : JSON.stringify(v);
    return s.length > 120 ? `${s.slice(0, 119)}…` : s;
};

/** A text area offset in `was`'s text, moved to the same character in `now`. */
function follow(units: number, was: Shadow | undefined, now: Shadow): number {
    const at = scalars(was ? was.text().slice(0, units) : '').length;
    const ref = at > 0 ? was?.refs[at - 1] : undefined;
    const i = ref ? now.indexOf(ref) : -1;
    const scalar = i >= 0 ? i + 1 : Math.min(at, now.length);
    return now.chars.slice(0, scalar).join('').length;
}

export function cardView(node: PMNode, view: EditorView, cards: CardFields, nextCid: () => string): NodeView {
    let current = node;
    const kind = String(node.attrs.kind);
    const paths = (node.attrs.layout as Layout).text;
    const bid = node.attrs.bid as string;

    /** The entry for this node, its shadows rebuilt only when the projected fields are new. */
    const entryFor = (n: PMNode) => {
        let entry = cards.get(bid);
        if (!entry || entry.from !== n.attrs.fields) {
            const shadows = new Map<string, Shadow>();
            for (const path of paths) {
                const f = (n.attrs.fields as Record<string, unknown>)[path] as TextField | undefined;
                shadows.set(path, f && typeof f === 'object' && 'runs' in f ? Shadow.fromField(f.text, refsOf(f)) : new Shadow());
            }
            entry = { from: n.attrs.fields, shadows, composing: entry?.composing };
            cards.set(bid, entry);
        }
        return entry;
    };

    const commit = (path: string, area: HTMLTextAreaElement) => {
        const shadow = entryFor(current).shadows.get(path);
        if (!shadow) return;
        const { ops, cids } = reconcileText({ bid, path }, area.value, shadow, nextCid);
        if (ops.length) view.dispatch(view.state.tr.setMeta(RECORDED, ops).setMeta(GESTURE_CIDS, cids).setMeta('addToHistory', false));
    };

    const dom = el('div', `codoc-kind codoc-kind-${kind}`);
    dom.contentEditable = 'false';
    dom.dataset.bid = bid;
    dom.dataset.kind = kind;
    const extras = el('div', 'codoc-kind-extras');
    const areas = new Map<string, HTMLTextAreaElement>();
    const rows: HTMLElement[] = [el('div', 'codoc-kind-label', kind)];
    if (!SHIPPED.has(kind)) rows.push(el('div', 'codoc-kind-note', UNKNOWN_KIND_NOTE));
    for (const path of paths) {
        const a = document.createElement('textarea');
        a.className = 'codoc-kind-text';
        a.dataset.path = path;
        a.rows = 2;
        const composing = (on: boolean) => { entryFor(current).composing = on; };
        a.addEventListener('compositionstart', () => composing(true));
        a.addEventListener('input', ev => { if ((ev as InputEvent).isComposing) composing(true); else commit(path, a); });
        a.addEventListener('compositionend', () => { commit(path, a); composing(false); });
        areas.set(path, a);
        const row = el('div', 'codoc-kind-field');
        row.append(el('span', 'codoc-kind-key', path), a);
        rows.push(row);
    }
    dom.append(...rows, extras);

    /** Bring the rows in line with `n`. `was` are the shadows the text areas showed. */
    const fill = (n: PMNode, was?: Map<string, Shadow>) => {
        const shadows = entryFor(n).shadows;
        for (const [path, a] of areas) {
            const now = shadows.get(path)!;
            const value = now.text();
            if (a.value === value) continue;
            const focused = a.ownerDocument.activeElement === a;
            const [s, e] = [a.selectionStart, a.selectionEnd];
            a.value = value;
            if (focused) a.setSelectionRange(follow(s, was?.get(path), now), follow(e, was?.get(path), now));
        }
        const fields = n.attrs.fields as Record<string, unknown>;
        extras.replaceChildren(...Object.entries(fields).filter(([k, v]) => !paths.includes(k) && v !== null && v !== undefined && v !== '')
            .map(([k, v]) => { const row = el('div', 'codoc-kind-field'); row.append(el('span', 'codoc-kind-key', k), el('span', 'codoc-kind-value', summary(v))); return row; }));
    };

    fill(node);
    return {
        dom,
        update(n) {
            if (n.type !== current.type || n.attrs.bid !== bid || n.attrs.kind !== kind) return false;
            const was = cards.get(bid)?.shadows;
            const fresh = n.attrs.fields !== current.attrs.fields;
            current = n;
            if (fresh) fill(n, was);
            return true;
        },
        // The text areas are the card's own; ProseMirror neither handles their events nor reads their DOM.
        stopEvent: ev => ev.target instanceof HTMLTextAreaElement,
        ignoreMutation: () => true,
        destroy() {
            let still = false;
            view.state.doc.forEach(x => { if (x.attrs.bid === bid) still = true; });
            if (!still) cards.delete(bid);
        },
    };
}
