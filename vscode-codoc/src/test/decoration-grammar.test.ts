/**
 * decoration-grammar.test.ts — guards U6's cohesion invariants at the source level (the vitest
 * node env can't read computed styles). The grammar: color = who/direction, shape/texture = kind,
 * motion = liveness; the lifecycle/stage indicator is ORTHOGONAL to the change-mark diff.
 *
 * The "does it FEEL cohesive / is the diff visible" judgment is the EDH gate (U7); these checks
 * pin the structural rules a regression would silently break.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';

const css = readFileSync(resolve(__dirname, '../webview/doc-view.css'), 'utf8');
const sugg = readFileSync(resolve(__dirname, '../webview/tiptap/suggestion-decorations.ts'), 'utf8');

describe('U6 — code→codoc diff carries a non-color direction label (R8)', () => {
    it('the amend resolve row emits a plain-text "from code" label, not just a colour/tooltip', () => {
        expect(sugg).toMatch(/ce-tc-dir/);
        expect(sugg).toMatch(/from code/);
    });
    it('the label has a CSS rule so it renders muted text (not hue-dependent)', () => {
        expect(css).toMatch(/\.ce-tc-dir\s*\{/);
    });
});

describe('U6 — lifecycle/stage indicator is orthogonal to the change-mark diff (R9 / KTD4)', () => {
    it('the "being realized" dot rides the status axis (--accent), never a direction hue', () => {
        const m = css.match(/\.ce-pending-dot\s*\{[^}]*\}/);
        expect(m).not.toBeNull();
        expect(m![0]).toContain('--accent');
        expect(m![0]).not.toContain('--dir-review');
    });
    it('the diff is a separate concern (ins/del marks keyed on --author-color), not the dot', () => {
        const ins = css.match(/ins\[data-change-id\]\s*\{[^}]*\}/);
        expect(ins).not.toBeNull();
        expect(ins![0]).toContain('--author-color');
        expect(css).toMatch(/del\[data-change-id\]/);
    });
});

describe('U6 — one direction hue, no per-op rainbow (cohesion R7)', () => {
    it('tree-pane proposals key off the single --dir-review direction hue', () => {
        expect(css).toMatch(/\.row\.proposal[\s\S]{0,250}var\(--dir-review\)/);
    });
    it('the in-situ diff strip keys off the same --dir-review, not an op-specific hue', () => {
        expect(css).toMatch(/\.ce-diff\.code-ahead\s*\{[^}]*var\(--dir-review\)/);
    });
});

describe('U3/U4/U5 — the captured→pending→resolving lifecycle is one cohesive ramp', () => {
    it('the captured family (phase 1) has CSS rules: body rail, heading dot, tree badge', () => {
        expect(css).toMatch(/\.ce-captured-rail::before\s*\{/);
        expect(css).toMatch(/\.ce-captured-dot\s*\{/);
        expect(css).toMatch(/\.badge\.captured\s*\{/);
    });

    it('captured rides the --accent STATUS axis, never a direction hue (it is who-neutral)', () => {
        const dot = css.match(/\.ce-captured-dot\s*\{[^}]*\}/)?.[0] ?? '';
        expect(dot).toContain('--accent');
        expect(dot).not.toContain('--dir-review');
    });

    it('intensity ramps: captured is STATIC, pending BREATHES, resolving PULSES', () => {
        // captured (recorded, not sent) is the calmest — no motion to gate.
        const cap = css.match(/\.ce-captured-dot\s*\{[^}]*\}/)?.[0] ?? '';
        expect(cap).not.toContain('animation');
        // pending (staged & sent) breathes; resolving (agent mid-edit) pulses — both stronger.
        expect(css.match(/\.ce-pending-dot\s*\{[^}]*\}/)?.[0] ?? '').toContain('breathe');
        expect(css).toMatch(/ce-phase-editing[\s\S]{0,120}pulse/);
    });

    it('captured has a high-contrast floor so "recorded, not sent" survives HC themes', () => {
        expect(css).toMatch(/vscode-high-contrast\s+\.ce-captured-dot/);
    });
});
