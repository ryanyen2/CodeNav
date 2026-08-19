import { describe, it, expect } from 'vitest';
import { claimsForMoment, unaccountedAt, isHumanActor } from '../state/history-claims';
import { nodeStatus, statusGlyphs } from '../state/node-status';
import type { FeatureChange, SnapshotFeature } from '../state/revision-model';

const feat = (fid: string, title: string, description: string): SnapshotFeature =>
    ({ fid, title, description, parentId: null });

const change = (over: Partial<FeatureChange> = {}): FeatureChange => ({
    fid: 'f-1',
    before: feat('f-1', 'Uploads', 'It retries twice.'),
    after: feat('f-1', 'Uploads', 'It retries five times.'),
    unresolved: false,
    kinds: ['amend'],
    ...over,
});

describe('a past moment is marked in the same grammar as the present', () => {
    it('draws the author\'s own moment in the human channel, already committed', () => {
        const claims = claimsForMoment([change()], 'human').get('f-1')!;
        expect(claims.every(c => c.channel === 'human')).toBe(true);
        expect(claims.every(c => c.stage === 'committed')).toBe(true);
    });

    it('draws the loop\'s moment in the code channel', () => {
        const claims = claimsForMoment([change()], 'loop').get('f-1')!;
        expect(claims.every(c => c.channel === 'code')).toBe(true);
    });

    it('draws an agent\'s moment in the code channel too — it reached the doc via the code', () => {
        expect(claimsForMoment([change()], 'claude-code').get('f-1')!
            .every(c => c.channel === 'code')).toBe(true);
        expect(isHumanActor('claude-code')).toBe(false);
    });

    it('marks the SENTENCE that moved, not the whole node', () => {
        const claims = claimsForMoment([change()], 'loop').get('f-1')!;
        const after = 'It retries five times.';
        const add = claims.find(c => c.edit === 'add')!;
        expect(after.slice(add.start, add.end)).toContain('five times');
        expect(after.slice(add.start, add.end).length).toBeLessThan(after.length + 1);
    });

    it('keeps the words the moment displaced, so the scrubber can show what it said', () => {
        const claims = claimsForMoment([change()], 'loop').get('f-1')!;
        expect(claims.some(c => c.removed?.includes('twice'))).toBe(true);
    });

    it('marks a title change as well as a description one', () => {
        const claims = claimsForMoment([change({
            after: feat('f-1', 'Upload retries', 'It retries twice.'),
        })], 'human').get('f-1')!;
        expect(claims.some(c => c.block.kind === 'title')).toBe(true);
    });

    it('offsets are into the text as it read AFTER the moment — what the scrubber shows', () => {
        const after = 'It retries five times.';
        for (const c of claimsForMoment([change()], 'loop').get('f-1')!) {
            expect(c.start).toBeLessThanOrEqual(after.length);
            expect(c.end).toBeLessThanOrEqual(after.length);
        }
    });
});

describe('it still says what it cannot reconstruct', () => {
    it('yields no claims for a change the ledger could not account for', () => {
        // A mark drawn over invented text is worse than no mark: the reader has no way
        // to tell which one they are looking at.
        expect(claimsForMoment([change({ unresolved: true })], 'human').size).toBe(0);
    });

    it('reports those features separately, as a different kind of statement', () => {
        const changes = [change({ unresolved: true }), change({ fid: 'f-2' })];
        expect(unaccountedAt(changes)).toEqual(new Set(['f-1']));
    });

    it('does not let one unreconstructible feature suppress its neighbours', () => {
        const changes = [change({ fid: 'f-1', unresolved: true }), change({ fid: 'f-2' })];
        const got = claimsForMoment(changes, 'loop');
        expect(got.has('f-1')).toBe(false);
        expect(got.has('f-2')).toBe(true);
    });
});

describe('the margin marker reads the past too', () => {
    it('summarises a past moment with the same slots the live document uses', () => {
        const claims = claimsForMoment([change()], 'human').get('f-1')!;
        const s = nodeStatus(claims, [], 0);
        expect(s.human).toBe('committed');
        expect(statusGlyphs(s).map(g => g.slot)).toEqual(['human']);
    });

    it('shows the loop\'s past moment as a code diff, signed by direction', () => {
        const claims = claimsForMoment([change()], 'loop').get('f-1')!;
        expect(nodeStatus(claims, [], 0).diff).toBe('both');   // replaced a sentence
    });
});

describe('a node that appeared or vanished in the moment', () => {
    it('marks a newly added feature entirely', () => {
        const claims = claimsForMoment([change({
            before: null, after: feat('f-1', 'Backoff', 'It waits longer.'),
        })], 'human').get('f-1')!;
        expect(claims.some(c => c.edit === 'add' && c.block.kind === 'title')).toBe(true);
    });

    it('carries the whole node as removed when it went', () => {
        const claims = claimsForMoment([change({
            before: feat('f-1', 'Backoff', 'It waits longer.'), after: null,
        })], 'loop').get('f-1')!;
        expect(claims.some(c => c.removed?.includes('waits longer'))).toBe(true);
    });
});
