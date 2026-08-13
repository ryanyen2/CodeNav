/**
 * insert-at-anchor.test.ts — ghost rows draw in the slot the accepted node will
 * actually occupy (apply honours after_id/before_id via rank_between), so the
 * placeholder the user judged and the node they get are the same row.
 */
import { describe, it, expect } from 'vitest';
import { insertAtAnchor } from '../state/suggestion-model';

describe('insertAtAnchor', () => {
    it('inserts after the named sibling', () => {
        const list = ['f-1', 'f-2', 'f-3'];
        insertAtAnchor(list, 'e-ghost', 'f-1', null);
        expect(list).toEqual(['f-1', 'e-ghost', 'f-2', 'f-3']);
    });

    it('inserts before the named sibling when only before_id is given', () => {
        const list = ['f-1', 'f-2', 'f-3'];
        insertAtAnchor(list, 'e-ghost', null, 'f-3');
        expect(list).toEqual(['f-1', 'f-2', 'e-ghost', 'f-3']);
    });

    it('falls back to append when the anchor vanished — same resolution apply uses', () => {
        const list = ['f-1', 'f-2'];
        insertAtAnchor(list, 'e-ghost', 'f-gone', 'f-also-gone');
        expect(list).toEqual(['f-1', 'f-2', 'e-ghost']);
    });

    it('appends with no anchors (every caller before ordering existed)', () => {
        const list = ['f-1'];
        insertAtAnchor(list, 'e-ghost');
        expect(list).toEqual(['f-1', 'e-ghost']);
    });
});
