/**
 * card.ts: a block of a kind this editor has no node type for.
 *
 * The card shows the kind's name, one text area per Text field the block's
 * layout names, and every other field read-only as a short JSON summary. A
 * kind codoc does not ship also gets a note saying why the block looks
 * generic. An edit in a text area is compared with that field's shadow
 * (`reconcileText`) and becomes text ops on the field's path, recorded like
 * any gesture, so the person is the author of the words. While an IME
 * composition is open nothing is compared.
 *
 * The shadows live in `CardFields`, keyed by block, outside the view: a view
 * ProseMirror draws again keeps the person's unacked words, and the outbox
 * resolves their provisional refs when the daemon acks.
 */
import type { Node as PMNode } from '@tiptap/pm/model';
import type { EditorView, NodeView } from '@tiptap/pm/view';

import { refsOf, type TextField } from '../calc/projection';
import { reconcileText } from '../calc/recorder';
import { Shadow } from '../calc/shadow';
import { GESTURE_CIDS, RECORDED } from './commands';

/** Per card block: the projected fields its shadows were built from, and one shadow per Text field. */
export type CardFields = Map<string, { from: unknown; shadows: Map<string, Shadow> }>;

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

export function cardView(node: PMNode, view: EditorView, cards: CardFields, nextCid: () => string): NodeView {
    let current = node;
    const dom = el('div', 'codoc-kind');
    dom.contentEditable = 'false';
    const areas = new Map<string, HTMLTextAreaElement>();

    /** The field shadows for this node, rebuilt only when the projected fields are new. */
    const shadowsFor = (n: PMNode): Map<string, Shadow> => {
        const bid = n.attrs.bid as string;
        let entry = cards.get(bid);
        if (!entry || entry.from !== n.attrs.fields) {
            const shadows = new Map<string, Shadow>();
            for (const path of n.attrs.text as string[]) {
                const f = (n.attrs.fields as Record<string, unknown>)[path] as TextField | undefined;
                shadows.set(path, f && typeof f === 'object' && 'runs' in f ? Shadow.fromField(f.text, refsOf(f)) : new Shadow());
            }
            entry = { from: n.attrs.fields, shadows };
            cards.set(bid, entry);
        }
        return entry.shadows;
    };

    const commit = (path: string, area: HTMLTextAreaElement) => {
        const shadow = shadowsFor(current).get(path);
        if (!shadow) return;
        const { ops, cids } = reconcileText({ bid: current.attrs.bid as string, path }, area.value, shadow, nextCid);
        if (ops.length) view.dispatch(view.state.tr.setMeta(RECORDED, ops).setMeta(GESTURE_CIDS, cids).setMeta('addToHistory', false));
    };

    const draw = (n: PMNode) => {
        const kind = String(n.attrs.kind);
        const text = n.attrs.text as string[];
        dom.className = `codoc-kind codoc-kind-${kind}`;
        dom.dataset.bid = n.attrs.bid;
        dom.dataset.kind = kind;
        const rows: HTMLElement[] = [el('div', 'codoc-kind-label', kind)];
        if (!SHIPPED.has(kind)) rows.push(el('div', 'codoc-kind-note', UNKNOWN_KIND_NOTE));
        const shadows = shadowsFor(n);
        for (const path of text) {
            // Keep a text area the person may be in; only its value follows the shadow.
            let area = areas.get(path);
            if (!area) {
                area = document.createElement('textarea');
                area.className = 'codoc-kind-text';
                area.dataset.path = path;
                area.rows = 2;
                const a = area;
                a.addEventListener('input', ev => { if (!(ev as InputEvent).isComposing) commit(path, a); });
                a.addEventListener('compositionend', () => commit(path, a));
                areas.set(path, a);
            }
            const value = shadows.get(path)!.text();
            if (area.value !== value) area.value = value;
            const row = el('div', 'codoc-kind-field');
            row.append(el('span', 'codoc-kind-key', path), area);
            rows.push(row);
        }
        for (const [k, v] of Object.entries(n.attrs.fields as Record<string, unknown>)) {
            if (text.includes(k) || v === null || v === undefined || v === '') continue;
            const row = el('div', 'codoc-kind-field');
            row.append(el('span', 'codoc-kind-key', k), el('span', 'codoc-kind-value', summary(v)));
            rows.push(row);
        }
        dom.replaceChildren(...rows);
    };

    draw(node);
    return {
        dom,
        update(n) {
            if (n.type !== current.type || n.attrs.bid !== current.attrs.bid) return false;
            const fresh = n.attrs.fields !== current.attrs.fields;
            current = n;
            if (fresh) draw(n);
            return true;
        },
        // The text areas are the card's own; ProseMirror neither handles their events nor reads their DOM.
        stopEvent: ev => ev.target instanceof HTMLTextAreaElement,
        ignoreMutation: () => true,
        destroy() {
            let still = false;
            view.state.doc.forEach(x => { if (x.attrs.bid === current.attrs.bid) still = true; });
            if (!still) cards.delete(current.attrs.bid as string);
        },
    };
}
