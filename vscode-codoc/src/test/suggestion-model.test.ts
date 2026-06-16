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
    diffDocsToSuggestions,
    applyDocAheadSuggestions,
    stripDocAheadSuggestions,
    Suggestion,
} from '../state/suggestion-model';
import { emptySidecar, SidecarData } from '../state/bindings-model';
import {
    makeDoc, featureHeadingNode, paragraphNode, textToInlineRuns,
    inlineRunsToText, blocksToDescriptionText, descriptionBlocksForFid, PMNode,
} from '../state/pm-doc';

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

describe('R2: buildSuggestions merges code-ahead + doc-ahead', () => {
    it('appends persisted doc-ahead suggestions after code-ahead', () => {
        const sc = sidecarWith({ by_feature: { 'f-a': { op: 'amend', event_id: 'e-1', tag: 'code drift', title: 'X' } }, by_event: {} });
        const docAhead: Suggestion[] = [{
            id: 'd-1', direction: 'doc-ahead', kind: 'amend', featureId: 'f-b', originRole: 'human', tag: 'you',
            descOld: 'Storage.', descNew: 'Storage with caching layer.',
        }];
        const all = buildSuggestions(sc, docAhead, curTitle, curDesc);
        expect(all).toHaveLength(2);
        expect(all[0].direction).toBe('code-ahead');
        expect(all[1]).toMatchObject({ direction: 'doc-ahead', featureId: 'f-b', descNew: 'Storage with caching layer.' });
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

describe('R4: diffDocsToSuggestions (Suggesting-mode capture)', () => {
    const baseline = makeDoc([
        featureHeadingNode({ fid: 'f-a', level: 0, retired: false, realized: true }, textToInlineRuns('Auth')),
        paragraphNode(textToInlineRuns('Login and sessions.')),
        featureHeadingNode({ fid: 'f-b', level: 0, retired: false, realized: true }, textToInlineRuns('Data')),
        paragraphNode(textToInlineRuns('Storage.')),
    ]);

    it('captures only the changed feature as a doc-ahead amend', () => {
        const edited = makeDoc([
            featureHeadingNode({ fid: 'f-a', level: 0, retired: false, realized: true }, textToInlineRuns('Auth')),
            paragraphNode(textToInlineRuns('Login, sessions, and OAuth.')),
            featureHeadingNode({ fid: 'f-b', level: 0, retired: false, realized: true }, textToInlineRuns('Data')),
            paragraphNode(textToInlineRuns('Storage.')),
        ]);
        const out = diffDocsToSuggestions(baseline, edited);
        expect(out).toHaveLength(1);
        expect(out[0]).toMatchObject({
            direction: 'doc-ahead', kind: 'amend', featureId: 'f-a', originRole: 'human',
            descOld: 'Login and sessions.', descNew: 'Login, sessions, and OAuth.',
        });
    });

    it('captures a title change too', () => {
        const edited = makeDoc([
            featureHeadingNode({ fid: 'f-a', level: 0, retired: false, realized: true }, textToInlineRuns('Authentication')),
            paragraphNode(textToInlineRuns('Login and sessions.')),
        ]);
        const out = diffDocsToSuggestions(baseline, edited);
        expect(out[0]).toMatchObject({ titleOld: 'Auth', titleNew: 'Authentication', featureId: 'f-a' });
    });

    it('returns nothing when unchanged', () => {
        expect(diffDocsToSuggestions(baseline, baseline)).toEqual([]);
    });

    it('uses a stable per-feature id (d-<fid>)', () => {
        const edited = makeDoc([
            featureHeadingNode({ fid: 'f-a', level: 0, retired: false, realized: true }, textToInlineRuns('Auth')),
            paragraphNode(textToInlineRuns('Changed.')),
        ]);
        expect(diffDocsToSuggestions(baseline, edited)[0].id).toBe('d-f-a');
    });
});

describe('WS4: inline suggesting — apply/strip doc-ahead suggestions (round-trip safe)', () => {
    const baseline = (): PMNode => makeDoc([
        featureHeadingNode({ fid: 'f-a', level: 0, retired: false, realized: true }, textToInlineRuns('Auth')),
        paragraphNode(textToInlineRuns('Login and sessions.')),
        featureHeadingNode({ fid: 'f-b', level: 0, retired: false, realized: true }, textToInlineRuns('Data')),
        paragraphNode(textToInlineRuns('Storage.')),
    ]);
    const sug = (): Suggestion[] => [{
        id: 'd-f-a', direction: 'doc-ahead', kind: 'amend', featureId: 'f-a', originRole: 'human', tag: 'you',
        titleOld: 'Auth', titleNew: 'Authentication', descOld: 'Login and sessions.', descNew: 'Login, sessions, and OAuth.',
    }];
    const descOf = (doc: PMNode, fid: string): string => blocksToDescriptionText(descriptionBlocksForFid(doc, fid));
    const titleOf = (doc: PMNode, fid: string): string =>
        inlineRunsToText((doc.content ?? []).find(b => b.type === 'featureHeading' && (b.attrs as { fid?: string }).fid === fid)?.content);

    it('apply splices the proposed (new) title + description into the baseline', () => {
        const out = applyDocAheadSuggestions(baseline(), sug());
        expect(titleOf(out, 'f-a')).toBe('Authentication');
        expect(descOf(out, 'f-a')).toBe('Login, sessions, and OAuth.');
        // untouched feature stays baseline
        expect(descOf(out, 'f-b')).toBe('Storage.');
    });

    it('strip resets an untouched proposal back to baseline', () => {
        const live = applyDocAheadSuggestions(baseline(), sug());     // doc now holds descNew
        const stripped = stripDocAheadSuggestions(live, sug());       // untouched → back to baseline
        expect(titleOf(stripped, 'f-a')).toBe('Auth');
        expect(descOf(stripped, 'f-a')).toBe('Login and sessions.');
    });

    it('strip LEAVES a feature the user edited past the proposal (a genuine Editing commit)', () => {
        // live editor holds neither baseline nor the proposal — the user kept typing
        const edited = makeDoc([
            featureHeadingNode({ fid: 'f-a', level: 0, retired: false, realized: true }, textToInlineRuns('Auth')),
            paragraphNode(textToInlineRuns('Login, sessions, OAuth, and SSO.')), // != descNew
            featureHeadingNode({ fid: 'f-b', level: 0, retired: false, realized: true }, textToInlineRuns('Data')),
            paragraphNode(textToInlineRuns('Storage.')),
        ]);
        const stripped = stripDocAheadSuggestions(edited, sug());
        expect(descOf(stripped, 'f-a')).toBe('Login, sessions, OAuth, and SSO.'); // commit survives
    });

    it('apply does NOT splice when the baseline diverged (agent rewrote the feature)', () => {
        const rewritten = makeDoc([
            featureHeadingNode({ fid: 'f-a', level: 0, retired: false, realized: true }, textToInlineRuns('Auth')),
            paragraphNode(textToInlineRuns('Agent reimplemented login with passkeys.')), // != descOld
            featureHeadingNode({ fid: 'f-b', level: 0, retired: false, realized: true }, textToInlineRuns('Data')),
            paragraphNode(textToInlineRuns('Storage.')),
        ]);
        const out = applyDocAheadSuggestions(rewritten, sug());
        expect(descOf(out, 'f-a')).toBe('Agent reimplemented login with passkeys.'); // authoritative text kept
    });

    it('apply→strip is identity on the canonical text (no leak across a round-trip)', () => {
        const before = baseline();
        const after = stripDocAheadSuggestions(applyDocAheadSuggestions(before, sug()), sug());
        expect(descOf(after, 'f-a')).toBe(descOf(before, 'f-a'));
        expect(titleOf(after, 'f-a')).toBe(titleOf(before, 'f-a'));
    });

    it('is a no-op when there are no doc-ahead amend suggestions', () => {
        const before = baseline();
        expect(applyDocAheadSuggestions(before, [])).toEqual(before);
        expect(stripDocAheadSuggestions(before, [])).toEqual(before);
    });
});
