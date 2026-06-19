/**
 * comment-decorations.ts — the minimal, store-driven visual for inline comments.
 *
 * The `comment` mark carries only a threadId (the anchor); ALL the affordance lives
 * here so the thread store (payload.comments) is the single source of truth: one
 * tiny icon at the TOP-RIGHT of each commented span (never per-text-node — adjacent
 * runs of the same thread merge to one), and a popover on hover/click showing the
 * note + the anchored snippet + Edit / Resolve. A mark with no live thread draws
 * nothing (the host GCs it), so resolving a comment clears the icon with no flicker.
 *
 * Design (per design-taste): one amber accent shared with the dotted underline; the
 * icon is a quiet superscript, not a badge; multiple comments stay uncluttered
 * because each is just a 12px glyph; motion is limited to a single fade on the
 * popover. `sent` (the agent has drained it into a STEER directive) reads as a
 * faded ✓ — informative without a second colour.
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { Node as PMModelNode } from '@tiptap/pm/model';
import type { CommentThread } from '../../state/comment-model';

export interface CommentHandlers {
    /** Resolve / delete the thread (drops the `> …` line + the anchor mark). */
    resolve: (id: string) => void;
    /** Open the composer prefilled to edit the thread's note. */
    edit: (thread: CommentThread) => void;
}

export interface CommentDecorationsOptions {
    getComments: () => CommentThread[];
    handlers: CommentHandlers;
}

export const COMMENTS_UPDATED = 'codocCommentsUpdated';
const cmtKey = new PluginKey('codocCommentDecorations');

function elc(tag: string, cls?: string, text?: string): HTMLElement {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
}

// ── the popover (note body + anchor snippet + Edit / Resolve) ──────────────────
let openPopEl: HTMLElement | null = null;
let popThreadId: string | null = null;
let hoverTimer = 0;
let popCleanup: (() => void) | null = null;
function closePop(): void {
    popCleanup?.();
    popCleanup = null;
    openPopEl?.remove();
    openPopEl = null;
    popThreadId = null;
}
// Close the transient comment/hover card on window resize so it never sits at a stale
// position (U5). Repositioning a hover card is pointless — it reopens on the next hover.
if (typeof window !== 'undefined') window.addEventListener('resize', closePop);

/** Tear down any open popover + pending hover timer. Called from the editor's
 *  destroy() — the popover state is module-level, so without this a hover timer
 *  in flight when the editor is replaced fires showPopover on a detached icon. */
export function resetCommentDecorations(): void {
    if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = 0; }
    closePop();
}

/* ─── generic single-open floating card (extracted from showPopover) ───────────
 *  showCard() owns ONLY the geometry + dismissal that both the comment popover and
 *  the hover-preview card (U4) share: position under the anchor (clamped on-screen),
 *  enforce one-open-at-a-time, and — when pinned — close on outside-click / Escape.
 *  The CONTENT is fully owned by the caller (a pre-built element), so this stays
 *  comment-free and the hover card reuses the same positioning rules verbatim.
 *
 *  A hover (`pinned:false`) card keeps itself alive while the pointer is over it
 *  (the caller's anchor mouseleave handles the "left both" dismissal); a pinned
 *  card registers the outside-click/Escape listeners synchronously. Returns a
 *  disposer (`closeCard`); the module enforces a single visible card across BOTH
 *  the comment popover and the hover card by routing every open through here. */
export function showCard(
    anchor: HTMLElement,
    content: HTMLElement,
    opts: { pinned: boolean },
): () => void {
    closePop();
    content.dataset.pinned = opts.pinned ? '1' : '0';
    document.body.append(content);
    const rect = anchor.getBoundingClientRect();
    content.style.top = `${Math.min(rect.bottom + 6, window.innerHeight - content.offsetHeight - 8)}px`;
    content.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - content.offsetWidth - 8))}px`;
    openPopEl = content;
    popThreadId = null; // a generic card has no threadId (comment popover re-sets it)
    if (opts.pinned) {
        // Register synchronously — the anchor's click handler stopPropagation's the
        // opening mousedown, so onDoc won't see it. (A deferred registration leaks
        // listeners when a second card opens first tick.)
        const onDoc = (e: MouseEvent): void => {
            if (!content.contains(e.target as Node) && !anchor.contains(e.target as Node)) closePop();
        };
        const onKey = (e: KeyboardEvent): void => { if (e.key === 'Escape') closePop(); };
        document.addEventListener('mousedown', onDoc, true);
        document.addEventListener('keydown', onKey, true);
        popCleanup = (): void => {
            document.removeEventListener('mousedown', onDoc, true);
            document.removeEventListener('keydown', onKey, true);
        };
    } else {
        // hover card: keep alive while the pointer is over the card itself; the
        // caller's anchor mouseleave decides the "left both" dismissal.
        content.addEventListener('mouseenter', () => { if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = 0; } });
        content.addEventListener('mouseleave', () => { if (content.dataset.pinned !== '1') closePop(); });
    }
    return closePop;
}

/** Whether `el` is the currently-open floating card (so a caller can decide a
 *  hover dismissal). Shared with the hover card so only one is ever visible. */
export function isOpenCard(el: HTMLElement | null): boolean {
    return !!el && el === openPopEl;
}

function buildPopover(t: CommentThread, handlers: CommentHandlers): HTMLElement {
    const pop = elc('div', 'ce-cmt-pop ' + (t.status === 'sent' ? 'sent' : 'open'));
    if (t.anchorText.trim()) {
        const a = elc('div', 'ce-cmt-anchor');
        a.append(elc('span', 'ce-cmt-quote', '“'), document.createTextNode(t.anchorText), elc('span', 'ce-cmt-quote', '”'));
        pop.append(a);
    }
    pop.append(elc('div', 'ce-cmt-body', t.body));
    const foot = elc('div', 'ce-cmt-foot');
    if (t.status === 'sent') {
        foot.append(elc('span', 'ce-cmt-state', '✓ sent to agent'));
    } else {
        foot.append(elc('span', 'ce-cmt-state', '→ for agent'));
        const edit = elc('button', 'ce-cmt-action', 'Edit');
        edit.addEventListener('mousedown', ev => ev.preventDefault());
        edit.addEventListener('click', ev => { ev.preventDefault(); ev.stopPropagation(); closePop(); handlers.edit(t); });
        foot.append(edit);
    }
    const resolve = elc('button', 'ce-cmt-action ce-cmt-resolve', 'Resolve');
    resolve.title = 'Resolve — remove this comment';
    resolve.addEventListener('mousedown', ev => ev.preventDefault());
    resolve.addEventListener('click', ev => { ev.preventDefault(); ev.stopPropagation(); closePop(); handlers.resolve(t.id); });
    foot.append(resolve);
    pop.append(foot);
    return pop;
}

function showPopover(anchor: HTMLElement, t: CommentThread, handlers: CommentHandlers, pinned: boolean): void {
    if (popThreadId === t.id && openPopEl && !pinned) return; // already showing for hover
    const pop = buildPopover(t, handlers);
    // showCard owns the geometry + single-open + pinned dismissal (shared with the
    // hover card); the comment popover only adds its own threadId bookkeeping.
    showCard(anchor, pop, { pinned });
    popThreadId = t.id;
}

function makeIcon(t: CommentThread, handlers: CommentHandlers): HTMLElement {
    const icon = elc('span', 'ce-cmt-icon ' + (t.status === 'sent' ? 'sent' : 'open'), t.status === 'sent' ? '✓' : '❝');
    icon.contentEditable = 'false';
    icon.title = t.status === 'sent' ? 'Comment — sent to the agent' : 'Comment — click to view';
    icon.addEventListener('mousedown', ev => ev.preventDefault());
    icon.addEventListener('click', ev => { ev.preventDefault(); ev.stopPropagation(); showPopover(icon, t, handlers, true); });
    icon.addEventListener('mouseenter', () => {
        if (hoverTimer) clearTimeout(hoverTimer);
        hoverTimer = window.setTimeout(() => { if (popThreadId !== t.id) showPopover(icon, t, handlers, false); }, 350);
    });
    icon.addEventListener('mouseleave', () => {
        if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = 0; }
        // let the popover's own mouseleave handle dismissal (pointer may move onto it)
        setTimeout(() => {
            if (openPopEl && openPopEl.dataset.pinned !== '1' && !openPopEl.matches(':hover')) closePop();
        }, 120);
    });
    return icon;
}

/** Merge adjacent text-node ranges that carry the same comment threadId, so a span
 *  split by other marks (bold inside a comment) still gets exactly ONE icon. */
interface Span { from: number; to: number; threadId: string; }
function commentSpans(doc: PMModelNode): Span[] {
    const spans: Span[] = [];
    doc.descendants((node, pos) => {
        if (!node.isText || !node.marks.length) return;
        const cm = node.marks.find(m => m.type.name === 'comment');
        const threadId = cm?.attrs.threadId as string | undefined;
        if (!threadId) return;
        const from = pos;
        const to = pos + node.nodeSize;
        const last = spans[spans.length - 1];
        if (last && last.threadId === threadId && last.to === from) last.to = to;
        else spans.push({ from, to, threadId });
    });
    return spans;
}

function buildDecorations(doc: PMModelNode, comments: CommentThread[], handlers: CommentHandlers): DecorationSet {
    const byId = new Map(comments.map(c => [c.id, c]));
    const decos: Decoration[] = [];
    for (const sp of commentSpans(doc)) {
        const t = byId.get(sp.threadId);
        if (!t) continue; // mark with no live thread → invisible (host GCs it)
        decos.push(Decoration.widget(sp.to, () => makeIcon(t, handlers), { side: 1, key: `cmt-${t.id}-${t.status}` }));
    }
    return DecorationSet.create(doc, decos);
}

export const CommentDecorations = Extension.create<CommentDecorationsOptions>({
    name: 'commentDecorations',
    addOptions() {
        return { getComments: () => [], handlers: { resolve: () => {}, edit: () => {} } };
    },
    addProseMirrorPlugins() {
        const getComments = (): CommentThread[] => this.options.getComments();
        const handlers = this.options.handlers;
        return [
            new Plugin({
                key: cmtKey,
                state: {
                    init: (_c, state) => buildDecorations(state.doc, getComments(), handlers),
                    apply: (tr, old, _o, newState) => {
                        if (tr.getMeta(COMMENTS_UPDATED) || tr.docChanged) {
                            return buildDecorations(newState.doc, getComments(), handlers);
                        }
                        return old.map(tr.mapping, tr.doc);
                    },
                },
                props: { decorations(state) { return cmtKey.getState(state); } },
            }),
        ];
    },
});
