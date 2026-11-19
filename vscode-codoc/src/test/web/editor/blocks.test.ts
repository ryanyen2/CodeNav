// @vitest-environment jsdom
/**
 * The block kinds and the gestures that make and change them, each driven
 * through the mounted editor and checked by the ops it recorded.
 */
import { describe, expect, it } from 'vitest';

import type { ProjectedBlock, Projection } from '../../../web/calc/projection';
import { bodies, block, ids, key, kindBlock, kinds, mount, para, pasteText, posIn, select, shadowFaults, shape, texts, typeRule, typeText,
         type Mounted } from './harness';

const ctrl = { ctrlKey: true };
/** The bid of the n-th block on the page. */
const bidAt = (m: Mounted, i: number): string => ids(m.view.state.doc)[i].split(':')[1];
const sent = (m: Mounted) => { m.handle.flush(); return bodies(m.submitted); };

describe('block kinds on the page', () => {
    it('lists, tables, diagrams and code come from the projection, and numbered items show their computed number', () => {
        const m = mount([block('h', 'heading', 'T'), block('n1', 'numbered', 'one', 1), block('n2', 'numbered', 'two', 1),
                         block('n3', 'numbered', 'sub', 2), block('n4', 'numbered', 'three', 1), block('b1', 'bullet', 'dot', 1),
                         block('n5', 'numbered', 'again', 1), block('c', 'code', 'x = 1\ny = 2', 0, 1, { lang: 'py' }),
                         block('t', 'table', '| a |\n| b |', 0), block('d', 'diagram', 'graph TD', 0, 1, { derived: true }), kindBlock('k')]);
        expect(shape(m.view.state.doc)).toEqual(['heading:1', 'numbered:1', 'numbered:1', 'numbered:2', 'numbered:1', 'bullet:1',
                                                 'numbered:1', 'code', 'table', 'diagram', 'image']);
        const numbers = Array.from(m.container.querySelectorAll('[data-item="numbered"]')).map(e => e.getAttribute('data-n'));
        expect(numbers).toEqual(['1.', '2.', '1.', '3.', '1.']);
        expect(texts(m.view.state.doc)[7]).toBe('x = 1\ny = 2');
        expect(m.container.querySelector('pre[data-source="code"]')?.getAttribute('data-lang')).toBe('py');
        expect(shadowFaults(m)).toEqual([]);
    });

    it('numbers items after a level jump as the export writes them', () => {
        const m = mount([block('h', 'heading', 'T'), block('a', 'numbered', 'a', 1), block('b', 'numbered', 'b', 3),
                         block('c', 'numbered', 'c', 2), block('d', 'numbered', 'd', 4), block('e', 'bullet', 'e', 1),
                         block('f', 'numbered', 'f', 2)]);
        const numbers = Array.from(m.container.querySelectorAll('[data-item="numbered"]')).map(e => e.getAttribute('data-n'));
        // block_md.render: "1. a", "    1. b", "    2. c", "        1. d", "- e", "    1. f"
        expect(numbers).toEqual(['1.', '1.', '2.', '1.', '1.']);
    });

    it('typing in a source block records text ops on its `source` field, and Enter types a newline', () => {
        const m = mount([block('h', 'heading', 'T'), block('c', 'code', 'ab', 0)]);
        select(m.view, posIn(m.view, 'c', 2));
        key(m.view, 'Enter', 13);
        typeText(m.view, 'z');
        expect(sent(m).map(o => [o.kind, o.target, (o.payload as { text: string }).text])).toEqual([
            ['text.insert', { bid: 'c', path: 'source' }, '\n'],
            ['text.insert', { bid: 'c', path: 'source' }, 'z'],
        ]);
        expect(texts(m.view.state.doc)[1]).toBe('ab\nz');
        expect(shadowFaults(m)).toEqual([]);
    });
});

describe('list gestures', () => {
    for (const [marker, kind] of [['-', 'bullet'], ['*', 'bullet'], ['1.', 'numbered']] as const) {
        it(`"${marker} " at the start of a paragraph makes it a ${kind} and drops the marker`, () => {
            const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'words')]);
            expect(typeRule(m.view, 'p', marker)).toBe(true);
            const ops = sent(m);
            expect(ops.map(o => o.kind)).toEqual(['text.insert', 'text.delete', 'block.set']);
            expect(ops[2]).toEqual({ kind: 'block.set', target: { bid: 'p', path: 'kind' }, payload: { value: kind, level: 1 } });
            expect(shape(m.view.state.doc)).toEqual(['heading:1', `${kind}:1`]);
            expect(texts(m.view.state.doc)).toEqual(['T', 'words']);
            expect(shadowFaults(m)).toEqual([]);
        });
    }

    it('"- " in a heading is typed text', () => {
        const m = mount([block('h', 'heading', 'T')]);
        expect(typeRule(m.view, 'h', '-')).toBe(false);
    });

    it('Enter splits an item into two at its level; Enter in an empty item makes it a paragraph; Shift-Enter is Enter', () => {
        const m = mount([block('h', 'heading', 'T'), block('b', 'bullet', 'one two', 2)]);
        select(m.view, posIn(m.view, 'b', 3));
        key(m.view, 'Enter', 13);
        expect(shape(m.view.state.doc)).toEqual(['heading:1', 'bullet:2', 'bullet:2']);
        const nb = bidAt(m, 2);
        select(m.view, posIn(m.view, 'b', 3));
        key(m.view, 'Enter', 13, { shiftKey: true });                 // " two" moves on; an empty item sits between
        const empty = bidAt(m, 2);
        key(m.view, 'Enter', 13);                                      // the caret is in the empty item
        const ops = sent(m);
        expect(ops[0]).toEqual({ kind: 'block.split', target: { bid: 'b', path: 'text' }, payload: { at: [expect.any(String), 2], new: nb } });
        expect(ops[1].kind).toBe('block.split');
        expect(ops[2]).toEqual({ kind: 'block.set', target: { bid: empty, path: 'kind' }, payload: { value: 'paragraph' } });
        expect(shape(m.view.state.doc)).toEqual(['heading:1', 'bullet:2', 'paragraph', 'bullet:2']);
        expect(texts(m.view.state.doc)).toEqual(['T', 'one', '', ' two']);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('Backspace at the start of an item steps it out a level, and at level 1 makes it a paragraph', () => {
        const m = mount([block('h', 'heading', 'T'), block('b', 'numbered', 'item', 2)]);
        select(m.view, posIn(m.view, 'b', 0));
        key(m.view, 'Backspace', 8);
        expect(shape(m.view.state.doc)).toEqual(['heading:1', 'numbered:1']);
        key(m.view, 'Backspace', 8);
        expect(sent(m)).toEqual([
            { kind: 'block.set', target: { bid: 'b', path: 'level' }, payload: { value: 1 } },
            { kind: 'block.set', target: { bid: 'b', path: 'kind' }, payload: { value: 'paragraph' } },
        ]);
        expect(shape(m.view.state.doc)).toEqual(['heading:1', 'paragraph']);
    });

    it('Tab and Shift-Tab change an item\'s level and stop at 1', () => {
        const m = mount([block('h', 'heading', 'T'), block('b', 'bullet', 'x', 1)]);
        select(m.view, posIn(m.view, 'b', 1));
        key(m.view, 'Tab', 9);
        key(m.view, 'Tab', 9, { shiftKey: true });
        expect(key(m.view, 'Tab', 9, { shiftKey: true })).toBe(true);
        expect(sent(m)).toEqual([
            { kind: 'block.set', target: { bid: 'b', path: 'level' }, payload: { value: 2 } },
            { kind: 'block.set', target: { bid: 'b', path: 'level' }, payload: { value: 1 } },
        ]);
    });
});

describe('Tab', () => {
    it('moves a heading in place with everything under it, one level deeper or shallower', () => {
        const m = mount([block('a', 'heading', 'A'), block('p', 'paragraph', 'x'), block('b', 'heading', 'B'),
                         block('c', 'heading', 'C', 2), block('q', 'paragraph', 'y')]);
        select(m.view, posIn(m.view, 'b', 1));
        key(m.view, 'Tab', 9);
        expect(shape(m.view.state.doc)).toEqual(['heading:1', 'paragraph', 'heading:2', 'heading:3', 'paragraph']);
        expect(m.view.state.selection.from).toBe(posIn(m.view, 'b', 1));
        key(m.view, 'Tab', 9, { shiftKey: true });
        key(m.view, 'Tab', 9, { shiftKey: true });                    // already level 1: nothing
        expect(sent(m)).toEqual([
            { kind: 'block.move', target: { bid: 'b' }, payload: { after: 'p', level_delta: 1 } },
            { kind: 'block.move', target: { bid: 'b' }, payload: { after: 'p', level_delta: -1 } },
        ]);
        expect(shape(m.view.state.doc)).toEqual(['heading:1', 'paragraph', 'heading:1', 'heading:2', 'paragraph']);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('does nothing in a paragraph and keeps the key from leaving the editor', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'x')]);
        select(m.view, posIn(m.view, 'p', 1));
        expect(key(m.view, 'Tab', 9)).toBe(true);
        expect(sent(m)).toEqual([]);
    });

    it('in a code block indents the caret\'s line by four spaces when no line is indented', () => {
        const m = mount([block('h', 'heading', 'T'), block('c', 'code', 'def f():\nreturn 1', 0, 1, { lang: 'py' })]);
        select(m.view, posIn(m.view, 'c', 9));                        // the start of "return 1"
        key(m.view, 'Tab', 9);
        expect(texts(m.view.state.doc)[1]).toBe('def f():\n    return 1');
        const nl = m.shadows.get('c')!.refs[8];
        expect(sent(m)).toEqual([{ kind: 'text.insert', target: { bid: 'c', path: 'source' }, payload: { after: nl, text: '    ' } }]);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('in a code block indents each selected line by the block\'s unit, and Shift-Tab takes one unit back', () => {
        const m = mount([block('h', 'heading', 'T'), block('c', 'code', 'if a:\n  b()\nc()', 0, 1, { lang: 'py' })]);
        select(m.view, posIn(m.view, 'c', 7), posIn(m.view, 'c', 13));   // from inside "  b()" to the end of "c()"
        key(m.view, 'Tab', 9);
        expect(texts(m.view.state.doc)[1]).toBe('if a:\n    b()\n  c()');
        key(m.view, 'Tab', 9, { shiftKey: true });
        key(m.view, 'Tab', 9, { shiftKey: true });
        expect(texts(m.view.state.doc)[1]).toBe('if a:\nb()\nc()');
        expect(sent(m).map(o => o.kind)).toEqual(['text.insert', 'text.insert', 'text.delete', 'text.delete', 'text.delete']);
        expect(shadowFaults(m)).toEqual([]);
    });
});

describe('removing an empty paragraph next to a heading', () => {
    it('Backspace deletes it and puts the caret at the end of the heading', () => {
        const m = mount([block('a', 'heading', 'A'), block('p', 'paragraph', ''), block('b', 'heading', 'B')]);
        select(m.view, posIn(m.view, 'p', 0));
        key(m.view, 'Backspace', 8);
        expect(sent(m)).toEqual([{ kind: 'block.delete', target: { bid: 'p' }, payload: {} }]);
        expect(ids(m.view.state.doc)).toEqual(['heading:a', 'heading:b']);
        expect(m.view.state.selection.from).toBe(posIn(m.view, 'a', 1));
        expect(shadowFaults(m)).toEqual([]);
    });

    it('Delete deletes it and puts the caret at the start of the heading', () => {
        const m = mount([block('a', 'heading', 'A'), block('p', 'paragraph', ''), block('b', 'heading', 'B')]);
        select(m.view, posIn(m.view, 'p', 0));
        key(m.view, 'Delete', 46);
        expect(sent(m)).toEqual([{ kind: 'block.delete', target: { bid: 'p' }, payload: {} }]);
        expect(m.view.state.selection.from).toBe(posIn(m.view, 'b', 0));
    });
});

describe('Backspace at the start of a heading', () => {
    it('turns a stray `# ` with a body under it back into a paragraph', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', ''), block('q', 'paragraph', 'after')]);
        typeRule(m.view, 'p', '#');
        select(m.view, posIn(m.view, 'p', 0));
        key(m.view, 'Backspace', 8);
        expect(sent(m).at(-1)).toEqual({ kind: 'block.set', target: { bid: 'p', path: 'kind' }, payload: { value: 'paragraph' } });
        expect(shape(m.view.state.doc)).toEqual(['heading:1', 'paragraph', 'paragraph']);
        expect(m.conflicts).toEqual([]);
        expect(shadowFaults(m)).toEqual([]);
    });

    // A heading that owns code, a theme with a child under it, a feature queued for a build.
    const cases: Array<[string, ProjectedBlock[], Partial<Projection>]> = [
        ['owns code', [block('f', 'heading', 'F'), block('p', 'paragraph', 'x')],
         { bindings: { by_feature: { f: ['a.py::f'] } } }],
        ['has a child heading', [block('f', 'heading', 'Theme'), block('c', 'heading', 'Child', 2)], {}],
        // Edited ranks above queued and proposed, so the phase alone would let this one go
        // mid-build or with a plan waiting.
        ['is edited with work in flight', [block('f', 'heading', 'New'), block('p', 'paragraph', 'x')],
         { phases: { f: 'edited' }, in_flight: ['f'] }],
        ['is proposed', [block('f', 'heading', 'New'), block('p', 'paragraph', 'x')], { phases: { f: 'proposed' } }],
        ['is drifted', [block('f', 'heading', 'Old'), block('p', 'paragraph', 'x')], { phases: { f: 'drifted' } }],
    ];
    for (const [name, blocks, extra] of cases) {
        it(`on a heading that ${name} does nothing but say how`, () => {
            const m = mount([block('a', 'heading', 'A'), ...blocks], extra);
            select(m.view, posIn(m.view, 'f', 0));
            key(m.view, 'Backspace', 8);
            expect(sent(m)).toEqual([]);
            expect(m.conflicts).toEqual(['Delete feature from the / menu']);
            expect(m.view.state.selection.from).toBe(posIn(m.view, 'f', 0));
        });
    }

    it('Delete in an empty heading moves into the next block and leaves the title empty', () => {
        const m = mount([block('h', 'heading', ''), block('p', 'paragraph', 'body')]);
        select(m.view, posIn(m.view, 'h', 0));
        key(m.view, 'Delete', 46);
        expect(sent(m)).toEqual([]);
        expect(texts(m.view.state.doc)).toEqual(['', 'body']);
        expect(m.view.state.selection.from).toBe(posIn(m.view, 'p', 0));
    });
});

describe('source blocks from a marker', () => {
    it('``` then Enter replaces the paragraph with a code block after the block before it', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', '```python')]);
        select(m.view, posIn(m.view, 'p', 9));
        key(m.view, 'Enter', 13);
        const nb = bidAt(m, 1);
        typeText(m.view, 'x');
        expect(sent(m)).toEqual([
            { kind: 'block.delete', target: { bid: 'p' }, payload: {} },
            { kind: 'block.add', target: { bid: nb }, payload: { kind: 'code', after: 'h', content: { lang: 'python' } } },
            { kind: 'text.insert', target: { bid: nb, path: 'source' }, payload: { after: 'START', text: 'x' } },
        ]);
        expect(shape(m.view.state.doc)).toEqual(['heading:1', 'code']);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('```mermaid makes a diagram', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', '```mermaid')]);
        select(m.view, posIn(m.view, 'p', 10));
        key(m.view, 'Enter', 13);
        expect(sent(m)[1].payload).toEqual({ kind: 'diagram', after: 'h', content: {} });
    });

    it('a `|` row typed here then Enter makes a table holding that row', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', '')]);
        select(m.view, posIn(m.view, 'p', 0));
        typeText(m.view, '| a | b |');
        key(m.view, 'Enter', 13);
        const nb = bidAt(m, 1);
        expect(sent(m).slice(1)).toEqual([
            { kind: 'block.delete', target: { bid: 'p' }, payload: {} },
            { kind: 'block.add', target: { bid: nb }, payload: { kind: 'table', after: 'h', content: {} } },
            { kind: 'text.insert', target: { bid: nb, path: 'source' }, payload: { after: 'START', text: '| a | b |' } },
        ]);
        expect(texts(m.view.state.doc)).toEqual(['T', '| a | b |']);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('the viewer\'s own row makes a table once acked, and once the projection holds it', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', '')]);
        select(m.view, posIn(m.view, 'p', 0));
        typeText(m.view, '|a|b|');
        m.handle.flush();
        m.handle.ack([{ cid: m.submitted[0].cid, op: 'v:7', conflict: null }]);    // acked, not yet in a projection
        key(m.view, 'Enter', 13);
        expect(shape(m.view.state.doc)).toEqual(['heading:1', 'table']);
        const m2 = mount([block('h', 'heading', 'T'), para('p', 1, [['|a|b|', { op: 'v:7', actor: 'human:a' }]])]);   // human:a is the viewer
        select(m2.view, posIn(m2.view, 'p', 5));
        key(m2.view, 'Enter', 13);
        expect(shape(m2.view.state.doc)).toEqual(['heading:1', 'table']);
        expect(sent(m2)[1].payload).toEqual({ kind: 'table', after: 'h', content: {} });
    });

    it('another actor\'s row stays a paragraph and splits', () => {
        const m = mount([block('h', 'heading', 'T'), para('p', 1, [['| a | b |', { op: 'o:3', actor: 'human:bob' }]])]);
        select(m.view, posIn(m.view, 'p', 9));
        key(m.view, 'Enter', 13);
        expect(sent(m).map(o => o.kind)).toEqual(['block.split']);
        expect(shape(m.view.state.doc)).toEqual(['heading:1', 'paragraph', 'paragraph']);
    });

    it('Enter after prose that starts with a bar splits as usual', () => {
        for (const text of ['|x| is the absolute value', '| a |']) {
            const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', text)]);
            select(m.view, posIn(m.view, 'p', text.length));
            key(m.view, 'Enter', 13);
            expect(sent(m).map(o => o.kind)).toEqual(['block.split']);
            expect(shape(m.view.state.doc)).toEqual(['heading:1', 'paragraph', 'paragraph']);
        }
    });

    it('Mod-Enter in a source block adds a paragraph after it; Backspace in an empty one deletes it', () => {
        const m = mount([block('h', 'heading', 'T'), block('c', 'code', 'x', 0), block('e', 'code', '', 0)]);
        select(m.view, posIn(m.view, 'c', 1));
        key(m.view, 'Enter', 13, ctrl);
        const nb = bidAt(m, 2);
        select(m.view, posIn(m.view, 'e', 0));
        key(m.view, 'Backspace', 8);
        expect(sent(m)).toEqual([
            { kind: 'block.add', target: { bid: nb }, payload: { kind: 'paragraph', after: 'c' } },
            { kind: 'block.delete', target: { bid: 'e' }, payload: {} },
        ]);
        expect(shape(m.view.state.doc)).toEqual(['heading:1', 'code', 'paragraph']);
        expect(shadowFaults(m)).toEqual([]);
    });
});

describe('marks from the keyboard', () => {
    it('Mod-B and Mod-E toggle bold and code on the selection', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'hello world')]);
        select(m.view, posIn(m.view, 'p', 0), posIn(m.view, 'p', 5));
        key(m.view, 'b', 66, ctrl);
        key(m.view, 'e', 69, ctrl);
        key(m.view, 'b', 66, ctrl);
        const ops = sent(m);
        expect(ops.map(o => [o.kind, (o.payload as { kind: string }).kind, (o.payload as { on: boolean }).on])).toEqual([
            ['text.mark', 'bold', true], ['text.mark', 'code', true], ['text.mark', 'bold', false]]);
        const ref = m.shadows.get('p')!.refs;
        expect(ops[1].payload).toEqual({ kind: 'code', attrs: {}, start: ref[0], end: ref[4], on: true });
    });

    it('Mod-B over two paragraphs marks each one', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'one'), block('q', 'paragraph', 'two')]);
        select(m.view, posIn(m.view, 'p', 1), posIn(m.view, 'q', 2));
        key(m.view, 'b', 66, ctrl);
        expect(sent(m).map(o => o.target)).toEqual([{ bid: 'p', path: 'text' }, { bid: 'q', path: 'text' }]);
    });

    it('Mod-K opens a field over the selection, and Enter in it links the words', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'see the docs')]);
        select(m.view, posIn(m.view, 'p', 8), posIn(m.view, 'p', 12));
        expect(key(m.view, 'k', 75, ctrl)).toBe(true);
        const field = m.container.querySelector('.codoc-link-input input') as HTMLInputElement;
        expect(field.closest('.codoc-link-input')!.hasAttribute('hidden')).toBe(false);
        field.value = 'example.com/docs';
        field.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
        const ref = m.shadows.get('p')!.refs;
        expect(sent(m)).toEqual([{ kind: 'text.mark', target: { bid: 'p', path: 'text' },
                                   payload: { kind: 'link', attrs: { href: 'https://example.com/docs' }, start: ref[8], end: ref[11], on: true } }]);
        // An empty field takes the link off.
        select(m.view, posIn(m.view, 'p', 8), posIn(m.view, 'p', 12));
        key(m.view, 'k', 75, ctrl);
        expect(field.value).toBe('https://example.com/docs');
        field.value = '';
        field.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
        expect((sent(m)[1].payload as { on: boolean }).on).toBe(false);
    });
});

describe('a paste of Markdown', () => {
    it('becomes those blocks and marks; an empty paragraph takes the first block\'s kind', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', '')]);
        select(m.view, posIn(m.view, 'p', 0));
        expect(pasteText(m, '## Sub\n\n- one\n    - **two**\n\n```js\nlet a;\n```\n\nSee [docs](https://x.io).')).toBe(true);
        expect(shape(m.view.state.doc)).toEqual(['heading:1', 'heading:2', 'bullet:1', 'bullet:2', 'code', 'paragraph']);
        expect(texts(m.view.state.doc)).toEqual(['T', 'Sub', 'one', 'two', 'let a;', 'See docs.']);
        const ops = sent(m);
        expect(ops.map(o => o.kind)).toEqual(['text.insert', 'block.set', 'block.add', 'text.insert', 'block.add', 'text.insert', 'text.mark',
                                              'block.add', 'text.insert', 'block.add', 'text.insert', 'text.mark']);
        expect(ops[1].payload).toEqual({ value: 'heading', level: 2 });
        expect(ops[4].payload).toMatchObject({ kind: 'bullet', level: 2 });
        expect(ops[7].payload).toMatchObject({ kind: 'code', content: { lang: 'js' } });
        expect(ops[8].target).toMatchObject({ path: 'source' });
        expect(ops[11].payload).toMatchObject({ kind: 'link', attrs: { href: 'https://x.io' } });
        expect(shadowFaults(m)).toEqual([]);
    });

    it('inline marks land in the caret\'s block', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'ab')]);
        select(m.view, posIn(m.view, 'p', 1));
        pasteText(m, '**bold** `c`');
        expect(texts(m.view.state.doc)[1]).toBe('abold cb');
        const ops = sent(m);
        const cid = m.submitted[0].cid;
        expect(ops.slice(1).map(o => o.payload)).toEqual([
            { kind: 'bold', attrs: {}, start: ['$cid', cid, 0], end: ['$cid', cid, 3], on: true },
            { kind: 'code', attrs: {}, start: ['$cid', cid, 5], end: ['$cid', cid, 5], on: true },
        ]);
        const marks = (i: number) => m.view.state.doc.nodeAt(posIn(m.view, 'p', i))!.marks.map(x => x.type.name);
        expect(marks(1)).toEqual(['bold']);
        expect(marks(6)).toEqual(['code']);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('keeps each line a block when one line has an inline mark', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', '')]);
        select(m.view, posIn(m.view, 'p', 0));
        pasteText(m, 'first line\n**second** line');
        expect(shape(m.view.state.doc)).toEqual(['heading:1', 'paragraph', 'paragraph']);
        expect(texts(m.view.state.doc)).toEqual(['T', 'first line', 'second line']);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('into a code block is the clipboard\'s text as it is: one insert on `source`, newlines kept', () => {
        const m = mount([block('h', 'heading', 'T'), block('c', 'code', 'x = 1', 0, 1, { lang: 'py' })]);
        const one = m.shadows.get('c')!.refs[4];
        select(m.view, posIn(m.view, 'c', 5));
        const code = '\ndef f(x):\n    # square it\n\n    return x ** 2\n- not a list';
        expect(pasteText(m, code)).toBe(true);
        expect(texts(m.view.state.doc)[1]).toBe(`x = 1${code}`);
        expect(sent(m)).toEqual([{ kind: 'text.insert', target: { bid: 'c', path: 'source' }, payload: { after: one, text: code } }]);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('plain lines without Markdown are one paragraph each', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', '')]);
        select(m.view, posIn(m.view, 'p', 0));
        pasteText(m, 'first line\nsecond line');
        expect(shape(m.view.state.doc)).toEqual(['heading:1', 'paragraph', 'paragraph']);
        expect(texts(m.view.state.doc)).toEqual(['T', 'first line', 'second line']);
    });
});

describe('a selection across blocks', () => {
    it('typed over: the ends lose the covered words, the blocks between go, and the last merges into the first', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one two'), block('p2', 'paragraph', 'mid'),
                         block('p3', 'paragraph', 'three four')]);
        const r1 = m.shadows.get('p1')!.refs.slice(), r3 = m.shadows.get('p3')!.refs.slice();
        select(m.view, posIn(m.view, 'p1', 4), posIn(m.view, 'p3', 5));
        typeText(m.view, 'X');
        expect(texts(m.view.state.doc)).toEqual(['T', 'one X four']);
        expect(sent(m)).toEqual([
            { kind: 'text.delete', target: { bid: 'p1', path: 'text' }, payload: { segments: [[r1[4][0], 4, 3]] } },
            { kind: 'text.delete', target: { bid: 'p3', path: 'text' }, payload: { segments: [[r3[0][0], 0, 5]] } },
            { kind: 'block.delete', target: { bid: 'p2' }, payload: { ids: ['p2'] } },
            { kind: 'block.merge', target: { into: 'p1', bid: 'p3', path: 'text' }, payload: {} },
            { kind: 'text.insert', target: { bid: 'p1', path: 'text' }, payload: { after: r1[3], text: 'X' } },
        ]);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('cut over a heading wholly selected deletes that heading alone', () => {
        const m = mount([block('a', 'heading', 'A'), block('p1', 'paragraph', 'one'), block('b', 'heading', 'B'), block('p2', 'paragraph', 'two')]);
        select(m.view, posIn(m.view, 'p1', 1), posIn(m.view, 'p2', 1));
        m.view.dispatch(m.view.state.tr.deleteSelection().setMeta('uiEvent', 'cut'));
        m.handle.flush();
        expect(kinds(m.submitted)).toEqual(['text.delete', 'text.delete', 'block.delete', 'block.merge']);
        expect(m.submitted[2].payload).toEqual({ ids: ['b'] });
        expect(texts(m.view.state.doc)).toEqual(['A', 'owo']);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('deletes a whole feature in one block.delete, so the applier reads each block\'s feature before any dies', () => {
        const m = mount([block('a', 'heading', 'A'), block('p1', 'paragraph', 'one'), block('b', 'heading', 'B'),
                         block('q1', 'paragraph', 'x'), block('q2', 'paragraph', 'y'), block('c', 'heading', 'C')]);
        select(m.view, posIn(m.view, 'b', 0), posIn(m.view, 'c', 0));
        key(m.view, 'Backspace', 8);
        expect(sent(m)).toEqual([{ kind: 'block.delete', target: { bid: 'b' }, payload: { ids: ['b', 'q1', 'q2'] } }]);
        expect(ids(m.view.state.doc)).toEqual(['heading:a', 'paragraph:p1', 'heading:c']);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('deletes a whole feature selected to the end of its last paragraph, and the last feature too', () => {
        for (const tail of [[block('c', 'heading', 'C')], []]) {
            const m = mount([block('a', 'heading', 'A'), block('p1', 'paragraph', 'one'), block('b', 'heading', 'B'),
                             block('q1', 'paragraph', 'x'), block('q2', 'paragraph', 'y'), ...tail]);
            select(m.view, posIn(m.view, 'b', 0), posIn(m.view, 'q2', 1));
            key(m.view, 'Backspace', 8);
            expect(sent(m)).toEqual([{ kind: 'block.delete', target: { bid: 'b' }, payload: { ids: ['b', 'q1', 'q2'] } }]);
            expect(ids(m.view.state.doc)).toEqual(['heading:a', 'paragraph:p1', ...tail.map(t => `heading:${t.id}`)]);
            expect(m.view.state.selection.from).toBe(posIn(m.view, 'p1', 3));
            expect(shadowFaults(m)).toEqual([]);
        }
    });

    it('typed over whole paragraphs keeps the first one for the typed words', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one'), block('p2', 'paragraph', 'two'), block('u', 'heading', 'U')]);
        select(m.view, posIn(m.view, 'p1', 0), posIn(m.view, 'p2', 3));
        typeText(m.view, 'X');
        expect(texts(m.view.state.doc)).toEqual(['T', 'X', 'U']);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('over part of a heading is refused with a notice and changes nothing', () => {
        const m = mount([block('a', 'heading', 'A'), block('p1', 'paragraph', 'one'), block('b', 'heading', 'Beta'), block('p2', 'paragraph', 'two')]);
        select(m.view, posIn(m.view, 'p1', 1), posIn(m.view, 'b', 2));
        typeText(m.view, 'X');
        expect(m.conflicts).toEqual(['a selection that covers part of a heading is not deleted; select the whole heading']);
        expect(sent(m)).toEqual([]);
        expect(texts(m.view.state.doc)).toEqual(['A', 'one', 'Beta', 'two']);
    });

    it('from the end of a heading into its body leaves the heading alone', () => {
        const m = mount([block('a', 'heading', 'A'), block('p1', 'paragraph', 'one two')]);
        select(m.view, posIn(m.view, 'a', 1), posIn(m.view, 'p1', 4));
        key(m.view, 'Backspace', 8);
        m.handle.flush();
        expect(kinds(m.submitted)).toEqual(['text.delete']);
        expect(texts(m.view.state.doc)).toEqual(['A', 'two']);
        expect(shadowFaults(m)).toEqual([]);
    });
});

describe('the / menu', () => {
    const open = (m: Mounted, bid: string) => {
        select(m.view, posIn(m.view, bid, 0));
        typeText(m.view, '/');
        return Array.from(m.container.querySelectorAll('.codoc-menu-item')) as HTMLElement[];
    };
    const label = (row: HTMLElement) => row.firstChild?.textContent;
    const pick = (rows: HTMLElement[], name: string) =>
        rows.find(r => label(r) === name)!.dispatchEvent(new window.MouseEvent('mousedown', { bubbles: true, cancelable: true }));

    it('offers the block kinds in a paragraph and makes a code block from an otherwise empty one', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', '')]);
        const rows = open(m, 'p');
        expect(rows.map(label)).toEqual(['Heading 1', 'Heading 2', 'Heading 3', 'Paragraph', 'Bullet list', 'Numbered list',
                                         'Table', 'Code block', 'Diagram']);
        pick(rows, 'Code block');
        const nb = bidAt(m, 1);
        expect(sent(m).slice(1)).toEqual([
            { kind: 'block.delete', target: { bid: 'p' }, payload: {} },
            { kind: 'block.add', target: { bid: nb }, payload: { kind: 'code', after: 'h', content: {} } },
        ]);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('turns a paragraph with words into a list item, dropping the typed /', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'words')]);
        pick(open(m, 'p'), 'Numbered list');
        expect(sent(m).slice(1).map(o => o.kind)).toEqual(['text.delete', 'block.set']);
        expect(shape(m.view.state.doc)).toEqual(['heading:1', 'numbered:1']);
        expect(texts(m.view.state.doc)).toEqual(['T', 'words']);
    });

    it('in a heading also offers Delete feature, which deletes it', () => {
        const m = mount([block('a', 'heading', 'A'), block('p', 'paragraph', 'x'), block('b', 'heading', 'B'), block('q', 'paragraph', 'y')]);
        const rows = open(m, 'b');
        expect(rows.map(label)).toContain('Delete feature');
        pick(rows, 'Delete feature');
        expect(sent(m).slice(1)).toEqual([{ kind: 'block.delete', target: { bid: 'b' }, payload: {} }]);
        expect(ids(m.view.state.doc)).toEqual(['heading:a', 'paragraph:p']);
        expect(shadowFaults(m)).toEqual([]);
    });
});

describe('an IME composition', () => {
    it('records nothing while it is open and one insert of the composed text when it ends', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'ab')]);
        const at = posIn(m.view, 'p', 2);
        select(m.view, at);
        m.view.dom.dispatchEvent(new window.CompositionEvent('compositionstart', { bubbles: true }));
        expect(m.view.composing).toBe(true);
        m.view.dispatch(m.view.state.tr.insertText('n', at, at));
        m.view.dispatch(m.view.state.tr.insertText('ni', at, at + 1));
        m.view.dispatch(m.view.state.tr.insertText('你', at, at + 2));
        m.handle.flush();
        expect(m.submitted).toEqual([]);
        m.view.dom.dispatchEvent(new window.CompositionEvent('compositionend', { bubbles: true, data: '你' }));
        const ref = m.shadows.get('p')!.refs[1];
        expect(sent(m)).toEqual([{ kind: 'text.insert', target: { bid: 'p', path: 'text' }, payload: { after: ref, text: '你' } }]);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('after Mod-B sends the bold the page shows, as typed text does', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'ab')]);
        const at = posIn(m.view, 'p', 2);
        select(m.view, at);
        key(m.view, 'b', 66, ctrl);
        m.view.dom.dispatchEvent(new window.CompositionEvent('compositionstart', { bubbles: true }));
        m.view.dispatch(m.view.state.tr.insertText('n', at, at));
        m.view.dispatch(m.view.state.tr.insertText('你', at, at + 1));
        m.view.dom.dispatchEvent(new window.CompositionEvent('compositionend', { bubbles: true, data: '你' }));
        expect(m.view.state.doc.nodeAt(at)!.marks.map(x => x.type.name)).toEqual(['bold']);
        const ops = sent(m);
        const cid = m.submitted[0].cid;
        expect(ops.map(o => o.kind)).toEqual(['text.insert', 'text.mark']);
        expect(ops[1].payload).toEqual({ kind: 'bold', attrs: {}, start: ['$cid', cid, 0], end: ['$cid', cid, 0], on: true });
        expect(shadowFaults(m)).toEqual([]);
    });

    it('over a selection across blocks lands the composed text inside what was selected', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one'), block('p2', 'paragraph', 'two'), block('u', 'heading', 'U')]);
        // jsdom has no DOM selection for ProseMirror to keep, so the selection is set once composing.
        m.view.dom.dispatchEvent(new window.CompositionEvent('compositionstart', { bubbles: true }));
        select(m.view, posIn(m.view, 'p1', 0), posIn(m.view, 'p2', 3));
        m.view.dispatch(m.view.state.tr.deleteSelection());           // the first DOM change only removes the selection
        m.view.dispatch(m.view.state.tr.insertText('你'));
        m.view.dom.dispatchEvent(new window.CompositionEvent('compositionend', { bubbles: true, data: '你' }));
        m.handle.flush();
        expect(texts(m.view.state.doc)).toEqual(['T', '你', 'U']);
        expect(shadowFaults(m)).toEqual([]);
    });
});

describe('text over a selection across blocks lands inside it', () => {
    const featB = (tail: boolean, aBody = true) => [block('a', 'heading', 'A'), ...(aBody ? [block('p1', 'paragraph', 'one')] : []),
        block('b', 'heading', 'B'), block('q1', 'paragraph', 'x'), block('q2', 'paragraph', 'y'), ...(tail ? [block('c', 'heading', 'C')] : [])];
    const overB = (m: Mounted) => select(m.view, posIn(m.view, 'b', 0), posIn(m.view, 'q2', 1));

    it('typed over a whole middle feature: in its emptied last paragraph', () => {
        const m = mount(featB(true));
        overB(m);
        typeText(m.view, 'X');
        expect(ids(m.view.state.doc)).toEqual(['heading:a', 'paragraph:p1', 'paragraph:q2', 'heading:c']);
        expect(texts(m.view.state.doc)).toEqual(['A', 'one', 'X', 'C']);
        expect(sent(m).map(o => o.kind)).toEqual(['text.delete', 'block.delete', 'text.insert']);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('typed over the whole last feature, or one after a feature with no body', () => {
        for (const [doc, want] of [[featB(false), ['A', 'one', 'X']], [featB(true, false), ['A', 'X', 'C']]] as const) {
            const m = mount([...doc]);
            overB(m);
            typeText(m.view, 'X');
            expect(texts(m.view.state.doc)).toEqual(want);
            expect(shadowFaults(m)).toEqual([]);
        }
    });

    it('typed over the whole first feature', () => {
        const m = mount([block('a', 'heading', 'A'), block('p1', 'paragraph', 'one'), block('b', 'heading', 'B'), block('q1', 'paragraph', 'x')]);
        select(m.view, posIn(m.view, 'a', 0), posIn(m.view, 'p1', 3));
        typeText(m.view, 'X');
        expect(texts(m.view.state.doc)).toEqual(['X', 'B', 'x']);
    });

    it('typed from a heading\'s end: the heading keeps its title', () => {
        const m = mount([block('a', 'heading', 'A'), block('p1', 'paragraph', 'one'), block('b', 'heading', 'B'), block('q1', 'paragraph', 'x')]);
        select(m.view, posIn(m.view, 'a', 1), posIn(m.view, 'p1', 3));
        typeText(m.view, 'X');
        expect(texts(m.view.state.doc)).toEqual(['A', 'X', 'B', 'x']);
    });

    it('typed between two headings left alone: in a new paragraph where the selection was', () => {
        const m = mount([block('a', 'heading', 'A'), block('p1', 'paragraph', 'one'), block('b', 'heading', 'B')]);
        select(m.view, posIn(m.view, 'a', 1), posIn(m.view, 'b', 0));
        typeText(m.view, 'X');
        const nb = bidAt(m, 1);
        expect(texts(m.view.state.doc)).toEqual(['A', 'X', 'B']);
        expect(sent(m).slice(0, 2)).toEqual([{ kind: 'block.delete', target: { bid: 'p1' }, payload: { ids: ['p1'] } },
                                              { kind: 'block.add', target: { bid: nb }, payload: { kind: 'paragraph', after: 'a' } }]);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('typed or pasted over a whole code block and a paragraph: never into the code block', () => {
        const doc = () => [block('h', 'heading', 'H'), block('k', 'code', 'x = 1', 0), block('p1', 'paragraph', 'one'), block('u', 'heading', 'U')];
        const m = mount(doc());
        select(m.view, posIn(m.view, 'k', 0), posIn(m.view, 'p1', 3));
        typeText(m.view, 'X');
        expect(ids(m.view.state.doc)).toEqual(['heading:h', 'paragraph:p1', 'heading:u']);
        expect(texts(m.view.state.doc)).toEqual(['H', 'X', 'U']);
        const m2 = mount(doc());
        select(m2.view, posIn(m2.view, 'k', 0), posIn(m2.view, 'p1', 3));
        pasteText(m2, '- item\n- two');
        expect(shape(m2.view.state.doc)).toEqual(['heading:1', 'bullet:1', 'bullet:1', 'heading:1']);
        expect(texts(m2.view.state.doc)).toEqual(['H', 'item', 'two', 'U']);
        expect(shadowFaults(m2)).toEqual([]);
    });

    it('pasted over a whole middle feature', () => {
        const m = mount(featB(true));
        overB(m);
        pasteText(m, '## New\n\nbody text');
        expect(texts(m.view.state.doc)).toEqual(['A', 'one', 'New', 'body text', 'C']);
        expect(shape(m.view.state.doc)).toEqual(['heading:1', 'paragraph', 'heading:2', 'paragraph', 'heading:1']);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('Enter over whole list items splits into two items of that kind and level', () => {
        const m = mount([block('h', 'heading', 'T'), block('b1', 'bullet', 'one', 2), block('b2', 'bullet', 'two', 2), block('u', 'heading', 'U')]);
        select(m.view, posIn(m.view, 'b1', 0), posIn(m.view, 'b2', 3));
        key(m.view, 'Enter', 13);
        expect(shape(m.view.state.doc)).toEqual(['heading:1', 'bullet:2', 'bullet:2', 'heading:1']);
        expect(sent(m).map(o => o.kind)).toEqual(['text.delete', 'text.delete', 'block.merge', 'block.split']);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('Enter over whole paragraphs after a code block, or over a whole feature, splits inside the selection', () => {
        const m = mount([block('h', 'heading', 'H'), block('k', 'code', 'x = 1', 0), block('p1', 'paragraph', 'one'),
                         block('p2', 'paragraph', 'two'), block('u', 'heading', 'U')]);
        select(m.view, posIn(m.view, 'p1', 0), posIn(m.view, 'p2', 3));
        key(m.view, 'Enter', 13);
        expect(texts(m.view.state.doc)).toEqual(['H', 'x = 1', '', '', 'U']);
        expect(shadowFaults(m)).toEqual([]);
        const m2 = mount(featB(true));
        overB(m2);
        key(m2.view, 'Enter', 13);
        expect(texts(m2.view.state.doc)).toEqual(['A', 'one', '', '', 'C']);
        expect(shadowFaults(m2)).toEqual([]);
    });
});
