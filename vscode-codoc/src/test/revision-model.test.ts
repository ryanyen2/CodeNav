/**
 * revision-model.test.ts — the tree as it was (W8).
 *
 * The load-bearing rules, each of which is a way for a version history to quietly lie:
 *   - undoing runs BACKWARDS from the live document, newest event first, so two amends
 *     to one feature compose correctly;
 *   - an op that did not record what it displaced marks the feature UNRESOLVED rather
 *     than leaving today's words showing as though they were yesterday's;
 *   - a retired feature comes back with the text and parent the retire recorded;
 *   - events cluster into moments by author + cause + proximity, so bootstrap is one
 *     stop on the scrubber and not fifty.
 */
import { describe, it, expect } from 'vitest';
import {
    MOMENT_GAP_MS, buildTimeline, changesAt, filesTouched, hlcMs, liveSnapshot,
    preorder, snapshotAt,
} from '../state/revision-model';
import type { RevisionEntry, RevisionsFile, Snapshot } from '../state/revision-model';
import { descriptionToBlocks, featureHeadingNode, makeDoc, textToInlineRuns } from '../state/pm-doc';

const T0 = 1_700_000_000_000;
const hlc = (msAfter: number): string =>
    `${String(T0 + msAfter).padStart(20, '0')}-${'0'.repeat(20)}-n`;

/** A live doc: `[fid, title, description, level]` rows in document order. */
function doc(rows: [string, string, string, number][]) {
    const blocks = rows.flatMap(([fid, title, description, level]) => [
        featureHeadingNode({ fid, level, retired: false, realized: true }, textToInlineRuns(title)),
        ...descriptionToBlocks(description),
    ]);
    return makeDoc(blocks);
}

function entry(e: Partial<RevisionEntry> & { event_id: string; kind: string; feature_id: string }): RevisionEntry {
    return { at: hlc(0), actor: 'human', mode: 'pen', ...e } as RevisionEntry;
}

/** Newest-first, the way the daemon writes it. */
function file(revisions: RevisionEntry[], extra: Partial<RevisionsFile> = {}): RevisionsFile {
    return { version: 1, revisions, directives: {}, ...extra };
}

const fidsOf = (s: Snapshot): string[] => preorder(s).map(r => r.fid);

// ── reading the live document ────────────────────────────────────────────────

describe('liveSnapshot', () => {
    it('reads titles, descriptions and parentage out of the flat outliner', () => {
        const s = liveSnapshot(doc([
            ['f-1', 'Storage', 'Owns the database.', 0],
            ['f-2', 'Index', 'Chunks and hashes.', 1],
            ['f-3', 'Serving', 'The hub.', 0],
        ]));
        expect(s.features.get('f-1')).toMatchObject({ title: 'Storage', description: 'Owns the database.', parentId: null });
        expect(s.features.get('f-2')?.parentId).toBe('f-1');
        expect(s.features.get('f-3')?.parentId).toBe('f-1' === 'f-1' ? null : null);
        expect(s.order).toEqual(['f-1', 'f-2', 'f-3']);
    });

    it('joins a multi-paragraph description the way the daemon serialises it', () => {
        const s = liveSnapshot(doc([['f-1', 'Storage', 'One.\n\nTwo.', 0]]));
        expect(s.features.get('f-1')?.description).toBe('One.\n\nTwo.');
    });

    it('re-parents correctly when depth jumps back out', () => {
        const s = liveSnapshot(doc([
            ['f-1', 'A', '', 0], ['f-2', 'B', '', 1], ['f-3', 'C', '', 2], ['f-4', 'D', '', 1],
        ]));
        expect(s.features.get('f-3')?.parentId).toBe('f-2');
        expect(s.features.get('f-4')?.parentId).toBe('f-1');
    });

    it('survives an empty or absent doc', () => {
        expect(liveSnapshot(null).features.size).toBe(0);
        expect(liveSnapshot(makeDoc([])).order).toEqual([]);
    });
});

// ── moments ──────────────────────────────────────────────────────────────────

describe('buildTimeline', () => {
    it('returns moments oldest first — the scrubber reads left to right', () => {
        const t = buildTimeline(file([
            entry({ event_id: 'e-3', kind: 'amend', feature_id: 'f-1', at: hlc(600_000) }),
            entry({ event_id: 'e-2', kind: 'amend', feature_id: 'f-1', at: hlc(300_000) }),
            entry({ event_id: 'e-1', kind: 'add_node', feature_id: 'f-1', at: hlc(0) }),
        ]));
        expect(t.moments.map(m => m.id)).toEqual(['e-1', 'e-2', 'e-3']);
    });

    it('folds a burst by one author for one reason into a single moment', () => {
        const t = buildTimeline(file([
            entry({ event_id: 'e-3', kind: 'add_node', feature_id: 'f-3', at: hlc(2_000), actor: 'loop' }),
            entry({ event_id: 'e-2', kind: 'add_node', feature_id: 'f-2', at: hlc(1_000), actor: 'loop' }),
            entry({ event_id: 'e-1', kind: 'add_node', feature_id: 'f-1', at: hlc(0), actor: 'loop' }),
        ]));
        expect(t.moments).toHaveLength(1);
        expect(t.moments[0].fids).toEqual(['f-1', 'f-2', 'f-3']);
        expect(t.moments[0].entries.map(e => e.event_id)).toEqual(['e-3', 'e-2', 'e-1']);
    });

    it('splits on a different author', () => {
        const t = buildTimeline(file([
            entry({ event_id: 'e-2', kind: 'amend', feature_id: 'f-1', at: hlc(1_000), actor: 'claude-code' }),
            entry({ event_id: 'e-1', kind: 'amend', feature_id: 'f-1', at: hlc(0), actor: 'human' }),
        ]));
        expect(t.moments.map(m => m.actor)).toEqual(['human', 'claude-code']);
    });

    it('splits on a different cause, so two directives never read as one change', () => {
        const t = buildTimeline(file([
            entry({ event_id: 'e-2', kind: 'amend', feature_id: 'f-2', at: hlc(1_000), actor: 'loop', caused_by: 'd-2' }),
            entry({ event_id: 'e-1', kind: 'amend', feature_id: 'f-1', at: hlc(0), actor: 'loop', caused_by: 'd-1' }),
        ]));
        expect(t.moments.map(m => m.causedBy)).toEqual(['d-1', 'd-2']);
    });

    it('splits once the gap is longer than a person calls one sitting', () => {
        const t = buildTimeline(file([
            entry({ event_id: 'e-2', kind: 'amend', feature_id: 'f-1', at: hlc(MOMENT_GAP_MS + 1) }),
            entry({ event_id: 'e-1', kind: 'amend', feature_id: 'f-1', at: hlc(0) }),
        ]));
        expect(t.moments).toHaveLength(2);
    });

    it('is empty and truncation-free for a missing file', () => {
        expect(buildTimeline(null)).toEqual({ moments: [], directives: {}, truncated: false });
    });

    it('carries truncation through, so the far end can say there is more', () => {
        expect(buildTimeline(file([], { truncated: true })).truncated).toBe(true);
    });
});

describe('hlcMs', () => {
    it('reads the wall clock out of an HLC', () => {
        expect(hlcMs(hlc(500))).toBe(T0 + 500);
    });
    it('is NaN for anything else, so callers can fall back rather than show 1970', () => {
        expect(hlcMs('nonsense')).toBeNaN();
        expect(hlcMs('')).toBeNaN();
    });
});

// ── reconstruction ───────────────────────────────────────────────────────────

describe('snapshotAt', () => {
    it('leaves the live document untouched at the newest moment', () => {
        const live = liveSnapshot(doc([['f-1', 'Sessions', 'Handles login and refresh.', 0]]));
        const t = buildTimeline(file([
            entry({ event_id: 'e-1', kind: 'amend', feature_id: 'f-1', at: hlc(0),
                    description: 'Handles login and refresh.', prev_description: 'Handles login.' }),
        ]));
        const s = snapshotAt(live, t, t.moments.length - 1);
        expect(s.features.get('f-1')?.description).toBe('Handles login and refresh.');
    });

    it('restores the previous description one moment back', () => {
        const live = liveSnapshot(doc([['f-1', 'Sessions', 'Handles login and refresh.', 0]]));
        const t = buildTimeline(file([
            entry({ event_id: 'e-1', kind: 'amend', feature_id: 'f-1', at: hlc(0),
                    description: 'Handles login and refresh.', prev_description: 'Handles login.' }),
        ]));
        expect(snapshotAt(live, t, -1).features.get('f-1')?.description).toBe('Handles login.');
    });

    it('unwinds two amends to one feature in the right order', () => {
        const live = liveSnapshot(doc([['f-1', 'Sessions', 'Third.', 0]]));
        const t = buildTimeline(file([
            entry({ event_id: 'e-2', kind: 'amend', feature_id: 'f-1', at: hlc(10 * MOMENT_GAP_MS),
                    description: 'Third.', prev_description: 'Second.' }),
            entry({ event_id: 'e-1', kind: 'amend', feature_id: 'f-1', at: hlc(0),
                    description: 'Second.', prev_description: 'First.' }),
        ]));
        expect(t.moments).toHaveLength(2);
        expect(snapshotAt(live, t, 1).features.get('f-1')?.description).toBe('Third.');
        expect(snapshotAt(live, t, 0).features.get('f-1')?.description).toBe('Second.');
        expect(snapshotAt(live, t, -1).features.get('f-1')?.description).toBe('First.');
    });

    it('leaves a field the op never touched alone', () => {
        const live = liveSnapshot(doc([['f-1', 'Sessions', 'Rewritten.', 0]]));
        const t = buildTimeline(file([
            entry({ event_id: 'e-1', kind: 'amend', feature_id: 'f-1',
                    description: 'Rewritten.', prev_description: 'Original.' }),
        ]));
        const s = snapshotAt(live, t, -1);
        expect(s.features.get('f-1')?.title).toBe('Sessions');
        expect(s.unresolved.has('f-1')).toBe(false);
    });

    it('marks a change UNRESOLVED rather than showing today’s words as yesterday’s', () => {
        // An event written before `prev_title` existed: it says the title changed and
        // not what it changed from. There is no backfill and there must not be one.
        const live = liveSnapshot(doc([['f-1', 'Sessions', 'Prose.', 0]]));
        const t = buildTimeline(file([
            entry({ event_id: 'e-1', kind: 'amend', feature_id: 'f-1', title: 'Sessions' }),
        ]));
        const s = snapshotAt(live, t, -1);
        expect(s.unresolved.has('f-1')).toBe(true);
        expect(s.features.get('f-1')?.title).toBe('Sessions');  // unchanged, not invented
    });

    it('removes a feature that had not been added yet', () => {
        const live = liveSnapshot(doc([['f-1', 'Storage', '', 0], ['f-2', 'Uploads', '', 0]]));
        const t = buildTimeline(file([
            entry({ event_id: 'e-1', kind: 'add_node', feature_id: 'f-2', title: 'Uploads' }),
        ]));
        expect(fidsOf(snapshotAt(live, t, -1))).toEqual(['f-1']);
    });

    it('keeps a node visible when the parent it was moved into disappears', () => {
        // Undoing an ADD must not silently drop a subtree the ledger never said to drop.
        const live = liveSnapshot(doc([['f-1', 'Storage', '', 0], ['f-2', 'Index', '', 1]]));
        const t = buildTimeline(file([
            entry({ event_id: 'e-1', kind: 'add_node', feature_id: 'f-1', title: 'Storage' }),
        ]));
        expect(fidsOf(snapshotAt(live, t, -1))).toEqual(['f-2']);
    });

    it('brings a retired feature back with the text and parent the retire recorded', () => {
        const live = liveSnapshot(doc([['f-1', 'Storage', '', 0]]));
        const t = buildTimeline(file([
            entry({ event_id: 'e-1', kind: 'retire_node', feature_id: 'f-9',
                    prev_title: 'Legacy export', prev_description: 'Writes the old CSV format.',
                    prev_parent_id: 'f-1' }),
        ]));
        const s = snapshotAt(live, t, -1);
        expect(s.features.get('f-9')).toMatchObject({
            title: 'Legacy export', description: 'Writes the old CSV format.', parentId: 'f-1',
        });
        expect(preorder(s)).toEqual([{ fid: 'f-1', level: 0 }, { fid: 'f-9', level: 1 }]);
    });

    it('leaves a retired feature absent when the retire recorded no text', () => {
        // A resurrected node with no title is worse than an honest gap.
        const live = liveSnapshot(doc([['f-1', 'Storage', '', 0]]));
        const t = buildTimeline(file([
            entry({ event_id: 'e-1', kind: 'retire_node', feature_id: 'f-9' }),
        ]));
        expect(fidsOf(snapshotAt(live, t, -1))).toEqual(['f-1']);
    });

    it('puts a moved node back under the parent it left', () => {
        const live = liveSnapshot(doc([
            ['f-1', 'Storage', '', 0], ['f-2', 'Serving', '', 0], ['f-3', 'Cache', '', 1],
        ]));
        expect(live.features.get('f-3')?.parentId).toBe('f-2');
        const t = buildTimeline(file([
            entry({ event_id: 'e-1', kind: 'move_node', feature_id: 'f-3',
                    parent_id: 'f-2', prev_parent_id: 'f-1' }),
        ]));
        const s = snapshotAt(live, t, -1);
        expect(s.features.get('f-3')?.parentId).toBe('f-1');
        expect(preorder(s)).toEqual([
            { fid: 'f-1', level: 0 }, { fid: 'f-3', level: 1 }, { fid: 'f-2', level: 0 },
        ]);
    });

    it('distinguishes "was a root" from "not recorded"', () => {
        const live = liveSnapshot(doc([['f-1', 'Storage', '', 0], ['f-2', 'Cache', '', 1]]));
        const rooted = buildTimeline(file([
            entry({ event_id: 'e-1', kind: 'move_node', feature_id: 'f-2', prev_parent_id: '' }),
        ]));
        expect(snapshotAt(live, rooted, -1).features.get('f-2')?.parentId).toBeNull();

        const unknown = buildTimeline(file([
            entry({ event_id: 'e-1', kind: 'move_node', feature_id: 'f-2' }),
        ]));
        const s = snapshotAt(live, unknown, -1);
        expect(s.unresolved.has('f-2')).toBe(true);
        expect(s.features.get('f-2')?.parentId).toBe('f-1');  // left where it is, not guessed
    });

    it('ignores binding ops — they change attribution, not the document', () => {
        const live = liveSnapshot(doc([['f-1', 'Storage', 'Prose.', 0]]));
        const t = buildTimeline(file([
            entry({ event_id: 'e-1', kind: 'attach', feature_id: 'f-1', bindings: ['db.py::open'] }),
        ]));
        expect(snapshotAt(live, t, -1).features.get('f-1')?.description).toBe('Prose.');
    });

    it('never mutates the snapshot it was given', () => {
        const live = liveSnapshot(doc([['f-1', 'Sessions', 'Now.', 0]]));
        const t = buildTimeline(file([
            entry({ event_id: 'e-1', kind: 'amend', feature_id: 'f-1',
                    description: 'Now.', prev_description: 'Then.' }),
        ]));
        snapshotAt(live, t, -1);
        expect(live.features.get('f-1')?.description).toBe('Now.');
        expect(live.unresolved.size).toBe(0);
    });
});

describe('preorder', () => {
    it('terminates on a cycle rather than hanging the editor', () => {
        const snap: Snapshot = {
            features: new Map([
                ['a', { fid: 'a', title: 'A', description: '', parentId: 'b' }],
                ['b', { fid: 'b', title: 'B', description: '', parentId: 'a' }],
            ]),
            order: ['a', 'b'],
            unresolved: new Set(),
        };
        expect(preorder(snap)).toEqual([]);   // neither is reachable from a root
    });

    it('treats a dangling parent as a root so nothing becomes invisible', () => {
        const snap: Snapshot = {
            features: new Map([['a', { fid: 'a', title: 'A', description: '', parentId: 'gone' }]]),
            order: ['a'],
            unresolved: new Set(),
        };
        expect(preorder(snap)).toEqual([{ fid: 'a', level: 0 }]);
    });
});

// ── what changed at a moment ─────────────────────────────────────────────────

describe('changesAt', () => {
    it('reports the change a reader perceives, not each intermediate step', () => {
        const live = liveSnapshot(doc([['f-1', 'Sessions', 'Third.', 0]]));
        const t = buildTimeline(file([
            entry({ event_id: 'e-2', kind: 'amend', feature_id: 'f-1', at: hlc(1_000),
                    description: 'Third.', prev_description: 'Second.' }),
            entry({ event_id: 'e-1', kind: 'amend', feature_id: 'f-1', at: hlc(0),
                    description: 'Second.', prev_description: 'First.' }),
        ]));
        expect(t.moments).toHaveLength(1);   // one sitting
        const [c] = changesAt(live, t, 0);
        expect(c.before?.description).toBe('First.');
        expect(c.after?.description).toBe('Third.');
    });

    it('marks a creation with no before', () => {
        const live = liveSnapshot(doc([['f-1', 'Uploads', 'Accepts files.', 0]]));
        const t = buildTimeline(file([
            entry({ event_id: 'e-1', kind: 'add_node', feature_id: 'f-1', title: 'Uploads' }),
        ]));
        const [c] = changesAt(live, t, 0);
        expect(c.before).toBeNull();
        expect(c.after?.title).toBe('Uploads');
    });

    it('marks a retirement with no after', () => {
        const live = liveSnapshot(doc([['f-1', 'Storage', '', 0]]));
        const t = buildTimeline(file([
            entry({ event_id: 'e-1', kind: 'retire_node', feature_id: 'f-9',
                    prev_title: 'Legacy', prev_description: 'Old.', prev_parent_id: '' }),
        ]));
        const [c] = changesAt(live, t, 0);
        expect(c.before?.title).toBe('Legacy');
        expect(c.after).toBeNull();
    });

    it('flags an unreconstructible change instead of diffing against the wrong words', () => {
        const live = liveSnapshot(doc([['f-1', 'Sessions', 'Prose.', 0]]));
        const t = buildTimeline(file([
            entry({ event_id: 'e-1', kind: 'amend', feature_id: 'f-1', description: 'Prose.' }),
        ]));
        expect(changesAt(live, t, 0)[0].unresolved).toBe(true);
    });

    it('reports an attribution-only moment with no prose delta', () => {
        const live = liveSnapshot(doc([['f-1', 'Storage', 'Prose.', 0]]));
        const t = buildTimeline(file([
            entry({ event_id: 'e-1', kind: 'attach', feature_id: 'f-1', bindings: ['db.py::open'] }),
        ]));
        const [c] = changesAt(live, t, 0);
        expect(c.before?.description).toBe(c.after?.description);
        expect(c.kinds).toEqual(['attach']);
    });

    it('is empty for an index off the end', () => {
        const live = liveSnapshot(doc([['f-1', 'A', '', 0]]));
        expect(changesAt(live, buildTimeline(file([])), 0)).toEqual([]);
    });
});

describe('filesTouched', () => {
    it('dedupes files across a moment and drops the symbol suffix', () => {
        const t = buildTimeline(file([
            entry({ event_id: 'e-2', kind: 'attach', feature_id: 'f-1', at: hlc(1_000),
                    bindings: ['auth.py::refresh', 'db.py::open'] }),
            entry({ event_id: 'e-1', kind: 'attach', feature_id: 'f-1', at: hlc(0),
                    bindings: ['auth.py::login'] }),
        ]));
        expect(filesTouched(t.moments[0])).toEqual(['auth.py', 'db.py']);
    });

    it('is empty when nothing bound', () => {
        const t = buildTimeline(file([entry({ event_id: 'e-1', kind: 'amend', feature_id: 'f-1' })]));
        expect(filesTouched(t.moments[0])).toEqual([]);
    });
});
