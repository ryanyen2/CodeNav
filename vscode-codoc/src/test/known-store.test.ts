/**
 * known-store.test.ts — where `base_text` is allowed to come from.
 *
 * A command's `base_text` is the value the AUTHOR last knew (loop_b._resolve_content
 * merges from it). Two sources are honest: this host's own emitted-but-unechoed writes,
 * and the baseline the settle CITES. A projection the author may never have adopted is
 * NOT one, and the failure is silent: base_text equal to the store's current text reads
 * as a clean continuation, so the daemon applies the incoming text verbatim over
 * whoever wrote in between — no merge, no conflict record, no notice (review finding #7).
 *
 * These pin the overlay's two rules (only own writes go in; only a confirming projection
 * takes them out) and the end-to-end consequence for the emitted command.
 */
import { describe, it, expect } from 'vitest';
import { advanceKnown, pruneKnown, emptyKnownStore } from '../state/known-store';
import { commandsForSettle, settleCommands, type FeatureUnit } from '../state/commands-from-doc';
import type { CommandEntry } from '../state/edits-channel';

const unit = (fid: string, title: string, description: string): FeatureUnit =>
    ({ fid, localId: null, title, description, parentId: null, retired: false, realized: true });

const setDesc = (fid: string, description: string): CommandEntry =>
    ({ id: `c-${fid}`, kind: 'set_description', feature_id: fid, payload: { description } });

describe('the optimistic overlay — only this host writes into it', () => {
    it('records a content command per field, leaving the other field unclaimed', () => {
        const k = advanceKnown(emptyKnownStore(), [
            { id: 'c1', kind: 'set_title', feature_id: 'f-1', payload: { title: 'Renamed' } },
        ]);
        expect(k.get('f-1')).toEqual({ title: 'Renamed' });
        // The author said nothing about the description, so nothing may claim to know it.
        expect(k.get('f-1')?.description).toBeUndefined();
    });

    it('ignores move and retire — neither writes text', () => {
        // A recorded entry for a kind no projection can ever confirm would pin a stale
        // base_text on the feature forever.
        const k = advanceKnown(emptyKnownStore(), [
            { id: 'c1', kind: 'move', feature_id: 'f-1', payload: { parent_id: 'f-2' } },
            { id: 'c2', kind: 'retire', feature_id: 'f-3' },
        ]);
        expect(k.size).toBe(0);
    });

    it('a projection that agrees drops the entry; one that disagrees keeps it', () => {
        const k = advanceKnown(emptyKnownStore(), [setDesc('f-1', 'mine'), setDesc('f-2', 'mine too')]);
        // f-1 landed in the store; f-2's command is still in flight (or was overwritten).
        const pruned = pruneKnown(k, [unit('f-1', 'A', 'mine'), unit('f-2', 'B', 'something else')]);
        expect(pruned.has('f-1')).toBe(false);
        expect(pruned.get('f-2')).toEqual({ description: 'mine too' });
    });

    it('drops a feature the projection no longer carries', () => {
        const k = advanceKnown(emptyKnownStore(), [setDesc('f-gone', 'mine')]);
        expect(pruneKnown(k, [unit('f-1', 'A', 'a')]).size).toBe(0);
    });

    it('prunes field-wise: a confirmed title does not carry an unconfirmed description out', () => {
        const k = advanceKnown(emptyKnownStore(), [
            { id: 'c1', kind: 'set_title', feature_id: 'f-1', payload: { title: 'New' } },
            setDesc('f-1', 'new prose'),
        ]);
        const pruned = pruneKnown(k, [unit('f-1', 'New', 'the agent rewrote this')]);
        expect(pruned.get('f-1')).toEqual({ description: 'new prose' });
    });
});

describe('base_text provenance — the author, never the newest projection', () => {
    const baseline = [unit('f-1', 'Auth', 'original')];

    it('falls back to the CITED baseline for a feature this host never wrote', () => {
        const next = [unit('f-1', 'Auth', 'my new prose')];
        const [cmd] = commandsForSettle(baseline, next, 't1', emptyKnownStore(), 'sess-a');
        // What the author saw — not what the store now holds, which is the whole point.
        expect(cmd.base_text).toBe('original');
    });

    it('prefers this host\'s own unechoed write, so a burst is not a conflict with itself', () => {
        const known = advanceKnown(emptyKnownStore(), [setDesc('f-1', 'first')]);
        const [cmd] = commandsForSettle(baseline, [unit('f-1', 'Auth', 'first second')], 't2', known, 'sess-a');
        expect(cmd.base_text).toBe('first');
    });

    it('#7: a foreign write the author never adopted is not claimed as their base', () => {
        // The sequence that used to lose data: the host reads projection N+1 carrying an
        // agent amend, but the editor is still showing (and citing) baseline N.
        const history = [{ id: 8, units: baseline }];
        const agentWrote = [unit('f-1', 'Auth', 'the agent rewrote this')];
        const cmds = settleCommands(
            history, 8, /* fallback = the newest projection */ agentWrote,
            [unit('f-1', 'Auth', 'my prose, typed against the old text')],
            't3', pruneKnown(emptyKnownStore(), agentWrote), 'sess-a',
        );
        expect(cmds).toHaveLength(1);
        // Had this been the agent's text, the daemon would see base == current, call it a
        // clean continuation, and apply verbatim over the amend.
        expect(cmds[0].base_text).toBe('original');
    });

    it('a title edit\'s base is the title the author saw, not one the overlay guessed', () => {
        // A whole-unit overlay had to fill the untouched field from the projection, which
        // smuggled the projection back in through the field the author never edited.
        const known = advanceKnown(emptyKnownStore(), [setDesc('f-1', 'prose I wrote')]);
        const cmds = commandsForSettle(
            baseline, [unit('f-1', 'Renamed', 'prose I wrote, then more')], 't4', known, 'sess-a');
        expect(cmds.map(c => [c.kind, c.base_text])).toEqual([
            ['set_title', 'Auth'],                  // the cited baseline: what the author saw
            ['set_description', 'prose I wrote'],   // the overlay: what this host already sent
        ]);
    });
});
