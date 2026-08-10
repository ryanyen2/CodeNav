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
    it('the verdict strip emits a plain-text "from code" label, not just a colour/tooltip', () => {
        expect(sugg).toMatch(/ce-tc-dir/);
        expect(sugg).toMatch(/from code/);
    });
    it('the label has a CSS rule so it renders muted text (not hue-dependent)', () => {
        expect(css).toMatch(/\.ce-tc-dir\s*\{/);
    });
});

describe('one resolution surface per feature (consolidation)', () => {
    it('the verdict is hidden at rest and revealed by hovering the feature it resolves', () => {
        expect(css).toMatch(/\.ce-verdict\s*\{[^}]*opacity:\s*0/);
        expect(css).toMatch(/\.codoc-feature-heading:hover\s+\.ce-verdict/);
        expect(css).toMatch(/\.ce-ghost-feature:hover\s+\.ce-verdict/);
    });
    it('a tree row never keeps a second always-lit verdict pair', () => {
        // `.row.proposal .verdict` used to force opacity 1 alongside the hover rule.
        expect(css).not.toMatch(/\.row\.proposal\s+\.verdict/);
    });
    it('the proposed node is drawn as a placeholder, not as a "+ new" card', () => {
        expect(sugg).toMatch(/ce-ghost-feature/);
        expect(sugg).not.toMatch(/\+ new/);
    });
    it('a placeholder lands at the end of its parent subtree, not under the parent heading', () => {
        expect(sugg).toMatch(/subtreeEnd/);
    });
    it('a verdict in flight on ANY surface makes every other surface inert', () => {
        expect(css).toMatch(/body\.applying[\s\S]{0,160}\.ce-verdict[\s\S]{0,80}pointer-events:\s*none/);
        expect(css).toMatch(/body\.applying[\s\S]{0,160}\.row \.verdict/);
    });
    it('the suggestion layer is structure-keyed, not rebuilt on every keystroke', () => {
        expect(sugg).toMatch(/nextDecorations/);
        expect(sugg).not.toMatch(/SUGGESTIONS_UPDATED\)\s*\|\|\s*tr\.docChanged/);
    });
});

describe('a code-writing Accept is distinguishable from a bookkeeping one', () => {
    const view = readFileSync(resolve(__dirname, '../webview/doc-view.ts'), 'utf8');

    it('three redundant channels — verb, plane glyph, launch motion — and no new hue', () => {
        expect(sugg).toMatch(/consequenceVerb/);                  // the label
        expect(sugg).toMatch(/paper-plane-tilt/);                 // the glyph
        expect(sugg).toMatch(/launchPlane/);                      // the motion
        expect(view).toMatch(/launchPlane\(glyph\)/);             // …in the tree pane too
    });

    it('the consequential accept rides the "sent" phase colour, not a new one', () => {
        expect(css).toMatch(/\.ce-verdict\.cq-build[\s\S]{0,200}var\(--ce-staged\)/);
        expect(css).not.toMatch(/--cq-|--consequence-/);          // no new token invented
    });

    it('the bulk Accept-all says how many of the batch reach code', () => {
        expect(view).toMatch(/to build/);
        expect(view).toMatch(/leavesForAgent\(consequenceForEvent/);
    });

    it('both consequential states show WITHOUT a hover; the boring one stays hidden', () => {
        expect(css).toMatch(/\.ce-verdict\.cq-build,\s*\.ce-verdict\.cq-remove/);
        expect(css).not.toMatch(/\.ce-verdict\.cq-record\s*\{[^}]*opacity:\s*0?\.\d/);
    });
});

describe('a recorded-but-unapplied verdict says so', () => {
    const view = readFileSync(resolve(__dirname, '../webview/doc-view.ts'), 'utf8');

    it('replaces the buttons with a state, so the click cannot look like it failed', () => {
        expect(sugg).toMatch(/verdictPending/);
        expect(sugg).toMatch(/recorded · waiting to apply/);
        expect(view).toMatch(/function verdictWaiting/);
    });

    it('the waiting mark is not an affordance — no button, and visible at rest', () => {
        expect(css).toMatch(/\.row \.verdict\.waiting\s*\{[^}]*opacity:\s*0\.8/);
    });

    it('the timeout notice stops blaming the daemon when the daemon did read the click', () => {
        expect(view).toMatch(/n\.proposal\?\.verdictPending/);
        expect(view).toMatch(/waits for a pass that can hand code work/);
    });

    it('its slow spin is gated on reduced motion like every other animation here', () => {
        expect(css).toMatch(/prefers-reduced-motion[\s\S]{0,200}\.verdict\.waiting \.ce-icon/);
        expect(css).toMatch(/vscode-reduce-motion[\s\S]{0,300}\.verdict\.waiting/);
    });
});

describe('a proposed retire covers the whole node', () => {
    it('the body blocks get their own decoration class, not just the heading', () => {
        expect(sugg).toMatch(/ce-retire-proposed-body/);
        expect(css).toMatch(/\.ce-retire-proposed-body\s*\{/);
    });
    it('the body is dimmed rather than struck (four struck lines are unreadable)', () => {
        const rule = css.match(/\.ce-retire-proposed-body\s*\{[^}]*\}/)?.[0] ?? '';
        expect(rule).toMatch(/opacity/);
        expect(rule).not.toMatch(/line-through/);
    });
    it('both panes strike a proposed retire in the SAME direction hue', () => {
        expect(css).toMatch(/\.row\.has-retire \.title\s*\{[^}]*var\(--dir-review\)/);
        expect(css).toMatch(/\.ce-retire-proposed\s*\{[^}]*var\(--dir-review\)/);
    });
    it('an unlanded edit of the user\'s own is called out before they accept', () => {
        expect(sugg).toMatch(/you edited this/);
        expect(css).toMatch(/\.ce-tc-contested\s*\{/);
    });
});

describe('one badge per feature (feature-state.ts)', () => {
    const state = readFileSync(resolve(__dirname, '../state/feature-state.ts'), 'utf8');
    it('the badge classes the state machine emits all have CSS rules', () => {
        for (const cls of ['working.write', 'working.read', 'proposed', 'sent', 'staged']) {
            expect(css).toMatch(new RegExp(`\\.badge\\.${cls.replace('.', '\\.')}`));
        }
    });
    it('the retired per-op badges are gone (they collapsed into "proposed")', () => {
        expect(css).not.toMatch(/\.badge\.(amend|retire|divergent|unrealized|captured|pending)\b/);
    });
    it('every state carries hover text saying what to do about it', () => {
        expect(state).toMatch(/title:/);
        expect(state).toMatch(/⌘S/);
    });
});

describe('U6 — lifecycle/stage indicator is orthogonal to the change-mark diff (R9 / KTD4)', () => {
    it('the "being realized" dot rides the STAGED phase colour (--ce-staged), never the review direction hue', () => {
        const m = css.match(/\.ce-pending-dot\s*\{[^}]*\}/);
        expect(m).not.toBeNull();
        expect(m![0]).toContain('--ce-staged');           // staged & sent = green phase
        expect(m![0]).not.toContain('--dir-review');       // NOT the agent-review (code-ahead) hue
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
    it('the in-situ proposal surfaces key off the same --dir-review, not an op-specific hue', () => {
        // placeholder (add/move) rail + proposed-retire strike — one direction hue for both
        expect(css).toMatch(/\.ce-ghost-feature\s*\{[^}]*var\(--dir-review\)/);
        expect(css).toMatch(/\.ce-retire-proposed\s*\{[^}]*var\(--dir-review\)/);
    });
});

describe('U3/U4/U5 — the captured→pending→resolving lifecycle is one cohesive ramp', () => {
    it('the captured family (phase 1) has CSS rules: body rail, heading dot, tree badge', () => {
        expect(css).toMatch(/\.ce-captured-rail::before\s*\{/);
        expect(css).toMatch(/\.ce-captured-dot\s*\{/);
        expect(css).toMatch(/\.badge\.staged\s*\{/);
    });

    it('the three lifecycle PHASE colours are defined as tokens (editing/del/staged)', () => {
        // P1/§E.2 retuned these toward pastel (calmer blue / softer amber / sage) — still the
        // three distinct phases (editing = blue-ish, del = amber, staged = green), just desaturated.
        expect(css).toMatch(/--ce-editing:\s*#5aa6e0/);  // editing = calmer blue
        expect(css).toMatch(/--ce-del:\s*#e0b46a/);      // deletion caret = softer amber
        expect(css).toMatch(/--ce-staged:\s*#6fae74/);   // staged & sent = sage
    });

    it('captured (editing) keys off --ce-editing; pending (staged) off --ce-staged — distinct phases', () => {
        const cap = css.match(/\.ce-captured-dot\s*\{[^}]*\}/)?.[0] ?? '';
        expect(cap).toContain('--ce-editing');
        const pend = css.match(/\.ce-pending-dot\s*\{[^}]*\}/)?.[0] ?? '';
        expect(pend).toContain('--ce-staged');
        expect(pend).not.toContain('--ce-editing');
    });

    it('the deletion caret rides the amber --ce-del (the one warranted removal hue)', () => {
        // match the MAIN rule (the one carrying var(--ce-del)), not the earlier HC floor rule
        expect(css).toMatch(/\.ce-captured-del\s*\{[^}]*var\(--ce-del\)/);
    });

    it('intensity ramps: captured is STATIC, pending BREATHES, resolving PULSES', () => {
        const cap = css.match(/\.ce-captured-dot\s*\{[^}]*\}/)?.[0] ?? '';
        expect(cap).not.toContain('animation');
        expect(css.match(/\.ce-pending-dot\s*\{[^}]*\}/)?.[0] ?? '').toContain('breathe');
        expect(css).toMatch(/ce-phase-editing[\s\S]{0,120}pulse/);
    });

    it('the captured family has high-contrast floors so the phases survive HC themes', () => {
        expect(css).toMatch(/vscode-high-contrast\s+\.ce-captured-dot/);
        expect(css).toMatch(/vscode-high-contrast\s+\.ce-captured-del/);
    });
});
