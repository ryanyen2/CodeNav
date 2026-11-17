import { describe, expect, it } from 'vitest';

import { findMatches } from '../../../web/editor/find';
import { codocSchema } from '../../../web/editor/schema';

const schema = codocSchema();
const para = (bid: string, text: string) => schema.nodes.paragraph.create({ bid, head: 1 }, text ? schema.text(text) : undefined);

describe('findMatches', () => {
    it('matches inside blocks only, case-insensitively', () => {
        const doc = schema.nodes.doc.create(null, [para('a', 'Retry once'), para('b', 'once more, ONCE')]);
        expect(findMatches(doc, 'once')).toEqual([{ from: 7, to: 11 }, { from: 13, to: 17 }, { from: 24, to: 28 }]);
        expect(findMatches(doc, 'once more')).toEqual([{ from: 13, to: 22 }]);
        expect(findMatches(doc, '')).toEqual([]);
    });
});
