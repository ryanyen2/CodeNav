/**
 * v7-workflow-surfaces.test.ts — the pure cores of the v7 workflow-legibility work:
 *
 *   1. translate-model — the `.codoc/translate.json` reader's lease honesty: a dead
 *      run must never skeleton-lock nodes, a finished run stops being news.
 *   2. doc-lang.pendingForTarget — the two-stage switch's honest count.
 *   3. busy-decorations — the skeleton spans + the per-section edit guard geometry.
 *   4. feature-state.railState — the minimap's ordered projection.
 *   5. auto-edit reviewDiffSpans — both halves of the in-situ diff (adds AND the
 *      struck old words; replacements are NOT suppressed like the captured caret).
 *   6. nodeEditsFor — accept-time edits ride the verdict only when they differ.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { Node as PMModelNode } from '@tiptap/pm/model';
import { codocSchema } from '../webview/tiptap/schema';
import {
    parseTranslateProgress, TRANSLATE_LEASE_MS, TRANSLATE_LINGER_MS,
    shouldQuietSkeleton,
} from '../state/translate-model';
import { pendingForTarget } from '../webview/doc-lang';
import {
    busyRanges, touchesBusy, buildBusyDecorations, type BusyInfo,
} from '../webview/tiptap/busy-decorations';
import { railState } from '../state/feature-state';
import { buildAutoEditDecorations } from '../webview/tiptap/auto-edit-decorations';
import { claimsFor } from '../state/settlement';
import {
    nodeEditsFor,
} from '../webview/tiptap/suggestion-decorations';
import type { Suggestion } from '../state/suggestion-model';

// ── 1. translate-model ────────────────────────────────────────────────────────

const NOW = 1_700_000_000_000;
const progress = (over: Record<string, unknown> = {}): string => JSON.stringify({
    version: 1, running: true, target: 'zh-Hans', target_name: 'Simplified Chinese',
    total: 4, translated: 1,
    skipped: [{ feature_id: 'f-s', title: 'Skipped', reason: 'dropped code citation' }],
    pending: ['f-a', 'f-b', 'f-c'], at: NOW / 1000, ...over,
});

describe('translate-model: lease-guarded progress', () => {
    it('reports a fresh running file as running, with its pending set', () => {
        const p = parseTranslateProgress(progress(), NOW - 5_000, NOW)!;
        expect(p.running).toBe(true);
        expect(p.pending).toEqual(['f-a', 'f-b', 'f-c']);
        expect(p.targetName).toBe('Simplified Chinese');
        expect(p.skipped[0].reason).toContain('citation');
    });

    it('reads a STALE running file as a dead run — and empties its pending set', () => {
        const p = parseTranslateProgress(progress(), NOW - TRANSLATE_LEASE_MS - 1, NOW);
        // Beyond the lease AND the linger, the whole block drops.
        expect(p).toBeNull();
    });

    it('a stale-but-recent crash inside the linger window still frees the skeletons', () => {
        // mtime is 4 minutes stale relative to a 5-minute lease… simulate a file
        // whose lease expired but linger has not (lease < age is impossible while
        // linger < lease, so pin the boundary the other way: running:false fresh).
        const p = parseTranslateProgress(progress({ running: false }), NOW - 1_000, NOW)!;
        expect(p.running).toBe(false);
        expect(p.pending).toEqual([]);   // never skeleton-lock for a run that ended
    });

    it('a long-finished run is no longer news (null past the linger)', () => {
        const p = parseTranslateProgress(
            progress({ running: false }), NOW - TRANSLATE_LINGER_MS - 1, NOW);
        expect(p).toBeNull();
    });

    it('degrades to null on garbage', () => {
        expect(parseTranslateProgress('{not json', NOW, NOW)).toBeNull();
        expect(parseTranslateProgress('{"no":"target"}', NOW, NOW)).toBeNull();
    });
});

// ── 2. pendingForTarget ───────────────────────────────────────────────────────

describe('doc-lang: pendingForTarget (the two-stage count)', () => {
    const nodes = [
        { id: 'f-en1' },                       // untagged → the tree's language
        { id: 'f-en2', lang: '' },             // blank tag = untagged
        { id: 'f-zh', lang: 'zh-Hans' },       // already in the target
        { id: 'f-ja', lang: 'ja' },            // a third language — still pending
    ];
    it('at click time (tree still en → target zh): untagged nodes count as pending', () => {
        expect(pendingForTarget(nodes, 'en', 'zh-Hans')).toEqual(['f-en1', 'f-en2', 'f-ja']);
    });
    it('after the switch (tree zh): only the tagged exceptions count', () => {
        // Post-switch the sidecar tags the English nodes; untagged now means zh.
        const after = [
            { id: 'f-en1', lang: 'en' }, { id: 'f-en2', lang: 'en' },
            { id: 'f-zh' }, { id: 'f-ja', lang: 'ja' },
        ];
        expect(pendingForTarget(after, 'zh-Hans', 'zh-Hans')).toEqual(['f-en1', 'f-en2', 'f-ja']);
    });
    it('no-op when everything already reads as the target', () => {
        expect(pendingForTarget([{ id: 'a' }], 'en', 'en')).toEqual([]);
    });
});

// ── 3. busy-decorations ───────────────────────────────────────────────────────

function modelDoc(): PMModelNode {
    return codocSchema().nodeFromJSON({
        type: 'doc',
        content: [
            { type: 'featureHeading', attrs: { fid: 'f-a', level: 0 }, content: [{ type: 'text', text: 'Auth' }] },
            { type: 'paragraph', content: [{ type: 'text', text: 'Login and sessions.' }] },
            { type: 'featureHeading', attrs: { fid: 'f-b', level: 0 }, content: [{ type: 'text', text: 'Data' }] },
            { type: 'paragraph', content: [{ type: 'text', text: 'Persistence.' }] },
        ],
    });
}
const busy = (fids: string[]): Map<string, BusyInfo> =>
    new Map(fids.map(f => [f, { kind: 'translating' as const, label: 'x' }]));

describe('busy-decorations: spans + the edit guard', () => {
    it('a busy feature spans its heading through its own body (not the next feature)', () => {
        const doc = modelDoc();
        const ranges = busyRanges(doc, busy(['f-a']));
        expect(ranges).toHaveLength(1);
        expect(ranges[0].fid).toBe('f-a');
        expect(ranges[0].from).toBe(0);
        // f-b's heading position — the span must stop exactly there.
        let bPos = -1;
        doc.forEach((n, pos) => { if (n.attrs?.fid === 'f-b') bPos = pos; });
        expect(ranges[0].to).toBe(bPos);
    });

    it('decorates each block in the span (heading + body), and nothing else', () => {
        const doc = modelDoc();
        const set = buildBusyDecorations(doc, busy(['f-b']));
        expect(set.find()).toHaveLength(2); // f-b's heading + its paragraph
    });

    it('touchesBusy: a splice inside the span hits; one outside does not', () => {
        const doc = modelDoc();
        const ranges = busyRanges(doc, busy(['f-a']));
        const trAt = (from: number, to: number) => ({
            docChanged: true,
            mapping: { maps: [{ forEach: (f: (a: number, b: number) => void) => f(from, to) }] },
        });
        // inside f-a's body
        expect(touchesBusy(trAt(3, 3), ranges)).toBe(true);
        // inside f-b (beyond f-a's span end)
        const bStart = ranges[0].to + 2;
        expect(touchesBusy(trAt(bStart, bStart), ranges)).toBe(false);
        // no doc change never blocks
        expect(touchesBusy({ docChanged: false, mapping: { maps: [] } }, ranges)).toBe(false);
    });
});

// ── 4. railState ──────────────────────────────────────────────────────────────

describe('feature-state: the minimap projection (first match wins)', () => {
    it('busy outranks everything — it explains why the rest is moving', () => {
        expect(railState({ busy: true, activeMode: 'write', proposalOp: 'amend' })).toBe('busy');
    });
    it('working > proposed > rewritten > sent > staged > planned > retired > settled', () => {
        expect(railState({ activeMode: 'write', proposalOp: 'amend' })).toBe('working');
        expect(railState({ proposalOp: 'amend', autoEdit: true })).toBe('proposed');
        expect(railState({ autoEdit: true, sent: true })).toBe('rewritten');
        expect(railState({ sent: true, staged: true })).toBe('sent');
        expect(railState({ staged: true, realized: false })).toBe('staged');
        expect(railState({ realized: false, retired: true })).toBe('planned');
        expect(railState({ retired: true })).toBe('retired');
        expect(railState({})).toBe('settled');
    });
    it('agrees with the row badge on the shared states (one story, two surfaces)', () => {
        // The row's `featureState` order is working→proposed→sent→staged→planned;
        // railState must not reorder any pair of those.
        expect(railState({ activeMode: 'read' })).toBe('working');
        expect(railState({ sent: true, realized: false })).toBe('sent');
    });
});

// ── 5. the auto-edit review diff — now the settlement model's CODE channel ────
//
// This module used to own both halves: the diff of what the loop rewrote AND the
// verdict on it. The diff moved to `state/settlement.ts`, where it is one of three
// channels sharing a grammar instead of a fourth encoding of its own; what these
// tests guarded is guarded there (settlement.test.ts, "claimsFor — the code channel"),
// including the half a captured caret could not say: the words that were displaced.

describe('the code channel says both halves of the rewrite', () => {
    const ft = (title: string, ...paras: string[]) => ({ title, paras });

    it('marks what the codebase now says, and keeps the words it displaced', () => {
        const projected = ft('T', 'uses LanceDB for search');
        const claims = claimsFor({
            code: { layerId: 'e-1', prev: ft('T', 'uses FAISS for search') },
            projected, live: projected,
        });
        const add = claims.find(c => c.channel === 'code' && c.edit === 'add')!;
        expect(projected.paras[0].slice(add.start, add.end)).toContain('LanceDB');
        expect(claims.some(c => c.channel === 'code' && c.removed?.includes('FAISS'))).toBe(true);
    });

    it('says nothing when the text is unchanged', () => {
        const same = ft('T', 'same');
        expect(claimsFor({ code: { layerId: 'e-1', prev: same }, projected: same, live: same }))
            .toEqual([]);
    });
});

describe('auto-edit decorations: the verdict, and only the verdict', () => {
    it('anchors one verdict widget per unresolved rewrite', () => {
        const doc = modelDoc();
        const found = buildAutoEditDecorations(doc, {
            'f-a': { at: 'hlc-1', prev: 'Old login prose.', written_by: 'human', rationale: 'code moved' },
        }).find();
        // Exactly one decoration now: the diff it is a verdict on is drawn by the
        // settlement layer, so this module no longer paints the prose at all.
        expect(found).toHaveLength(1);
        // …and nothing bleeds onto f-b.
        let bPos = -1;
        doc.forEach((n, pos) => { if (n.attrs?.fid === 'f-b') bPos = pos; });
        expect(found.every(d => d.from <= bPos)).toBe(true);
    });

    it('draws nothing when every rewrite is resolved', () => {
        expect(buildAutoEditDecorations(modelDoc(), {}).find()).toHaveLength(0);
    });
});


// ── 6. accept-time edits, read off the node itself ───────────────────────────
//
// A proposed ADD is materialized into the document now (state/plan-materialize.ts), so
// "edit it before accepting" is just editing the document. The module-level draft store
// this used to need — keyed by suggestion id, pruned on rebuild, guarded against leaking
// onto a later proposal that reused nothing but memory — is gone with the widget it
// served.

describe('nodeEditsFor: edits ride the verdict only when they differ', () => {
    const proposal: Suggestion = {
        id: 'e-1', direction: 'code-ahead', kind: 'add', featureId: null,
        originRole: 'claude-code', titleNew: 'Theme system', descNew: 'A switcher.',
    };
    /** The materialized node as it stands in the document, built through the real
     *  schema — the accept payload is read off actual nodes, so a hand-rolled stand-in
     *  would be testing a shape the production path never sees. */
    const node = (title: string, ...paras: string[]) => {
        const doc = codocSchema().nodeFromJSON({
            type: 'doc',
            content: [
                { type: 'featureHeading', attrs: { fid: null, level: 0, proposed: 'e-1' },
                  content: title ? [{ type: 'text', text: title }] : [] },
                ...paras.map(t => ({ type: 'paragraph', content: t ? [{ type: 'text', text: t }] : [] })),
            ],
        });
        const body: { node: PMModelNode }[] = [];
        doc.forEach(n => { if (n.type.name === 'paragraph') body.push({ node: n }); });
        return { heading: doc.child(0), body };
    };

    it('an untouched node sends nothing — accept exactly as proposed', () => {
        expect(nodeEditsFor(node('Theme system', 'A switcher.'), proposal)).toBeUndefined();
    });

    it('a reshaped title and description ride the accept', () => {
        expect(nodeEditsFor(node('Theme + contrast system', 'A better switcher.'), proposal))
            .toEqual({ title: 'Theme + contrast system', description: 'A better switcher.' });
    });

    it('a blanked title never rides — an empty title would blank the node', () => {
        const edits = nodeEditsFor(node('   ', 'A switcher.'), proposal);
        expect(edits?.title).toBeUndefined();
    });

    it('a cleared description DOES ride — emptying it is a real decision', () => {
        expect(nodeEditsFor(node('Theme system'), proposal)).toEqual({ description: '' });
    });
});

describe('a whole-document translation is guarded, not animated', () => {
    const run = (pending: number, total: number) => ({
        running: true, target: 'zh-Hans', targetName: 'Chinese (Simplified)',
        total, translated: total - pending, skipped: [], pending: Array.from(
            { length: pending }, (_, i) => `f-${i}`),
    });

    it('a few nodes in flight still shimmer — that is what the skeleton is for', () => {
        expect(shouldQuietSkeleton(run(3, 25))).toBe(false);
    });

    it('a fresh language, where every node is pending, does not', () => {
        // The reported bug: switching to a language the tree has never been in
        // put all 25 nodes in the pending set, and the whole document dimmed and
        // swept for the length of the run. The old prose was still there and
        // still true; only its language was about to change.
        expect(shouldQuietSkeleton(run(25, 25))).toBe(true);
    });

    it('and it comes back as the run drains past halfway', () => {
        expect(shouldQuietSkeleton(run(13, 25))).toBe(true);
        expect(shouldQuietSkeleton(run(12, 25))).toBe(false);
    });

    it('a run with nothing pending is never quiet, because it is not busy at all', () => {
        expect(shouldQuietSkeleton(run(0, 25))).toBe(false);
    });

    it('a total of zero cannot divide, and reads as not-quiet', () => {
        expect(shouldQuietSkeleton(run(0, 0))).toBe(false);
    });
});
