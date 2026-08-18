/**
 * ask-bar.ts — the compact stepper for a `/codoc:ask` walkthrough.
 *
 * A small pill anchored over the doc pane — absolutely positioned, like the
 * find widget, so it never reflows the document — carrying a truncated echo of
 * the question and a stepper through the stops. The answer is NOT printed
 * here: it already lives, worded per-stop, in the inline chips and notes
 * drawn on the headings (`ask-decorations.ts`), so repeating the whole thing
 * as a permanent paragraph up top was redundant chrome. Hovering (or clicking,
 * for keyboard/touch) the question reveals the full question + answer in the
 * same single-open popover card used for dependency hovers (`showCard`), so
 * looking it up feels like every other "peek" in this editor rather than a new
 * kind of UI.
 *
 * It is a plain DOM component with no editor knowledge: it reports which stop
 * the reader moved to and lets the caller do the navigating, so the same bar
 * works against the extension's editor and the hub's.
 */
import type { AskWalkthrough } from '../state/ask-model';
import { isOpenCard, showCard } from './tiptap/comment-decorations';

export interface AskBarHandle {
    element: HTMLElement;
    /** Swap in a new walkthrough (or null to hide the bar). Resets to stop 1. */
    setWalkthrough: (walk: AskWalkthrough | null) => void;
    /** The reader scrolled/clicked onto a feature — if it is on the path, move the
     *  counter to it. Keeps "3 of 7" true when they navigate by hand. */
    syncActive: (fid: string) => void;
    /** The stop the reader is on, or '' when there is no walkthrough. */
    currentFid: () => string;
    destroy: () => void;
}

export interface AskBarOptions {
    /** Go to a stop (the caller scrolls the editor and moves the emphasis). */
    onStep: (fid: string) => void;
    /** Take the walkthrough down for good. */
    onDismiss: () => void;
}

function button(label: string, title: string, cls: string, onClick: () => void): HTMLButtonElement {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = cls;
    b.textContent = label;
    b.title = title;
    b.addEventListener('mousedown', ev => ev.preventDefault());  // never steal the caret
    b.addEventListener('click', ev => { ev.preventDefault(); onClick(); });
    return b;
}

/** Same card chrome as the dependency hover-preview (`.ce-hovercard`), so the
 *  answer reads as the same kind of thing as everywhere else something appears
 *  on hover, not a bespoke widget. */
function answerCard(walk: AskWalkthrough): HTMLElement {
    const pop = document.createElement('div');
    pop.className = 'ce-hovercard ce-ask-card';
    const q = document.createElement('div');
    q.className = 'ce-hc-meta ce-ask-card-q';
    q.textContent = walk.question;
    const a = document.createElement('div');
    a.className = 'ce-hc-gist';
    a.textContent = walk.answer || 'No summary yet.';
    pop.append(q, a);
    return pop;
}

const HOVER_DELAY_MS = 350; // mirror the dependency-link hover delay

export function createAskBar(opts: AskBarOptions): AskBarHandle {
    let walk: AskWalkthrough | null = null;
    let index = 0;
    let hoverTimer = 0;
    let cardEl: HTMLElement | null = null;
    let closeCard: (() => void) | null = null;

    const bar = document.createElement('div');
    bar.className = 'ce-ask-bar';
    bar.hidden = true;

    const question = document.createElement('button');
    question.type = 'button';
    question.className = 'ce-ask-question';

    function dismissCard(): void {
        if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = 0; }
        if (cardEl && isOpenCard(cardEl)) closeCard?.();
        cardEl = null;
        closeCard = null;
    }

    function openCard(pinned: boolean): void {
        if (!walk) return;
        dismissCard();
        const content = answerCard(walk);
        cardEl = content;
        closeCard = showCard(question, content, { pinned });
    }

    question.addEventListener('mouseenter', () => {
        if (hoverTimer) clearTimeout(hoverTimer);
        hoverTimer = window.setTimeout(() => openCard(false), HOVER_DELAY_MS);
    });
    question.addEventListener('mouseleave', () => {
        if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = 0; }
        // Short grace period covers the question→card gap, same as the dependency
        // hover card — the pointer may not register :hover on the card yet.
        window.setTimeout(() => {
            if (cardEl && isOpenCard(cardEl) && !cardEl.matches(':hover')) dismissCard();
        }, 120);
    });
    question.addEventListener('click', ev => {
        ev.preventDefault();
        if (cardEl && isOpenCard(cardEl)) { dismissCard(); return; }
        openCard(true);  // pinned: click is the keyboard/touch path, Escape/outside-click dismiss
    });

    const nav = document.createElement('div');
    nav.className = 'ce-ask-nav';
    const count = document.createElement('span');
    count.className = 'ce-ask-count';

    const go = (delta: number): void => {
        if (!walk?.steps.length) return;
        const n = walk.steps.length;
        index = ((index + delta) % n + n) % n;
        paint();
        opts.onStep(walk.steps[index].feature_id);
    };

    nav.append(
        button('‹', 'Previous stop', 'ce-ask-step-btn', () => go(-1)),
        count,
        button('›', 'Next stop', 'ce-ask-step-btn', () => go(+1)),
        button('✕', 'Dismiss this walkthrough', 'ce-ask-close', () => opts.onDismiss()),
    );

    bar.append(question, nav);

    function paint(): void {
        if (!walk) { bar.hidden = true; dismissCard(); return; }
        bar.hidden = false;
        question.textContent = walk.question || 'Walkthrough';
        const step = walk.steps[index];
        // The label, not the ordinal: on a grouped path the reader sees "1b" on the
        // heading, so the counter has to say the same thing or they are two schemes.
        count.textContent = `${step?.label ?? ''} · ${index + 1} of ${walk.steps.length}`;
    }

    return {
        element: bar,
        setWalkthrough: (next: AskWalkthrough | null) => {
            // Same walkthrough arriving again (any repaint reposts the payload) must
            // not throw the reader back to stop 1 — only a genuinely new one does.
            const same = next && walk && next.id === walk.id;
            walk = next;
            if (!same) index = 0;
            else index = Math.min(index, Math.max(0, (next?.steps.length ?? 1) - 1));
            paint();
        },
        syncActive: (fid: string) => {
            if (!walk || !fid) return;
            const i = walk.steps.findIndex(s => s.feature_id === fid);
            if (i === -1 || i === index) return;
            index = i;
            paint();
        },
        currentFid: () => walk?.steps[index]?.feature_id ?? '',
        destroy: () => { dismissCard(); bar.remove(); },
    };
}
