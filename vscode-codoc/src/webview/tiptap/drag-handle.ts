/**
 * drag-handle.ts — the grip beside a feature, and the line showing where it lands.
 *
 * All the geometry lives in feature-drag.ts and is unit-tested; this is the thin
 * interaction shell over it. Two choices worth stating:
 *
 * POINTER events, not HTML5 drag-and-drop. The native drag image is not stylable
 * across hosts, its `dragover` cadence is coarse enough to make the drop line
 * stutter, and it fires inside a contenteditable in ways ProseMirror already
 * handles for text — which is a different gesture from moving a whole feature.
 *
 * EVENT DELEGATION, not a listener per handle. Binding a closure into each widget
 * would mean re-creating every handle whenever the document changed, just to give
 * them a fresh view reference — a dispatched transaction per keystroke, which is
 * exactly the kind of churn the decoration policy exists to remove. The handle
 * carries its position in a data attribute instead, and one listener reads it.
 *
 * Handles are widget decorations rather than nodes in the document, so they can
 * never be typed into, selected, copied, or serialised into `tree.codoc`.
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import type { Node as PMModelNode } from '@tiptap/pm/model';
import { Decoration, DecorationSet, EditorView } from '@tiptap/pm/view';
import {
    featureSlices, sliceAt, dropPositions, nearestDrop, moveSlice, nudgeTarget,
    type FeatureSlice,
} from './feature-drag';
import { nextDecorations } from './decoration-policy';

const dragKey = new PluginKey<DragState>('codocDragHandles');
const SET_DROP = 'codocDropTarget';

/** Below this the press is a click, not a zero-length drag — grabbing a handle
 *  to look at a feature must never nudge it. */
const DRAG_THRESHOLD_PX = 4;

interface DragState { handles: DecorationSet; dropAt: number | null }

export const HANDLE_ATTR = 'data-codoc-feature-pos';

function handleWidget(pos: number): HTMLElement {
    const grip = document.createElement('span');
    grip.className = 'ce-drag-handle';
    grip.contentEditable = 'false';
    grip.setAttribute(HANDLE_ATTR, String(pos));
    grip.setAttribute('role', 'button');
    grip.setAttribute('aria-label', 'Move feature');
    grip.title = 'Drag to move · ⌥⌘↑ / ⌥⌘↓';
    return grip;
}

function buildHandles(doc: PMModelNode): DecorationSet {
    return DecorationSet.create(doc, featureSlices(doc).map(s =>
        Decoration.widget(s.from + 1, () => handleWidget(s.from),
                          { side: -1, key: `drag-${s.from}` })));
}

export function dragHandlePlugin(): Plugin<DragState> {
    let dragging: FeatureSlice | null = null;
    let targets: number[] = [];
    let armed = false;
    let startX = 0, startY = 0;

    return new Plugin<DragState>({
        key: dragKey,
        state: {
            init: (_c, state) => ({ handles: buildHandles(state.doc), dropAt: null }),
            apply: (tr, prev, _o, newState) => {
                const drop = tr.getMeta(SET_DROP);
                return {
                    // Structure-keyed: a handle belongs to a heading, so typing
                    // moves it rather than changing which handles exist. Rebuilding
                    // on every keystroke is what decoration-policy exists to stop.
                    handles: nextDecorations(tr, prev.handles, false,
                                             () => buildHandles(newState.doc)),
                    dropAt: drop === undefined ? (tr.docChanged ? null : prev.dropAt)
                                               : (drop as number | null),
                };
            },
        },
        props: {
            decorations(state) {
                const s = dragKey.getState(state);
                if (!s) return DecorationSet.empty;
                if (s.dropAt == null) return s.handles;
                return s.handles.add(state.doc, [
                    Decoration.widget(s.dropAt, () => {
                        const line = document.createElement('div');
                        line.className = 'ce-drop-line';
                        line.contentEditable = 'false';
                        return line;
                    }, { side: -1, key: 'drop-line' }),
                ]);
            },
            handleDOMEvents: {
                pointerdown: (view, ev) => {
                    const el = (ev.target as HTMLElement | null)?.closest?.(`[${HANDLE_ATTR}]`);
                    if (!el) return false;
                    const slice = sliceAt(view.state.doc, Number(el.getAttribute(HANDLE_ATTR)));
                    if (!slice) return false;

                    dragging = slice;
                    targets = dropPositions(view.state.doc, slice);
                    startX = (ev as PointerEvent).clientX;
                    startY = (ev as PointerEvent).clientY;
                    armed = false;

                    const setDrop = (at: number | null) => {
                        if (dragKey.getState(view.state)?.dropAt === at) return;
                        view.dispatch(view.state.tr.setMeta(SET_DROP, at));
                    };
                    const finish = () => {
                        window.removeEventListener('pointermove', move);
                        window.removeEventListener('pointerup', up);
                        window.removeEventListener('keydown', onKey, true);
                        document.body.classList.remove('ce-dragging-feature');
                        dragging = null; armed = false;
                    };
                    const move = (e: PointerEvent) => {
                        if (!dragging) return;
                        if (!armed) {
                            if (Math.hypot(e.clientX - startX, e.clientY - startY) < DRAG_THRESHOLD_PX) return;
                            armed = true;
                            document.body.classList.add('ce-dragging-feature');
                        }
                        const at = view.posAtCoords({ left: e.clientX, top: e.clientY });
                        if (at) setDrop(nearestDrop(targets, at.pos));
                    };
                    const up = () => {
                        const slice2 = dragging;
                        const to = dragKey.getState(view.state)?.dropAt ?? null;
                        const moved = armed;
                        finish();
                        setDrop(null);
                        if (!moved || !slice2 || to == null) return;
                        const tr = moveSlice(view.state, slice2, to);
                        if (tr) view.dispatch(tr);
                    };
                    const onKey = (e: KeyboardEvent) => {
                        if (e.key !== 'Escape') return;
                        // Escape abandons the drag with nothing written — the
                        // gesture must be abortable after it looks committed.
                        e.preventDefault(); e.stopPropagation();
                        finish(); setDrop(null);
                    };

                    window.addEventListener('pointermove', move);
                    window.addEventListener('pointerup', up);
                    window.addEventListener('keydown', onKey, true);
                    ev.preventDefault();   // no caret placement / text selection
                    return true;
                },
            },
        },
    });
}

/** Move the feature containing the caret one step among its siblings.
 *
 *  The keyboard half of the gesture. Not an afterthought: a drag is mouse-only,
 *  and restructuring a tree must not be. */
export function nudgeFeature(view: EditorView, dir: -1 | 1): boolean {
    const here = view.state.selection.$from.before(1);
    // The caret is usually in a feature's PROSE, not its heading, so resolve to
    // the slice that contains it rather than requiring an exact heading hit.
    const slice = featureSlices(view.state.doc).filter(s => s.from <= here && here < s.to).pop();
    if (!slice) return false;
    const to = nudgeTarget(view.state.doc, slice, dir);
    if (to == null) return false;
    const tr = moveSlice(view.state, slice, to);
    if (!tr) return false;
    view.dispatch(tr.scrollIntoView());
    return true;
}

export const DragHandle = Extension.create({
    name: 'codocDragHandle',
    addProseMirrorPlugins() { return [dragHandlePlugin()]; },
    addKeyboardShortcuts() {
        return {
            'Mod-Alt-ArrowUp': () => nudgeFeature(this.editor.view, -1),
            'Mod-Alt-ArrowDown': () => nudgeFeature(this.editor.view, 1),
        };
    },
});
