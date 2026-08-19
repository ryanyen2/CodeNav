/**
 * provenance-card.ts — the one card that answers "why does this say this?" (W8).
 *
 * Two surfaces ask the question and get the same card: the timeline (about a moment in
 * the tree's history) and the History stance's per-feature label (about the paragraph
 * under the pointer). One implementation, because two cards showing the same chain in
 * two shapes is how a reader learns to distrust both.
 *
 * It borrows the hover-card chrome (`.ce-hovercard`) verbatim, the way the walkthrough's
 * answer card does — same card, same typography, different fields.
 *
 * Only ONE is ever open. The card can outlive its hover (its action is clickable), so
 * without that rule a reader who grazed three headings has three cards to dismiss.
 */
import type { TraceRow } from '../state/provenance';

export interface ProvenanceAction {
    label: string;
    title: string;
    run: () => void;
}

export interface ProvenanceCardOptions {
    /** The element the card hangs under. */
    anchor: HTMLElement;
    /** Heading line — who and when, in one phrase. */
    head: string;
    rows: TraceRow[];
    /** Calls to action — opening the code the change produced, or the conversation that
     *  asked for it. Absent/empty ⇒ the card is purely informational. */
    actions?: ProvenanceAction[];
}

let open: HTMLElement | null = null;

export function closeProvenanceCard(): void {
    open?.remove();
    open = null;
}

/** True when a card is currently up — callers use it to make a click toggle. */
export function isProvenanceCardOpen(): boolean {
    return !!open;
}

export function showProvenanceCard(opts: ProvenanceCardOptions): HTMLElement {
    closeProvenanceCard();
    const el = document.createElement('div');
    el.className = 'ce-hovercard ce-tl-card';
    el.contentEditable = 'false';

    const head = document.createElement('div');
    head.className = 'ce-hc-meta';
    head.textContent = opts.head;
    el.append(head);

    if (!opts.rows.length) {
        const none = document.createElement('div');
        none.className = 'ce-tl-row-v';
        none.textContent = 'codoc recorded no reason for this change.';
        el.append(none);
    }
    for (const row of opts.rows) {
        const line = document.createElement('div');
        line.className = 'ce-tl-row';
        const k = document.createElement('span');
        k.className = 'ce-tl-row-k';
        k.textContent = row.label;
        const v = document.createElement('span');
        v.className = 'ce-tl-row-v';
        v.textContent = row.value;
        line.append(k, v);
        el.append(line);
    }

    const actions = (opts.actions ?? []).filter(Boolean);
    if (actions.length) {
        const bar = document.createElement('div');
        bar.className = 'ce-tl-actions';
        for (const action of actions) {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'ce-tl-diff';
            btn.textContent = action.label;
            btn.title = action.title;
            btn.addEventListener('mousedown', ev => ev.preventDefault());
            btn.addEventListener('click', ev => {
                ev.preventDefault();
                ev.stopPropagation();
                closeProvenanceCard();
                action.run();
            });
            bar.append(btn);
        }
        el.append(bar);
    }

    // Fixed positioning off the anchor's box: the card must survive its own surface
    // scrolling under it (the reader can scroll while it is up), and clamping to the
    // viewport keeps it reachable when the anchor is near an edge.
    const box = opts.anchor.getBoundingClientRect();
    el.style.position = 'fixed';
    el.style.left = `${Math.max(8, Math.min(box.left, window.innerWidth - 480))}px`;
    el.style.top = `${box.bottom + 6}px`;
    document.body.append(el);
    open = el;
    el.addEventListener('mouseleave', () => {
        if (!el.contains(document.activeElement)) closeProvenanceCard();
    });
    return el;
}
