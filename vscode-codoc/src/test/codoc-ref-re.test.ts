/**
 * codoc-ref-re.test.ts — pins the shared `codocRefRe()` factory (tree-model.ts),
 * the single source of truth for the inline `codoc:` citation regex used by the
 * three provider files (decoration.ts, hover.ts, doc-links.ts). The factory
 * returns a FRESH `g`-flagged RegExp per call so consumers never corrupt each
 * other's mutable `lastIndex`; these tests assert the match shapes AND the
 * no-shared-state property.
 */
import { describe, it, expect } from 'vitest';
import { codocRefRe } from '../state/tree-model';

/** Collect every `(file, symbol)` match in a line via a fresh factory regex. */
function matchAll(line: string): { file: string; symbol: string | null }[] {
    const re = codocRefRe();
    const out: { file: string; symbol: string | null }[] = [];
    let m: RegExpExecArray | null;
    while ((m = re.exec(line)) !== null) {
        out.push({ file: m[1], symbol: m[2] ?? null });
    }
    return out;
}

describe('codocRefRe — match shapes', () => {
    it('captures (file, symbol) for a symbol ref', () => {
        expect(matchAll('see [login](codoc:auth.py#Session.login) here')).toEqual([
            { file: 'auth.py', symbol: 'Session.login' },
        ]);
    });

    it('captures (file, null) for a file-only ref', () => {
        expect(matchAll('the [module](codoc:auth.py) owns it')).toEqual([
            { file: 'auth.py', symbol: null },
        ]);
    });

    it('captures both refs on a single line', () => {
        const got = matchAll('[a](codoc:x.py#f) and [b](codoc:y.ts#g)');
        expect(got).toEqual([
            { file: 'x.py', symbol: 'f' },
            { file: 'y.ts', symbol: 'g' },
        ]);
    });

    it('label is non-capturing (group 1 is the file, not the label)', () => {
        const re = codocRefRe();
        const m = re.exec('[my label](codoc:a.py#b)');
        expect(m).not.toBeNull();
        expect(m![1]).toBe('a.py');   // file, not "my label"
        expect(m![2]).toBe('b');      // symbol
    });

    it('does NOT match an external https:// link', () => {
        expect(matchAll('see [docs](https://example.com)')).toEqual([]);
    });

    it('is global (g flag) so .exec walks every match', () => {
        expect(codocRefRe().flags).toContain('g');
    });
});

describe('codocRefRe — fresh per call (no shared lastIndex)', () => {
    it('two consumers do not corrupt each other’s iteration state', () => {
        const a = codocRefRe();
        const b = codocRefRe();
        expect(a).not.toBe(b);             // distinct instances
        a.exec('[x](codoc:a.py#f) [y](codoc:b.py#g)');  // advances a.lastIndex
        expect(a.lastIndex).toBeGreaterThan(0);
        expect(b.lastIndex).toBe(0);       // b is untouched
        // b still matches from the top.
        expect(b.exec('[z](codoc:c.py#h)')![1]).toBe('c.py');
    });
});
