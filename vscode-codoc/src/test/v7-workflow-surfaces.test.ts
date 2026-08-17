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
 *   6. ghostEditsFor — accept-time edits ride the verdict only when they differ.
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
import { reviewDiffSpans, buildAutoEditDecorations } from '../webview/tiptap/auto-edit-decorations';
import {
    ghostEditsFor, resetGhostDrafts, setGhostDraft,
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

// ── 5. the auto-edit review diff ──────────────────────────────────────────────

describe('auto-edit reviewDiffSpans: both halves of the diff', () => {
    it('maps an insertion to an underline range in the live text', () => {
        const spans = reviewDiffSpans('parses files', 'parses and caches files', 10);
        const adds = spans.filter(s => s.kind === 'add');
        expect(adds.length).toBeGreaterThan(0);
    });
    it('keeps the deleted words — including in a REPLACEMENT (unlike the captured caret)', () => {
        const spans = reviewDiffSpans('uses FAISS for search', 'uses LanceDB for search', 0);
        const dels = spans.filter(s => s.kind === 'del');
        expect(dels.map(d => (d as { text: string }).text).join('')).toContain('FAISS');
        expect(spans.some(s => s.kind === 'add')).toBe(true);
    });
    it('returns nothing when the text is unchanged', () => {
        expect(reviewDiffSpans('same', 'same', 0)).toEqual([]);
    });
});

describe('auto-edit decorations: verdict strip anchored per rewritten feature', () => {
    it('marks the heading, the body, and one verdict widget per unresolved rewrite', () => {
        const doc = modelDoc();
        const set = buildAutoEditDecorations(doc, {
            'f-a': { at: 'hlc-1', prev: 'Old login prose.', written_by: 'human', rationale: 'code moved' },
        });
        const found = set.find();
        // heading node deco + verdict widget + paragraph node deco + inline/del marks ≥ 3
        expect(found.length).toBeGreaterThanOrEqual(3);
        // and nothing bleeds onto f-b
        let bPos = -1;
        doc.forEach((n, pos) => { if (n.attrs?.fid === 'f-b') bPos = pos; });
        expect(found.every(d => d.from <= bPos)).toBe(true);
    });
    it('draws nothing when every rewrite is resolved', () => {
        expect(buildAutoEditDecorations(modelDoc(), {}).find()).toHaveLength(0);
    });
});

// ── 6. ghost accept-time edits ────────────────────────────────────────────────

describe('ghostEditsFor: edits ride the verdict only when they differ', () => {
    const ghost: Suggestion = {
        id: 'e-1', direction: 'code-ahead', kind: 'add', featureId: null,
        originRole: 'claude-code', titleNew: 'Theme system', descNew: 'A switcher.',
    };
    beforeEach(() => resetGhostDrafts());

    it('untouched ghost → no edits payload (accept exactly as proposed)', () => {
        expect(ghostEditsFor(ghost)).toBeUndefined();
    });
    it('a reshaped title/description rides the accept', () => {
        setGhostDraft('e-1', 'title', 'Theme + contrast system');
        setGhostDraft('e-1', 'description', 'A better switcher.');
        expect(ghostEditsFor(ghost)).toEqual({
            title: 'Theme + contrast system', description: 'A better switcher.',
        });
    });
    it('typing the proposal back verbatim sends nothing', () => {
        setGhostDraft('e-1', 'title', 'Theme system');
        expect(ghostEditsFor(ghost)).toBeUndefined();
    });
    it('a blanked title never rides (an empty title would blank the node)', () => {
        setGhostDraft('e-1', 'title', '   ');
        expect(ghostEditsFor(ghost)).toBeUndefined();
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
