/**
 * timeline.test.ts — the pure parts of the time machine (W8): the captions that name a
 * moment, the provenance trace behind it, and the paragraph diffing the past page draws.
 *
 * The DOM assembly (`createTimelineBar`, `renderRevisionPage`) is EDH-verified like every
 * other visual layer — these tests cover what a caption SAYS and what a diff CONTAINS,
 * which is where the meaning lives.
 */
import { describe, it, expect } from 'vitest';
import { momentCaption, momentScope, momentVerb } from '../webview/timeline-bar';
import { featureTrace, momentTrace, traceBaseSha } from '../state/provenance';
import { paraDiffs } from '../webview/revision-view';
import { buildTimeline } from '../state/revision-model';
import type { RevisionEntry, RevisionsFile, Timeline } from '../state/revision-model';

const T0 = 1_700_000_000_000;
const hlc = (msAfter: number): string =>
    `${String(T0 + msAfter).padStart(20, '0')}-${'0'.repeat(20)}-n`;

function entry(e: Partial<RevisionEntry> & { event_id: string; kind: string; feature_id: string }): RevisionEntry {
    return { at: hlc(0), actor: 'human', mode: 'pen', ...e } as RevisionEntry;
}
function timelineOf(revisions: RevisionEntry[], directives: RevisionsFile['directives'] = {}): Timeline {
    return buildTimeline({ version: 1, revisions, directives });
}

describe('momentScope', () => {
    it('counts features, singular and plural', () => {
        const t = timelineOf([
            entry({ event_id: 'e-2', kind: 'amend', feature_id: 'f-2', at: hlc(100) }),
            entry({ event_id: 'e-1', kind: 'amend', feature_id: 'f-1', at: hlc(0) }),
        ]);
        expect(momentScope(t.moments[0])).toBe('2 features');
        expect(momentScope(timelineOf([entry({ event_id: 'e-1', kind: 'amend', feature_id: 'f-1' })]).moments[0]))
            .toBe('1 feature');
    });
});

describe('momentVerb', () => {
    const verbOf = (kinds: string[]): string => momentVerb(timelineOf(
        kinds.map((kind, i) => entry({ event_id: `e-${i}`, kind, feature_id: `f-${i}`, at: hlc(i) })),
    ).moments[0]);

    it('names the change a person would notice, not the machinery', () => {
        // A save is an amend plus the bindings it moved. "edited, bound, unbound"
        // describes the pipeline; "edited" describes what happened.
        expect(verbOf(['amend', 'attach', 'refresh'])).toBe('edited');
        expect(verbOf(['attach', 'detach'])).toBe('rebound code in');
        expect(verbOf(['retire_node'])).toBe('retired');
        expect(verbOf(['move_node'])).toBe('moved');
    });

    it('distinguishes a plain creation from a creation that was then written', () => {
        expect(verbOf(['add_node'])).toBe('added');
        expect(verbOf(['add_node', 'amend'])).toBe('added and edited');
    });
});

describe('momentCaption', () => {
    it('reads as a sentence about who did what, when', () => {
        const t = timelineOf([
            entry({ event_id: 'e-2', kind: 'amend', feature_id: 'f-2', actor: 'loop', at: hlc(0) }),
            entry({ event_id: 'e-1', kind: 'amend', feature_id: 'f-1', actor: 'loop', at: hlc(0) }),
        ]);
        expect(momentCaption(t.moments[0], T0 + 3 * 3600_000))
            .toBe('codoc edited 2 features · 3h ago');
    });

    it('drops the time rather than printing a bogus one', () => {
        const t = timelineOf([entry({ event_id: 'e-1', kind: 'amend', feature_id: 'f-1', at: 'garbage' })]);
        expect(momentCaption(t.moments[0], T0)).toBe('You edited 1 feature');
    });
});

describe('momentTrace', () => {
    it('walks the whole chain: change → directive → prompt → session → commit', () => {
        const t = timelineOf(
            [entry({ event_id: 'e-1', kind: 'amend', feature_id: 'f-1', actor: 'loop',
                     caused_by: 'd-abc', rationale: 'aligned with the renamed handler',
                     bindings: ['upload.py::handle'] })],
            { 'd-abc': {
                id: 'd-abc', kind: 'amend', feature_id: 'f-1', text: 'UPDATE FEATURE', done: true,
                asked: 'add rate limiting to the upload endpoint',
                session_id: '0568a9e3', base_sha: 'a1b2c3d4e5f6' } },
        );
        const rows = momentTrace(t.moments[0], t);
        expect(rows).toEqual([
            { label: 'Why', value: 'aligned with the renamed handler' },
            { label: 'Implements', value: 'a completed request' },
            { label: 'You asked', value: '“add rate limiting to the upload endpoint”' },
            { label: 'Session', value: '0568a9e3' },
            { label: 'From commit', value: 'a1b2c3d4' },
            { label: 'Code', value: 'upload.py' },
        ]);
    });

    it('says a cause is no longer kept rather than dropping the row', () => {
        // "We know this had a reason and no longer have it" is a different fact from
        // "this had no reason", and the bounded logs make the first one routine.
        const t = timelineOf([entry({ event_id: 'e-1', kind: 'amend', feature_id: 'f-1', caused_by: 'd-gone' })]);
        expect(momentTrace(t.moments[0], t)).toEqual([
            { label: 'Implements', value: 'd-gone (details no longer kept)' },
        ]);
    });

    it('omits rows it has no value for, rather than showing empty ones', () => {
        const t = timelineOf([entry({ event_id: 'e-1', kind: 'amend', feature_id: 'f-1' })]);
        expect(momentTrace(t.moments[0], t)).toEqual([]);
    });

    it('says what the stated reason RESTS ON, right after the reason', () => {
        // The chain rows say what happened before the claim; this row is the only one
        // that says whether to believe it. Ordered as one thought: reason, then ground.
        const t = timelineOf([entry({
            event_id: 'e-1', kind: 'amend', feature_id: 'f-1', actor: 'claude-code',
            rationale: 'the client retries now',
            warrant: [
                { kind: 'commit', ref: '1a2b3c4d', quote: 'Retry only on timeout — the server can duplicate a post.' },
                { kind: 'intent', quote: 'add a retry guard to fan-out' },
            ],
        })]);
        expect(momentTrace(t.moments[0], t)).toEqual([
            { label: 'Why', value: 'the client retries now' },
            { label: 'Rests on commit 1a2b3c4d', value: '“Retry only on timeout — the server can duplicate a post.”' },
            { label: 'Rests on your ask', value: '“add a retry guard to fan-out”' },
        ]);
    });

    it('grounds the reason it actually showed, not a neighbouring op’s', () => {
        // One save can produce several ops. Pairing the Why row with another entry's
        // warrant would attribute evidence to a claim it was never offered for.
        const t = timelineOf([
            entry({ event_id: 'e-2', kind: 'amend', feature_id: 'f-2', at: hlc(1),
                    rationale: 'renamed to match the handler' }),
            entry({ event_id: 'e-1', kind: 'amend', feature_id: 'f-1', at: hlc(0),
                    rationale: 'the client retries now',
                    warrant: [{ kind: 'commit', ref: 'aaaaaaaa', quote: 'Retry only on timeout' }] }),
        ]);
        const rows = momentTrace(t.moments[0], t);
        expect(rows[0]).toEqual({ label: 'Why', value: 'renamed to match the handler' });
        expect(rows.some(r => r.label.startsWith('Rests on'))).toBe(false);
    });

    it('shows a warrant even when nothing recorded a rationale', () => {
        const t = timelineOf([entry({
            event_id: 'e-1', kind: 'amend', feature_id: 'f-1',
            warrant: [{ kind: 'directive', ref: 'f-1', quote: 'make the queue crash-safe' }],
        })]);
        expect(momentTrace(t.moments[0], t)).toEqual([
            { label: 'Rests on a request', value: '“make the queue crash-safe”' },
        ]);
    });

    it('draws nothing for a change with no warrant — the ordinary case', () => {
        // Most descriptions report what code achieves and make no claim needing
        // evidence, so a "Rests on: —" row would turn the normal into a defect.
        const t = timelineOf([entry({ event_id: 'e-1', kind: 'amend', feature_id: 'f-1',
                                      rationale: 'computes the total on write now' })]);
        expect(momentTrace(t.moments[0], t)).toEqual([
            { label: 'Why', value: 'computes the total on write now' },
        ]);
    });

    it('drops a quoteless warrant rather than drawing an empty ground', () => {
        const t = timelineOf([entry({ event_id: 'e-1', kind: 'amend', feature_id: 'f-1',
                                      warrant: [{ kind: 'commit', ref: 'abc', quote: '' }] })]);
        expect(momentTrace(t.moments[0], t)).toEqual([]);
    });

    it('labels an unrecognised evidence kind without inventing a name for it', () => {
        const t = timelineOf([entry({ event_id: 'e-1', kind: 'amend', feature_id: 'f-1',
                                      warrant: [{ kind: 'issue', quote: 'from the tracker' }] })]);
        expect(momentTrace(t.moments[0], t)).toEqual([
            { label: 'Rests on', value: '“from the tracker”' },
        ]);
    });

    it('distinguishes a queued request from a completed one', () => {
        const t = timelineOf(
            [entry({ event_id: 'e-1', kind: 'amend', feature_id: 'f-1', caused_by: 'd-q' })],
            { 'd-q': { id: 'd-q', kind: 'amend', feature_id: 'f-1', text: '', done: false } },
        );
        expect(momentTrace(t.moments[0], t)[0]).toEqual(
            { label: 'Implements', value: 'a queued request' });
    });
});

describe('paraDiffs', () => {
    it('diffs a paragraph in place', () => {
        const [p] = paraDiffs('Handles login.', 'Handles login and refresh.');
        expect(p.removed).toBe(false);
        expect(p.runs.filter(r => r.t === 'ins').map(r => r.s.trim()).join(' ')).toContain('refresh');
    });

    it('pairs paragraphs rather than zipping them by index', () => {
        // Inserting a paragraph in the middle shifts every later one. Index-pairing
        // would then report the whole rest of the description as rewritten.
        const out = paraDiffs('One.\n\nThree.', 'One.\n\nTwo.\n\nThree.');
        expect(out).toHaveLength(3);
        expect(out[0].runs.every(r => r.t === 'same')).toBe(true);
        expect(out[2].runs.every(r => r.t === 'same')).toBe(true);
        expect(out[1].runs.some(r => r.t === 'ins')).toBe(true);
    });

    it('keeps a deleted paragraph where it stood, struck', () => {
        const out = paraDiffs('One.\n\nGone.\n\nThree.', 'One.\n\nThree.');
        expect(out.map(p => p.removed)).toEqual([false, true, false]);
        expect(out[1].runs).toEqual([{ t: 'del', s: 'Gone.' }]);
    });

    it('reads a first description as a pure insertion', () => {
        const out = paraDiffs('', 'Brand new.');
        expect(out).toHaveLength(1);
        expect(out[0].runs.every(r => r.t === 'ins')).toBe(true);
    });

    it('reads a cleared description as a pure deletion', () => {
        const out = paraDiffs('Was here.', '');
        expect(out).toEqual([{ runs: [{ t: 'del', s: 'Was here.' }], removed: true }]);
    });

    it('is empty when nothing changed and nothing exists', () => {
        expect(paraDiffs('', '')).toEqual([]);
    });

    it('does not dump later deletions above the surviving text', () => {
        // An INSERTED paragraph knows nothing about where we are in the baseline, so it
        // must flush nothing. Treating it as "we are at the end of the baseline" hoisted
        // every remaining deletion to the top of the description.
        const out = paraDiffs('A\n\nB\n\nC', 'C\n\nA');
        const kept = out.findIndex(p => !p.removed && p.runs.every(r => r.t === 'same'));
        const struckB = out.findIndex(p => p.removed && p.runs[0].s === 'B');
        expect(kept).toBeGreaterThanOrEqual(0);
        expect(struckB).toBeGreaterThan(kept);   // B stood AFTER A, and still reads that way
    });

    it('never marks a paragraph removed when it merely moved', () => {
        // Striking it as well would show the reader the same paragraph twice and claim it
        // was deleted while it is right there.
        const out = paraDiffs('A\n\nB\n\nC\n\nD', 'D\n\nB');
        const struck = out.filter(p => p.removed).map(p => p.runs[0].s);
        expect(struck).toEqual(['A', 'C']);
        expect(struck).not.toContain('B');
    });

    it('reports a paragraph that really did go', () => {
        const out = paraDiffs('A\n\nB', 'A');
        expect(out.filter(p => p.removed).map(p => p.runs[0].s)).toEqual(['B']);
    });
});

describe('featureTrace', () => {
    const dirs = { 'd-abc': {
        id: 'd-abc', kind: 'amend', feature_id: 'f-1', text: '', done: true,
        asked: 'make uploads rate-limited', session_id: '0568a9e3', base_sha: 'deadbeefcafe',
    } };

    it('answers "why does this paragraph say this" at the paragraph', () => {
        const rows = featureTrace([
            { at: hlc(0), kind: 'amend', actor: 'claude-code', mode: 'auto',
              caused_by: 'd-abc', rationale: 'matched the new handler' },
        ], dirs, T0 + 2 * 3600_000);
        expect(rows).toEqual([
            { label: 'Last change', value: 'claude-code edited · 2h ago' },
            { label: 'Why', value: 'matched the new handler' },
            { label: 'Implements', value: 'a completed request' },
            { label: 'You asked', value: '“make uploads rate-limited”' },
            { label: 'Session', value: '0568a9e3' },
            { label: 'From commit', value: 'deadbeef' },
        ]);
    });

    it('grounds the paragraph’s stated reason too', () => {
        const rows = featureTrace([
            { at: hlc(0), kind: 'amend', actor: 'claude-code', mode: 'auto',
              rationale: 'the client retries now',
              warrant: [{ kind: 'prior', ref: 'f-1', quote: 'the old sentence said one attempt' }] },
        ], {}, T0);
        expect(rows).toEqual([
            { label: 'Last change', value: 'claude-code edited · just now' },
            { label: 'Why', value: 'the client retries now' },
            { label: 'Rests on an earlier note', value: '“the old sentence said one attempt”' },
        ]);
    });

    it('chases only the newest change’s cause', () => {
        // A feature's history is a list of separate changes, each with its own reason.
        // Stacking four directives into one card presents them as one explanation.
        const rows = featureTrace([
            { at: hlc(100), kind: 'amend', actor: 'human', mode: 'pen' },
            { at: hlc(0), kind: 'amend', actor: 'loop', mode: 'auto', caused_by: 'd-abc' },
        ], dirs, T0 + 100);
        expect(rows.some(r => r.label === 'You asked')).toBe(false);
        expect(rows.at(-1)).toEqual({ label: 'Earlier', value: '1 more recorded change' });
    });

    it('pluralises the earlier-changes count', () => {
        const rows = featureTrace([
            { at: hlc(2), kind: 'amend', actor: 'human', mode: 'pen' },
            { at: hlc(1), kind: 'amend', actor: 'human', mode: 'pen' },
            { at: hlc(0), kind: 'amend', actor: 'human', mode: 'pen' },
        ], {}, T0 + 2);
        expect(rows.at(-1)).toEqual({ label: 'Earlier', value: '2 more recorded changes' });
    });

    it('is empty for a feature with no recorded history', () => {
        expect(featureTrace([], dirs)).toEqual([]);
    });
});

describe('traceBaseSha', () => {
    const dirs = {
        'd-new': { id: 'd-new', kind: 'amend', feature_id: 'f-1', text: '', done: false },
        'd-old': { id: 'd-old', kind: 'amend', feature_id: 'f-1', text: '', done: true, base_sha: 'abc123' },
    };

    it('falls back through the history to the newest change that recorded one', () => {
        // The newest change may be a human edit with no directive at all; the reader
        // still wants the code the last AGENT change started from.
        expect(traceBaseSha([
            { at: hlc(2), kind: 'amend', actor: 'human', mode: 'pen' },
            { at: hlc(1), kind: 'amend', actor: 'loop', mode: 'auto', caused_by: 'd-new' },
            { at: hlc(0), kind: 'amend', actor: 'loop', mode: 'auto', caused_by: 'd-old' },
        ], dirs)).toBe('abc123');
    });

    it('is empty when nothing anchored — the card then offers no diff', () => {
        expect(traceBaseSha([{ at: hlc(0), kind: 'amend', actor: 'human', mode: 'pen' }], dirs)).toBe('');
    });
});
