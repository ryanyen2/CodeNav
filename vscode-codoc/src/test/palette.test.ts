/**
 * palette.test.ts — the pure logic of the ⌘K command palette (P4 / spec §D). The DOM (the
 * floating card, scrim, keyboard nav, render) is EDH-only; this covers the matcher ranking +
 * highlight spans, the contextual command-set assembly, and the empty/welcome/no-match
 * selection — the parts that determine WHAT the palette shows and in what order.
 */
import { describe, it, expect } from 'vitest';
import {
    fuzzyMatch, rankItems, buildActions, buildFeatureItems, welcomeItems, createFeatureItem,
    sectionLabel, PaletteContext,
} from '../webview/palette';

const baseCtx = (over: Partial<PaletteContext> = {}): PaletteContext => ({
    features: [], driftFids: [], pendingFids: [], divergentFids: [],
    activeFid: null, activeTitle: '', activeHeld: false, activeBound: false,
    pendingEventCount: 0, draftCount: 0, caretInProposal: false, glance: false, featureCount: 0,
    ...over,
});

describe('fuzzyMatch — no-dep subsequence ranking (§D.1)', () => {
    it('returns null when the query is not a subsequence', () => {
        expect(fuzzyMatch('xyz', 'Loop A')).toBeNull();
    });
    it('matches case-insensitively and reports coalesced contiguous spans', () => {
        const r = fuzzyMatch('loop', 'Loop A')!;
        expect(r).not.toBeNull();
        expect(r.spans).toEqual([{ start: 0, end: 4 }]); // "Loop" is one contiguous run
    });
    it('ranks a word-boundary contiguous match above a scattered mid-word one', () => {
        const boundary = fuzzyMatch('loop', 'Loop A')!.score;
        const scattered = fuzzyMatch('loop', 'develOOPment')!.score;
        expect(boundary).toBeGreaterThan(scattered);
    });
    it('an empty query is a zero-score match with no spans', () => {
        expect(fuzzyMatch('', 'anything')).toEqual({ score: 0, spans: [] });
    });
    it('splits non-adjacent matches into separate spans', () => {
        const r = fuzzyMatch('lp', 'Loop')!; // l@0, p@3 — not contiguous
        expect(r.spans).toEqual([{ start: 0, end: 1 }, { start: 3, end: 4 }]);
    });
});

describe('rankItems — sort + cap', () => {
    const items = [{ t: 'Persist drafts' }, { t: 'Loop A' }, { t: 'Loop B' }, { t: 'Bindings' }];
    it('an empty query keeps input order, capped', () => {
        expect(rankItems('', items, i => i.t, 2).map(r => r.item.t)).toEqual(['Persist drafts', 'Loop A']);
    });
    it('a query filters to matches and ranks best-first', () => {
        const out = rankItems('loop', items, i => i.t).map(r => r.item.t);
        expect(out).toEqual(['Loop A', 'Loop B']); // both match; alpha-stable on tie
    });
    it('caps the result count', () => {
        const many = Array.from({ length: 50 }, (_, i) => ({ t: `Feature ${i}` }));
        expect(rankItems('feature', many, i => i.t, 30)).toHaveLength(30);
    });
});

describe('buildActions — contextual command set (§D.2)', () => {
    it('shows NO bulk/draft actions when nothing is applicable (no dead rows)', () => {
        const ids = buildActions(baseCtx()).map(a => a.action);
        expect(ids).not.toContain('accept-all');
        expect(ids).not.toContain('hand-off');
        expect(ids).not.toContain('withdraw');
        // toggle-glance + collapse/expand are always present
        expect(ids).toEqual(expect.arrayContaining(['toggle-glance', 'collapse-all', 'expand-all']));
    });
    it('surfaces accept/reject-all when proposals exist, with their C-icons', () => {
        const a = buildActions(baseCtx({ pendingEventCount: 2 }));
        const accept = a.find(x => x.action === 'accept-all')!;
        expect(accept.title).toBe('Accept all proposals (2)');
        expect(accept.icon).toBe('check-circle');
        expect(a.find(x => x.action === 'reject-all')?.icon).toBe('x-circle');
    });
    it('surfaces hand-off with the paper-plane icon when drafts exist', () => {
        const hand = buildActions(baseCtx({ draftCount: 1 })).find(x => x.action === 'hand-off')!;
        expect(hand.title).toBe('Hand to agent (1 draft)');
        expect(hand.icon).toBe('paper-plane-tilt');
    });
    it('surfaces withdraw + open-code only for an applicable active feature', () => {
        const a = buildActions(baseCtx({ activeFid: 'f-x', activeTitle: 'X', activeHeld: true, activeBound: true }));
        expect(a.find(x => x.action === 'withdraw')?.arg).toBe('f-x');
        expect(a.find(x => x.action === 'open-code')?.arg).toBe('f-x');
    });
    it('surfaces accept/reject-at-cursor only when the caret is in a proposal', () => {
        expect(buildActions(baseCtx({ caretInProposal: true })).some(x => x.action === 'accept-cursor')).toBe(true);
        expect(buildActions(baseCtx()).some(x => x.action === 'accept-cursor')).toBe(false);
    });
    it('the glance label reflects the current pref', () => {
        expect(buildActions(baseCtx({ glance: true })).find(x => x.action === 'toggle-glance')?.title).toBe('Turn Glance off');
        expect(buildActions(baseCtx({ glance: false })).find(x => x.action === 'toggle-glance')?.title).toBe('Toggle Glance');
    });
});

describe('buildFeatureItems — nav rows + status glyph + secondary action (§D.2)', () => {
    const ctx = baseCtx({
        features: [
            { id: 'f-a', title: 'A', bound: true, detail: '3 refs · a.py' },
            { id: 'f-b', title: 'B', bound: false },
            { id: 'f-c', title: 'C', bound: true },
        ],
        pendingFids: ['f-a'], divergentFids: ['f-c'],
    });
    it('a bound feature gets a ⇧↵ secondary (open bound code)', () => {
        const items = buildFeatureItems(ctx);
        expect(items.find(i => i.arg === 'f-a')?.hasSecondary).toBe(true);
        expect(items.find(i => i.arg === 'f-b')?.hasSecondary).toBe(false);
    });
    it('the leading glyph hints attention (pending → filled diamond, divergent → warning)', () => {
        const items = buildFeatureItems(ctx);
        expect(items.find(i => i.arg === 'f-a')?.icon).toBe('diamond-fill');   // pending
        expect(items.find(i => i.arg === 'f-c')?.icon).toBe('warning-diamond'); // divergent
        expect(items.find(i => i.arg === 'f-b')?.icon).toBeUndefined();         // plain
    });
});

describe('welcomeItems — the zero-typing dashboard (§D.3)', () => {
    const ctx = baseCtx({
        features: [{ id: 'f-a', title: 'A', bound: true }, { id: 'f-b', title: 'B', bound: false }],
        featureCount: 2, pendingEventCount: 2, draftCount: 1,
    });
    it('lists recent features (max 3) then quick actions', () => {
        const items = welcomeItems(ctx, ['f-b', 'f-a', 'gone', 'f-a']);
        const recent = items.filter(i => i.section === 'recent').map(i => i.title);
        expect(recent).toEqual(['B', 'A']); // 'gone' filtered (not live); the duplicate 'f-a' deduped
        expect(items.find(i => i.id === 'quick-review')?.title).toBe('2 proposals to review');
        expect(items.find(i => i.id === 'quick-handoff')?.title).toBe('1 draft to hand off');
    });
    it('keeps 3 live recents even when a stale fid sits in the first three (filter+dedupe before cap)', () => {
        const ctx3 = baseCtx({
            features: [{ id: 'f-a', title: 'A', bound: false }, { id: 'f-b', title: 'B', bound: false }, { id: 'f-c', title: 'C', bound: false }],
            featureCount: 3,
        });
        const recent = welcomeItems(ctx3, ['f-b', 'gone', 'f-a', 'f-c'])
            .filter(i => i.section === 'recent').map(i => i.title);
        expect(recent).toEqual(['B', 'A', 'C']); // slice-first would have dropped 'C' (only B,A)
    });
    it('a fresh repo (no features) shows only the init affordance', () => {
        const items = welcomeItems(baseCtx({ featureCount: 0 }), []);
        expect(items).toHaveLength(1);
        expect(items[0].title).toBe('Run codoc init to bootstrap the tree');
    });
});

describe('createFeatureItem — no-match authoring (§D.3)', () => {
    it('offers "Create feature \"xyz\"" for a non-empty query', () => {
        const item = createFeatureItem('  New thing ')!;
        expect(item.title).toBe('Create feature "New thing"');
        expect(item.action).toBe('create');
        expect(item.arg).toBe('New thing');
    });
    it('is null for an empty query (the welcome shows instead)', () => {
        expect(createFeatureItem('   ')).toBeNull();
    });
});

describe('sectionLabel', () => {
    it('names each section', () => {
        expect(sectionLabel('features')).toBe('Features');
        expect(sectionLabel('actions')).toBe('Actions');
        expect(sectionLabel('quick')).toBe('Quick actions');
    });
});
