/**
 * tree-center.test.ts — guards U2's pure centering geometry + the scroll-source gate.
 * The eased tween + focus line are verified in the EDH gate (U7).
 */
import { describe, it, expect } from 'vitest';
import { shouldCenter, centerScrollTarget } from '../webview/tree-center';

describe('U2 — scroll-source gate (keystroke-jank guard)', () => {
    it('re-centers on the scroll-driven spy', () => {
        expect(shouldCenter('scroll')).toBe(true);
    });
    it('does NOT re-center on a caret/selection move', () => {
        expect(shouldCenter('selection')).toBe(false);
    });
});

describe('U2 — centerScrollTarget geometry (R1)', () => {
    it('centers a mid-list row in the viewport', () => {
        // ideal = 1000 - (600-28)/2 = 714, clamped to [0, 2400], far from current=0 → 714
        expect(centerScrollTarget(1000, 28, 600, 3000, 0)).toBe(714);
    });
    it('clamps to 0 for a row near the top (no negative scroll)', () => {
        expect(centerScrollTarget(10, 28, 600, 3000, 2000)).toBe(0);
    });
    it('clamps to scrollHeight−viewport for a row near the bottom (no overscroll)', () => {
        expect(centerScrollTarget(2950, 28, 600, 3000, 0)).toBe(2400);
    });
    it('is a no-op (returns current scrollTop) when the row is within the ±15% deadband', () => {
        // ideal 714, current 700 → |14| <= 600*0.15 (=90) → stays put
        expect(centerScrollTarget(1000, 28, 600, 3000, 700)).toBe(700);
    });
    it('moves once the row drifts past the deadband', () => {
        // current 600, ideal 714 → |114| > 90 → re-centers to 714
        expect(centerScrollTarget(1000, 28, 600, 3000, 600)).toBe(714);
    });
});
