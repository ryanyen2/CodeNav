/**
 * drag.ts — drag handles (09-editor §8).
 *
 * A handle before every block; dropping it above or below another block moves
 * the dragged block, with everything it dominates, through `moveBlock`, which
 * is one recorded `block.move`. No positional bookkeeping survives the drop.
 */
import type { Node as PMNode } from '@tiptap/pm/model';
import { Plugin, PluginKey } from '@tiptap/pm/state';

import { REPLACED } from './commands';
import { Decoration, DecorationSet, type EditorView } from '@tiptap/pm/view';

export const dragKey = new PluginKey('codoc-drag');
const MIME = 'application/x-codoc-bid';

function handles(doc: PMNode): DecorationSet {
    const decos: Decoration[] = [];
    doc.forEach((block, pos) => {
        const bid = block.attrs.bid as string | null;
        if (!bid) return;
        decos.push(Decoration.widget(pos + 1, () => {
            const h = document.createElement('span');
            h.className = 'codoc-handle';
            h.title = 'drag to move';
            h.draggable = true;
            h.contentEditable = 'false';
            h.addEventListener('dragstart', ev => { ev.dataTransfer?.setData(MIME, bid); ev.dataTransfer!.effectAllowed = 'move'; });
            return h;
        }, { side: -2, key: `handle:${bid}`, ignoreSelection: true }));
    });
    return DecorationSet.create(doc, decos);
}


export function dragPlugin(move: (bid: string, after: string | 'START') => void): Plugin<DecorationSet> {
    let over: HTMLElement | null = null;
    const clear = () => { over?.classList.remove('is-drop-above', 'is-drop-below'); over = null; };
    return new Plugin<DecorationSet>({
        key: dragKey,
        // One handle per block, and a block's handle does not depend on what is
        // selected or on what any of its words say. Building three thousand
        // widgets on every transaction — a keystroke, a selection move, an ack —
        // was half the cost of typing in a large tree; they are kept and mapped,
        // and rebuilt only when the set of blocks changes.
        state: {
            init: (_c, state) => handles(state.doc),
            apply(tr, set: DecorationSet) {
                if (!tr.docChanged) return set;
                if (tr.getMeta(REPLACED)) return handles(tr.doc);
                const before = new Set<string>(), after = new Set<string>();
                tr.before.forEach(n => { if (n.attrs.bid) before.add(n.attrs.bid as string); });
                tr.doc.forEach(n => { if (n.attrs.bid) after.add(n.attrs.bid as string); });
                const same = before.size === after.size && [...before].every(b => after.has(b));
                return same ? set.map(tr.mapping, tr.doc) : handles(tr.doc);
            },
        },
        props: {
            decorations(state) {
                return dragKey.getState(state) as DecorationSet;
            },
            handleDOMEvents: {
                dragover(view: EditorView, ev: DragEvent) {
                    if (!ev.dataTransfer?.types.includes(MIME)) return false;
                    ev.preventDefault();
                    const t = targetBlock(view, ev);
                    clear();
                    if (t) { over = t.dom; over.classList.add(t.below ? 'is-drop-below' : 'is-drop-above'); }
                    return true;
                },
                dragleave() { clear(); return false; },
                drop(view: EditorView, ev: DragEvent) {
                    const bid = ev.dataTransfer?.getData(MIME);
                    clear();
                    if (!bid) return false;
                    ev.preventDefault();
                    const t = targetBlock(view, ev);
                    if (!t || t.bid === bid) return true;
                    const after = t.below ? t.bid : previousBid(view, t.bid);
                    if (after !== bid) move(bid, after);
                    return true;
                },
            },
        },
    });
}

function targetBlock(view: EditorView, ev: DragEvent): { bid: string; dom: HTMLElement; below: boolean } | null {
    const el = (ev.target as HTMLElement | null)?.closest?.('[data-bid]') as HTMLElement | null;
    if (!el || !view.dom.contains(el)) return null;
    const r = el.getBoundingClientRect();
    return { bid: el.dataset.bid!, dom: el, below: ev.clientY > r.top + r.height / 2 };
}

function previousBid(view: EditorView, bid: string): string | 'START' {
    let prev: string | 'START' = 'START';
    let found: string | 'START' = 'START';
    view.state.doc.forEach(n => { if (n.attrs.bid === bid) found = prev; prev = n.attrs.bid as string; });
    return found;
}
