import { describe, it, expect } from 'vitest';
import {
    displacedHuman, editKey, unseenEdits, pruneSeen, catchUpLabel,
} from '../state/auto-edits';
import type { AutoEdit } from '../state/bindings-model';

const edit = (over: Partial<AutoEdit> = {}): AutoEdit => ({
    at: '1', prev: 'old prose', written_by: 'loop', rationale: '', ...over,
});

describe('weighting — whose words were displaced', () => {
    it('the loop revising its own bootstrap prose reads as housekeeping', () => {
        expect(displacedHuman(edit({ written_by: 'loop' }))).toBe(false);
    });

    it("a rewrite of the reader's own words is named as theirs", () => {
        expect(displacedHuman(edit({ written_by: 'human' }))).toBe(true);
    });

    it('an agent counts as not-the-reader — it is not the person being surprised', () => {
        expect(displacedHuman(edit({ written_by: 'claude-code' }))).toBe(false);
    });

    it('a legacy row with no authorship degrades to the quiet reading', () => {
        expect(displacedHuman(edit({ written_by: '' }))).toBe(false);
    });
});

describe('the seen-set is keyed per REWRITE, not per feature', () => {
    it('a later rewrite of the same feature is unseen again', () => {
        const first = edit({ at: '1' });
        const later = edit({ at: '2' });
        const seen = new Set([editKey('f-a', first)]);
        expect(unseenEdits({ 'f-a': first }, seen, ['f-a'])).toEqual([]);
        // the loop came back and rewrote it a second time — that is news
        expect(unseenEdits({ 'f-a': later }, seen, ['f-a'])).toHaveLength(1);
    });

    it('returns the unseen ones in the order they were asked for (document order)', () => {
        const edits = { 'f-b': edit({ at: '1' }), 'f-a': edit({ at: '1' }) };
        expect(unseenEdits(edits, new Set(), ['f-a', 'f-b']).map(u => u.fid))
            .toEqual(['f-a', 'f-b']);
    });

    it('skips features with no rewrite rather than throwing on the gap', () => {
        expect(unseenEdits({ 'f-a': edit() }, new Set(), ['f-missing', 'f-a']))
            .toHaveLength(1);
    });
});

describe('pruneSeen keeps the acknowledgement set from growing forever', () => {
    it('drops keys whose rewrite is no longer offered', () => {
        const e = edit({ at: '1' });
        const seen = new Set([editKey('f-a', e), editKey('f-gone', e)]);
        expect([...pruneSeen(seen, { 'f-a': e })]).toEqual([editKey('f-a', e)]);
    });

    it('drops the acknowledgement when the SAME feature gets a newer rewrite', () => {
        const seen = new Set([editKey('f-a', edit({ at: '1' }))]);
        expect(pruneSeen(seen, { 'f-a': edit({ at: '2' }) }).size).toBe(0);
    });

    it('empties out when nothing is pending', () => {
        expect(pruneSeen(new Set(['f-a@1']), {}).size).toBe(0);
    });
});

describe('the catch-up line spends words only on the distinction that matters', () => {
    it('says nothing at all when there is nothing to catch up on', () => {
        expect(catchUpLabel([])).toBe('');
    });

    it('counts plainly when the loop only revised its own prose', () => {
        expect(catchUpLabel([{ edit: edit() }, { edit: edit() }]))
            .toBe('codoc rewrote 2 descriptions');
        expect(catchUpLabel([{ edit: edit() }])).toBe('codoc rewrote 1 description');
    });

    it("names it as YOURS when the reader's own wording was displaced", () => {
        const mine = { edit: edit({ written_by: 'human' }) };
        expect(catchUpLabel([mine])).toBe('codoc edited your wording');
        expect(catchUpLabel([mine, mine])).toBe('codoc edited your wording in 2 places');
    });

    it('separates the two when the batch is mixed', () => {
        expect(catchUpLabel([{ edit: edit({ written_by: 'human' }) }, { edit: edit() }]))
            .toBe('codoc rewrote 2 descriptions (1 of yours)');
    });
});
