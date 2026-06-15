import { describe, it, expect } from 'vitest';
import { emptySidecar, SidecarData, FeatureMeta } from '../state/bindings-model';

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
