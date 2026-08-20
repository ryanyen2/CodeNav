/**
 * hidden-display-css.test.ts — every element the webview hides with `.hidden = true`
 * must carry its own `[hidden] { display: none }` rule.
 *
 * The UA stylesheet's `[hidden] { display: none }` is USER-AGENT origin, so any author
 * `display` on the same element wins and the property does nothing. The find widget and
 * the ask bar both learned this the hard way and carry the rule with a comment saying
 * so; `.codoc-whole-editor` did not, which is how the History stance shipped painting
 * the live document and the past reconstruction on top of each other — `renderHistory()`
 * sets `editor.hidden = viewing` and the editor's `display: flex` simply ignored it,
 * leaving two `flex: 1` prose columns in one `.doc-host`.
 *
 * Node-env harness (no jsdom, no computed styles), so this is a source-level guard in
 * the style of design-system-css.test.ts.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';

const css = readFileSync(resolve(__dirname, '../webview/doc-view.css'), 'utf8');
const docView = readFileSync(resolve(__dirname, '../webview/doc-view.ts'), 'utf8');

/** Does the stylesheet give `selector` an author-origin `display: none` when hidden? */
function hasHiddenRule(selector: string): boolean {
    const esc = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return new RegExp(`${esc}\\[hidden\\][^{]*\\{[^}]*display:\\s*none`).test(css);
}

describe('elements hidden from script carry an author-origin display:none', () => {
    // The one this test exists for: without it the past page and the live editor both
    // paint, which is the version-history bug a reader sees as superimposed prose.
    it('.codoc-whole-editor — the live doc renderHistory() hides to show the past', () => {
        expect(hasHiddenRule('.codoc-whole-editor')).toBe(true);
    });

    it('and the two that already knew: the ask bar and the find widget', () => {
        expect(hasHiddenRule('.ce-ask-bar')).toBe(true);
        expect(hasHiddenRule('.ce-find')).toBe(true);
    });

    it('.codoc-whole-editor still declares the flex display the rule has to beat', () => {
        // If this ever stops being true the guard above is guarding nothing — but the
        // rule is harmless, so the assertion is here to keep the REASON legible.
        expect(css).toMatch(/\.codoc-whole-editor\s*\{[^}]*display:\s*flex/);
    });

    it('renderHistory really is the caller that relies on it', () => {
        expect(docView).toContain('editor.hidden = viewing');
    });
});
