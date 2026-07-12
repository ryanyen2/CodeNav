import { describe, it, expect } from 'vitest';
import { activeFeatureModes, featurePhases, isAgentActive, ActivityData, EPOCH_UI_TTL_MS, FEATURE_PHASE_TTL_MS } from '../state/activity-model';
import { SidecarData, emptySidecar } from '../state/bindings-model';

function openEpoch(over: Partial<ActivityData> = {}): ActivityData {
    return {
        epoch: { id: 'ep-1', origin: 'interactive', open: true, started_at: null, ended_at: null },
        touched: {},
        ...over,
    };
}

describe('activeFeatureModes', () => {
    it('is empty when no epoch is open', () => {
        const data: ActivityData = {
            epoch: { id: 'ep-1', origin: 'interactive', open: false, started_at: null, ended_at: null },
            touched: { 'a.py': { symbols: [], feature_ids: ['f-a'], last: null, mode: 'write' } },
        };
        expect(activeFeatureModes(data, null).size).toBe(0);
    });

    it('maps explicit feature_ids to their touch mode', () => {
        const data = openEpoch({ touched: { 'a.py': { symbols: [], feature_ids: ['f-a'], last: null, mode: 'write' } } });
        expect(activeFeatureModes(data, null).get('f-a')).toBe('write');
    });

    it('write wins over read for the same feature', () => {
        const data = openEpoch({
            touched: {
                'a.py': { symbols: [], feature_ids: ['f-a'], last: null, mode: 'read' },
                'b.py': { symbols: [], feature_ids: ['f-a'], last: null, mode: 'write' },
            },
        });
        expect(activeFeatureModes(data, null).get('f-a')).toBe('write');
    });

    it('resolves features via the sidecar by_file when feature_ids are absent', () => {
        const sc: SidecarData = {
            ...emptySidecar(),
            by_file: { 'a.py': [{ symbol: 'a.py::foo', feature_id: 'f-a', feature_title: 'A' }] },
        };
        const data = openEpoch({ touched: { 'a.py': { symbols: [], feature_ids: [], last: null, mode: 'read' } } });
        expect(activeFeatureModes(data, sc).get('f-a')).toBe('read');
    });

    it('goes empty once the epoch lease expires, even though open=true (WS1.1)', () => {
        const data = openEpoch({ touched: { 'a.py': { symbols: [], feature_ids: ['f-a'], last: null, mode: 'write' } } });
        const mtimeMs = 1_000_000;
        expect(activeFeatureModes(data, null, mtimeMs, mtimeMs + EPOCH_UI_TTL_MS + 1).size).toBe(0);
    });

    it('does not widen an already-resolved touch out to a sibling feature sharing the file', () => {
        const sc: SidecarData = {
            ...emptySidecar(),
            by_file: { 'shared.py': [
                { symbol: 'One.run', feature_id: 'f-one', feature_title: 'One' },
                { symbol: 'Two.run', feature_id: 'f-two', feature_title: 'Two' },
            ] },
        };
        const data = openEpoch({ touched: { 'shared.py': { symbols: [], feature_ids: ['f-one'], last: null, mode: 'write' } } });
        const modes = activeFeatureModes(data, sc);
        expect(modes.get('f-one')).toBe('write');
        expect(modes.has('f-two')).toBe(false);
    });
});

describe('featurePhases', () => {
    it('reads the per-feature phase block', () => {
        const data: ActivityData = { features: { 'f-a': { phase: 'editing' }, 'f-b': { phase: 'done' } } };
        const phases = featurePhases(data);
        expect(phases.get('f-a')).toBe('editing');
        expect(phases.get('f-b')).toBe('done');
    });

    it('is empty when the block is absent', () => {
        expect(featurePhases({}).size).toBe(0);
    });

    it('keeps an entry with no `at` (no lease info, no staleness verdict)', () => {
        const data: ActivityData = { features: { 'f-a': { phase: 'editing' } } };
        expect(featurePhases(data, 1_000_000).get('f-a')).toBe('editing');
    });

    it('keeps a fresh entry within the TTL', () => {
        const now = 1_000_000;
        const data: ActivityData = { features: { 'f-a': { phase: 'editing', at: new Date(now - 1000).toISOString() } } };
        expect(featurePhases(data, now).get('f-a')).toBe('editing');
    });

    it('drops a stale entry past the TTL (WS1.2) — an interrupted session must not stick forever', () => {
        const now = 1_000_000;
        const at = new Date(now - FEATURE_PHASE_TTL_MS - 1).toISOString();
        const data: ActivityData = { features: { 'f-a': { phase: 'editing', at } } };
        expect(featurePhases(data, now).has('f-a')).toBe(false);
    });
});

describe('isAgentActive (lease, WS1.1)', () => {
    it('trusts the raw flag when no mtime is supplied (backward compat)', () => {
        const data: ActivityData = { epoch: { id: 'e', origin: 'interactive', open: true, started_at: null, ended_at: null } };
        expect(isAgentActive(data)).toBe(true);
    });

    it('is false when the epoch is closed, regardless of mtime', () => {
        const data: ActivityData = { epoch: { id: 'e', origin: 'interactive', open: false, started_at: null, ended_at: null } };
        expect(isAgentActive(data, 1_000_000, 1_000_000)).toBe(false);
    });

    it('is alive when open and written within the TTL', () => {
        const data: ActivityData = { epoch: { id: 'e', origin: 'interactive', open: true, started_at: null, ended_at: null } };
        const mtimeMs = 1_000_000;
        expect(isAgentActive(data, mtimeMs, mtimeMs + EPOCH_UI_TTL_MS - 1)).toBe(true);
    });

    it('is dead when open=true but the file has not been written in the TTL (a hard-killed session)', () => {
        const data: ActivityData = { epoch: { id: 'e', origin: 'interactive', open: true, started_at: null, ended_at: null } };
        const mtimeMs = 1_000_000;
        expect(isAgentActive(data, mtimeMs, mtimeMs + EPOCH_UI_TTL_MS + 1)).toBe(false);
    });
});
