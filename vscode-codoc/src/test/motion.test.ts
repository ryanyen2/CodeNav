/**
 * motion.test.ts — guards U1's pure motion logic (the parts vitest's node env CAN see).
 * The DOM tweens (tweenScrollTop/staggerHover) are verified in the manual EDH gate (U7).
 */
import { describe, it, expect } from 'vitest';
import {
    prefersReducedMotion,
    navDuration,
    muteWindowFor,
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
