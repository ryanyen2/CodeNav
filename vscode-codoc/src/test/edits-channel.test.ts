/**
 * edits-channel.test.ts — pins the host side of the `.codoc/edits.json`
 * provenance/intent channel (schema mirrored by codoc/loop/edits.py) plus the
 * v4 sidecar ledger consumers (agentAmendsByFeature, reconcileDoc pencil
 * re-stamp, causedBy on code-ahead suggestions).
 */
import { describe, it, expect } from 'vitest';
import {
    parseEditsFile, emptyEditsFile, annotationsForSettle, intentsFromSuggestions,
    appendCancellation,
} from '../state/edits-channel';
import { agentAmendsByFeature, type SidecarData } from '../state/bindings-model';
import { codeAheadSuggestions, type Suggestion } from '../state/suggestion-model';
import { reconcileDoc, groupByHeading } from '../state/doc-reconcile';
import { MARK_AUTHOR, type PMNode } from '../state/pm-doc';

const F = (id: string | null, title: string, description = ''): { id: string | null; title: string; description: string } =>
    ({ id, title, description });

describe('U6 — cancellations (realize withdraw)', () => {
    it('appendCancellation adds an entry the loop will drain', () => {
        const file = appendCancellation(emptyEditsFile(), 'f-x', 7);
        expect(file.cancellations).toEqual([{ feature_id: 'f-x', ts: 7 }]);
    });

    it('dedups a repeated withdraw of the same feature (latest ts wins)', () => {
        let file = appendCancellation(emptyEditsFile(), 'f-x', 1);
        file = appendCancellation(file, 'f-x', 9);
        expect(file.cancellations).toEqual([{ feature_id: 'f-x', ts: 9 }]);
    });

    it('preserves edits + intents alongside a cancellation', () => {
        const base = parseEditsFile({
            version: 1,
            edits: [{ feature_id: 'f-a', fields: ['title'], actor: 'human', mode: 'pen', ts: 1 }],
            intents: [{ id: 's1', feature_id: 'f-b', actor: 'human', ts: 1 }],
        });
        const file = appendCancellation(base, 'f-x', 2);
        expect(file.edits).toHaveLength(1);
        expect(file.intents).toHaveLength(1);
        expect(file.cancellations).toEqual([{ feature_id: 'f-x', ts: 2 }]);
    });

    it('parseEditsFile round-trips the cancellations list', () => {
        const file = parseEditsFile({ version: 1, edits: [], intents: [],
            cancellations: [{ feature_id: 'f-x', ts: 3 }] });
        expect(file.cancellations).toEqual([{ feature_id: 'f-x', ts: 3 }]);
    });
});

describe('annotationsForSettle', () => {
    it('annotates only the features whose title/description changed', () => {
        const prev = [F('f-1', 'Auth', 'Handles login.'), F('f-2', 'Util', 'Helpers.')];
        const next = [F('f-1', 'Auth', 'Handles login + logout.'), F('f-2', 'Util', 'Helpers.')];
        const anns = annotationsForSettle(prev, next, { actor: 'human', mode: 'pen', ts: 7 });
        expect(anns).toEqual([
            { feature_id: 'f-1', fields: ['description'], actor: 'human', mode: 'pen', ts: 7 },
        ]);
    });

    it('records both fields and carries the suggestion id', () => {
        const prev = [F('f-1', 'Auth', 'a')];
        const next = [F('f-1', 'Authentication', 'b')];
        const [a] = annotationsForSettle(prev, next,
            { actor: 'human', mode: 'pen', ts: 1, suggestionId: 'd-f-1' });
        expect(a.fields).toEqual(['title', 'description']);
        expect(a.suggestion_id).toBe('d-f-1');
    });

    it('ignores new features (no prior fid) — ADD defaults are fine', () => {
        const anns = annotationsForSettle([], [F('f-9', 'New', 'x')],
            { actor: 'human', mode: 'pen', ts: 1 });
        expect(anns).toEqual([]);
    });
});

describe('intentsFromSuggestions', () => {
    const sugg = (over: Partial<Suggestion>): Suggestion => ({
        id: 'd-f-1', direction: 'doc-ahead', kind: 'amend', featureId: 'f-1',
        originRole: 'human', ...over,
    });

    it('mirrors doc-ahead suggestions only (the hold set)', () => {
        const intents = intentsFromSuggestions([
            sugg({}),
            sugg({ id: 'e-2', direction: 'code-ahead', featureId: 'f-2' }),
            sugg({ id: 'd-x', featureId: null }),
        ], 5);
        expect(intents).toEqual([{ id: 'd-f-1', feature_id: 'f-1', actor: 'human', ts: 5 }]);
    });

    it('carries the suggested text as payload — only for fields the suggestion changes', () => {
        // The payload is what Loop B's intent drain applies (the agent-side
        // "apply"); an unchanged field must stay ABSENT so the loop never
        // clobbers it (mirrors codoc/loop/edits.py Intent).
        const intents = intentsFromSuggestions([
            sugg({ descOld: 'Validates input.', descNew: 'Should reject tabs.' }),
            sugg({ id: 'd-f-2', featureId: 'f-2', titleOld: 'Auth', titleNew: 'Sessions' }),
            sugg({ id: 'd-f-3', featureId: 'f-3', titleOld: 'Same', titleNew: 'Same' }),
        ], 5);
        expect(intents).toEqual([
            { id: 'd-f-1', feature_id: 'f-1', actor: 'human', ts: 5, description: 'Should reject tabs.' },
            { id: 'd-f-2', feature_id: 'f-2', actor: 'human', ts: 5, title: 'Sessions' },
            { id: 'd-f-3', feature_id: 'f-3', actor: 'human', ts: 5 },
        ]);
    });
});

describe('parseEditsFile', () => {
    it('tolerates garbage and missing keys', () => {
        expect(parseEditsFile(null)).toEqual(emptyEditsFile());
        expect(parseEditsFile({ edits: 'nope' })).toEqual(emptyEditsFile());
        const ok = parseEditsFile({ version: 1, edits: [], intents: [{ id: 'd', feature_id: 'f', actor: 'human', ts: 0 }] });
        expect(ok.intents).toHaveLength(1);
    });
});

const sidecarWith = (over: Partial<SidecarData>): SidecarData => ({
    version: 4, by_feature: {}, by_file: {}, features: {},
    proposals: { by_feature: {}, by_event: {} }, ...over,
});

describe('agentAmendsByFeature (v4 changes feed)', () => {
    it('keeps the newest non-human amend per feature', () => {
        const sc = sidecarWith({
            changes: [
                { event_id: 'e3', at: '3', kind: 'amend', feature_id: 'f-1', actor: 'claude-code', mode: 'auto', caused_by: '' },
                { event_id: 'e2', at: '2', kind: 'amend', feature_id: 'f-1', actor: 'human', mode: 'pen', caused_by: '' },
                { event_id: 'e1', at: '1', kind: 'amend', feature_id: 'f-2', actor: 'human', mode: 'pen', caused_by: '' },
                { event_id: 'e0', at: '0', kind: 'refresh', feature_id: 'f-3', actor: 'loop', mode: 'auto', caused_by: '' },
            ],
        });
        const m = agentAmendsByFeature(sc);
        expect(m.get('f-1')).toBe('claude-code');
        expect(m.has('f-2')).toBe(false);  // human amend — no pencil stamp
        expect(m.has('f-3')).toBe(false);  // refresh isn't prose
    });

    it('newest entry wins (feed is newest-first)', () => {
        const sc = sidecarWith({
            changes: [
                { event_id: 'e9', at: '9', kind: 'amend', feature_id: 'f-1', actor: 'human', mode: 'pen', caused_by: '' },
                { event_id: 'e1', at: '1', kind: 'amend', feature_id: 'f-1', actor: 'codex', mode: 'auto', caused_by: '' },
            ],
        });
        expect(agentAmendsByFeature(sc).has('f-1')).toBe(false);
    });
});

describe('reconcileDoc — agent pencil re-stamp', () => {
    const TREE = '- Auth  ⟨f-aaaaaaaa⟩\n    Validates and sanitizes input.\n';
    const SAVED_TREE = '- Auth  ⟨f-aaaaaaaa⟩\n    Validates input.\n';

    const savedDoc = (): PMNode => reconcileDoc(SAVED_TREE, null);

    it('stamps changed descriptions as the agent pencil when the feed names an agent', () => {
        const doc = reconcileDoc(TREE, savedDoc(), undefined, new Map([['f-aaaaaaaa', 'claude-code']]));
        const [g] = groupByHeading(doc);
        const run = g.blocks[0]?.content?.[0];
        const author = (run?.marks ?? []).find(m => m.type === MARK_AUTHOR);
        expect(author).toBeDefined();
        expect(author?.attrs?.role).toBe('claude-code');
        expect(author?.attrs?.mode).toBe('pencil');
    });

    it('still resets marks when no agent is attributed (human/raw-text drift)', () => {
        const doc = reconcileDoc(TREE, savedDoc(), undefined, new Map());
        const [g] = groupByHeading(doc);
        const run = g.blocks[0]?.content?.[0];
        expect((run?.marks ?? []).find(m => m.type === MARK_AUTHOR)).toBeUndefined();
    });
});

describe('codeAheadSuggestions — v4 provenance', () => {
    it('carries caused_by and prefers the ledger actor for the role', () => {
        const sc = sidecarWith({
            proposals: {
                by_feature: {
                    'f-1': { op: 'amend', event_id: 'e-1', tag: 'code drift', description: 'New prose.', actor: 'codex', mode: 'suggest', caused_by: 'd-11aa22bb' },
                },
                by_event: {
                    'e-2': { op: 'add', tag: 'agent reflection', title: 'Burst window', parent_id: null, actor: 'claude-code', caused_by: 'd-11aa22bb' },
                },
            },
        });
        const out = codeAheadSuggestions(sc, () => 'Auth', () => 'Old prose.');
        const amend = out.find(s => s.kind === 'amend');
        const add = out.find(s => s.kind === 'add');
        expect(amend?.causedBy).toBe('d-11aa22bb');
        expect(amend?.originRole).toBe('codex');
        expect(add?.causedBy).toBe('d-11aa22bb');
    });

    it('legacy sidecar (no provenance) keeps today’s behavior', () => {
        const sc = sidecarWith({
            proposals: { by_feature: { 'f-1': { op: 'retire', event_id: 'e-1', tag: 'code drift' } }, by_event: {} },
        });
        const [s] = codeAheadSuggestions(sc, () => '', () => '');
        expect(s.causedBy).toBeUndefined();
        expect(s.originRole).toBe('claude-code');
    });
});
