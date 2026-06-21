/**
 * motion-lifecycle.test.ts — guards the REDUCED-MOTION branch of the P0 lifecycle helpers
 * (spec §C.2/§C.3/§C.5). The full-motion tween path runs anime.js against the DOM and is
 * verified visually in the EDH gate; here we assert the gate itself: with reduced motion on,
 * every helper JUMPS to its final frame instantly and starts NO tween (KTD4 — the harness
 * is the only place this contract is machine-checkable without a browser).
 *
 * A tiny fake element captures `.style` writes; a stubbed `document.body` flips
 * `prefersReducedMotion()` true. We never let the helpers reach anime.js in these cases, so
 * no real DOM is needed.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import {
    popLanded, spinReject, launchPlane, sparkIn, saveShimmer, collapseRowOut,
    glideTo, presenceTrail, spinForever,
} from '../webview/motion';

/** A minimal stand-in for an HTML/SVG element: records style writes the helpers make. */
function fakeEl(): { style: Record<string, string> & { setProperty(k: string, v: string): void }; offsetHeight: number } {
    const props: Record<string, string> = {};
    const style = new Proxy(props as Record<string, string>, {}) as Record<string, string> & {
        setProperty(k: string, v: string): void;
    };
    style.setProperty = (k: string, v: string): void => { props[k] = v; };
    return { style, offsetHeight: 24 };
}

const orig = (globalThis as { document?: unknown }).document;

function setReduceMotion(on: boolean): void {
    (globalThis as { document?: unknown }).document = {
        body: { classList: { contains: (c: string): boolean => on && c === 'vscode-reduce-motion' } },
    };
}

describe('P0 lifecycle motion — reduced-motion gate (§C.5)', () => {
    beforeEach(() => setReduceMotion(true));
    afterEach(() => { (globalThis as { document?: unknown }).document = orig; });

    it('popLanded shows the final frame (opacity 1, no transform) with no bounce', () => {
        const el = fakeEl();
        popLanded(el as never);
        expect(el.style.opacity).toBe('1');
        expect(el.style.transform).toBe('none');
    });

    it('sparkIn lands the glyph at its resting frame', () => {
        const el = fakeEl();
        sparkIn(el as never);
        expect(el.style.opacity).toBe('1');
        expect(el.style.transform).toBe('none');
    });

    it('spinReject / launchPlane are no-ops under reduced motion (no style writes)', () => {
        const a = fakeEl(), b = fakeEl();
        spinReject(a as never);
        launchPlane(b as never);
        expect(Object.keys(a.style).filter(k => k !== 'setProperty')).toHaveLength(0);
        expect(Object.keys(b.style).filter(k => k !== 'setProperty')).toHaveLength(0);
    });

    it('saveShimmer is a no-op under reduced motion (the rails graduate on reconcile instead)', () => {
        const rail = fakeEl();
        saveShimmer([rail as never]);
        expect(rail.style['--shimmer']).toBeUndefined();
    });

    it('collapseRowOut calls onDone immediately under reduced motion (no height tween)', () => {
        let done = false;
        collapseRowOut(fakeEl() as never, () => { done = true; });
        expect(done).toBe(true);
    });

    it('a null target is always a safe no-op (defensive callers)', () => {
        expect(() => { popLanded(null); spinReject(null); launchPlane(null); sparkIn(null); }).not.toThrow();
        let done = false;
        collapseRowOut(null, () => { done = true; });
        expect(done).toBe(true);
    });

    it('an empty rail list short-circuits saveShimmer (no getComputedStyle call)', () => {
        // getComputedStyle is undefined in node — this proves the early-out fires before it.
        expect(() => saveShimmer([])).not.toThrow();
    });
});

describe('P3 presence motion — reduced-motion gate (§B.5)', () => {
    beforeEach(() => setReduceMotion(true));
    afterEach(() => { (globalThis as { document?: unknown }).document = orig; });

    it('glideTo JUMPS to the destination (no tween) — the avatar just appears', () => {
        const el = fakeEl();
        glideTo(el as never, { top: 120, left: 40 }, 500);
        expect(el.style.top).toBe('120px');
        expect(el.style.left).toBe('40px');
    });

    it('presenceTrail emits NOTHING under reduced motion (no dots created)', () => {
        let made = 0;
        presenceTrail(() => { made++; return null; }, { top: 0, left: 0 }, { top: 100, left: 0 });
        expect(made).toBe(0);
    });

    it('spinForever is a no-op controller under reduced motion', () => {
        const c = spinForever(fakeEl() as never);
        expect(() => c.cancel()).not.toThrow(); // a real loop would need pause(); the NOOP is safe
    });

    it('a null target is a safe no-op for the presence helpers', () => {
        expect(() => { glideTo(null, { top: 1, left: 1 }, 10); spinForever(null).cancel(); }).not.toThrow();
    });
});
