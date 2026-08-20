import { describe, it, expect } from 'vitest';
import {
    queuesCodeWork, realizeOffer, verdictConsequences, statusBarView,
    RUN_QUEUE_ACTION, type StatusBarInput,
} from '../state/status-presentation';
import { isAgentActive, EPOCH_SPAWN_TTL_MS, EPOCH_UI_TTL_MS } from '../state/activity-model';
import type { ProposalsMap } from '../state/bindings-model';

// The bug this covers: accepting a /codoc:plan node outside the agent's blocking
// `codoc_await_verdicts` window mints a handed-off directive into .codoc/realize.md,
// stamps `awaiting_impl`, and then NOTHING runs it — the user's workaround was
// typing a throwaway message into Claude Code to trip the UserPromptSubmit hook.

describe('the decision to offer running the queue', () => {
    it('offers on an accepted build with no live session', () => {
        const offer = realizeOffer({
            accept: true, consequences: ['build'], sessionLive: false,
        });
        expect(offer).not.toBeNull();
        expect(offer!.action).toBe(RUN_QUEUE_ACTION);
        expect(offer!.message).toContain('1 change to build');
        expect(offer!.message).toContain('no coding-agent session is running');
    });

    it('stays quiet while a session is live — that session implements it', () => {
        expect(realizeOffer({
            accept: true, consequences: ['build'], sessionLive: true,
        })).toBeNull();
    });

    it('stays quiet on a reject — a reject hands nothing to anybody', () => {
        expect(realizeOffer({
            accept: false, consequences: ['build', 'remove'], sessionLive: false,
        })).toBeNull();
    });

    it('stays quiet when every accept only reconciles the tree to existing code', () => {
        expect(realizeOffer({
            accept: true, consequences: ['record', 'record'], sessionLive: false,
        })).toBeNull();
        expect(queuesCodeWork(true, ['record'])).toBe(false);
    });

    it('names deletion explicitly, because a remove accept deletes source', () => {
        const offer = realizeOffer({
            accept: true, consequences: ['remove', 'remove'], sessionLive: false,
        });
        expect(offer!.message).toContain('2 code removals');
    });

    it('counts only the code-implying half of a mixed batch', () => {
        const offer = realizeOffer({
            accept: true,
            consequences: ['record', 'build', 'record', 'remove'],
            sessionLive: false,
        });
        expect(offer!.message).toContain('2 changes to build and to remove code');
        expect(offer!.message).toContain('implement them');
    });
});

describe('reading the consequence off the sidecar the buttons were drawn from', () => {
    const proposals: ProposalsMap = {
        by_feature: {
            'f-1': { op: 'retire', event_id: 'e-del', tag: 'agent plan', writes_code: 'remove' },
            'f-2': { op: 'amend', event_id: 'e-say', tag: 'code drift', writes_code: null },
        },
        by_event: {
            'e-add': { op: 'add', tag: 'agent plan', writes_code: 'build' },
            'e-move': { op: 'move', tag: 'code drift', writes_code: null },
            // An older daemon against a newer IDE: no writes_code field at all.
            'e-legacy': { op: 'add', tag: 'agent plan' },
        },
    };

    it('resolves both halves of the overlay and the legacy plan tag', () => {
        expect(verdictConsequences(proposals, ['e-del', 'e-say', 'e-add', 'e-move', 'e-legacy']))
            .toEqual(['remove', 'record', 'build', 'record', 'build']);
    });

    it('treats an unknown event id as record — never a reason to start an agent', () => {
        expect(verdictConsequences(proposals, ['e-nope'])).toEqual(['record']);
        expect(verdictConsequences(undefined, ['e-add'])).toEqual(['record']);
    });

    it('an accept-all over a plan is exactly the reported case', () => {
        const ids = ['e-add', 'e-legacy', 'e-say'];
        const consequences = verdictConsequences(proposals, ids);
        expect(queuesCodeWork(true, consequences)).toBe(true);
        expect(realizeOffer({ accept: true, consequences, sessionLive: false })).not.toBeNull();
    });
});

describe('which lease answers "is a session live?"', () => {
    // A session parked inside the blocking codoc_await_verdicts renews activity.json
    // only on tool calls, so the 90 s DISPLAY lease calls it dead within a minute.
    // Offering there would start a second agent over the very turn waiting for the
    // accept, so the spawn tier waits far longer before believing it is gone.
    const open = { epoch: { id: 'ep-1', origin: 'interactive' as const, open: true, started_at: null, ended_at: null } };
    const now = 1_000_000_000;

    it('the display tier gives up on a quiet-but-live session; the spawn tier does not', () => {
        const quietFor = 5 * 60_000;
        expect(isAgentActive(open, now - quietFor, now)).toBe(false);
        expect(isAgentActive(open, now - quietFor, now, EPOCH_SPAWN_TTL_MS)).toBe(true);
    });

    it('a closed epoch is dead on every tier — the Stop hook ends EVERY turn', () => {
        const closed = { epoch: { ...open.epoch, open: false } };
        expect(isAgentActive(closed, now, now, EPOCH_SPAWN_TTL_MS)).toBe(false);
    });

    it('the spawn lease still expires, so a hard-killed session self-heals', () => {
        expect(isAgentActive(open, now - (EPOCH_SPAWN_TTL_MS + 1), now, EPOCH_SPAWN_TTL_MS)).toBe(false);
        expect(EPOCH_SPAWN_TTL_MS).toBeGreaterThan(EPOCH_UI_TTL_MS);
    });
});

describe('the waiting status bar names the missing agent, not a dead daemon', () => {
    const base: StatusBarInput = {
        initialized: true, provisioning: false, agentActive: false, agentFileCount: 0,
        state: 'awaiting_impl', pending: 3, detail: '', featureCount: 12,
    };

    it('does not read as work in progress', () => {
        const v = statusBarView(base);
        expect(v.text).toContain('queued, no agent');
        // The daemon-scoped sentence belongs to the daemonDown branch alone. Saying it
        // here sent authors hunting for a dead daemon that was visibly printing passes.
        expect(v.text).not.toContain('daemon not running');
        expect(v.tooltip).toContain('nothing is implementing them');
        expect(v.warn).toBe(true);
    });

    it('ignores the daemon detail, which repeats the same half-truth', () => {
        const v = statusBarView({ ...base, detail: '3 change(s) ready to implement — run /codoc:sync' });
        expect(v.tooltip).toContain('nothing is implementing');
    });

    it('agrees in the singular', () => {
        expect(statusBarView({ ...base, pending: 1 }).tooltip)
            .toContain('1 accepted tree edit is queued');
    });
});
