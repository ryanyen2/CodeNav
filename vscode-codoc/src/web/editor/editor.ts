/**
 * editor.ts: `mountEditor`, which assembles the editor over a projection.
 *
 * The parts, and where each lives:
 *
 *   schema.ts      the nodes and marks
 *   card.ts        the card for a kind without a node type, and its text areas
 *   keymap.ts      which key runs which gesture
 *   commands.ts    the gestures: each emits its ops and changes the document
 *   paste.ts       what a paste brings, as Markdown blocks or the clipboard's own
 *   guards.ts      refuses unrecorded structural changes; keeps the page NFC
 *   outbox.ts      records transactions into ops, batches them, takes acks,
 *                  and says which blocks are held
 *   adoption.ts    brings the page in line with a new projection
 *   decorations.ts draws ink, ground, plan words, phase markers and list numbers
 *   drag.ts, find.ts, menus.ts   the drag handle, find and replace, the `/`
 *                  and `@` menus and the link field
 *
 * `mountEditor` builds the document and a shadow per block from the first
 * projection, wires those parts to one ProseMirror view, and returns the
 * handle the page uses (app.ts).
 *
 * Two kinds of transaction are rerouted before they apply. One that replaces
 * a selection spanning blocks (typing over it, cutting it) becomes the
 * `deleteSelection` gesture followed by the typed text. One applied while an
 * IME composition is open is recorded only when the composition ends, as one
 * insert of the composed text (outbox.ts).
 *
 * ProseMirror's own history is not installed. Undo submits the recorded
 * inverse of a batch as new ops, so the page and the store share one history.
 */
import { EditorState, Plugin, type Transaction } from '@tiptap/pm/state';
import { ReplaceStep } from '@tiptap/pm/transform';
import { EditorView } from '@tiptap/pm/view';

import { docFrom } from '../calc/adopt';
import { newBid, newCid } from '../calc/ids';
import { isOneLine, isProvisional, kindOf, pathOf, textField, type Ack, type CharRef, type Projection, type Submission } from '../calc/projection';
import { scalarOffset } from '../calc/recorder';
import { adopter, type Adopt } from './adoption';
import { RECORDED, deleteFeature, deleteSelection, moveBlock, pasteBlocks, selectedBlocks, type Env } from './commands';
import { Paints, REPAINT, listNumbers, type Shown } from './decorations';
import { dragPlugin } from './drag';
import { FindWidget, findPlugin } from './find';
import { nfcGuard, sameStructure, structureGuard } from './guards';
import { gestureKeys } from './keymap';
import { LinkInput, Menus } from './menus';
import { Outbox } from './outbox';
import { clipboardPieces } from './paste';
import { cardView, type CardFields } from './card';
import { cardFence, codocSchema } from './schema';

export interface EditorHost {
    /** Send submissions to the daemon's inbox. */
    submit(subs: Submission[]): void;
    /** The caret moved into this feature. */
    onFeature?(fid: string | null): void;
    /** Open the code a citation names. */
    openCite?(file: string, symbol: string): void;
    /** A gesture was not performed (`bid` is empty and `conflict` says why), or
     *  the daemon refused an op and its block was reloaded. */
    onConflict?(bid: string, conflict: string): void;
    /** The viewer's actor as the projection's runs name it (`status.actor`). Call
     *  `repaint` when it changes. */
    actor?(): string;
}

export interface EditorHandle {
    view: EditorView;
    adopt: Adopt;
    ack(acks: Ack[]): void;
    /** Send queued ops now instead of waiting out the settle. */
    flush(): void;
    /** Show who wrote each run of words (the History stance). */
    setHistory(on: boolean): void;
    /** Redraw every paint, because the viewer changed. */
    repaint(): void;
    find(replace: boolean): void;
    /** The selection as a comment anchor: its block, its words, and the first and last character. */
    selectionAnchor(): { bid: string; text: string; start: CharRef; end: CharRef } | null;
    deleteFeature(fid: string): void;
    moveBlock(bid: string, afterBid: string | 'START', levelDelta?: number): void;
}

export function mountEditor(container: HTMLElement, proj: Projection, host: EditorHost): EditorHandle {
    const schema = codocSchema();
    const { doc, shadows } = docFrom(schema, proj);
    const viewer = () => host.actor?.() ?? '';
    const shown: Shown = { projection: proj, history: false, viewer };
    const cards: CardFields = new Map();
    const env: Env = {
        shadows, nextBid: newBid, nextCid: newCid, refuse: why => host.onConflict?.('', why), projection: () => shown.projection, cards,
        // Unacked, applied for this page, or in one of the block's projected runs by the viewer.
        mine: bid => {
            const b = shown.projection.blocks.find(x => x.id === bid);
            const ops = new Set((b ? textField(b, pathOf(b.kind))?.runs ?? [] : []).filter(x => x.actor === viewer()).map(x => x.op));
            return r => isProvisional(r) || outbox.applied.has(r[0]) || ops.has(r[0]);
        },
    };
    const paints = new Paints(shown);
    const outbox = new Outbox(shadows, subs => host.submit(subs), bid => paints.mark(bid), cards);
    /** Record what a finished IME composition left on the page. */
    const settle = () => { if (!view.composing) outbox.settle(view.state.doc); };

    let menus: Menus | null = null;
    let link: LinkInput | null = null;
    const state = EditorState.create({
        schema,
        doc,
        plugins: [
            new Plugin({ props: {
                handleKeyDown: (_v, ev) => { settle(); return menus?.handleKeyDown(ev) ?? false; },
                handleDOMEvents: { compositionend: () => { window.setTimeout(settle, 50); return false; } },
            } }),
            structureGuard(),
            nfcGuard(),
            ...gestureKeys(env, () => link?.show() ?? false),
            paints.plugin,
            listNumbers(),
            findPlugin(),
            dragPlugin((bid, after) => { const g = moveBlock(view.state, env, bid, after); if (g) view.dispatch(g.tr); }),
        ],
    });

    /** Whether `tr` replaces a selection that spans blocks with inline text or
     *  nothing: typing over it, or cutting it. */
    const replacesAcross = (st: EditorState, tr: Transaction): boolean =>
        !tr.getMeta(RECORDED) && tr.docChanged && tr.getMeta('uiEvent') !== 'drop' && !st.selection.empty
        && tr.steps.length === 1 && tr.steps[0] instanceof ReplaceStep
        && selectedBlocks(st).length > 1 && !sameStructure(st.doc, tr.doc);

    const view = new EditorView(container, {
        state,
        nodeViews: { kindBlock: (node, v) => cardView(node, v, cards, newCid) },
        dispatchTransaction(tr) {
            if (replacesAcross(view.state, tr)) {
                settle();
                const slice = (tr.steps[0] as ReplaceStep).slice;
                const typed = slice.content.textBetween(0, slice.content.size, ' ');
                // Typed text, or an IME composition whose first change only removes the selection.
                const g = deleteSelection(view.state, env, !!typed || view.composing);
                if (!g) return;
                view.dispatch(g.tr);
                if (typed) view.dispatch(view.state.tr.insertText(typed));
                return;
            }
            // Record what the page took, not what it was asked to take. The
            // structural guard can refuse a transaction, and a refused one
            // must leave no ops and no change to the shadows.
            const { state: next, transactions } = view.state.applyTransaction(tr);
            for (const applied of transactions) outbox.record(applied, view.composing);
            view.updateState(next);
            settle();
            if (tr.selectionSet || tr.docChanged) { host.onFeature?.(featureAt(next.selection.from)); menus?.update(); }
        },
        // A copied card is its `codoc:` fence in the plain text, like the export writes it.
        clipboardTextSerializer: slice => slice.content.textBetween(0, slice.content.size, '\n\n',
                                                                    n => (n.type.name === 'kindBlock' ? cardFence(n) : '')),
        handlePaste(_v, event, slice) {
            settle();
            const plain = (event.clipboardData?.getData('text/plain') ?? '').replace(/\r\n?/g, '\n');
            const { $from, $to } = view.state.selection;
            if ($from.parent.type.spec.code && $from.sameParent($to)) {
                // A table, a diagram or a code block takes the text as it is,
                // newlines and indentation included: typed text the recorder reads.
                const text = plain || slice.content.textBetween(0, slice.content.size, '\n');
                if (text) view.dispatch(view.state.tr.insertText(text));
                return true;
            }
            const pieces = clipboardPieces(slice, plain, event.clipboardData?.getData('text/html') ?? '');
            if (!pieces) return false;                  // inline words only: the recorder reads them
            const g = pasteBlocks(view.state, env, pieces);
            if (g) view.dispatch(g.tr);
            return true;
        },
        handleClickOn(_v, _pos, node, _np, event) {
            const cite = (event.target as HTMLElement | null)?.closest?.('[data-cite]') as HTMLElement | null;
            if (cite && host.openCite) {
                const m = node.marks.find(x => x.type.name === 'cite') ?? null;
                if (m) { host.openCite(m.attrs.file, m.attrs.symbol); return true; }
            }
            return false;
        },
    });

    menus = new Menus(view, env, () => shown.projection, container);
    link = new LinkInput(view, container);
    const finder = new FindWidget(view, container);
    const adopt = adopter(view, shadows, outbox, paints, shown);
    const repaint = () => { paints.mark(); view.dispatch(view.state.tr.setMeta(REPAINT, true)); };

    /** The feature a position is in: the nearest heading at or above it. */
    function featureAt(pos: number): string | null {
        const $p = view.state.doc.resolve(Math.min(pos, view.state.doc.content.size));
        if ($p.depth < 1) return null;
        for (let i = $p.index(0); i >= 0; i--) {
            const n = view.state.doc.child(i);
            if (n.type.name === 'heading') return n.attrs.bid as string;
        }
        return null;
    }

    return {
        view,
        adopt,
        ack(acks) {
            const refused = outbox.ack(acks);
            for (const [bid, conflict] of refused) host.onConflict?.(bid, conflict);
            // Adopt the projection in hand again, always. A block this ack
            // released is re-read exactly when the projection already contains
            // its op, and a refused block once none of its ops is in flight.
            adopt(shown.projection);
        },
        flush: () => { settle(); outbox.flush(); },
        setHistory(on) {
            shown.history = on;
            repaint();
        },
        repaint,
        find: replace => finder.open(replace),
        selectionAnchor() {
            const { from, to, empty } = view.state.selection;
            const $f = view.state.doc.resolve(from), $t = view.state.doc.resolve(to);
            // A source block holds no marks, so no comment anchors in one.
            if (empty || $f.depth !== 1 || $t.depth !== 1 || $f.parent !== $t.parent || !isOneLine(kindOf($f.parent))) return null;
            const bid = $f.parent.attrs.bid as string;
            const sh = shadows.get(bid);
            if (!sh) return null;
            const a = scalarOffset($f.parent, $f.parentOffset), b = scalarOffset($t.parent, $t.parentOffset);
            if (b <= a) return null;
            return { bid, text: sh.chars.slice(a, b).join(''), start: sh.ref(a), end: sh.ref(b - 1) };
        },
        deleteFeature(fid) {
            const g = deleteFeature(view.state, env, fid);
            if (g) view.dispatch(g.tr);
        },
        moveBlock(bid, after, delta = 0) {
            const g = moveBlock(view.state, env, bid, after, delta);
            if (g) view.dispatch(g.tr);
        },
    };
}
