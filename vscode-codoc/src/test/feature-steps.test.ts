import { describe, it, expect } from 'vitest';
import { featureSteps, EPOCH_UI_TTL_MS, type ActivityData } from '../state/activity-model';
import { emptySidecar, type SidecarData } from '../state/bindings-model';

function openEpoch(over: Partial<ActivityData> = {}): ActivityData {
    return {
        epoch: { id: 'e', origin: 'interactive', open: true, started_at: null, ended_at: null },
        ...over,
    };
}

describe('featureSteps', () => {
    it('is empty when no epoch is open', () => {
        const data: ActivityData = {
            epoch: { id: 'e', origin: 'interactive', open: false, started_at: null, ended_at: null },
            recent: [{ tool: 'Edit', file: 'a.py', feature_ids: ['f1'], at: '1', phase: 'editing' }],
        };
        expect(featureSteps(data, null).size).toBe(0);
    });

    it('humanises recent tool calls into ordered steps; last is active', () => {
        const data = openEpoch({
            recent: [
                { tool: 'Read', file: 'src/agent.py', feature_ids: ['f1'], at: '1', phase: 'editing' },
                { tool: 'Edit', file: 'src/models.py', feature_ids: ['f1'], at: '2', phase: 'editing' },
                { tool: 'Bash', file: 'pytest', feature_ids: ['f1'], at: '3', phase: 'editing' },
            ],
        });
        const steps = featureSteps(data, null).get('f1')!;
        expect(steps.map(s => s.label)).toEqual(['reading agent.py', 'editing models.py', 'running pytest']);
        expect(steps.map(s => s.done)).toEqual([true, true, false]);  // last active
    });

    it('collapses consecutive duplicate labels', () => {
        const data = openEpoch({
            recent: [
                { tool: 'Edit', file: 'a.py', feature_ids: ['f1'], at: '1', phase: 'editing' },
                { tool: 'Edit', file: 'a.py', feature_ids: ['f1'], at: '2', phase: 'editing' },
            ],
        });
        expect(featureSteps(data, null).get('f1')!.map(s => s.label)).toEqual(['editing a.py']);
    });

    it('caps at 5 steps (older fall off the top)', () => {
        const recent = Array.from({ length: 8 }, (_, i) => ({
            tool: 'Edit', file: `f${i}.py`, feature_ids: ['f1'], at: String(i), phase: 'editing',
        }));
        const steps = featureSteps(openEpoch({ recent }), null).get('f1')!;
        expect(steps).toHaveLength(5);
        expect(steps[0].label).toBe('editing f3.py');   // 0..2 dropped
    });

    it('falls back to touched when no event log is present', () => {
        const data = openEpoch({
            touched: {
                'a.py': { symbols: [], feature_ids: ['f1'], last: null, mode: 'read' },
                'b.py': { symbols: [], feature_ids: ['f1'], last: null, mode: 'write' },
            },
        });
        const labels = featureSteps(data, null).get('f1')!.map(s => s.label);
        expect(labels).toContain('reading a.py');
        expect(labels).toContain('editing b.py');
    });

    it('goes empty once the epoch lease expires, even though open=true (WS1.1)', () => {
        const data = openEpoch({
            recent: [{ tool: 'Edit', file: 'a.py', feature_ids: ['f1'], at: '1', phase: 'editing' }],
        });
        const mtimeMs = 1_000_000;
        expect(featureSteps(data, null, mtimeMs, mtimeMs + EPOCH_UI_TTL_MS + 1).size).toBe(0);
    });

    it('resolves files to features via the sidecar by_file index', () => {
        const sidecar: SidecarData = { ...emptySidecar(), by_file: { 'x.py': [{ symbol: 's', feature_id: 'f9', feature_title: 'Nine' }] } };
        const data = openEpoch({ recent: [{ tool: 'Edit', file: 'x.py', feature_ids: [], at: '1', phase: 'editing' }] });
        expect(featureSteps(data, sidecar).get('f9')!.map(s => s.label)).toEqual(['editing x.py']);
    });

    it('does not fan a resolved edit out to every sibling feature bound to the same file', () => {
        // shared.py is bound to two features; the event was already resolved (by the
        // hook) to just f-one — the sidecar must not widen that back out to f-two.
        const sidecar: SidecarData = {
            ...emptySidecar(),
            by_file: { 'shared.py': [
                { symbol: 'One.run', feature_id: 'f-one', feature_title: 'One' },
                { symbol: 'Two.run', feature_id: 'f-two', feature_title: 'Two' },
            ] },
        };
        const data = openEpoch({ recent: [{ tool: 'Edit', file: 'shared.py', feature_ids: ['f-one'], at: '1', phase: 'editing' }] });
        const steps = featureSteps(data, sidecar);
        expect(steps.has('f-one')).toBe(true);
        expect(steps.has('f-two')).toBe(false);
    });
});
