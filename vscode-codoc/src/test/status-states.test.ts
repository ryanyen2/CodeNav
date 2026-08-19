import { describe, it, expect } from 'vitest';
import { statusBarView, StatusBarInput } from '../state/status-presentation';

/** A baseline initialized, idle, in_sync input; override per case. */
function base(overrides: Partial<StatusBarInput> = {}): StatusBarInput {
    return {
        initialized: true,
        provisioning: false,
        agentActive: false,
        agentFileCount: 0,
        state: 'in_sync',
        pending: 0,
        detail: '',
        featureCount: 5,
        ...overrides,
    };
}

describe('statusBarView', () => {
    it('offers one-click setup when not initialized', () => {
        const v = statusBarView(base({ initialized: false }));
        expect(v.text).toContain('Set up codoc');
        expect(v.text).toContain('$(rocket)');
        expect(v.command).toBe('codoc.setup');
        expect(v.warn).toBe(false);
    });

    it('shows the provisioning state and takes precedence over not-initialized', () => {
        const v = statusBarView(base({ initialized: false, provisioning: true }));
        expect(v.text).toContain('setting up');
        expect(v.text).toContain('$(cloud-download)');
        expect(v.command).toBeUndefined(); // no click action mid-setup
    });

    it('provisioning takes precedence even over an initialized repo', () => {
        const v = statusBarView(base({ provisioning: true }));
        expect(v.text).toContain('setting up');
    });

    it('shows the agent-active state with the file count', () => {
        const v = statusBarView(base({ agentActive: true, agentFileCount: 3 }));
        expect(v.text).toContain('agent working');
        expect(v.text).toContain('(3 files)');
        expect(v.command).toBe('codoc.open');
    });

    it('warns (background) only on awaiting_impl', () => {
        const awaiting = statusBarView(base({ state: 'awaiting_impl', pending: 2 }));
        expect(awaiting.warn).toBe(true);
        expect(awaiting.text).toContain('2 queued, not running');

        for (const state of ['in_sync', 'code_drift', 'tree_dirty', 'realizing'] as const) {
            expect(statusBarView(base({ state, pending: 1 })).warn).toBe(false);
        }
    });

    it('shows realizing / tree_dirty spinners', () => {
        expect(statusBarView(base({ state: 'realizing' })).text).toContain('implementing');
        expect(statusBarView(base({ state: 'tree_dirty' })).text).toContain('applying tree edits');
    });

    it('shows proposal count on code_drift (singular vs plural)', () => {
        expect(statusBarView(base({ state: 'code_drift', pending: 1 })).text).toContain('1 proposal');
        expect(statusBarView(base({ state: 'code_drift', pending: 3 })).text).toContain('3 proposals');
    });

    it('shows the feature count when in sync', () => {
        const v = statusBarView(base({ state: 'in_sync', featureCount: 7 }));
        expect(v.text).toBe('$(check) codoc: 7');
        expect(v.command).toBe('codoc.open');
        expect(v.warn).toBe(false);
    });

    it('pending>0 in an otherwise in_sync state still surfaces proposals', () => {
        const v = statusBarView(base({ state: 'in_sync', pending: 2 }));
        expect(v.text).toContain('2 proposals');
    });
});

describe('the pill when the daemon is not consuming edits', () => {
    const base = {
        initialized: true, provisioning: false, agentActive: false,
        agentFileCount: 0, state: 'in_sync' as const, pending: 0,
        detail: '', featureCount: 25,
    };

    it('outranks the stale lifecycle, and says what to run', () => {
        // The lifecycle came from a status.json only the daemon updates. Showing
        // "in sync" here is repeating a dead process's last words.
        const v = statusBarView({ ...base, daemonDown: true });
        expect(v.text).toContain('not running');
        expect(v.warn).toBe(true);
        expect(v.tooltip).toContain('codoc watch');
    });

    it('yields to provisioning and to a missing .codoc', () => {
        expect(statusBarView({ ...base, daemonDown: true, provisioning: true }).text)
            .toContain('setting up');
        expect(statusBarView({ ...base, daemonDown: true, initialized: false }).text)
            .toContain('Set up codoc');
    });

    it('absent means the ordinary lifecycle states stand', () => {
        expect(statusBarView(base).text).toContain('25');
    });
});
