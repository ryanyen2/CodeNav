/**
 * find.test.ts — the pure logic of in-document find & replace. The widget DOM
 * (find-view.ts) and the editor integration (whole-doc-editor.ts) are covered by
 * hand; this locks the parts that decide WHAT matches and what a replacement
 * becomes — matching modes, stepping/wrapping, capture groups, case preservation.
 */
import { describe, it, expect } from 'vitest';
import {
    buildMatcher, findInBlocks, stepIndexFrom, wrapIndex, replacementFor, matchCase,
    matchLabel, DEFAULT_FIND_OPTIONS, type SearchBlock, type FindOptions,
} from '../webview/find';

const opts = (over: Partial<FindOptions> = {}): FindOptions => ({ ...DEFAULT_FIND_OPTIONS, ...over });

/** One block starting at doc position `base`. */
const block = (text: string, base = 1, fid = 'f1'): SearchBlock =>
    ({ text, base, fid, field: 'description' });

describe('buildMatcher', () => {
    it('escapes regex metacharacters in literal mode', () => {
        const re = buildMatcher('a.b', opts())!;
        expect(re.test('axb')).toBe(false);
        expect(re.test('a.b')).toBe(true);
    });
    it('honours the regex flag', () => {
        expect(buildMatcher('a.b', opts({ regex: true }))!.test('axb')).toBe(true);
    });
    it('returns null for an empty query and for a broken regex', () => {
        expect(buildMatcher('', opts())).toBeNull();
        expect(buildMatcher('(unclosed', opts({ regex: true }))).toBeNull();
    });
    it('whole-word uses script-agnostic boundaries', () => {
        const re = buildMatcher('cat', opts({ wholeWord: true }))!;
        expect('cat sat'.match(re)).not.toBeNull();
        expect('category'.match(re)).toBeNull();
    });
});

describe('findInBlocks', () => {
    it('maps a match to its document range via the block base', () => {
        const m = findInBlocks([block('the header', 5)], 'header', opts());
        expect(m).toHaveLength(1);
        expect(m[0]).toMatchObject({ from: 9, to: 15, text: 'header' });
    });
    it('is case-insensitive by default and case-sensitive on request', () => {
        expect(findInBlocks([block('Header')], 'header', opts())).toHaveLength(1);
        expect(findInBlocks([block('Header')], 'header', opts({ caseSensitive: true }))).toHaveLength(0);
    });
    it('returns matches across blocks in document order', () => {
        const ms = findInBlocks([block('a x', 1), block('x a', 10)], 'x', opts());
        expect(ms.map(m => m.from)).toEqual([3, 10]);
    });
    it('does not loop forever on a zero-width match', () => {
        const ms = findInBlocks([block('abc')], 'x*', opts({ regex: true }));
        expect(ms).toEqual([]);  // every match is empty → all skipped, no hang
    });
    it('captures groups for a regex replacement', () => {
        const ms = findInBlocks([block('v1.2')], 'v(\\d)\\.(\\d)', opts({ regex: true }));
        expect(ms[0].groups).toEqual(['1', '2']);
    });
});

describe('stepIndexFrom', () => {
    const ms = findInBlocks([block('x..x..x', 1)], 'x', opts()); // from = 1, 4, 7
    it('finds the first match at or after the caret going forward', () => {
        expect(stepIndexFrom(ms, 2, true)).toBe(1);  // next after pos 2 is the one at 4
    });
    it('wraps forward past the last match', () => {
        expect(stepIndexFrom(ms, 8, true)).toBe(0);
    });
    it('finds the last match before the caret going backward', () => {
        expect(stepIndexFrom(ms, 5, false)).toBe(1); // last before pos 5 is the one at 4
    });
    it('wraps backward before the first match', () => {
        expect(stepIndexFrom(ms, 0, false)).toBe(2);
    });
    it('returns -1 with no matches', () => {
        expect(stepIndexFrom([], 0, true)).toBe(-1);
    });
});

describe('wrapIndex', () => {
    it('wraps in both directions', () => {
        expect(wrapIndex(2, 3, +1)).toBe(0);
        expect(wrapIndex(0, 3, -1)).toBe(2);
    });
    it('is -1 for an empty set', () => {
        expect(wrapIndex(0, 0, +1)).toBe(-1);
    });
});

describe('replacementFor', () => {
    const m = findInBlocks([block('v1.2')], 'v(\\d)\\.(\\d)', opts({ regex: true }))[0];
    it('expands $1/$2/$& in regex mode', () => {
        expect(replacementFor(m, '$2.$1', opts({ regex: true }))).toBe('2.1');
        expect(replacementFor(m, '[$&]', opts({ regex: true }))).toBe('[v1.2]');
    });
    it('leaves $ literal in non-regex mode', () => {
        const lit = findInBlocks([block('price')], 'price', opts())[0];
        expect(replacementFor(lit, '$1', opts())).toBe('$1');
    });
    it('preserves the shape of what it replaced when asked', () => {
        const caps = findInBlocks([block('The HEADER here')], 'header', opts())[0];
        expect(replacementFor(caps, 'footer', { ...opts(), preserveCase: true })).toBe('FOOTER');
    });
});

describe('matchCase', () => {
    it('mirrors all-caps and Capitalized, leaves lower and mixed alone', () => {
        expect(matchCase('FOO', 'bar')).toBe('BAR');
        expect(matchCase('Foo', 'bar')).toBe('Bar');
        expect(matchCase('foo', 'bar')).toBe('bar');
        expect(matchCase('fOo', 'bar')).toBe('bar'); // mixed → untouched, no guessing
    });
});

describe('matchLabel', () => {
    it('reads "No results", "3 of 17", and marks the ceiling', () => {
        expect(matchLabel(-1, 0)).toBe('No results');
        expect(matchLabel(2, 17)).toBe('3 of 17');
        expect(matchLabel(0, 2000)).toBe('1 of 2000+');
    });
});
