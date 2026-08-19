/**
 * comment-anchor.ts — a comment you can actually find (W10).
 *
 * ## The bug this exists for
 *
 * You selected a sentence, wrote a note, sent it — and then the note was nowhere. The
 * commented words kept a dotted underline, and that was all: the thread's card only ever
 * rendered when the right margin happened to be wide enough to hold one, which on most
 * real window widths it is not. So the common outcome of commenting was a highlight with
 * nothing behind it, and the only tooltip in reach belonged to a different layer (the
 * hold rail's "Queued for the agent…"), which made it look as though the note had been
 * swallowed by the queue.
 *
 * ## The design
 *
 * The anchor gets a small mark of its own — an affordance, not decoration. It says a
 * conversation is attached here, it survives every window width, and it is the one place
 * a reader has to look:
 *
 *   - **hover** → the thread, as a transient card: what you wrote, and what came back.
 *   - **click**  → the same card, pinned, so you can read a long reply or reach its
 *     actions without keeping the pointer still.
 *
 * This is where the comment UI lived originally (see `comment-decorations.ts`'s header);
 * it was moved wholesale into the margin, and the margin turned out not to fit. The
 * margin card stays where there is room for it — this is what makes the thread reachable
 * when there is not.
 *
 * Pure builder + a widget factory, like every other layer here: the factory only runs at
 * render, so the geometry is unit-testable without a view.
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { Node as PMModelNode } from '@tiptap/pm/model';
import type { CommentThread } from '../../state/comment-model';
import { nextDecorations } from './decoration-policy';

export const COMMENT_ANCHORS_UPDATED = 'codocCommentAnchorsUpdated';
const anchorKey = new PluginKey('codocCommentAnchors');

export interface CommentAnchorHandlers {
    /** Show the thread transiently, anchored to the marker. */
    peek: (thread: CommentThread, at: HTMLElement) => void;
    /** Show the thread pinned (the reader asked for it). */
    open: (thread: CommentThread, at: HTMLElement) => void;
    /** Stop showing a transient peek. */
    close: () => void;
}

export interface CommentAnchorOptions {
    getThreads: () => CommentThread[];
    handlers?: CommentAnchorHandlers;
}

/** Every distinct `threadId` in the doc, with the position its span ENDS at.
 *
 *  The end, not the start: the marker reads as a footnote on the phrase it follows, and
 *  a marker before the words would separate the anchor from the sentence it belongs to.
 *  A thread whose mark spans several text nodes reports the last of them. */
export function commentAnchorEnds(doc: PMModelNode): Map<string, number> {
    const ends = new Map<string, number>();
    doc.descendants((node, pos) => {
        if (!node.isText) return true;
        for (const mark of node.marks) {
            if (mark.type.name !== 'comment') continue;
            const id = (mark.attrs as { threadId?: string }).threadId;
            if (id) ends.set(id, pos + node.nodeSize);
        }
        return true;
    });
    return ends;
}

/** How many answers a thread has, for the marker's own label. Zero shows the bare
 *  marker — a count of nothing is noise, and the marker already says "there is a note". */
export function replyCount(thread: CommentThread): number {
    return thread.replies?.length ?? 0;
}

export function buildCommentAnchors(
    doc: PMModelNode, threads: CommentThread[], handlers?: CommentAnchorHandlers,
): DecorationSet {
    if (!threads.length) return DecorationSet.empty;
    const ends = commentAnchorEnds(doc);
    const decos: Decoration[] = [];
    for (const thread of threads) {
        const at = ends.get(thread.id);
        if (at === undefined) continue;         // its span is gone — nothing to anchor to
        const replies = replyCount(thread);
        decos.push(Decoration.widget(at, () => {
            const marker = document.createElement('button');
            marker.type = 'button';
            marker.className = 'ce-cmt-anchor-mark ' + thread.status;
            marker.contentEditable = 'false';
            marker.dataset.threadId = thread.id;
            marker.textContent = replies ? String(replies) : '';
            marker.title = replies
                ? `${replies} repl${replies === 1 ? 'y' : 'ies'} — hover to read, click to keep open`
                : 'Your note — hover to read, click to keep open';
            // Never steal the caret: a reader checking a note mid-sentence has to come
            // back to where they were typing.
            marker.addEventListener('mousedown', ev => ev.preventDefault());
            marker.addEventListener('mouseenter', () => handlers?.peek(thread, marker));
            marker.addEventListener('mouseleave', () => handlers?.close());
            marker.addEventListener('click', ev => {
                ev.preventDefault();
                ev.stopPropagation();
                handlers?.open(thread, marker);
            });
            return marker;
        }, { side: 1, key: `cmt-${thread.id}@${thread.status}:${replies}` }));
    }
    return DecorationSet.create(doc, decos);
}

export const CommentAnchors = Extension.create<CommentAnchorOptions>({
    name: 'commentAnchors',

    addOptions() {
        return { getThreads: () => [], handlers: undefined };
    },

    addProseMirrorPlugins() {
        const threads = (): CommentThread[] => this.options.getThreads();
        const handlers = (): CommentAnchorHandlers | undefined => this.options.handlers;
        return [
            new Plugin({
                key: anchorKey,
                state: {
                    init: (_c, state) => buildCommentAnchors(state.doc, threads(), handlers()),
                    // Structure-keyed: a marker is placed from the mark set, so typing a
                    // character inside a commented span moves it rather than changing it.
                    apply: (tr, old, _o, newState) => nextDecorations(
                        tr, old, !!tr.getMeta(COMMENT_ANCHORS_UPDATED),
                        () => buildCommentAnchors(newState.doc, threads(), handlers()),
                    ),
                },
                props: { decorations(state) { return anchorKey.getState(state); } },
            }),
        ];
    },
});
