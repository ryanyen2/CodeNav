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
 * The rail + underline this file used to pin are GONE: they were the human channel of
 * `state/settlement.ts` drawn a second time, from their own diff, in the code channel's
 * hue — see the hold-decorations header. What is pinned here is what survived: one chip
 * per held heading, inked by whoever is waiting.
 *
 * The toolbar/bubble removals (Editing/Suggesting toggle, pen/pencil) and the
 * no-flicker commit behavior are editor-runtime concerns (a live TipTap view / DOM) —
 * covered by manual EDH verification, not this node harness.
 */
import { describe, it, expect } from 'vitest';
import { Node as PMModelNode } from '@tiptap/pm/model';
import { codocSchema } from '../webview/tiptap/schema';
import { buildHoldDecorations, pendingTitle } from '../webview/tiptap/hold-decorations';
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

describe('U3 — buildHoldDecorations (the "queued for the agent" chip)', () => {
    it('decorates a held feature: heading node hook + trailing chip. Nothing on the prose', () => {
        const set = buildHoldDecorations(twoFeatureDoc(), new Set(['f-a']));
        // The description block is deliberately untouched — the settlement layer owns
        // what the author changed, and marking it here too was two answers to one
        // question that agreed only by luck.
        expect(set.find().length).toBe(2);
    });

    it('decorates every held feature when more than one is awaiting realization', () => {
        const set = buildHoldDecorations(twoFeatureDoc(), new Set(['f-a', 'f-b']));
        expect(set.find().length).toBe(4); // 2 features × (heading node + chip)
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

describe('the chip does not mark the prose', () => {
    it('leaves a held feature\'s bold focus runs alone', () => {
        // The comment bug, in miniature. A steer directive (what a comment becomes)
        // carries no baseline, so the old underline fell back to marking every bold run
        // in the feature — lighting up text nobody had touched, in the code channel's
        // green, because somebody had left a note.
        const classes = classesOf(buildHoldDecorations(heldDocWithBold(), new Set(['f-a']),
            undefined, { 'f-a': { kind: 'steer', intent: 'address your note' } }));
        expect(classes).not.toContain('ce-intent-underline');
        expect(classes).not.toContain('ce-pending-rail');
        expect(classes).toContain('ce-realizing');
    });

    it('leaves the prose alone even when a baseline IS available', () => {
        const doc = codocSchema().nodeFromJSON({
            type: 'doc',
            content: [
                { type: 'featureHeading', attrs: { fid: 'f-a', level: 0, retired: false, realized: true }, content: [{ type: 'text', text: 'Auth' }] },
                { type: 'paragraph', content: [{ type: 'text', text: 'Login and sessions, now with tokens.' }] },
            ],
        });
        const set = buildHoldDecorations(doc, new Set(['f-a']), undefined,
            { 'f-a': { kind: 'amend', intent: 'x', baseline: 'Login and sessions.' } });
        // settlement.ts draws this, in the author's blue, from the projection the
        // daemon wrote — not from a second diff taken here.
        expect(classesOf(set)).not.toContain('ce-intent-underline');
    });
});

describe('the chip is inked by whoever is waiting', () => {
    it('names the author when the queue holds their own edit', () => {
        expect(pendingTitle({ kind: 'amend', intent: 'x', origin: 'human' }, false))
            .toContain('Your edit');
    });

    it('names the plan when the queue holds one the author accepted', () => {
        // Same lifecycle position, different authorship — and the reader is owed the
        // difference, because one of them is words they wrote and one is not.
        expect(pendingTitle({ kind: 'amend', intent: 'x', origin: 'plan' }, false))
            .toContain('The plan you accepted');
    });

    it('reads a daemon that predates `origin` as the author\'s own', () => {
        expect(pendingTitle({ kind: 'amend', intent: 'x' }, false)).toContain('Your edit');
    });
});

describe('W3: session-aware pending wording', () => {
    it('says "lands on the next agent turn" while a session is live', () => {
        const t = pendingTitle({ kind: 'amend', intent: 'x' }, true);
        expect(t).toContain('lands on the next agent turn');
        expect(t).not.toContain('/codoc:sync');
    });

    it('says "run /codoc:sync" when no session is live', () => {
        expect(pendingTitle({ kind: 'amend', intent: 'x' }, false)).toContain('/codoc:sync');
    });

    it('carries the intent gloss, so the chip reports RECOGNITION and not just a count', () => {
        expect(pendingTitle({ kind: 'amend', intent: 'update the code to match your new intent' }, false))
            .toContain('update the code to match your new intent');
    });
});
