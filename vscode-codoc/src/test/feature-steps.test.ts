import { describe, it, expect } from 'vitest';
import {
    featureSteps, activeFeatureModes, stepCutoff,
    EPOCH_UI_TTL_MS, STEP_TTL_MS, type ActivityData,
} from '../state/activity-model';
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

// ── a step stops narrating once the agent has moved on ────────────────────────

describe('step staleness (STEP_TTL_MS, agent-relative)', () => {
    const iso = (ms: number): string => new Date(ms).toISOString();
    const T0 = Date.parse('2026-08-09T12:00:00.000Z');

    it('drops a feature the agent left behind, while the epoch is still open', () => {
        // The agent read sessions.py once, then spent the next few minutes elsewhere.
        // `recent` is pruned by COUNT, never by age, so both entries are still on disk.
        const data = openEpoch({
            recent: [
                { tool: 'Read', file: 'sessions.py', feature_ids: ['f-old'], at: iso(T0), phase: 'pre' },
                { tool: 'Edit', file: 'adapters.py', feature_ids: ['f-now'],
                  at: iso(T0 + STEP_TTL_MS + 1), phase: 'pre' },
            ],
        });
        const now = T0 + STEP_TTL_MS + 1;
        const steps = featureSteps(data, null, now, now);
        expect(steps.has('f-old')).toBe(false);
        expect(steps.get('f-now')!.map(s => s.label)).toEqual(['editing adapters.py']);
    });

    it('keeps a step that is still the newest thing the agent did, however long it runs', () => {
        // A 3-minute pytest is the newest event by the agent's own clock; a wall-clock
        // cutoff would blank the ribbon mid-run and claim the agent left.
        const data = openEpoch({
            recent: [{ tool: 'Bash', file: '', feature_ids: ['f-1'], at: iso(T0), phase: 'pre',
                       action: 'test', label: 'running pytest' }],
        });
        const later = T0 + 5 * STEP_TTL_MS;
        expect(featureSteps(data, null, later - 1_000, later).get('f-1')!.map(s => s.label))
            .toEqual(['running pytest']);
    });

    it('ages the touched fallback the same way', () => {
        const data = openEpoch({
            touched: {
                'old.py': { symbols: [], feature_ids: ['f-old'], last: iso(T0), mode: 'read' },
                'now.py': { symbols: [], feature_ids: ['f-now'],
                            last: iso(T0 + STEP_TTL_MS + 1), mode: 'write' },
            },
        });
        const now = T0 + STEP_TTL_MS + 1;
        const steps = featureSteps(data, null, now, now);
        expect(steps.has('f-old')).toBe(false);
        expect(steps.has('f-now')).toBe(true);
    });

    it('stops the tree row pulsing on a feature the agent left', () => {
        const data = openEpoch({
            touched: {
                'old.py': { symbols: [], feature_ids: ['f-old'], last: iso(T0), mode: 'write' },
                'now.py': { symbols: [], feature_ids: ['f-now'],
                            last: iso(T0 + STEP_TTL_MS + 1), mode: 'write' },
            },
        });
        const now = T0 + STEP_TTL_MS + 1;
        const modes = activeFeatureModes(data, null, now, now);
        expect(modes.has('f-old')).toBe(false);
        expect(modes.get('f-now')).toBe('write');
    });

    it('keeps everything when nothing carries a parseable timestamp (older activity.json)', () => {
        expect(stepCutoff([null, undefined, 'not-a-date'])).toBeNull();
        const data = openEpoch({
            recent: [{ tool: 'Edit', file: 'a.py', feature_ids: ['f1'], at: 'nope', phase: 'pre' }],
        });
        expect(featureSteps(data, null, Date.now()).has('f1')).toBe(true);
    });
});

// ── W1: action steps (Bash test runs / git verbs) in the ribbon ───────────────

describe('W1: action steps', () => {
    it('renders an action entry with its own label + kind, attributed only to its explicit fids', () => {
        const data = {
            epoch: { id: 'ep-1', origin: 'interactive', open: true, started_at: '', ended_at: null },
            touched: {},
            recent: [
                { tool: 'Edit', file: 'src/a.py', feature_ids: ['f-1'], at: 't1', phase: 'pre' },
                { tool: 'Bash', file: '', feature_ids: ['f-1'], at: 't2', phase: 'pre',
                  action: 'test', label: 'running pytest' },
                { tool: 'Bash', file: '', feature_ids: ['f-1'], at: 't3', phase: 'pre',
                  action: 'git', label: 'git commit' },
            ],
        } as never;
        const steps = featureSteps(data, null, Date.now());
        const f1 = steps.get('f-1') ?? [];
        expect(f1.map(s => s.label)).toEqual(['editing a.py', 'running pytest', 'git commit']);
        expect(f1.map(s => s.kind)).toEqual([undefined, 'test', 'git']);
        expect(f1.map(s => s.done)).toEqual([true, true, false]);  // last is active
    });

    it('an action with no editing feature attribution decorates nothing (no global spam)', () => {
        const data = {
            epoch: { id: 'ep-1', origin: 'interactive', open: true, started_at: '', ended_at: null },
            touched: {},
            recent: [
                { tool: 'Bash', file: '', feature_ids: [], at: 't1', phase: 'pre',
                  action: 'test', label: 'running vitest' },
            ],
        } as never;
        expect(featureSteps(data, null, Date.now()).size).toBe(0);
    });
});

// ── W1: per-agent identity ────────────────────────────────────────────────────
import { agentRole } from '../state/activity-model';

describe('W1: agentRole', () => {
    it('reads the epoch agent id', () => {
        expect(agentRole({ epoch: { id: 'ep-1', origin: 'interactive', open: true,
            started_at: '', ended_at: null, agent: { id: 'codex' } } } as never)).toBe('codex');
    });
    it('falls back to claude when identity is absent (legacy epoch)', () => {
        expect(agentRole({ epoch: { id: 'ep-1', origin: 'interactive', open: true,
            started_at: '', ended_at: null } } as never)).toBe('claude');
        expect(agentRole({} as never)).toBe('claude');
    });
});
