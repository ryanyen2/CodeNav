/**
 * drag.ts: drag handles.
 *
 * A handle before every block; dropping it above or below another block moves
 * the dragged block, with everything it dominates, through `moveBlock`, which
 * is one recorded `block.move`.
 *
 * The handle is a CSS `::before` on the block, not a widget decoration. It
 * never depends on what a block says, and one widget per block would cost
 * every transaction: ProseMirror scans the decoration set once per top-level
 * child on each view update, so N decorated blocks are about N²/2 comparisons.
 *
 * `mousedown` inside the handle's strip makes the block draggable, and the end
 * of the drag makes it not draggable again. A contenteditable block that
 * stayed draggable could not be selected in.
 */
import { Plugin } from '@tiptap/pm/state';
import type { EditorView } from '@tiptap/pm/view';

const MIME = 'application/x-codoc-bid';
const HANDLE_LEFT = -34, HANDLE_WIDTH = 12, HANDLE_TOP = 0, HANDLE_HEIGHT = 22;   // must match app.css

/** Whether this pointer is over the block's handle strip rather than its text. */
function onHandle(el: HTMLElement, ev: MouseEvent): boolean {
    const r = el.getBoundingClientRect();
    const x = ev.clientX - r.left, y = ev.clientY - r.top;
    return x >= HANDLE_LEFT && x <= HANDLE_LEFT + HANDLE_WIDTH && y >= HANDLE_TOP && y <= HANDLE_TOP + HANDLE_HEIGHT;
}

export function dragPlugin(move: (bid: string, after: string | 'START') => void): Plugin {
    let over: HTMLElement | null = null;
    let armed: HTMLElement | null = null;
    const clear = () => { over?.classList.remove('is-drop-above', 'is-drop-below'); over = null; };
    const disarm = () => { if (armed) { armed.draggable = false; armed = null; } };
    return new Plugin({
        props: {
            handleDOMEvents: {
                mousedown(_view: EditorView, ev: MouseEvent) {
                    disarm();
                    const el = (ev.target as HTMLElement | null)?.closest?.('[data-bid]') as HTMLElement | null;
                    if (!el || !onHandle(el, ev)) return false;
                    armed = el;
                    el.draggable = true;
                    return false;                     // the browser still starts the drag from here
                },
                mouseup() { disarm(); return false; },
                dragstart(_view: EditorView, ev: DragEvent) {
                    const el = (ev.target as HTMLElement | null)?.closest?.('[data-bid]') as HTMLElement | null;
                    if (!el || el !== armed || !el.dataset.bid) return false;
                    ev.dataTransfer?.setData(MIME, el.dataset.bid);
                    if (ev.dataTransfer) ev.dataTransfer.effectAllowed = 'move';
                    // Return true so ProseMirror's own dragstart does not run.
                    // It would clear the data transfer, erasing the block id,
                    // and set `view.dragging` to a slice of the selection, so
                    // the drop would move whatever words were selected.
                    return true;
                },
                dragend() { clear(); disarm(); return false; },
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
                    disarm();
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
