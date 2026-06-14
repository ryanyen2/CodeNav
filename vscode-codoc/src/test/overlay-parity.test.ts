/**
 * TS-side parity guard for the in-place overlay design (Phase 1).
 *
 * The Python renderer (codoc/codoc_file/render.py) emits ONLY ADD/MOVE ghosts as
 * text; RETIRE/AMEND ride in the sidecar's `proposals.by_feature`. These tests
 * lock the TS parser + sidecar lookups to that contract so the two stay in sync.
 */
import { describe, it, expect } from 'vitest';
import { parseTreeCodoc } from '../state/tree-model';
import { proposalForFeature, isUnrealized, emptySidecar, SidecarData } from '../state/bindings-model';

describe('parseTreeCodoc: ADD/MOVE ghosts only in text', () => {
    it('harvests an ADD ghost hunk and skips it as a live node', () => {
        const text = [
            '- Data layer  ⟨f-aaaa1111⟩',
            '    All database access.',
            '',
            '+   - Query cache  ⟨e-1111aaaa⟩',
            '+       Caches query results.',
            '+       code drift · no node fits',
            '',
        ].join('\n');
        const { features, proposals } = parseTreeCodoc(text);
        expect(features.map(f => f.title)).toEqual(['Data layer']);  // ghost is NOT a live node
        expect(proposals).toHaveLength(1);
        expect(proposals[0]).toMatchObject({ op: 'add', eventId: 'e-1111aaaa' });
    });

    it('harvests a MOVE destination ghost', () => {
        const text = [
            '- Indexing layer  ⟨f-bbbb2222⟩',
            '',
            '~   - Index snapshot diff  ⟨e-2222bbbb⟩',
            '~       move → Indexing layer · code drift',
            '',
        ].join('\n');
        const { proposals } = parseTreeCodoc(text);
        expect(proposals).toHaveLength(1);
        expect(proposals[0]).toMatchObject({ op: 'move', eventId: 'e-2222bbbb' });
    });

    it('a clean tree with no proposals yields no ghost hunks (round-trip shape)', () => {
        const text = [
            '- Auth  ⟨f-cccc3333⟩',
            '    Login + sessions.',
            '',
            '  - OAuth  ⟨f-dddd4444⟩',
            '      Google / GitHub flow.',
            '',
        ].join('\n');
        const { features, proposals } = parseTreeCodoc(text);
        expect(proposals).toHaveLength(0);
        expect(features.map(f => f.title)).toEqual(['Auth', 'OAuth']);
        expect(features[1].parent_id).toBe('f-cccc3333');
    });
});

describe('sidecar overlay lookups', () => {
    const sidecar: SidecarData = {
        version: 3,
        by_feature: {},
        by_file: {},
        features: {
            'f-aaaa1111': { title: 'Data layer', parent_id: null, realized: true },
            'f-plan0001': { title: 'Dark mode', parent_id: null, realized: false },
            'f-legacy00': { title: 'Old', parent_id: null },  // no `realized` (pre-v3)
        },
        proposals: {
            by_feature: {
                'f-aaaa1111': { op: 'retire', event_id: 'e-ret00001', tag: 'code drift' },
                'f-bbbb2222': { op: 'amend', event_id: 'e-amd00001', tag: 'agent reflection', title: 'New', description: 'New prose.' },
            },
            by_event: {},
        },
    };

    it('proposalForFeature returns retire/amend overlays', () => {
        expect(proposalForFeature(sidecar, 'f-aaaa1111')?.op).toBe('retire');
        expect(proposalForFeature(sidecar, 'f-bbbb2222')).toMatchObject({ op: 'amend', title: 'New' });
        expect(proposalForFeature(sidecar, 'f-nope0000')).toBeUndefined();
    });

    it('isUnrealized is true only for realized===false', () => {
        expect(isUnrealized(sidecar, 'f-plan0001')).toBe(true);
        expect(isUnrealized(sidecar, 'f-aaaa1111')).toBe(false);
        expect(isUnrealized(sidecar, 'f-legacy00')).toBe(false);  // absent ⇒ realized
    });

    it('emptySidecar has a well-formed proposals map', () => {
        const e = emptySidecar();
        expect(e.proposals).toEqual({ by_feature: {}, by_event: {} });
        expect(proposalForFeature(e, 'anything')).toBeUndefined();
    });
});
