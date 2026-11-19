// @vitest-environment jsdom
import { Fragment, Slice } from '@tiptap/pm/model';
import { AllSelection } from '@tiptap/pm/state';
import { describe, expect, it } from 'vitest';

import type { TextField } from '../../../web/calc/projection';
import { deleteFeature } from '../../../web/editor/commands';
import { block, ids, key, kindBlock, kinds, mount, paste, pasteSlice, pasteText, perform, posIn, select, shadowFaults, shape, texts, typeText }
    from './harness';

describe('a selection across paragraphs', () => {
    it('typed over: the page changes only if the ops say so', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one two'), block('p2', 'paragraph', 'three four')]);
        select(m.view, posIn(m.view, 'p1', 4), posIn(m.view, 'p2', 5));
        typeText(m.view, 'X');
        m.handle.flush();
        expect(shadowFaults(m)).toEqual([]);
        if (texts(m.view.state.doc)[1] === 'one two') expect(m.submitted).toEqual([]);   // refused, so nothing recorded
        else expect(texts(m.view.state.doc)).toEqual(['T', 'one X four']);
    });

    it('cut: removes the words as ops or leaves everything as it was', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one two'), block('p2', 'paragraph', 'three four')]);
        select(m.view, posIn(m.view, 'p1', 4), posIn(m.view, 'p2', 5));
        // ProseMirror's cut handler: tr.deleteSelection() tagged uiEvent=cut.
        m.view.dispatch(m.view.state.tr.deleteSelection().setMeta('uiEvent', 'cut'));
        m.handle.flush();
        expect(shadowFaults(m)).toEqual([]);
        if (texts(m.view.state.doc)[1] === 'one two') expect(m.submitted).toEqual([]);
        else expect(texts(m.view.state.doc)).toEqual(['T', 'one  four']);
    });

    it('Backspace across a plugin block deletes it with the words, and the shadows stand', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'aaa bbb'), kindBlock('k'), block('p2', 'paragraph', 'ccc ddd')]);
        select(m.view, posIn(m.view, 'p1', 4), posIn(m.view, 'p2', 3));
        key(m.view, 'Backspace', 8);
        m.handle.flush();
        expect(shadowFaults(m)).toEqual([]);
        expect(kinds(m.submitted)).toEqual(['text.delete', 'text.delete', 'block.delete', 'block.merge']);
        expect(texts(m.view.state.doc)).toEqual(['T', 'aaa  ddd']);
    });

    it('select-all + Backspace over a tree with headings is refused with a reason', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'hello world')]);
        m.view.dispatch(m.view.state.tr.setSelection(new AllSelection(m.view.state.doc)));
        key(m.view, 'Backspace', 8);
        m.handle.flush();
        expect(shadowFaults(m)).toEqual([]);
        expect(m.conflicts.length).toBeGreaterThan(0);
        expect(m.submitted).toEqual([]);
        expect(texts(m.view.state.doc)).toEqual(['T', 'hello world']);
    });
});

describe('a selection inside one paragraph', () => {
    it('Enter deletes it and splits there', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'hello world')]);
        select(m.view, posIn(m.view, 'p', 2), posIn(m.view, 'p', 5));      // "he|llo| world"
        key(m.view, 'Enter', 13);
        m.handle.flush();
        expect(shadowFaults(m)).toEqual([]);
        expect(texts(m.view.state.doc)).toEqual(['T', 'he', ' world']);
        expect(kinds(m.submitted)).toEqual(expect.arrayContaining(['text.delete', 'block.split']));
    });

    it('Backspace over the whole paragraph deletes its words and keeps the block', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'hello world'), block('q', 'paragraph', 'tail')]);
        select(m.view, posIn(m.view, 'p', 0), posIn(m.view, 'p', 11));
        key(m.view, 'Backspace', 8);
        m.handle.flush();
        expect(shadowFaults(m)).toEqual([]);
        expect(texts(m.view.state.doc)[1]).toBe('');
        expect(kinds(m.submitted)).toContain('text.delete');
    });

    it('typing over the whole paragraph replaces its words', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'hello world'), block('q', 'paragraph', 'tail')]);
        select(m.view, posIn(m.view, 'p', 0), posIn(m.view, 'p', 11));
        typeText(m.view, 'X');
        m.handle.flush();
        expect(shadowFaults(m)).toEqual([]);
        expect(texts(m.view.state.doc)[1]).toBe('X');
    });
});

describe('Enter in a heading', () => {
    it('at the end and at the start adds a paragraph after and before it', () => {
        const m = mount([block('h', 'heading', 'Title'), block('p', 'paragraph', 'body')]);
        select(m.view, posIn(m.view, 'h', 5));
        key(m.view, 'Enter', 13);
        expect(shadowFaults(m)).toEqual([]);
        expect(ids(m.view.state.doc).length).toBe(3);
        select(m.view, posIn(m.view, 'h', 0));
        key(m.view, 'Enter', 13);
        expect(shadowFaults(m)).toEqual([]);
        expect(texts(m.view.state.doc)).toEqual(['', 'Title', '', 'body']);
        // With a selection inside the heading: refused or exact, never out of step.
        select(m.view, posIn(m.view, 'h', 1), posIn(m.view, 'h', 3));
        key(m.view, 'Enter', 13);
        m.handle.flush();
        expect(shadowFaults(m)).toEqual([]);
    });
});

describe('the `## ` rule', () => {
    it('drops exactly the hashes at the start of a paragraph that has words', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', '##abc')]);
        select(m.view, posIn(m.view, 'p', 2));
        const handled = m.view.someProp('handleTextInput', f => f(m.view, posIn(m.view, 'p', 2), posIn(m.view, 'p', 2), ' ', () => m.view.state.tr)) ?? false;
        expect(handled).toBe(true);
        m.handle.flush();
        expect(ids(m.view.state.doc)).toEqual(['heading:h', 'heading:p']);
        expect(texts(m.view.state.doc)).toEqual(['T', 'abc']);
        expect(shadowFaults(m)).toEqual([]);
        expect(kinds(m.submitted)).toEqual(['text.delete', 'block.set']);
    });
});

describe('deleting a feature', () => {
    it('is refused when it would empty the tree, and the shadows stand', () => {
        const m = mount([block('h', 'heading', 'Only'), block('p', 'paragraph', 'body')]);
        perform(m, (s, env) => deleteFeature(s, env, 'h'));
        expect(ids(m.view.state.doc)).toEqual(['heading:h', 'paragraph:p']);
        expect(shadowFaults(m)).toEqual([]);
        select(m.view, posIn(m.view, 'p', 4));
        typeText(m.view, '!');
        m.handle.flush();
        expect(kinds(m.submitted)).toEqual(['text.insert']);
    });
});

describe('a paste of several paragraphs', () => {
    it('over a selection keeps one bid per block', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'hello world')]);
        select(m.view, posIn(m.view, 'p', 3), posIn(m.view, 'p', 7));      // "hel|lo w|orld"
        expect(paste(m, ['first', 'second'])).toBe(true);
        m.handle.flush();
        expect(shadowFaults(m)).toEqual([]);
        expect(texts(m.view.state.doc)).toEqual(['T', 'helfirst', 'secondorld']);
    });

    it('keeps the pasted lines in the order they were copied, with the words after the caret last', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'Retries once.')]);
        select(m.view, posIn(m.view, 'p', 8));                       // "Retries |once."
        pasteText(m, 'at most\ntwice, then');
        m.handle.flush();
        expect(texts(m.view.state.doc)).toEqual(['T', 'Retries at most', 'twice, thenonce.']);
        expect(kinds(m.submitted)).toEqual(['text.insert', 'block.split', 'text.insert']);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('keeps a last piece of another kind a block of its own, before the words after the caret', () => {
        const list = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'hello world')]);
        select(list.view, posIn(list.view, 'p', 6));
        pasteText(list, '- a\n- b\n- c');
        expect(shape(list.view.state.doc)).toEqual(['heading:1', 'paragraph', 'bullet:1', 'bullet:1', 'paragraph']);
        expect(texts(list.view.state.doc)).toEqual(['T', 'hello a', 'b', 'c', 'world']);
        expect(shadowFaults(list)).toEqual([]);
        const head = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'hello world')]);
        select(head.view, posIn(head.view, 'p', 6));
        pasteText(head, 'intro\n# New feature');
        expect(shape(head.view.state.doc)).toEqual(['heading:1', 'paragraph', 'heading:1', 'paragraph']);
        expect(texts(head.view.state.doc)).toEqual(['T', 'hello intro', 'New feature', 'world']);
        expect(shadowFaults(head)).toEqual([]);
    });

    it('gives the first line the bold it continues, as the store does', () => {
        const p = block('p', 'paragraph', 'ab cd');
        (p.fields.text as TextField).spans.push({ kind: 'bold', attrs: {}, start: 0, end: 2 });
        const m = mount([block('h', 'heading', 'T'), p]);
        select(m.view, posIn(m.view, 'p', 2));                        // "ab|", the end of the bold
        pasteText(m, 'xy\nzw');
        m.handle.flush();
        const marks = (bid: string, i: number) => m.view.state.doc.nodeAt(posIn(m.view, bid, i))!.marks.map(x => x.type.name);
        expect(texts(m.view.state.doc)).toEqual(['T', 'abxy', 'zw cd']);
        expect([marks('p', 2), marks('p', 3)]).toEqual([['bold'], ['bold']]);
        expect(marks(ids(m.view.state.doc)[2].split(':')[1], 0)).toEqual([]);
        // The store grows the bold over "xy" by itself: no mark is sent.
        expect(kinds(m.submitted)).toEqual(['text.insert', 'block.split', 'text.insert']);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('at the start of a block puts the pieces before it, and the last joins it', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', ' once.')]);
        select(m.view, posIn(m.view, 'p', 0));
        pasteText(m, '```\nx = 1\n```\n\nRetries');
        m.handle.flush();
        expect(shape(m.view.state.doc)).toEqual(['heading:1', 'code', 'paragraph']);
        expect(texts(m.view.state.doc)).toEqual(['T', 'x = 1', 'Retries once.']);
        expect(ids(m.view.state.doc)[2]).toBe('paragraph:p');
        expect(shadowFaults(m)).toEqual([]);
    });

    it('submits every client id its provisional refs name', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'head tail')]);
        select(m.view, posIn(m.view, 'p', 5));
        paste(m, ['first', 'second']);
        m.handle.flush();
        const sent = new Set(m.submitted.filter(s => s.kind === 'text.insert').map(s => s.cid));
        const dangling: string[] = [];
        for (const [bid, sh] of m.shadows) for (const r of sh.refs) if (r.length === 3 && !sent.has(r[1])) dangling.push(`${bid}:${r[1]}`);
        expect(dangling).toEqual([]);
        // So the daemon can resolve the next keystroke's anchor. Type right after "first".
        select(m.view, posIn(m.view, 'p', 10));
        typeText(m.view, '!');
        m.handle.flush();
        const anchor = (m.submitted[m.submitted.length - 1].payload as { after: unknown }).after as [string, string, number];
        expect(anchor[0] === '$cid' ? sent.has(anchor[1]) : true).toBe(true);
    });

    it('lands in an empty paragraph, and a slice open at one end is handled', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', ''), block('q', 'paragraph', 'after')]);
        select(m.view, posIn(m.view, 'p', 0));
        paste(m, ['first', 'second']);
        m.handle.flush();
        expect(shadowFaults(m)).toEqual([]);
        expect(texts(m.view.state.doc)).toEqual(['T', 'first', 'second', 'after']);
        const schema = m.view.state.schema;
        select(m.view, posIn(m.view, 'q', 5));
        pasteSlice(m, new Slice(Fragment.from([schema.nodes.paragraph.create(null, schema.text('A')), schema.nodes.paragraph.create(null, schema.text('B'))]), 0, 1));
        m.handle.flush();
        expect(shadowFaults(m)).toEqual([]);
    });

    it('leaves the page NFC, like the shadow and the op', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'ab')]);
        select(m.view, posIn(m.view, 'p', 2));
        expect(paste(m, ['x', 'café ok'])).toBe(true);      // a decomposed é, as a macOS paste gives it
        m.handle.flush();
        expect(shadowFaults(m)).toEqual([]);
        expect(m.submitted.filter(s => s.kind === 'text.insert').map(s => (s.payload as { text: string }).text))
            .toContain('café ok');
        const nb = ids(m.view.state.doc)[2].split(':')[1];
        select(m.view, posIn(m.view, nb, 4));                     // after "café"
        typeText(m.view, '!');
        m.handle.flush();
        expect(shadowFaults(m)).toEqual([]);
        expect(texts(m.view.state.doc)[2]).toBe('café! ok');
    });
});

describe('a decomposed keystroke', () => {
    it('is one scalar to the shadow, and the next keystroke anchors on it', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', 'caf')]);
        select(m.view, posIn(m.view, 'p', 3));
        typeText(m.view, 'é');
        typeText(m.view, 's');
        m.handle.flush();
        expect(m.shadows.get('p')!.length).toBe(5);
        const last = m.submitted[m.submitted.length - 1].payload as { after: [string, string, number] };
        expect(last.after).toEqual(['$cid', m.submitted[m.submitted.length - 2].cid, 0]);
    });
});

describe('random gestures', () => {
    function rng(seed: number) { let s = seed >>> 0; return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 2 ** 32; }; }

    it('40 seeds × 25 gestures: every shadow says what its block says after each one', () => {
        const failures = new Map<string, string>();
        for (let seed = 1; seed <= 40; seed++) {
            const r = rng(seed);
            const m = mount([block('h', 'heading', 'Title'), block('p1', 'paragraph', 'one two three'), block('p2', 'paragraph', 'four five'), block('p3', 'paragraph', 'six')]);
            const log: string[] = [];
            for (let step = 0; step < 25; step++) {
                const blocks: Array<{ bid: string; start: number; len: number }> = [];
                m.view.state.doc.forEach((n, pos) => { if (n.type.name !== 'kindBlock') blocks.push({ bid: n.attrs.bid, start: pos + 1, len: n.content.size }); });
                const a = blocks[Math.floor(r() * blocks.length)], b = blocks[Math.floor(r() * blocks.length)];
                const pa = a.start + Math.floor(r() * (a.len + 1)), pb = b.start + Math.floor(r() * (b.len + 1));
                const from = Math.min(pa, pb), to = Math.max(pa, pb);
                const gesture = Math.floor(r() * 7);
                let name = '';
                try {
                    if (gesture === 0) { select(m.view, from); typeText(m.view, 'x'); name = `type@${from}`; }
                    else if (gesture === 1) { select(m.view, from, to); typeText(m.view, 'y'); name = `type over ${from}-${to}`; }
                    else if (gesture === 2) { select(m.view, from, to); key(m.view, 'Backspace', 8); name = `bs ${from}-${to}`; }
                    else if (gesture === 3) { select(m.view, from, to); key(m.view, 'Delete', 46); name = `del ${from}-${to}`; }
                    else if (gesture === 4) { select(m.view, from, to); key(m.view, 'Enter', 13); name = `enter ${from}-${to}`; }
                    else if (gesture === 5) { select(m.view, from, to); paste(m, ['P', 'Q']); name = `paste ${from}-${to}`; }
                    else { select(m.view, from); paste(m, ['single']); name = `paste1@${from}`; }
                } catch (e) { name += ` THREW ${(e as Error).message}`; }
                log.push(name);
                const faults = shadowFaults(m);
                if (faults.length) {
                    const sig = faults[0].replace(/"[^"]*"/g, '"…"').replace(/b-[0-9a-f]+/g, 'b-*');
                    if (!failures.has(sig)) failures.set(sig, `seed ${seed} step ${step}: ${log.join(' → ')}\n    ${faults.join('\n    ')}`);
                    break;
                }
            }
        }
        expect([...failures.values()]).toEqual([]);
    }, 30_000);
});
