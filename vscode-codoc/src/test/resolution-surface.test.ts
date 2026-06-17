/**
 * resolution-surface.test.ts — U5 guard for the host→payload divergence passthrough.
 *
 * The divergence DECISION is daemon-side (codoc/loop/divergence.py, classified in
 * Loop A and persisted to resolution.json → sidecar.feature_resolution). The webview
 * just projects it: a feature flagged here gets the "review what the AI did" badge.
 * This pins the pure read contract; the badge rendering is an EDH concern.
 */
import { describe, it, expect } from 'vitest';
import { divergentFeatures, emptySidecar, type SidecarData } from '../state/bindings-model';

describe('U5 — divergentFeatures (host → payload mapping)', () => {
    it('returns the sidecar feature_resolution map verbatim', () => {
        const sidecar: SidecarData = { ...emptySidecar(), feature_resolution: { 'f-y': 'scope' } };
        expect(divergentFeatures(sidecar)).toEqual({ 'f-y': 'scope' });
    });

    it('defaults to no divergences when the sidecar predates the slice', () => {
        expect(divergentFeatures(emptySidecar())).toEqual({}); // tolerant for old sidecars
    });
});
