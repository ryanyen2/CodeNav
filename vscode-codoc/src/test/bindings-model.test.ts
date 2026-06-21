import { describe, it, expect } from 'vitest';
import {
    emptySidecar,
    SidecarData,
    FeatureMeta,
    FeatureKind,
    FeatureDrift,
    SeeAlsoEntry,
    kindForFeature,
    seeAlsoForFeature,
    driftForFeature,
    isUnrealized,
    lifecycleForFeature,
    phaseForFeature,
} from '../state/bindings-model';

// The TS side only READS the pitch (Python derives it). These tests pin the
// sidecar-v5 contract: emptySidecar() reports version 5, and a v5 sidecar's
// FeatureMeta carries an optional `pitch` the model surfaces.

describe('sidecar v5 / pitch', () => {
    it('emptySidecar reports version 5', () => {
        expect(emptySidecar().version).toBe(5);
    });

    it('FeatureMeta carries pitch from a v5 sidecar', () => {
        const sidecar: SidecarData = {
            ...emptySidecar(),
            features: {
                'f-a': {
                    title: 'Authentication',
                    parent_id: null,
                    realized: true,
                    pitch: 'Handles login and session creation.',
                },
            },
        };
        const meta: FeatureMeta = sidecar.features['f-a'];
        expect(meta.pitch).toBe('Handles login and session creation.');
    });

    it('pitch is optional — an older (< v5) sidecar still parses', () => {
        // No pitch field on the meta; the reader keys on presence, not version.
        const legacy: SidecarData = {
            version: 4,
            by_feature: {},
            by_file: {},
            features: { 'f-a': { title: 'Legacy', parent_id: null } },
        };
        expect(legacy.features['f-a'].pitch).toBeUndefined();
        expect(legacy.features['f-a'].title).toBe('Legacy');
    });
});

// B-U3: inferred structure — kind hint + See-Also. The TS side only READS these
// slices (Python derives them). Parity with codoc/codoc_file/render.py:
// _compute_kinds / _compute_see_also.

describe('sidecar v5 / inferred structure (kind + see_also)', () => {
    const sidecar: SidecarData = {
        ...emptySidecar(),
        feature_kind: {
            'f-theme': 'overview',
            'f-bound': 'reference',
            'f-leaf': 'unclassified',
            'f-gone': 'retired',
        } as Record<string, FeatureKind>,
        feature_see_also: {
            'f-bound': [
                { to: 'f-other', weight: 2, kinds: ['call'], rationale: 'call' },
                { to: 'f-third', weight: 1, kinds: ['import'], rationale: 'import' },
            ],
        },
    };

    it('kindForFeature surfaces the derived kind, absent ⇒ undefined', () => {
        expect(kindForFeature(sidecar, 'f-theme')).toBe('overview');
        expect(kindForFeature(sidecar, 'f-bound')).toBe('reference');
        expect(kindForFeature(sidecar, 'f-leaf')).toBe('unclassified');
        expect(kindForFeature(sidecar, 'f-gone')).toBe('retired');
        expect(kindForFeature(sidecar, 'f-missing')).toBeUndefined();
    });

    it('seeAlsoForFeature returns the ranked rows; empty when no edges', () => {
        const rows: SeeAlsoEntry[] = seeAlsoForFeature(sidecar, 'f-bound');
        expect(rows.map(r => r.to)).toEqual(['f-other', 'f-third']);
        expect(rows[0].weight).toBe(2);          // heaviest first
        expect(rows[0].rationale).toBe('call');  // edge-kind rationale
        expect(seeAlsoForFeature(sidecar, 'f-theme')).toEqual([]); // no edges
    });

    it('both slices are optional — a < v5 sidecar still parses', () => {
        const legacy: SidecarData = {
            version: 4, by_feature: {}, by_file: {},
            features: { 'f-a': { title: 'Legacy', parent_id: null } },
        };
        expect(legacy.feature_kind).toBeUndefined();
        expect(kindForFeature(legacy, 'f-a')).toBeUndefined();
        expect(seeAlsoForFeature(legacy, 'f-a')).toEqual([]);
    });
});

// B-U4: the loop-computed drift/trust slice. The TS side only READS it (the loop
// computes it; render re-emits it from drift.json). Parity with
// codoc/loop/loop_a.py:_compute_drift + codoc/loop/edits.py drift states.

describe('sidecar v5 / drift (feature_drift)', () => {
    const sidecar: SidecarData = {
        ...emptySidecar(),
        feature_drift: {
            'f-stale': 'questioned',
            'f-empty': 'binding-lost',
        } as Record<string, FeatureDrift>,
        // f-followed is deliberately ABSENT — followed = no badge.
    };

    it('driftForFeature surfaces the recorded state', () => {
        expect(driftForFeature(sidecar, 'f-stale')).toBe('questioned');
        expect(driftForFeature(sidecar, 'f-empty')).toBe('binding-lost');
    });

    it('a followed (absent) feature yields undefined → no badge', () => {
        expect(driftForFeature(sidecar, 'f-followed')).toBeUndefined();
        expect(driftForFeature(sidecar, 'f-missing')).toBeUndefined();
    });

    it('the slice is optional — a < v5 sidecar still parses', () => {
        const legacy: SidecarData = {
            version: 4, by_feature: {}, by_file: {},
            features: { 'f-a': { title: 'Legacy', parent_id: null } },
        };
        expect(legacy.feature_drift).toBeUndefined();
        expect(driftForFeature(legacy, 'f-a')).toBeUndefined();
    });
});

describe('A1 lifecycle + Proposal B feature_phase', () => {
    it('lifecycleForFeature prefers the named state, falls back to realized', () => {
        const sidecar: SidecarData = {
            ...emptySidecar(),
            features: {
                'f-plan': { title: 'Plan', parent_id: null, lifecycle: 'planned' },
                'f-act': { title: 'Active', parent_id: null, lifecycle: 'active' },
                // pre-A1 meta: only the legacy `realized` view present.
                'f-legacy-plan': { title: 'Legacy plan', parent_id: null, realized: false },
                'f-legacy-act': { title: 'Legacy active', parent_id: null },
            },
        };
        expect(lifecycleForFeature(sidecar, 'f-plan')).toBe('planned');
        expect(lifecycleForFeature(sidecar, 'f-act')).toBe('active');
        expect(lifecycleForFeature(sidecar, 'f-legacy-plan')).toBe('planned');
        expect(lifecycleForFeature(sidecar, 'f-legacy-act')).toBe('active');
    });

    it('isUnrealized reads the named lifecycle when present', () => {
        const sidecar: SidecarData = {
            ...emptySidecar(),
            features: {
                'f-plan': { title: 'Plan', parent_id: null, lifecycle: 'planned' },
                'f-act': { title: 'Active', parent_id: null, lifecycle: 'active' },
            },
        };
        expect(isUnrealized(sidecar, 'f-plan')).toBe(true);
        expect(isUnrealized(sidecar, 'f-act')).toBe(false);
    });

    it('phaseForFeature surfaces the projection; synced (absent) is undefined', () => {
        const sidecar: SidecarData = {
            ...emptySidecar(),
            feature_phase: { 'f-q': 'queued', 'f-d': 'drifted' },
        };
        expect(phaseForFeature(sidecar, 'f-q')).toBe('queued');
        expect(phaseForFeature(sidecar, 'f-d')).toBe('drifted');
        expect(phaseForFeature(sidecar, 'f-synced')).toBeUndefined();
    });

    it('the slices are optional — a pre-A1/B sidecar still parses', () => {
        const legacy: SidecarData = {
            version: 4, by_feature: {}, by_file: {},
            features: { 'f-a': { title: 'Legacy', parent_id: null } },
        };
        expect(legacy.feature_phase).toBeUndefined();
        expect(phaseForFeature(legacy, 'f-a')).toBeUndefined();
        expect(lifecycleForFeature(legacy, 'f-a')).toBe('active');
    });
});
