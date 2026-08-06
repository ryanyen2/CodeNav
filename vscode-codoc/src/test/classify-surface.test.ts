/**
 * classify-surface.test.ts — U3 guard for the single-editing-surface model.
 *
 * The webview no longer decides "suggest vs edit": every human edit commits, and the
 * "being realized" badge is a pure projection of the daemon's doc-wins hold set
 * (sidecar.holds = live doc-ahead intents ∪ queued realize directives; computed by
 * codoc/loop/edits.py:hold_set off classify.py's implies_code gate). This pins the two
 * pure contracts the badge rides on: the host→payload mapping (heldFeatures) and the
 * decoration builder (buildHoldDecorations marks only held feature headings).
 *
 * The toolbar/bubble removals (Editing/Suggesting toggle, pen/pencil) and the
 * no-flicker commit behavior are editor-runtime concerns (a live TipTap view / DOM) —
 * covered by manual EDH verification, not this node harness.
 */
import { describe, it, expect } from 'vitest';
import { Node as PMModelNode } from '@tiptap/pm/model';
import { codocSchema } from '../webview/tiptap/schema';
import { buildHoldDecorations, changedRange } from '../webview/tiptap/hold-decorations';
import { heldFeatures, emptySidecar, type SidecarData } from '../state/bindings-model';

function twoFeatureDoc(): PMModelNode {
    return codocSchema().nodeFromJSON({
        type: 'doc',
        content: [
            { type: 'featureHeading', attrs: { fid: 'f-a', level: 0, retired: false, realized: true }, content: [{ type: 'text', text: 'Auth' }] },
            { type: 'paragraph', content: [{ type: 'text', text: 'Login and sessions.' }] },
            { type: 'featureHeading', attrs: { fid: 'f-b', level: 0, retired: false, realized: true }, content: [{ type: 'text', text: 'Data' }] },
            { type: 'paragraph', content: [{ type: 'text', text: 'Persistence.' }] },
        ],
    });
}

describe('U3 — heldFeatures (host → payload mapping)', () => {
    it('returns the sidecar hold set verbatim', () => {
        const sidecar: SidecarData = { ...emptySidecar(), holds: ['f-a', 'f-c'] };
        expect(heldFeatures(sidecar)).toEqual(['f-a', 'f-c']);
    });

    it('defaults to no held features when the sidecar predates the holds slice', () => {
        expect(heldFeatures(emptySidecar())).toEqual([]); // emptySidecar has no `holds`
    });
});

describe('U3 — buildHoldDecorations (the "being realized" badge + pending rail)', () => {
    it('decorates a held feature: heading node + chip + a rail on its description block', () => {
        const set = buildHoldDecorations(twoFeatureDoc(), new Set(['f-a']));
        // f-a held → heading node deco + trailing chip widget + 1 rail on "Login and
        // sessions." (no bold → no underline); f-b untouched.
        expect(set.find().length).toBe(3);
    });

    it('decorates every held feature when more than one is awaiting realization', () => {
        const set = buildHoldDecorations(twoFeatureDoc(), new Set(['f-a', 'f-b']));
        expect(set.find().length).toBe(6); // 2 features × (heading node + chip + rail)
    });

    it('is empty when nothing is held (a pure-doc edit shows no badge — AE1)', () => {
        expect(buildHoldDecorations(twoFeatureDoc(), new Set()).find().length).toBe(0);
    });

    it('ignores a held id with no matching heading (stale hold → no badge)', () => {
        expect(buildHoldDecorations(twoFeatureDoc(), new Set(['f-gone'])).find().length).toBe(0);
    });
});

// A held feature whose description carries a bolded "focus" run — the case the
// pending-intent underline marks (the recognised actionable request).
function heldDocWithBold(): PMModelNode {
    return codocSchema().nodeFromJSON({
        type: 'doc',
        content: [
            { type: 'featureHeading', attrs: { fid: 'f-a', level: 0, retired: false, realized: true }, content: [{ type: 'text', text: 'Auth' }] },
            { type: 'paragraph', content: [
                { type: 'text', text: 'Login and sessions. ' },
                { type: 'text', text: 'Should validate tokens', marks: [{ type: 'bold' }] },
                { type: 'text', text: ' on each request.' },
            ] },
        ],
    });
}

// The decoration's class/title live on its internal `type.attrs` (not on the public
// Decoration type), so reach them through a narrow cast for assertions.
function attrsOf(d: unknown): { class?: string; title?: string } | undefined {
    return (d as { type?: { attrs?: { class?: string; title?: string } } }).type?.attrs;
}

function classesOf(set: ReturnType<typeof buildHoldDecorations>): string[] {
    return set.find().map(d => attrsOf(d)?.class).filter(Boolean) as string[];
}

describe('pending-intent rail + underline (the in-situ "what is queued" signal)', () => {
    it('rails the held feature description block AND underlines its bold focus run', () => {
        const classes = classesOf(buildHoldDecorations(heldDocWithBold(), new Set(['f-a'])));
        expect(classes).toContain('ce-pending-rail');
        expect(classes).toContain('ce-intent-underline');
    });

    it('shows neither rail nor underline when the feature is not held', () => {
        const classes = classesOf(buildHoldDecorations(heldDocWithBold(), new Set(['f-other'])));
        expect(classes).not.toContain('ce-pending-rail');
        expect(classes).not.toContain('ce-intent-underline');
    });

    it("carries the intent gloss as the rail's hover title (recognition, not just a count)", () => {
        const set = buildHoldDecorations(heldDocWithBold(), new Set(['f-a']), undefined,
            { 'f-a': { kind: 'amend', intent: 'update the code to match your new intent' } });
        const rail = set.find().find(d => attrsOf(d)?.class === 'ce-pending-rail');
        expect(attrsOf(rail)?.title).toContain('update the code to match your new intent');
    });

    it('underlines the text the author CHANGED when a baseline is provided', () => {
        const doc = codocSchema().nodeFromJSON({
            type: 'doc',
            content: [
                { type: 'featureHeading', attrs: { fid: 'f-a', level: 0, retired: false, realized: true }, content: [{ type: 'text', text: 'Auth' }] },
                { type: 'paragraph', content: [{ type: 'text', text: 'Login and sessions, now with tokens.' }] },
            ],
        });
        const set = buildHoldDecorations(doc, new Set(['f-a']), undefined,
            { 'f-a': { kind: 'amend', intent: 'x', baseline: 'Login and sessions.' } });
        expect(classesOf(set)).toContain('ce-intent-underline'); // the changed tail is marked
    });
});

describe('changedRange (pending-change word diff)', () => {
    it('is null when the text is unchanged', () => {
        expect(changedRange('abc def', 'abc def')).toBeNull();
    });
    it('spans an inserted word', () => {
        const cur = 'rewards poems by length.';
        const r = changedRange('rewards by length.', cur)!;
        expect(r).not.toBeNull();
        expect(cur.slice(r.start, r.end)).toContain('poems');
    });
    it('spans a replaced word', () => {
        const cur = 'the slow fox';
        const r = changedRange('the quick fox', cur)!;
        expect(cur.slice(r.start, r.end)).toContain('slow');
    });
    it('is null for a pure trailing deletion (nothing added in current)', () => {
        expect(changedRange('one two three', 'one two')).toBeNull();
    });

    it('stable episode-start baseline keeps the WHOLE change visible across iterations', () => {
        // The field bug: iterating eroded the baseline to the previous keystroke, so the
        // diff collapsed and the decoration vanished. With the daemon freezing the baseline
        // at episode start, the diff vs that stable baseline spans the entire addition even
        // 3 edits deep — and an eroded baseline visibly collapses it (why the freeze matters).
        const current = 'Caches values. Should also cache reads, writes, and evictions.';
        const stable = changedRange('Caches values.', current)!;
        expect(current.slice(stable.start, stable.end))
            .toContain('Should also cache reads, writes, and evictions');
        const eroded = changedRange('Caches values. Should also cache reads, writes, and', current);
        const erodedSpan = eroded ? current.slice(eroded.start, eroded.end) : '';
        expect(erodedSpan.length).toBeLessThan(current.slice(stable.start, stable.end).length);
    });
});


describe('W3: session-aware pending wording', () => {
    it('says "lands on the next agent turn" while a session is live', () => {
        const set = buildHoldDecorations(heldDocWithBold(), new Set(['f-a']), undefined,
            { 'f-a': { kind: 'amend', intent: 'x' } }, true);
        const rail = set.find().find(d => attrsOf(d)?.class === 'ce-pending-rail');
        expect(attrsOf(rail)?.title).toContain('Lands on the next agent turn');
        expect(attrsOf(rail)?.title).not.toContain('/codoc:sync');
    });

    it('says "awaiting /codoc:sync" when no session is live', () => {
        const set = buildHoldDecorations(heldDocWithBold(), new Set(['f-a']), undefined,
            { 'f-a': { kind: 'amend', intent: 'x' } }, false);
        const rail = set.find().find(d => attrsOf(d)?.class === 'ce-pending-rail');
        expect(attrsOf(rail)?.title).toContain('/codoc:sync');
    });
});
