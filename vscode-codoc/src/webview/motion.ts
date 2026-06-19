/**
 * motion.ts — the ONE anime.js (v4) adapter for the webview, runtime-gated on the
 * VS Code reduced-motion / screen-reader preferences (U1).
 *
 * Why a runtime gate and not just CSS: VS Code relays "Reduce Motion" to a webview as the
 * body class `vscode-reduce-motion` (the `@media (prefers-reduced-motion)` query is
 * unreliable in the webview host), and a JS-driven tween never sees a CSS gate — so every
 * animation here checks the body classes at call time and JUMPS to the final value instead
 * of tweening when either preference is set. `vscode-using-screen-reader` is treated the
 * same (suppress non-essential motion).
 *
 * The DOM-touching helpers (tweenScrollTop, staggerHover) are verified in the manual EDH
 * gate (U7); the pure decision/shape helpers (prefersReducedMotion, motionGuard,
 * navDuration, waveDelays) are unit-tested in motion.test.ts.
 */
import { animate } from 'animejs';

/** A handle to an in-flight tween so callers can cancel it (e.g. on a new nav click or
 *  keyboard-repeat) before starting another — prevents queued/competing scroll tweens. */
export interface TweenController {
    cancel(): void;
}
const NOOP_CONTROLLER: TweenController = { cancel() { /* nothing in flight */ } };

interface ClassListLike { contains(token: string): boolean; }
interface BodyLike { classList: ClassListLike; }

/** True when the user asked for reduced motion or is on a screen reader — both suppress
 *  non-essential motion. Reads `document.body` by default; tests pass a fake body. */
export function prefersReducedMotion(body?: BodyLike | null): boolean {
    const b = body ?? (typeof document !== 'undefined' ? document.body : null);
    if (!b) return false;
    return b.classList.contains('vscode-reduce-motion')
        || b.classList.contains('vscode-using-screen-reader');
}

/** Pure gate: run `applyFinal` (instant) when motion is reduced, else `runTween`. Keeps the
 *  branch testable without a DOM (callers pass `prefersReducedMotion()`). */
export function motionGuard<T>(reduced: boolean, applyFinal: () => T, runTween: () => T): T {
    return reduced ? applyFinal() : runTween();
}

/** Distance-scaled, clamped tween duration for programmatic navigation — short hops snappier,
 *  long jumps longer (R3). Pure. */
export function navDuration(
    distancePx: number,
    opts: { min?: number; max?: number; factor?: number } = {},
): number {
    const { min = 220, max = 520, factor = 0.25 } = opts;
    const d = min + Math.abs(distancePx) * factor;
    return Math.round(Math.max(min, Math.min(max, d)));
}

/** The mute-spy window that must cover a scroll tween: the tween duration plus a small buffer,
 *  so the scroll-spy doesn't flicker-select a neighbour mid-glide (U3). Pure. */
export function muteWindowFor(tweenDuration: number, buffer = 80): number {
    return Math.round(tweenDuration + buffer);
}

/** Per-tick wave delays around the hovered index — a symmetric falloff (delay grows with
 *  index distance), bounded so a long rail's far ticks don't lag forever (U4). Pure. */
export function waveDelays(
    fromIndex: number,
    count: number,
    opts: { step?: number; maxDelay?: number } = {},
): number[] {
    const { step = 30, maxDelay = 180 } = opts;
    const out: number[] = [];
    for (let i = 0; i < count; i++) {
        out.push(Math.min(Math.abs(i - fromIndex) * step, maxDelay));
    }
    return out;
}

/** Tween an element's scrollTop with momentum. Reduced motion → set scrollTop instantly and
 *  return a no-op controller. The proxy-object pattern: anime.js animates a plain object's
 *  value and we write it onto the element each frame (anime.js has no first-class scrollTop). */
export function tweenScrollTop(
    el: HTMLElement,
    to: number,
    opts: { duration?: number; ease?: string; onComplete?: () => void } = {},
): TweenController {
    const target = Math.round(to);
    if (prefersReducedMotion()) {
        el.scrollTop = target;
        opts.onComplete?.();   // keep the caller's lifecycle symmetric (e.g. clear a busy flag)
        return NOOP_CONTROLLER;
    }
    const proxy = { v: el.scrollTop };
    const anim = animate(proxy, {
        v: target,
        duration: opts.duration ?? 320,
        ease: opts.ease ?? 'outQuad',
        onUpdate: () => { el.scrollTop = proxy.v; },
        onComplete: () => { opts.onComplete?.(); },
    });
    // pause() stops where it is (cancel() would revert to the original value — wrong here).
    return { cancel() { anim.pause(); } };
}

/** Animate a CSS property across a band of elements as a wave radiating from `fromIndex`
 *  (used for the minimap hover sweep and its settle). Reduced motion → no-op (CSS handles
 *  the single-tick hover). `valueAt` maps index-distance → the property value; elements
 *  outside `radius` get the rest value with no delay so a settle call lands everything home. */
export function staggerHover(
    els: HTMLElement[],
    fromIndex: number,
    prop: string,
    valueAt: (indexDistance: number) => number,
    opts: { radius?: number; step?: number; duration?: number; ease?: string } = {},
): void {
    if (prefersReducedMotion()) return;
    const { radius = 3, step = 30, duration = 120, ease = 'outQuad' } = opts;
    els.forEach((el, i) => {
        const dist = Math.abs(i - fromIndex);
        const params: Record<string, unknown> = {
            duration,
            ease,
            delay: dist <= radius ? dist * step : 0,
        };
        params[prop] = valueAt(Math.min(dist, radius + 1));
        animate(el, params);
    });
}
