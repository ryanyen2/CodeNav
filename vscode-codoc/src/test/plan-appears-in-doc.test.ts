/**
 * A plan has to appear in the DOC, not only in the tree pane.
 *
 * The doc's reload is short-circuited when the baseline text and the proposal set are
 * both unchanged — the common case right after a settle round-trips, where reloading
 * would only reset the caret. Neither half of that test could see a plan ADD:
 * `renderTreeFromDoc` skips `proposed` nodes by design (the guard that keeps an
 * agent's words out of `tree.codoc`), so a materialized plan renders to the text of a
 * document without it; and the signature only covered amends. So the skip fired, and
 * the plan reached the editor only when some unrelated write forced a reload. The tree
 * pane redraws from the payload with no gate, so it showed the plan at once — which is
 * how this presented: the plan in one pane and not the other, for about half a minute.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const src = readFileSync(resolve(__dirname, '../webview/tiptap/whole-doc-editor.ts'), 'utf8');
const sig = src.slice(src.indexOf('function proposalsSig()'),
                      src.indexOf('function scheduleSettle()'));

describe('the reload trigger sees every proposal the host materializes', () => {
    it('covers adds — a plan node is invisible to the text compare', () => {
        expect(sig).toMatch(/kind === 'add'/);
    });

    it('covers retires, which materialize the same way', () => {
        expect(sig).toMatch(/kind === 'retire'/);
    });

    it('still covers amends, whose marks it was written for', () => {
        expect(sig).toMatch(/kind === 'amend'/);
    });

    it('keys on the anchors, because they decide where a plan node is drawn', () => {
        // Re-anchoring a proposal moves it on screen without changing its text.
        for (const field of ['parentId', 'afterId', 'beforeId']) {
            expect(sig).toContain(field);
        }
    });

    it('and the skip still requires BOTH the text and the set to be unchanged', () => {
        // The skip itself is worth keeping: reloading on an echo resets the caret.
        expect(src).toMatch(/if \(sameText && sig === lastProposalsSig\)/);
    });
});
