/**
 * suggestion-model.test.ts — R2 unified-suggestion guard.
 */
import { describe, it, expect } from 'vitest';
import {
    codeAheadSuggestions,
    buildSuggestions,
    suggestionsForFeature,
    addsUnderParent,
    parseDocFile,
    emptyDocFile,
    Suggestion,
} from '../state/suggestion-model';
import { emptySidecar, SidecarData } from '../state/bindings-model';
import { makeDoc, featureHeadingNode, textToInlineRuns } from '../state/pm-doc';

function sidecarWith(proposals: SidecarData['proposals']): SidecarData {
    return { ...emptySidecar(), proposals };
}

const curTitle = (fid: string) => ({ 'f-a': 'Auth', 'f-b': 'Data' }[fid] ?? '');
const curDesc = (fid: string) => ({ 'f-a': 'Login and sessions.', 'f-b': 'Storage.' }[fid] ?? '');

describe('R2: codeAheadSuggestions from sidecar proposals', () => {
    it('builds an amend suggestion with old (settled) + new (proposed) for both fields', () => {
        const sc = sidecarWith({
            by_feature: { 'f-a': { op: 'amend', event_id: 'e-1', tag: 'agent reflection', title: 'Authentication', description: 'Login, sessions, and OAuth.' } },
            by_event: {},
        });
        const [s] = codeAheadSuggestions(sc, curTitle, curDesc);
        expect(s).toMatchObject({
            direction: 'code-ahead', kind: 'amend', featureId: 'f-a', eventId: 'e-1',
            titleOld: 'Auth', titleNew: 'Authentication',
            descOld: 'Login and sessions.', descNew: 'Login, sessions, and OAuth.',
        });
    });

    it('builds a retire suggestion', () => {
        const sc = sidecarWith({ by_feature: { 'f-b': { op: 'retire', event_id: 'e-2', tag: 'code drift' } }, by_event: {} });
        expect(codeAheadSuggestions(sc, curTitle, curDesc)[0]).toMatchObject({ kind: 'retire', featureId: 'f-b', eventId: 'e-2', direction: 'code-ahead' });
    });

    it('builds add/move suggestions from by_event', () => {
        const sc = sidecarWith({
            by_feature: {},
            by_event: {
                'e-3': { op: 'add', tag: 'agent plan', parent_id: 'f-a', title: 'OAuth', description: 'Third-party login.' },
                'e-4': { op: 'move', tag: 'code drift', parent_id: 'f-b', feature_id: 'f-x' },
            },
        });
        const got = codeAheadSuggestions(sc, curTitle, curDesc);
        expect(got.find(s => s.kind === 'add')).toMatchObject({ parentId: 'f-a', titleNew: 'OAuth', descNew: 'Third-party login.', eventId: 'e-3' });
        expect(got.find(s => s.kind === 'move')).toMatchObject({ featureId: 'f-x', parentId: 'f-b', eventId: 'e-4' });
    });
});

describe('R2: buildSuggestions (agent code-ahead from the sidecar)', () => {
    it('returns the code-ahead proposals (no doc-ahead since U3/U2b)', () => {
        const sc = sidecarWith({ by_feature: { 'f-a': { op: 'amend', event_id: 'e-1', tag: 'code drift', title: 'X' } }, by_event: {} });
        const all = buildSuggestions(sc, curTitle, curDesc);
        expect(all).toHaveLength(1);
        expect(all[0]).toMatchObject({ direction: 'code-ahead', featureId: 'f-a' });
    });

    it('suggestionsForFeature / addsUnderParent filter correctly', () => {
        const list: Suggestion[] = [
            { id: '1', direction: 'code-ahead', kind: 'amend', featureId: 'f-a', originRole: 'claude-code' },
            { id: '2', direction: 'code-ahead', kind: 'add', featureId: null, parentId: 'f-a', originRole: 'claude-code' },
            { id: '3', direction: 'code-ahead', kind: 'add', featureId: null, parentId: null, originRole: 'claude-code' },
        ];
        expect(suggestionsForFeature(list, 'f-a').map(s => s.id)).toEqual(['1']);
        expect(addsUnderParent(list, 'f-a').map(s => s.id)).toEqual(['2']);
        expect(addsUnderParent(list, null).map(s => s.id)).toEqual(['3']);
    });
});

describe('R2: tree.doc.json wrapper parsing', () => {
    const doc = makeDoc([featureHeadingNode({ fid: 'f-a', level: 0, retired: false, realized: true }, textToInlineRuns('Auth'))]);

    it('wraps a bare ProseMirror doc (U4 forward-compat) — comments default to []', () => {
        const df = parseDocFile(doc);
        expect(df?.doc.type).toBe('doc');
        expect(df?.suggestions).toEqual([]);
        expect(df?.comments).toEqual([]);
    });

    it('parses a full wrapper with suggestions', () => {
        const wrapper = { version: 1, doc, suggestions: [{ id: 'd-1', direction: 'doc-ahead', kind: 'amend', featureId: 'f-a', originRole: 'human' }] };
        const df = parseDocFile(wrapper);
        expect(df?.suggestions).toHaveLength(1);
        expect(df?.suggestions[0].id).toBe('d-1');
    });

    it('deserializes the comments array, and defaults it to [] when absent', () => {
        const withComments = { version: 1, doc, suggestions: [], comments: [{ id: 'cm-1', featureId: 'f-a', anchorText: 'x', body: 'note', status: 'open', author: 'human', createdAt: 0 }] };
        expect(parseDocFile(withComments)?.comments).toHaveLength(1);
        expect(parseDocFile(withComments)?.comments[0].id).toBe('cm-1');
        // a wrapper written before the comments field existed → []
        expect(parseDocFile({ version: 1, doc, suggestions: [] })?.comments).toEqual([]);
    });

    it('returns null for junk and round-trips emptyDocFile', () => {
        expect(parseDocFile(null)).toBeNull();
        expect(parseDocFile({ foo: 1 })).toBeNull();
        expect(emptyDocFile(doc)).toMatchObject({ version: 1, suggestions: [], comments: [] });
    });
});

describe('sibling anchors ride from the sidecar into the suggestion', () => {
    it('add carries after_id/before_id so the ghost draws where the node will land', () => {
        const sc = sidecarWith({
            by_feature: {},
            by_event: {
                'e-9': {
                    op: 'add', tag: 'agent plan', parent_id: 'f-a', title: 'Rate limiting',
                    after_id: 'f-x', before_id: null,
                },
            },
        });
        const [s] = codeAheadSuggestions(sc, curTitle, curDesc);
        expect(s).toMatchObject({ kind: 'add', afterId: 'f-x', beforeId: null });
    });

    it('anchors default to null on an older sidecar without the fields', () => {
        const sc = sidecarWith({
            by_feature: {},
            by_event: { 'e-8': { op: 'add', tag: 'agent plan', parent_id: 'f-a', title: 'X' } },
        });
        const [s] = codeAheadSuggestions(sc, curTitle, curDesc);
        expect(s.afterId ?? null).toBeNull();
        expect(s.beforeId ?? null).toBeNull();
    });
});
