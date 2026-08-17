/**
 * ask-bar.ts — the header for a `/codoc:ask` walkthrough.
 *
 * One quiet strip at the top of the doc pane: the question that was asked, the
 * answer in a sentence, and a stepper through the stops. The answer is the point
 * — somebody who reads only that line is already better off, and the numbered
 * path exists to show them where it lives.
 *
 * It is a plain DOM component with no editor knowledge: it reports which stop the
 * reader moved to and lets the caller do the navigating, so the same bar works
 * against the extension's editor and the hub's.
 */
import type { AskWalkthrough } from '../state/ask-model';

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

export function createAskBar(opts: AskBarOptions): AskBarHandle {
    let walk: AskWalkthrough | null = null;
    let index = 0;

    const bar = document.createElement('div');
    bar.className = 'ce-ask-bar';
    bar.hidden = true;

    const lede = document.createElement('div');
    lede.className = 'ce-ask-lede';
    const question = document.createElement('div');
    question.className = 'ce-ask-question';
    const answer = document.createElement('div');
    answer.className = 'ce-ask-answer';
    lede.append(question, answer);

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

    bar.append(lede, nav);

    function paint(): void {
        if (!walk) { bar.hidden = true; return; }
        bar.hidden = false;
        question.textContent = walk.question;
        answer.textContent = walk.answer;
        answer.hidden = !walk.answer;
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
        destroy: () => bar.remove(),
    };
}
