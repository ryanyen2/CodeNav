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
    // Guard the onComplete-always-fires contract: a non-finite target must not start a tween
    // that may never complete (which would leak a caller's busy flag). Fire onComplete and bail.
    if (!Number.isFinite(target)) { opts.onComplete?.(); return NOOP_CONTROLLER; }
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

// ─── Lifecycle micro-interactions (P0 / spec §C.2–C.3) ───────────────────────
// Every helper below cross-fades / pops / collapses an element and is gated on
// prefersReducedMotion(): reduced → JUMP to the final visual instantly (per KTD4 the
// CSS blanket also zeroes any transition, so these never leave a half-tween behind).
// The icons are inline SVG, so transitions are pure opacity/transform (no path redraw).

/** A motion target: an HTML or SVG element (the lifecycle glyphs are inline <svg>). Both
 *  carry `.style`, which is all these helpers read; anime.js animates either. */
type MotionEl = HTMLElement | SVGElement;

/** Cross-fade + scale between two stacked inline lifecycle icons that share an anchor
 *  (spec §C.2). `from` shrinks to 0.6 + fades out; `to` scales 0.6→1 + fades in with a
 *  small overshoot, after a brief stagger so the "old crystallises into the new" reads.
 *  Reduced motion → `from` hidden, `to` shown at the final frame instantly. */
export function morphLifecycle(
    from: MotionEl | null,
    to: MotionEl | null,
    opts: { out?: number; in?: number; stagger?: number } = {},
): void {
    const { out = 140, in: inDur = 180, stagger = 40 } = opts;
    if (prefersReducedMotion()) {
        if (from) { from.style.opacity = '0'; from.style.transform = 'scale(0.6)'; }
        if (to) { to.style.opacity = '1'; to.style.transform = 'none'; }
        return;
    }
    if (from) {
        animate(from, { opacity: [1, 0], scale: [1, 0.6], duration: out, ease: 'outQuad' });
    }
    if (to) {
        to.style.opacity = '0';
        animate(to, { opacity: [0, 1], scale: [0.6, 1], duration: inDur, delay: stagger, ease: 'outBack' });
    }
}

/** The "it landed" pop shared by accept + resolving→done (spec §C.2/§C.3): a single
 *  satisfying spring `scale [0 → 1.15 → 1]` over ~260 ms. Reduced motion → instant final
 *  frame (scale 1, opacity 1) with no bounce. */
export function popLanded(el: MotionEl | null, opts: { duration?: number } = {}): void {
    if (!el) return;
    const { duration = 260 } = opts;
    if (prefersReducedMotion()) { el.style.opacity = '1'; el.style.transform = 'none'; return; }
    el.style.opacity = '1';
    animate(el, { scale: [0, 1.15, 1], opacity: [0, 1, 1], duration, ease: 'outElastic(1, .5)' });
}

/** Collapse a row's height to 0 as it is removed (spec §C.3) — never a hard display:none,
 *  so the removal reads. Calls `onDone` when the collapse finishes (or immediately under
 *  reduced motion) so the caller can detach the node. */
export function collapseRowOut(el: HTMLElement | null, onDone: () => void, opts: { duration?: number } = {}): void {
    if (!el) { onDone(); return; }
    const { duration = 180 } = opts;
    if (prefersReducedMotion()) { onDone(); return; }
    const h = el.offsetHeight;
    el.style.overflow = 'hidden';
    animate(el, {
        height: [h, 0], opacity: [1, 0], duration, ease: 'outQuad',
        onComplete: () => onDone(),
    });
}

/** The ⌘S save-shimmer (spec §C.3): every captured rail in view recolours blue→green,
 *  staggered top-to-bottom (~20 ms each). Reduced motion → no shimmer (the recolour lands
 *  on the next reconcile when the rails graduate to pending). The tween animates each rail's
 *  `--shimmer` CSS var between the captured (blue) and staged (green) tokens — RESOLVED to
 *  their computed rgb() so anime.js can interpolate (it cannot tween a raw `var()` string).
 *  `from`/`to` override the resolved tokens (tests pass concrete colours). */
export function saveShimmer(
    rails: HTMLElement[],
    opts: { step?: number; duration?: number; from?: string; to?: string } = {},
): void {
    if (prefersReducedMotion() || !rails.length) return;
    const { step = 20, duration = 260 } = opts;
    const cs = getComputedStyle(rails[0]);
    const from = opts.from ?? (cs.getPropertyValue('--ce-editing').trim() || '#5aa6e0');
    const to = opts.to ?? (cs.getPropertyValue('--ce-staged').trim() || '#6fae74');
    rails.forEach((el, i) => {
        el.style.setProperty('--shimmer', from);
        animate(el, {
            '--shimmer': [from, to],
            duration, delay: i * step, ease: 'outQuad',
        });
    });
}

/** The code→doc "spark" rise (spec §A.5 / §C.3, reused by the done check): a glyph fades
 *  in with a small upward settle. Reduced motion → instant final frame. */
export function sparkIn(el: MotionEl | null, opts: { duration?: number; rise?: number } = {}): void {
    if (!el) return;
    const { duration = 220, rise = 6 } = opts;
    if (prefersReducedMotion()) { el.style.opacity = '1'; el.style.transform = 'none'; return; }
    animate(el, { translateY: [rise, 0], opacity: [0, 1], duration, ease: 'outBack' });
}

/** The reject quarter-spin (spec §C.3): the x-circle rotates −90° and settles — quieter than
 *  the accept pop by design (dismissing should not celebrate). Reduced motion → no spin. */
export function spinReject(el: MotionEl | null, opts: { duration?: number } = {}): void {
    if (!el) return;
    const { duration = 160 } = opts;
    if (prefersReducedMotion()) return;
    animate(el, { rotate: [-90, 0], duration, ease: 'outQuad' });
}

/** The hand-to-agent "launch" (spec §C.3): the paper-plane glyph drifts up-right + fades,
 *  then snaps back home — the universally legible "sent." Reduced motion → no launch. */
export function launchPlane(el: MotionEl | null, opts: { duration?: number } = {}): void {
    if (!el) return;
    const { duration = 220 } = opts;
    if (prefersReducedMotion()) return;
    animate(el, {
        translateX: [0, 4, 0], translateY: [0, -4, 0], opacity: [1, 0.2, 1],
        duration, ease: 'outQuad',
    });
}

// ─── Agent presence (P3 / spec §B) ───────────────────────────────────────────

/** Glide the presence avatar from its current `top`/`left` to a new one (spec §B.2). Distance-
 *  scaled via the existing navDuration (a long hop takes longer, a short one snaps), with the
 *  one earned overshoot (--ease-spring). Reduced motion → JUMP to the destination (the avatar
 *  simply appears, still legible). The element must be absolutely positioned. */
export function glideTo(
    el: HTMLElement | null,
    to: { top: number; left: number },
    distancePx: number,
): void {
    if (!el) return;
    if (prefersReducedMotion()) { el.style.top = `${to.top}px`; el.style.left = `${to.left}px`; return; }
    const from = { top: parseFloat(el.style.top) || to.top, left: parseFloat(el.style.left) || to.left };
    animate(el, {
        top: [from.top, to.top], left: [from.left, to.left],
        duration: navDuration(distancePx), ease: 'outBack',
    });
}

/** A soft comet trail along a glide path (spec §B.2): N fading ink dots from `from`→`to`,
 *  staggered, each fading opacity .35→0 over ~500 ms then removed. Pure decoration — reduced
 *  motion emits NOTHING (the avatar just appears). `makeDot` builds one positioned dot; the
 *  caller appends/owns the container. */
export function presenceTrail(
    makeDot: (top: number, left: number) => HTMLElement | null,
    from: { top: number; left: number },
    to: { top: number; left: number },
    opts: { count?: number; step?: number; duration?: number } = {},
): void {
    if (prefersReducedMotion()) return;
    const { count = 4, step = 60, duration = 500 } = opts;
    for (let i = 1; i <= count; i++) {
        const t = i / (count + 1);                       // points BETWEEN from and to
        const top = from.top + (to.top - from.top) * t;
        const left = from.left + (to.left - from.left) * t;
        const dot = makeDot(top, left);
        if (!dot) continue;
        animate(dot, {
            opacity: [0.35, 0], duration, delay: (count - i) * step, ease: 'outQuad',
            onComplete: () => dot.remove(),
        });
    }
}

/** Spin a glyph 360° once (spec §B.2 reflecting working-pulse). Reduced motion → no spin.
 *  Returns a controller so the caller can stop the loop when the phase changes. */
export function spinForever(el: MotionEl | null, opts: { duration?: number } = {}): TweenController {
    if (!el || prefersReducedMotion()) return NOOP_CONTROLLER;
    const { duration = 1600 } = opts;
    const anim = animate(el, { rotate: [0, 360], duration, ease: 'linear', loop: true });
    return { cancel() { anim.pause(); } };
}
