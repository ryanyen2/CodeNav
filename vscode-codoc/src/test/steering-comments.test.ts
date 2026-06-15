/**
 * Steering comments (`> …`) — TS parser parity with codoc/codoc_file/parse.py
 * (tests/codoc_file/test_steering.py). A blockquote line inside a description
 * is a note TO THE AGENT: collected into `comments`, excluded from prose, and
 * a comment-only edit must not change the parsed description.
 */
import { describe, it, expect } from 'vitest';
import { parseTreeCodoc } from '../state/tree-model';

function node(text: string, title = 'Feat') {
    const { features } = parseTreeCodoc(text);
    const f = features.find(x => x.title === title);
    expect(f).toBeDefined();
    return f!;
}

describe('steering comments', () => {
    it('collects a comment and excludes it from the description', () => {
        const f = node([
            '- Feat  ⟨f-0000aaaa⟩',
            '  Validates user input.',
            '  > also handle unicode emails',
        ].join('\n'));
        expect(f.description).toBe('Validates user input.');
        expect(f.comments).toEqual([{ text: 'also handle unicode emails', line: 2 }]);
    });

    it('a contiguous run is one comment; two runs are two', () => {
        const f = node([
            '- Feat  ⟨f-0000aaaa⟩',
            '  prose',
            '  > first line',
            '  > second line',
            '',
            '  > another note',
        ].join('\n'));
        expect(f.comments.map(c => c.text)).toEqual(['first line\nsecond line', 'another note']);
        expect(f.comments.map(c => c.line)).toEqual([2, 5]);
        expect(f.description).toBe('prose');
    });

    it('a comment-only edit does not change the parsed prose', () => {
        const plain = node('- Feat  ⟨f-0000aaaa⟩\n  para one\n\n  para two\n');
        const commented = node([
            '- Feat  ⟨f-0000aaaa⟩',
            '  para one',
            '',
            '  > steer here',
            '',
            '  para two',
        ].join('\n'));
        expect(commented.description).toBe(plain.description);
        expect(commented.description).toBe('para one\n\npara two');
    });

    it('comments before prose and at EOF', () => {
        const f = node([
            '- Feat  ⟨f-0000aaaa⟩',
            '  > do it first',
            '  prose',
            '  > and last',
        ].join('\n'));
        expect(f.description).toBe('prose');
        expect(f.comments.map(c => c.text)).toEqual(['do it first', 'and last']);
    });

    it('a # line separates two steering runs (parity with parse.py)', () => {
        const f = node([
            '- Feat  ⟨f-0000aaaa⟩',
            '  prose',
            '  > one',
            '  # divider',
            '  > two',
        ].join('\n'));
        expect(f.comments.map(c => c.text)).toEqual(['one', 'two']);
    });

    it('a comment outside any feature is ignored', () => {
        const { features } = parseTreeCodoc('> stray note\n- Feat  ⟨f-0000aaaa⟩\n  prose\n');
        expect(features[0].comments).toEqual([]);
        expect(features[0].description).toBe('prose');
    });
});
