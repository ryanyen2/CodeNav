/**
 * motion.test.ts — guards U1's pure motion logic (the parts vitest's node env CAN see).
 * The DOM tweens (tweenScrollTop/staggerHover) are verified in the manual EDH gate (U7).
 */
import { describe, it, expect } from 'vitest';
import {
    prefersReducedMotion,
    motionGuard,
    navDuration,
    muteWindowFor,
    waveDelays,
} from '../webview/motion';

const fakeBody = (...classes: string[]) => ({
    classList: { contains: (c: string) => classes.includes(c) },
});

describe('U1 — prefersReducedMotion gate', () => {
    it('is true when the reduce-motion body class is present', () => {
        expect(prefersReducedMotion(fakeBody('vscode-reduce-motion'))).toBe(true);
    });
    it('is true on a screen reader (motion suppressed too)', () => {
        expect(prefersReducedMotion(fakeBody('vscode-using-screen-reader'))).toBe(true);
    });
    it('is false with neither preference set', () => {
        expect(prefersReducedMotion(fakeBody('vscode-high-contrast'))).toBe(false);
    });
});

describe('U1 — motionGuard branch', () => {
    it('runs applyFinal (not the tween) when motion is reduced', () => {
        let final = 0, tween = 0;
        const r = motionGuard(true, () => { final++; return 'instant'; }, () => { tween++; return 'tween'; });
        expect(r).toBe('instant');
        expect(final).toBe(1);
        expect(tween).toBe(0);
    });
    it('runs the tween factory when motion is allowed', () => {
        let final = 0, tween = 0;
        const r = motionGuard(false, () => { final++; return 'instant'; }, () => { tween++; return 'tween'; });
        expect(r).toBe('tween');
        expect(final).toBe(0);
        expect(tween).toBe(1);
    });
});

describe('U1 — navDuration distance scaling (R3)', () => {
    it('a longer travel yields a longer duration than a short hop', () => {
        expect(navDuration(1000)).toBeGreaterThan(navDuration(50));
    });
    it('clamps to the floor for tiny distances and the ceiling for huge ones', () => {
        expect(navDuration(0)).toBe(220);
        expect(navDuration(100000)).toBe(520);
    });
    it('honors custom bounds', () => {
        expect(navDuration(0, { min: 100, max: 300 })).toBe(100);
        expect(navDuration(99999, { min: 100, max: 300 })).toBe(300);
    });
});

describe('U1 — muteWindowFor covers the glide', () => {
    it('returns the tween duration plus a buffer', () => {
        expect(muteWindowFor(400)).toBe(480);
        expect(muteWindowFor(520, 100)).toBe(620);
    });
});

describe('U1 — waveDelays symmetric falloff (R4)', () => {
    it('the hovered tick has zero delay; neighbours grow with distance', () => {
        const d = waveDelays(3, 7, { step: 30, maxDelay: 1000 });
        expect(d[3]).toBe(0);
        expect(d[2]).toBe(30);
        expect(d[4]).toBe(30);
        expect(d[0]).toBe(90);
        expect(d[6]).toBe(90);
    });
    it('is symmetric around the hovered index', () => {
        const d = waveDelays(3, 7);
        expect(d[2]).toBe(d[4]);
        expect(d[1]).toBe(d[5]);
        expect(d[0]).toBe(d[6]);
    });
    it('bounds far ticks to maxDelay', () => {
        const d = waveDelays(0, 20, { step: 30, maxDelay: 180 });
        expect(Math.max(...d)).toBe(180);
    });
});
