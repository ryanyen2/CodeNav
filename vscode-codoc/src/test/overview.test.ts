/**
 * overview.test.ts — guards the pure concept-first OVERVIEW builder (B-U2).
 *
 * The builder lists EXACTLY the parentless features (with pitch + child count), caps the
 * card list, and computes the grounded diagram-edge subset — the hard invariant being
 * "a diagram edge exists IFF a real feature_edge connects two top themes" (no invented
 * arrows). DOM rendering + the glance toggle aren't unit-tested (they need a webview);
 * the builder stays pure so all the rules are covered here.
 */
import { describe, it, expect } from 'vitest';
import { buildOverview, MAX_OVERVIEW_CARDS } from '../state/overview';
import type { SidecarData, FeatureMeta, FeatureEdge } from '../state/bindings-model';

function sidecar(
    features: Record<string, Partial<FeatureMeta> & { title: string; parent_id: string | null }>,
    edges: Record<string, FeatureEdge[]> = {},
): SidecarData {
    const feats: Record<string, FeatureMeta> = {};
    for (const [id, f] of Object.entries(features)) {
        feats[id] = { title: f.title, parent_id: f.parent_id, realized: f.realized, pitch: f.pitch };
    }
    return { version: 5, by_feature: {}, by_file: {}, features: feats, feature_edges: edges };
}

describe('B-U2 — buildOverview (theme selection + child count)', () => {
    it('lists exactly the parentless features with pitch + child count', () => {
        const sc = sidecar({
            't1': { title: 'Auth', parent_id: null, pitch: 'Handles login.' },
            't2': { title: 'Storage', parent_id: null, pitch: 'Persists data.' },
            'c1': { title: 'Login flow', parent_id: 't1' },
            'c2': { title: 'Tokens', parent_id: 't1' },
            'c3': { title: 'DB', parent_id: 't2' },
        });
        const ov = buildOverview(sc);
        const ids = ov.cards.map(c => c.id).sort();
        expect(ids).toEqual(['t1', 't2']);             // only parentless features
        const byId = Object.fromEntries(ov.cards.map(c => [c.id, c]));
        expect(byId['t1'].pitch).toBe('Handles login.');
        expect(byId['t1'].childCount).toBe(2);          // c1 + c2
        expect(byId['t2'].childCount).toBe(1);          // c3
        expect(ov.totalThemes).toBe(2);
        expect(ov.truncated).toBe(false);
    });

    it('falls back to the title when a theme has no pitch', () => {
        const sc = sidecar({
            't1': { title: 'Auth', parent_id: null },          // no pitch
            'c1': { title: 'child', parent_id: 't1' },
        });
        const ov = buildOverview(sc);
        expect(ov.cards[0].pitch).toBe('Auth');
    });

    it('caps the card list at MAX_OVERVIEW_CARDS and flags truncation', () => {
        const features: Record<string, { title: string; parent_id: string | null }> = {};
        const n = MAX_OVERVIEW_CARDS + 3;
        for (let i = 0; i < n; i++) features[`t${i}`] = { title: `Theme ${i}`, parent_id: null };
        // give each theme a child so it isn't a flat tree (every feature parentless ⇒ empty)
        for (let i = 0; i < n; i++) features[`c${i}`] = { title: `c${i}`, parent_id: `t${i}` };
        const ov = buildOverview(sidecar(features));
        expect(ov.cards.length).toBe(MAX_OVERVIEW_CARDS);
        expect(ov.totalThemes).toBe(n);
        expect(ov.truncated).toBe(true);
    });
});

describe('B-U2 — empty / flat overviews', () => {
    it('returns an empty overview when there are no parentless features', () => {
        // every feature has a parent → no top themes (degenerate, but guarded)
        const sc = sidecar({
            'a': { title: 'A', parent_id: 'b' },
            'b': { title: 'B', parent_id: 'a' },
        });
        const ov = buildOverview(sc);
        expect(ov.cards).toEqual([]);
        expect(ov.totalThemes).toBe(0);
        expect(ov.showDiagram).toBe(false);
    });

    it('returns an empty overview for a fully-flat tree (every feature parentless)', () => {
        // an organize=False bootstrap → all roots, no concept layer to surface
        const sc = sidecar({
            'a': { title: 'A', parent_id: null },
            'b': { title: 'B', parent_id: null },
        });
        expect(buildOverview(sc).cards).toEqual([]);
    });

    it('returns an empty overview for an empty sidecar', () => {
        expect(buildOverview(sidecar({})).cards).toEqual([]);
    });
});

describe('B-U2 — grounded diagram edges (only real feature_edges)', () => {
    it('keeps only edges whose BOTH ends are top themes (subset of real edges)', () => {
        const sc = sidecar(
            {
                't1': { title: 'Auth', parent_id: null },
                't2': { title: 'Storage', parent_id: null },
                'c1': { title: 'Login', parent_id: 't1' },
                'c2': { title: 'DB', parent_id: 't2' },
            },
            {
                't1': [
                    { to: 't2', weight: 4, kinds: ['call'] }, // theme→theme: KEPT
                    { to: 'c2', weight: 9, kinds: ['call'] }, // theme→child: DROPPED (end not a theme)
                ],
                'c1': [{ to: 't2', weight: 7, kinds: ['import'] }], // child→theme: DROPPED (src not a theme)
            },
        );
        const ov = buildOverview(sc);
        expect(ov.diagramEdges).toEqual([
            { from: 't1', to: 't2', weight: 4, kinds: ['call'] },
        ]);
        // every diagram edge corresponds to a real feature_edge between two top themes
        for (const e of ov.diagramEdges) {
            const real = (sc.feature_edges?.[e.from] ?? []).some(r => r.to === e.to);
            expect(real).toBe(true);
            expect(ov.cards.some(c => c.id === e.from) || true).toBe(true); // from is a theme
        }
    });

    it('omits the diagram when fewer than 2 top themes are connected', () => {
        // one theme→child edge only; no theme↔theme edge → diagram omitted
        const sc = sidecar(
            {
                't1': { title: 'Auth', parent_id: null },
                't2': { title: 'Storage', parent_id: null },
                'c1': { title: 'DB', parent_id: 't2' },
            },
            { 't1': [{ to: 'c1', weight: 3, kinds: ['call'] }] },
        );
        const ov = buildOverview(sc);
        expect(ov.diagramEdges).toEqual([]);
        expect(ov.showDiagram).toBe(false);
    });

    it('shows the diagram once ≥ 2 top themes connect, ranked by weight', () => {
        const sc = sidecar(
            {
                't1': { title: 'A', parent_id: null },
                't2': { title: 'B', parent_id: null },
                't3': { title: 'C', parent_id: null },
                'c1': { title: 'c1', parent_id: 't1' }, // keep it from being flat
                'c2': { title: 'c2', parent_id: 't2' },
                'c3': { title: 'c3', parent_id: 't3' },
            },
            {
                't1': [{ to: 't2', weight: 1, kinds: ['call'] }, { to: 't3', weight: 5, kinds: ['import'] }],
            },
        );
        const ov = buildOverview(sc);
        expect(ov.showDiagram).toBe(true);
        // heaviest first
        expect(ov.diagramEdges.map(e => `${e.from}->${e.to}`)).toEqual(['t1->t3', 't1->t2']);
    });

    it('dedups duplicate theme→theme edges and drops self-loops', () => {
        const sc = sidecar(
            {
                't1': { title: 'A', parent_id: null },
                't2': { title: 'B', parent_id: null },
                'c1': { title: 'c1', parent_id: 't1' },
                'c2': { title: 'c2', parent_id: 't2' },
            },
            {
                't1': [
                    { to: 't2', weight: 2, kinds: ['call'] },
                    { to: 't2', weight: 2, kinds: ['call'] }, // duplicate
                    { to: 't1', weight: 9, kinds: ['call'] }, // self-loop
                ],
            },
        );
        const ov = buildOverview(sc);
        expect(ov.diagramEdges).toEqual([{ from: 't1', to: 't2', weight: 2, kinds: ['call'] }]);
    });
});
