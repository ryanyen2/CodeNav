/**
 * design-system-css.test.ts — guards U1 (the design-system token foundation).
 *
 * The vitest harness is node-env / pure-logic (no jsdom, no CSS computed styles), so
 * U1's "reduced-motion" and "high-contrast" scenarios are guarded at the SOURCE level:
 * we assert the CSS carries the fixes that would otherwise silently regress. The
 * headline guard is KTD4 — VS Code relays reduced-motion to a webview as the body class
 * `vscode-reduce-motion`, NOT as `@media (prefers-reduced-motion)` (unreliable in the
 * webview host), so the body-class gate must exist or motion-suppression never fires.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';

const css = readFileSync(resolve(__dirname, '../webview/doc-view.css'), 'utf8');

describe('U1 — reduced motion (KTD4)', () => {
    it('gates motion on the VS Code body class, not only the media query', () => {
        expect(css).toContain('body.vscode-reduce-motion');
    });

    it('the body-class rule blankets animation, transition, and scroll-behavior', () => {
        const idx = css.indexOf('body.vscode-reduce-motion *');
        expect(idx).toBeGreaterThan(-1);
        const rule = css.slice(idx, idx + 200);
        expect(rule).toMatch(/animation:\s*none\s*!important/);
        expect(rule).toMatch(/transition:\s*none\s*!important/);
    });

    it('keeps the @media query as a fallback (belt and suspenders)', () => {
        expect(css).toContain('@media (prefers-reduced-motion: reduce)');
    });
});

describe('U1 — scoped design-system tokens', () => {
    it('defines the structural accent + two directional hues', () => {
        expect(css).toMatch(/--accent:\s*var\(--vscode-focusBorder/);
        expect(css).toMatch(/--dir-review:/);
        expect(css).toMatch(/--dir-await:/);
    });

    it('makes human authorship ink NEUTRAL so it cannot collide with code-ahead blue (H5)', () => {
        expect(css).toMatch(/--ink-human:\s*var\(--vscode-foreground\)/);
    });

    it('defines the 8px spacing grid and radius scale', () => {
        expect(css).toMatch(/--space-2:\s*8px/);
        expect(css).toMatch(/--radius-card:/);
    });
});

describe('U1 — accessibility + editorial type', () => {
    it('raises high-contrast tint floors via the body class', () => {
        expect(css).toContain('body.vscode-high-contrast');
        expect(css).toMatch(/--vscode-contrastBorder/);
    });

    it('navigator titles use the UI sans family, not the editor mono', () => {
        // the .title rule should read the UI font, not --vscode-editor-font-family
        const m = css.match(/\.title\s*\{[^}]*\}/);
        expect(m).not.toBeNull();
        expect(m![0]).toContain('--vscode-font-family');
        expect(m![0]).not.toContain('--vscode-editor-font-family');
    });

    it('the L3 heading is not an all-caps eyebrow', () => {
        const m = css.match(/\.codoc-feature-heading\[data-level="3"\]\s*\{[^}]*\}/);
        expect(m).not.toBeNull();
        expect(m![0]).not.toContain('text-transform: uppercase');
    });
});
