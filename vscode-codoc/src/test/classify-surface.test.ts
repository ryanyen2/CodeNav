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
import { buildHoldDecorations } from '../webview/tiptap/hold-decorations';
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

describe('U3 — buildHoldDecorations (the "being realized" badge)', () => {
    it('decorates ONLY held feature headings (one node deco + one chip widget each)', () => {
        const set = buildHoldDecorations(twoFeatureDoc(), new Set(['f-a']));
        // f-a held → its heading gets a node decoration + a trailing chip widget; f-b none.
        expect(set.find().length).toBe(2);
    });

    it('decorates every held heading when more than one is awaiting realization', () => {
        const set = buildHoldDecorations(twoFeatureDoc(), new Set(['f-a', 'f-b']));
        expect(set.find().length).toBe(4); // 2 headings × (node + widget)
    });

    it('is empty when nothing is held (a pure-doc edit shows no badge — AE1)', () => {
        expect(buildHoldDecorations(twoFeatureDoc(), new Set()).find().length).toBe(0);
    });

    it('ignores a held id with no matching heading (stale hold → no badge)', () => {
        expect(buildHoldDecorations(twoFeatureDoc(), new Set(['f-gone'])).find().length).toBe(0);
    });
});
