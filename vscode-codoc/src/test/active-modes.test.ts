import { describe, it, expect } from 'vitest';
import { activeFeatureModes, featurePhases, ActivityData } from '../state/activity-model';
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
});
