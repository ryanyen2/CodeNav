import { describe, it, expect } from 'vitest';
import {
    emptySidecar,
    SidecarData,
    FeatureMeta,
    FeatureKind,
    SeeAlsoEntry,
    kindForFeature,
    seeAlsoForFeature,
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
