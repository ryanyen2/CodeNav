/**
 * consequence.test.ts — "what will this Accept do to my code?", the axis the surface
 * used to hide.
 *
 * Most proposals reconcile the tree to code that already exists; two kinds hand work
 * to the agent. Getting this wrong is asymmetric — mislabelling a build as a record
 * means someone's code changes without them agreeing to it — so the mapping is pinned
 * here rather than left to whichever surface happens to render it.
 */
import { describe, it, expect } from 'vitest';
import {
    consequenceOf, consequenceVerb, consequenceNote, leavesForAgent,
} from '../state/grammar';
import { buildSuggestions } from '../state/suggestion-model';
import { emptySidecar, type SidecarData } from '../state/bindings-model';

describe('consequenceOf', () => {
    it('reads the sidecar flag when the daemon sends one', () => {
        expect(consequenceOf('build')).toBe('build');
        expect(consequenceOf('remove')).toBe('remove');
        expect(consequenceOf(null)).toBe('record');
    });

    it('defaults to the harmless reading — a proposal is bookkeeping unless it says otherwise', () => {
        expect(consequenceOf(undefined)).toBe('record');
        expect(consequenceOf(null, 'code drift')).toBe('record');
        expect(consequenceOf(null, 'agent reflection')).toBe('record');
    });

    it('falls back to the plan tag for an older daemon that has no writes_code field', () => {
        // Forward-compat matters in the DANGEROUS direction: a new IDE against an old
        // daemon must not silently label a build request as a plain Accept.
        expect(consequenceOf(undefined, 'agent plan')).toBe('build');
    });

    it('an explicit flag beats the tag — the daemon knows better than a string sniff', () => {
        expect(consequenceOf('remove', 'agent plan')).toBe('remove');
    });
});

describe('the verb carries the consequence', () => {
    it('names the code effect for the two kinds that have one', () => {
        expect(consequenceVerb('build')).toBe('Accept & build');
        expect(consequenceVerb('remove')).toBe('Accept & delete code');
    });

    it('stays plain for the majority, so the loud verbs keep their meaning', () => {
        expect(consequenceVerb('record')).toBe('Accept');
    });

    it('every consequence has a note that says what happens to the CODE', () => {
        expect(consequenceNote('build')).toMatch(/code will change/i);
        expect(consequenceNote('remove')).toMatch(/delete/i);
        expect(consequenceNote('record')).toMatch(/no code changes/i);
    });

    it('leavesForAgent is the single bit driving the plane glyph and the launch motion', () => {
        expect(leavesForAgent('build')).toBe(true);
        expect(leavesForAgent('remove')).toBe(true);
        expect(leavesForAgent('record')).toBe(false);
    });
});

describe('the consequence survives the trip from sidecar to Suggestion', () => {
    const sidecar = (props: SidecarData['proposals']): SidecarData =>
        ({ ...emptySidecar(), proposals: props });

    it('carries writes_code + verdict_pending onto every proposal kind', () => {
        const s = buildSuggestions(sidecar({
            by_feature: {
                'f-kill': {
                    op: 'retire', event_id: 'e-1', tag: 'agent reflection',
                    writes_code: 'remove', verdict_pending: true,
                },
                'f-drift': {
                    op: 'amend', event_id: 'e-2', tag: 'code drift',
                    description: 'new', writes_code: null, verdict_pending: false,
                },
            },
            by_event: {
                'e-3': {
                    op: 'add', tag: 'agent plan', title: 'Not built yet',
                    parent_id: 'f-root', writes_code: 'build', verdict_pending: false,
                },
            },
        }), () => 'old title', () => 'old desc');

        const byId = Object.fromEntries(s.map(x => [x.id, x]));
        expect(consequenceOf(byId['e-1'].writesCode, byId['e-1'].tag)).toBe('remove');
        expect(byId['e-1'].verdictPending).toBe(true);
        expect(consequenceOf(byId['e-2'].writesCode, byId['e-2'].tag)).toBe('record');
        expect(byId['e-2'].verdictPending).toBe(false);
        expect(consequenceOf(byId['e-3'].writesCode, byId['e-3'].tag)).toBe('build');
    });

    it('a payload with neither field degrades to "record", never to a false build', () => {
        const s = buildSuggestions(sidecar({
            by_feature: { 'f-a': { op: 'retire', event_id: 'e-9', tag: 'code drift' } },
            by_event: {},
        }), () => '', () => '');
        expect(consequenceOf(s[0].writesCode, s[0].tag)).toBe('record');
        expect(s[0].verdictPending).toBe(false);
    });
});
