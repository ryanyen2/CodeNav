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
import { directionOrigin } from '../state/grammar';

const css = readFileSync(resolve(__dirname, '../webview/doc-view.css'), 'utf8');
const sugg = readFileSync(resolve(__dirname, '../webview/tiptap/suggestion-decorations.ts'), 'utf8');

describe('U6 — code→codoc diff carries a non-color direction label (R8)', () => {
    it('the verdict strip emits a plain-text origin label, not just a colour/tooltip', () => {
        expect(sugg).toMatch(/ce-tc-dir/);
        expect(sugg).toMatch(/directionOrigin\(s\.direction\)/);
    });
    it('the words live in the grammar, not in the decoration layer', () => {
        // They were hard-coded here as the literal "from code" for EVERY proposal —
        // including the reader's own deferred edit, which is not from code at all.
        expect(directionOrigin('code-ahead')).toBe('from code');
        expect(directionOrigin('yours')).not.toMatch(/from code/);
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
    it('both panes strike a proposed retire in the SAME channel ink — the plan\'s', () => {
        // A retire is a PROPOSAL: an agent's words about what should go. So it reads in
        // the plan channel, in both panes, and not in the review-blue that is now the
        // author's own ink everywhere else.
        expect(css).toMatch(/\.row\.has-retire \.title\s*\{[^}]*var\(--st-plan\)/);
        expect(css).toMatch(/\.ce-retire-proposed\s*\{[^}]*var\(--st-plan\)/);
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
    it('the "being realized" dot is inked by WHOSE words are waiting, not by one status hue', () => {
        // A hold is reached two ways — you committed an edit, or you accepted an agent's
        // plan — and they are the human and plan channels. One sage green for both said
        // neither, and said it in the colour the code channel uses for what the codebase
        // actually did.
        expect(css).toMatch(/\.ce-pending-dot\.human\s*\{[^}]*var\(--st-human\)/);
        expect(css).toMatch(/\.ce-pending-dot\.plan\s*\{[^}]*var\(--st-plan\)/);
        const m = css.match(/\.ce-pending-dot\s*\{[^}]*\}/);
        expect(m).not.toBeNull();
        expect(m![0]).not.toContain('--ce-staged');        // green is the CODE channel now
    });
    it('the engine\'s ins/del marks are STRUCTURAL only — the plan channel paints them', () => {
        // They used to carry a per-author tint, and that was one of three competing
        // underlines the settlement redesign collapsed. The marks still do their real
        // job (keeping a proposal out of tree.codoc, and being what a reject deletes),
        // but painting them here would double the mark the model already draws.
        const rule = css.match(/ins\[data-change-id\][^{]*\{[^}]*\}/);
        expect(rule).not.toBeNull();
        expect(rule![0]).toContain('text-decoration: none');
        expect(css).not.toMatch(/ins\[data-author-id="claude-code"\]/);
    });
});

describe('settlement — one visual axis per channel, so claims compose', () => {
    it('gives each channel its own property: ink, opacity, ground', () => {
        expect(css).toMatch(/\.ce-settle\.human\s*\{[^}]*color:/);
        expect(css).toMatch(/\.ce-settle\.plan\s*\{[^}]*opacity:/);
        expect(css).toMatch(/\.ce-settle\.code\.add\s*\{[^}]*background:/);
    });

    it('spends motion only on the one state with an action attached', () => {
        expect(css).toMatch(/\.ce-settle\.human\.open\s*\{[^}]*animation:/);
        // Nothing else in the family animates — a condition that moves spends the
        // reader's attention on something they cannot act on.
        for (const cls of ['\\.ce-settle\\.human\\.committed', '\\.ce-settle\\.plan\\.accepted', '\\.ce-settle\\.code\\.add']) {
            const m = css.match(new RegExp(cls + '\\s*\\{[^}]*\\}'));
            if (m) expect(m[0]).not.toContain('animation:');
        }
    });

    it('drops the pulse under the VS Code reduced-motion body class, not only the media query', () => {
        expect(css).toMatch(/body\.vscode-reduce-motion \.ce-settle\.human\.open/);
        expect(css).toMatch(/body\.vscode-reduce-motion \.ce-mark \.st-human\.open/);
    });

    it('pins the diff grounds up in high contrast rather than dropping them', () => {
        expect(css).toMatch(/body\.vscode-high-contrast \.ce-settle\.code\.add/);
        expect(css).toMatch(/--st-ground-hc/);
    });

    it('strikes a CUT in place — it is still on screen, unlike a deletion', () => {
        expect(css).toMatch(/\.ce-settle\.cut\s*\{[^}]*line-through/);
    });

    it('lets a CUT keep the ink of whoever wrote the words it wants gone', () => {
        // The plan's gray is scoped to what the plan WROTE. A cut is somebody else's
        // sentence, and recolouring it credits the agent with the author's words at the
        // exact moment the reader decides whether to let the agent delete them.
        expect(css).toMatch(/\.ce-settle\.plan:not\(\.cut\)\s*\{[^}]*color:/);
        const planInk = css.match(/^\.ce-settle\.plan\s*\{[^}]*\}/m);
        expect(planInk?.[0]).not.toContain('color:');
    });

    it('fades a code deletion of PLANNED wording, so the promise is visible in the ghost', () => {
        // The one cell of the matrix a `del` cannot reach by stacking: it prints its own
        // ghost instead of covering text, so there is nothing underneath to tint.
        expect(css).toMatch(/\.ce-settle-ghost\.code\.planned\s*\{[^}]*opacity:/);
    });

    it('fills a node marker\'s ring to mean the claim reached the code, in both channels', () => {
        expect(css).toMatch(/\.ce-mark \.st-human\.fulfilled,\s*\.ce-mark \.st-plan\.fulfilled\s*\{[^}]*background:/);
    });
});

describe('U6 — one direction hue, no per-op rainbow (cohesion R7)', () => {
    it('tree-pane proposals key off the plan channel, the same one the prose uses', () => {
        expect(css).toMatch(/\.row\.proposal[\s\S]{0,300}var\(--st-plan\)/);
    });
    it('the in-situ proposal surfaces key off the same channel, not an op-specific hue', () => {
        // placeholder (add/move) rail + proposed-retire strike — one channel ink for both
        expect(css).toMatch(/\.ce-ghost-feature\s*\{[^}]*var\(--st-plan\)/);
        expect(css).toMatch(/\.ce-retire-proposed\s*\{[^}]*var\(--st-plan\)/);
    });
});

describe('the edit lifecycle is one cohesive ramp', () => {
    it('the three lifecycle PHASE colours are defined as tokens (editing/del/staged)', () => {
        // P1/§E.2 retuned these toward pastel (calmer blue / softer amber / sage) — still the
        // three distinct phases (editing = blue-ish, del = amber, staged = green), just desaturated.
        expect(css).toMatch(/--ce-editing:\s*#5aa6e0/);  // editing = calmer blue
        expect(css).toMatch(/--ce-del:\s*#e0b46a/);      // deletion caret = softer amber
        expect(css).toMatch(/--ce-staged:\s*#6fae74/);   // staged & sent = sage
    });

    it('the human channel keeps the editing phase colour, so nobody is retrained', () => {
        // The captured family's rail, dot and caret are gone — their state is the
        // settlement model's human channel now — but the value is the same blue, so a
        // reader who learned the old surface has not been retrained for nothing.
        expect(css).toMatch(/--st-human:\s*#5aa6e0/);
        expect(css).toMatch(/--ce-editing:\s*#5aa6e0/);
    });

    it('the pending chip is the human channel, not a fourth "staged" phase', () => {
        // "Staged & sent" was a phase of its own, in sage. But the fact it reports is
        // whose claim is outstanding, which is a CHANNEL — and sage is the code
        // channel's. So the phase collapsed into the channel it always belonged to,
        // with fill-vs-outline carrying how far along it is.
        const pend = css.match(/\.ce-pending-dot\.human\s*\{[^}]*\}/)?.[0] ?? '';
        expect(pend).toContain('--st-human');
        expect(pend).not.toContain('--ce-staged');
    });

    it('no status surface reports a CHANNEL in a hue another channel owns', () => {
        // The regression this exists to catch: a surface being re-hued in isolation and
        // landing back on somebody else's colour. Every status mark on the tree rows,
        // the minimap rail and the pending chip must name a channel token.
        for (const sel of ['.badge.proposed', '.badge.sent', '.badge.staged',
                           '.ce-tick.st-proposed', '.ce-tick.st-sent', '.ce-tick.st-rewritten']) {
            const rule = css.match(new RegExp(sel.replace(/[.]/g, '\\.') + '\\s*\\{[^}]*\\}'))?.[0] ?? '';
            expect(rule, sel).toMatch(/var\(--st-(human|plan|code-add|code-del)\)/);
        }
    });

    it('intensity ramps: unsent BREATHES, everything settled is static', () => {
        // The ramp inverted deliberately. It used to be captured=static, pending=breathe:
        // motion marked how far along the machine was. Motion now marks the one state
        // with an ACTION attached, which is the unsent edit — the reader can press ⌘S,
        // and cannot do anything about a directive already in flight.
        expect(css).toMatch(/\.ce-mark \.st-human\.open\s*\{[^}]*animation:/);
        const committed = css.match(/\.ce-settle\.human\.committed\s*\{[^}]*\}/)?.[0] ?? '';
        expect(committed).not.toContain('animation');
    });
});

describe('a proposal row points somewhere', () => {
    it('the ghost carries the same id the tree row is keyed by', () => {
        // The tree row for a pending proposal is keyed by the store event id, and
        // so is the ghost the editor draws for it. Selecting the row scrolls to
        // that element; if these two ever stop agreeing, the row silently selects
        // and the document does not move.
        expect(sugg).toMatch(/setAttribute\('data-suggestion', s\.id\)/);
        const view = readFileSync(resolve(__dirname, '../webview/doc-view.ts'), 'utf8');
        expect(view).toMatch(/data-suggestion="/);
        expect(view).toMatch(/scrollToGhost/);
    });
});
