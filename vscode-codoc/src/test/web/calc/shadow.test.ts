import { describe, expect, it } from 'vitest';

import { Shadow } from '../../../web/calc/shadow';
import { START, refsOf, type TextField } from '../../../web/calc/projection';

function field(text: string, runs: [string, number, number][]): TextField {
    return {
        text,
        runs: runs.map(([op, start, end]) => ({ op, k0: 0, start, end, actor: 'human:a', at: '1', plan: null })),
        spans: [],
        suggest_del: [],
    };
}

describe('Shadow', () => {
    it('maps live offsets to identities and counts scalars, not UTF-16 units', () => {
        const f = field('a😀b', [['s:1', 0, 3]]);
        const sh = Shadow.fromField(f.text, refsOf(f));
        expect(sh.length).toBe(3);
        expect(sh.ref(1)).toEqual(['s:1', 1]);
        expect(sh.ref(2)).toEqual(['s:1', 2]);
        expect(sh.anchorBefore(0)).toBe(START);
        expect(sh.anchorBefore(3)).toEqual(['s:1', 2]);
    });

    it('records local inserts provisionally and resolves them on ack', () => {
        const f = field('ab', [['s:1', 0, 2]]);
        const sh = Shadow.fromField(f.text, refsOf(f));
        sh.insert(1, 'xy', 'c1');
        expect(sh.text()).toBe('axyb');
        expect(sh.ref(1)).toEqual(['$cid', 'c1', 0]);
        expect(sh.resolve('c1', 's:9')).toBe(2);
        expect(sh.ref(2)).toEqual(['s:9', 1]);
        expect(sh.refs.some(r => r[0] === '$cid')).toBe(false);
    });

    it('delete returns the identities removed; split and append preserve identity', () => {
        const f = field('abcdef', [['s:1', 0, 6]]);
        const sh = Shadow.fromField(f.text, refsOf(f));
        expect(sh.delete(1, 3)).toEqual([['s:1', 1], ['s:1', 2]]);
        expect(sh.text()).toBe('adef');
        const tail = sh.split(2);
        expect(sh.text()).toBe('ad');
        expect(tail.text()).toBe('ef');
        expect(tail.ref(0)).toEqual(['s:1', 4]);
        sh.append(tail);
        expect(sh.text()).toBe('adef');
        expect(sh.indexOf(['s:1', 5])).toBe(3);
    });
});
