/**
 * commands-from-doc.test.ts — U4: webview as projection consumer + COMMAND EMITTER.
 *
 * The host no longer persists tree.doc.json or infers ops from a doc/text diff. On a
 * settle it diffs the settled doc against the daemon's last projection KEYED BY IDENTITY
 * (fid, else localId) and emits identity-keyed commands (U3). These pin that contract:
 * an edit emits a command, a delete emits a `retire`, an unchanged settle emits nothing.
 */
import { describe, it, expect } from 'vitest';
import { commandsForSettle, featureUnits, moveCommand, settleCommands } from '../state/commands-from-doc';
import { makeDoc, featureHeadingNode, paragraphNode, textToInlineRuns } from '../state/pm-doc';
import type { PMNode } from '../state/pm-doc';

/** A featureHeading + one description paragraph, matching the projection shape. */
const feat = (attrs: { fid?: string | null; localId?: string | null; level?: number }, title: string, desc: string): PMNode[] => [
    featureHeadingNode(
        { fid: attrs.fid ?? null, level: attrs.level ?? 0, retired: false, realized: true, localId: attrs.localId ?? null },
        textToInlineRuns(title),
    ),
    ...(desc ? [paragraphNode(textToInlineRuns(desc))] : []),
];

describe('featureUnits — identity + parent extraction', () => {
    it('resolves parent from the level stack and carries fid/localId', () => {
        const doc = makeDoc([
            ...feat({ fid: 'f-parent' }, 'Parent', 'p'),
            ...feat({ fid: 'f-child', level: 1 }, 'Child', 'c'),
        ]);
        const units = featureUnits(doc);
        expect(units.map(u => [u.fid, u.parentId])).toEqual([
            ['f-parent', null],
            ['f-child', 'f-parent'],
        ]);
    });
});

describe('commandsForSettle — identity-keyed command emission', () => {
    const baseline = makeDoc([
        ...feat({ fid: 'f-1' }, 'Auth', 'Validates input.'),
        ...feat({ fid: 'f-2' }, 'Theme', 'Switcher.'),
    ]);
    const prev = featureUnits(baseline);

    it('a title edit emits exactly one set_title command keyed by fid', () => {
        const next = featureUnits(makeDoc([
            ...feat({ fid: 'f-1' }, 'Authentication', 'Validates input.'),
            ...feat({ fid: 'f-2' }, 'Theme', 'Switcher.'),
        ]));
        const cmds = commandsForSettle(prev, next, 1);
        expect(cmds).toHaveLength(1);
        expect(cmds[0]).toMatchObject({ kind: 'set_title', feature_id: 'f-1', payload: { title: 'Authentication' } });
    });

    it('a description edit emits one set_description command', () => {
        const next = featureUnits(makeDoc([
            ...feat({ fid: 'f-1' }, 'Auth', 'Validates and sanitizes input.'),
            ...feat({ fid: 'f-2' }, 'Theme', 'Switcher.'),
        ]));
        const cmds = commandsForSettle(prev, next, 1);
        expect(cmds).toHaveLength(1);
        expect(cmds[0]).toMatchObject({ kind: 'set_description', feature_id: 'f-1' });
    });

    it('a brand-new node (no fid, has localId) emits one add keyed to the localId', () => {
        const next = featureUnits(makeDoc([
            ...feat({ fid: 'f-1' }, 'Auth', 'Validates input.'),
            ...feat({ fid: 'f-2' }, 'Theme', 'Switcher.'),
            ...feat({ localId: 'L-9' }, 'Brand new', 'fresh'),
        ]));
        const cmds = commandsForSettle(prev, next, 1);
        expect(cmds).toHaveLength(1);
        expect(cmds[0]).toMatchObject({ kind: 'add', local_id: 'L-9', payload: { title: 'Brand new' } });
    });

    it('an add command id is DETERMINISTIC from the localId (no salt) — FIX B', () => {
        const next = featureUnits(makeDoc([
            ...feat({ fid: 'f-1' }, 'Auth', 'Validates input.'),
            ...feat({ fid: 'f-2' }, 'Theme', 'Switcher.'),
            ...feat({ localId: 'L-9' }, 'Brand new', 'fresh'),
        ]));
        // Two settles with DIFFERENT salts produce the SAME add command id, so a
        // re-emitted add collides on the daemon's ledger and never mints a second node.
        const a = commandsForSettle(prev, next, 111);
        const b = commandsForSettle(prev, next, 222);
        expect(a[0].id).toBe('c-add-L-9');
        expect(b[0].id).toBe('c-add-L-9');
        expect(a[0].id).toBe(b[0].id);
    });

    it('a re-emit of the same add (changed title, same localId) keeps the same id — FIX B', () => {
        // The settle fires again before the fid echoes back; the user has kept typing,
        // so the title changed but the localId is stable. The add id must NOT change,
        // so the ledger folds it (no duplicate feature).
        const first = commandsForSettle(prev, featureUnits(makeDoc([
            ...feat({ fid: 'f-1' }, 'Auth', 'Validates input.'),
            ...feat({ fid: 'f-2' }, 'Theme', 'Switcher.'),
            ...feat({ localId: 'L-9' }, 'Palette', 'fresh'),
        ])), 1);
        const second = commandsForSettle(prev, featureUnits(makeDoc([
            ...feat({ fid: 'f-1' }, 'Auth', 'Validates input.'),
            ...feat({ fid: 'f-2' }, 'Theme', 'Switcher.'),
            ...feat({ localId: 'L-9' }, 'Palette renamed', 'fresh'),
        ])), 2);
        expect(first[0].id).toBe(second[0].id);  // same id despite the title change
        expect(second[0]).toMatchObject({ kind: 'add', local_id: 'L-9', payload: { title: 'Palette renamed' } });
    });

    it('a deleted node emits exactly one retire command (R7 — no resurrection)', () => {
        const next = featureUnits(makeDoc([
            ...feat({ fid: 'f-1' }, 'Auth', 'Validates input.'),
            // f-2 removed
        ]));
        const cmds = commandsForSettle(prev, next, 1);
        expect(cmds).toHaveLength(1);
        expect(cmds[0]).toMatchObject({ kind: 'retire', feature_id: 'f-2' });
    });

    it('an unchanged settle emits NO command (no churn, no spurious re-mint)', () => {
        const next = featureUnits(baseline);
        expect(commandsForSettle(prev, next, 1)).toEqual([]);
    });

    it('a reparent emits a move command keyed by fid', () => {
        const next = featureUnits(makeDoc([
            ...feat({ fid: 'f-1' }, 'Auth', 'Validates input.'),
            ...feat({ fid: 'f-2', level: 1 }, 'Theme', 'Switcher.'),  // now under f-1
        ]));
        const cmds = commandsForSettle(prev, next, 1);
        expect(cmds).toHaveLength(1);
        expect(cmds[0]).toMatchObject({ kind: 'move', feature_id: 'f-2', payload: { parent_id: 'f-1' } });
    });
});

describe('settleCommands — #4: diff against the CITED baseline, not a newer projection', () => {
    // Baseline B0: what the editor was showing. The user edits f-1's title in it.
    const b0 = makeDoc([
        ...feat({ fid: 'f-1' }, 'Auth', 'Validates input.'),
        ...feat({ fid: 'f-2' }, 'Theme', 'Switcher.'),
    ]);
    const b0Units = featureUnits(b0);
    // B1: a NEWER projection the daemon pushed mid-typing — it added f-3.
    const b1Units = featureUnits(makeDoc([
        ...feat({ fid: 'f-1' }, 'Auth', 'Validates input.'),
        ...feat({ fid: 'f-2' }, 'Theme', 'Switcher.'),
        ...feat({ fid: 'f-3' }, 'Agent-added', 'new'),
    ]));
    // The settled editor doc (computed from B0, so it has NO f-3) with f-1 retitled.
    const settledUnits = featureUnits(makeDoc([
        ...feat({ fid: 'f-1' }, 'Authentication', 'Validates input.'),
        ...feat({ fid: 'f-2' }, 'Theme', 'Switcher.'),
    ]));
    const history = [{ id: 1, units: b0Units }, { id: 2, units: b1Units }];

    it('diffs against the cited baseline B0 → the edit only, NO phantom retire of f-3', () => {
        // fallback is the LATEST projection B1 (has f-3) — the old bug diffed against this.
        const cmds = settleCommands(history, 1, b1Units, settledUnits, 1);
        expect(cmds).toHaveLength(1);
        expect(cmds[0]).toMatchObject({ kind: 'set_title', feature_id: 'f-1' });
        expect(cmds.some(c => c.kind === 'retire')).toBe(false);
    });

    it('WITHOUT the fix (diff against the newer projection) f-3 would phantom-retire', () => {
        // Demonstrates the bug the baseline id prevents: diffing the B0-editor doc against
        // B1 reads f-3 (added by the daemon) as a user deletion.
        const buggy = commandsForSettle(b1Units, settledUnits, 1);
        expect(buggy.some(c => c.kind === 'retire' && c.feature_id === 'f-3')).toBe(true);
    });

    it('an uncited baseline suppresses retire (conservative) but keeps other commands', () => {
        // A genuine deletion of f-2 in the settled doc, but no baselineId cited.
        const deleted = featureUnits(makeDoc([
            ...feat({ fid: 'f-1' }, 'Authentication', 'Validates input.'),
        ]));
        const cmds = settleCommands(history, undefined, b0Units, deleted, 1);
        expect(cmds.some(c => c.kind === 'retire')).toBe(false);   // suppressed
        expect(cmds.some(c => c.kind === 'set_title' && c.feature_id === 'f-1')).toBe(true);
    });

    it('an evicted baseline id (not in history) also suppresses retire', () => {
        const deleted = featureUnits(makeDoc([...feat({ fid: 'f-1' }, 'Auth', 'Validates input.')]));
        const cmds = settleCommands(history, 999, b0Units, deleted, 1);
        expect(cmds.some(c => c.kind === 'retire')).toBe(false);
    });

    it('a cited baseline allows a genuine retire through', () => {
        const deleted = featureUnits(makeDoc([...feat({ fid: 'f-1' }, 'Auth', 'Validates input.')]));
        const cmds = settleCommands(history, 1, b0Units, deleted, 1);
        expect(cmds.some(c => c.kind === 'retire' && c.feature_id === 'f-2')).toBe(true);
    });
});

describe('moveCommand — the tree-pane drag handler', () => {
    it('emits a move keyed by source fid with the new parent', () => {
        expect(moveCommand('f-2', 'f-1', 7)).toMatchObject({
            kind: 'move', feature_id: 'f-2', payload: { parent_id: 'f-1' },
        });
    });
    it('to-root carries a null parent', () => {
        expect(moveCommand('f-2', null, 7).payload).toEqual({ parent_id: null });
    });
});
