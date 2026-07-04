/**
 * comment-decorations.ts — shared single-open floating-card infrastructure.
 *
 * This once also drew an inline icon + hover popover per comment; the comment UI now
 * lives in the doc's RIGHT MARGIN (whole-doc-editor.ts: persistent margin cards aligned
 * to the anchored text + an in-margin composer). What remains here is the generic
 * single-open floating-card primitive — `showCard` / `isOpenCard` — reused by the
 * tier-1 hover-preview card (hover-card.ts). Only one card is ever visible across both
 * callers; `resetCommentDecorations` tears any open card down on editor teardown.
 */

// ── the single shared floating card (hover-preview today; was also the comment popover) ──
let openPopEl: HTMLElement | null = null;
let hoverTimer = 0;
let popCleanup: (() => void) | null = null;
function closePop(): void {
    popCleanup?.();
    popCleanup = null;
    openPopEl?.remove();
    openPopEl = null;
}
// Close the transient card on window resize so it never sits at a stale position (U5).
if (typeof window !== 'undefined') window.addEventListener('resize', closePop);

/** Tear down any open card + pending hover timer. Called from the editor's destroy()
 *  — the card state is module-level, so without this a hover timer in flight when the
 *  editor is replaced fires on a detached anchor. */
export function resetCommentDecorations(): void {
    if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = 0; }
    closePop();
}

/* ─── generic single-open floating card ───────────────────────────────────────
 *  showCard() owns the geometry + dismissal: position under the anchor (clamped
 *  on-screen), enforce one-open-at-a-time, and — when pinned — close on
 *  outside-click / Escape. The CONTENT is fully owned by the caller (a pre-built
 *  element). A hover (`pinned:false`) card keeps itself alive while the pointer is
 *  over it (the caller's anchor mouseleave handles the "left both" dismissal); a
 *  pinned card registers the outside-click/Escape listeners synchronously. Returns a
 *  disposer (`closeCard`); the module enforces a single visible card across every
 *  caller by routing every open through here. */
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
