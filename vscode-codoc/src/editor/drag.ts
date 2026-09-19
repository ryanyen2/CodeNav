/**
 * drag.ts — drag handles (09-editor §8).
 *
 * A handle before every block; dropping it above or below another block moves
 * the dragged block, with everything it dominates, through `moveBlock`, which
 * is one recorded `block.move`. No positional bookkeeping survives the drop.
 *
 * The handle is a CSS `::before` on the block, not a widget decoration. A
 * decoration would be three thousand of them in a large tree, and ProseMirror
 * consults a decoration set once per top-level child per view update — a scan
 * from the start of a flat array each time, so N decorated blocks cost ~N²/2
 * comparisons per transaction. That was half the cost of typing, for a mark
 * that never depends on what any block says. What is left here is two DOM
 * events: `mousedown` inside the handle's strip arms the block as a drag
 * source, and the drag ends disarm it — armed only while it is being used,
 * because a permanently draggable contenteditable block cannot be selected in.
 */
import { Plugin, PluginKey } from '@tiptap/pm/state';
import type { EditorView } from '@tiptap/pm/view';

export const dragKey = new PluginKey('codoc-drag');
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
        key: dragKey,
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
                    // TRUE, or ProseMirror's own dragstart runs next and calls
                    // `dataTransfer.clearData()` — erasing the block id — then
                    // sets `view.dragging` to a slice of the SELECTION. The
                    // handle would do nothing with an empty selection, and with
                    // words selected anywhere would move those words to the
                    // drop point as an edit the recorder attributes to the
                    // person. This drag is ours from here.
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
