// @vitest-environment jsdom
/**
 * A block of a kind this editor has no node type for: drawn as a card whose
 * Text fields (from the block's layout) are editable text areas.
 */
import { describe, expect, it } from 'vitest';

import { TextSelection } from '@tiptap/pm/state';

import type { ProjectedBlock } from '../../../web/calc/projection';
import { bodies, block, ids, mount, pasteText, posIn, proj, shadowFaults, type Mounted } from './harness';

const UNKNOWN_KIND_NOTE = 'made by a newer codoc or a plugin this editor does not have';

/** A `chart` block, a kind no codoc this editor knows ships. */
function chart(head = 1, title = 'Q3 sales', runs = [{ op: 'k:1', text: title, actor: 'human:a' }]): ProjectedBlock {
    let at = 0;
    return {
        id: 'k', kind: 'chart', level: 0, rank: 'k', head,
        fields: {
            title: { text: runs.map(r => r.text).join(''), spans: [], suggest_del: [], runs: runs.map(r => {
                const start = at;
                at += Array.from(r.text).length;
                return { op: r.op, k0: 0, start, end: at, actor: r.actor, at: '1', plan: null };
            }) },
            caption: { text: '', runs: [], spans: [], suggest_del: [] },
            spec: { type: 'bar', series: [1, 2, 3] },
        },
        layout: { text: ['title', 'caption'], flow: true, export: true },
    };
}

const area = (m: Mounted, path: string) => m.container.querySelector(`[data-kind="chart"] textarea[data-path="${path}"]`) as HTMLTextAreaElement;
const edit = (m: Mounted, path: string, value: string, init: InputEventInit = {}) => {
    const a = area(m, path);
    a.value = value;
    a.dispatchEvent(new window.InputEvent('input', { bubbles: true, ...init }));
};
const sent = (m: Mounted) => { m.handle.flush(); return bodies(m.submitted); };

describe('a block of a kind this editor does not know', () => {
    it('is a card: its name, a note, a text area per Text field, the rest read-only', () => {
        const m = mount([block('h', 'heading', 'T'), chart(), block('p', 'paragraph', 'after')]);
        const card = m.container.querySelector('[data-kind="chart"]')!;
        expect(card.querySelector('.codoc-kind-label')!.textContent).toBe('chart');
        expect(card.querySelector('.codoc-kind-note')!.textContent).toBe(UNKNOWN_KIND_NOTE);
        expect([...card.querySelectorAll('textarea')].map(t => [t.dataset.path, t.value])).toEqual([['title', 'Q3 sales'], ['caption', '']]);
        expect(card.querySelector('.codoc-kind-value')!.textContent).toBe('{"type":"bar","series":[1,2,3]}');
        expect(ids(m.view.state.doc)).toEqual(['heading:h', 'kindBlock:k', 'paragraph:p']);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('records an edit in a text area as text ops on that field', () => {
        const m = mount([block('h', 'heading', 'T'), chart()]);
        edit(m, 'title', 'Q3 sales!');
        edit(m, 'caption', 'by region');
        edit(m, 'title', 'Q3 sale!');
        expect(sent(m)).toEqual([
            { kind: 'text.insert', target: { bid: 'k', path: 'title' }, payload: { after: ['k:1', 7], text: '!' } },
            { kind: 'text.insert', target: { bid: 'k', path: 'caption' }, payload: { after: 'START', text: 'by region' } },
            { kind: 'text.delete', target: { bid: 'k', path: 'title' }, payload: { segments: [['k:1', 7, 1]] } },
        ]);
    });

    it('anchors the next edit on the acked op, and records an IME composition once it ends', () => {
        const m = mount([block('h', 'heading', 'T'), chart()]);
        edit(m, 'title', 'Q3 sales!');
        m.handle.flush();
        m.handle.ack([{ cid: m.submitted[0].cid, op: 'h:5', conflict: null }]);
        edit(m, 'title', 'Q3 sales!n', { isComposing: true });
        edit(m, 'title', 'Q3 sales!你', { isComposing: true });
        expect(sent(m).length).toBe(1);
        area(m, 'title').dispatchEvent(new window.CompositionEvent('compositionend', { bubbles: true }));
        expect(sent(m)[1]).toEqual({ kind: 'text.insert', target: { bid: 'k', path: 'title' }, payload: { after: ['h:5', 0], text: '你' } });
    });

    it('keeps the person\'s words while their edit is in flight, then follows the projection', () => {
        const m = mount([block('h', 'heading', 'T'), chart()]);
        edit(m, 'title', 'Q3 sales!');
        m.handle.flush();
        m.handle.adopt(proj([block('h', 'heading', 'T'), chart(2, 'Q3 sales, bob')], { head: 20 }));
        expect(area(m, 'title').value).toBe('Q3 sales!');
        m.handle.ack([{ cid: m.submitted[0].cid, op: 'h:21', conflict: null }]);
        m.handle.adopt(proj([block('h', 'heading', 'T'),
                             chart(3, '', [{ op: 'k:1', text: 'Q3 sales', actor: 'human:a' }, { op: 'h:21', text: '!', actor: 'human:a' },
                                           { op: 'b:1', text: ', bob', actor: 'human:bob' }])], { head: 22, frontier: { h: 21 } }));
        expect(area(m, 'title').value).toBe('Q3 sales!, bob');
    });

    it('pasted as a `codoc:` fence keeps its kind, its layout and its fields, and its card is editable', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', '')]);
        m.view.dispatch(m.view.state.tr.setSelection(TextSelection.create(m.view.state.doc, posIn(m.view, 'p', 0))));
        pasteText(m, '```codoc:widget {#b-0123456789ab}\n'
            + '{"$layout": {"export": true, "flow": true, "text": ["alt", "caption"]}, "caption": "A **plot**", "url": "https://x.org"}\n```');
        const nb = ids(m.view.state.doc)[2].split(':')[1];
        expect(ids(m.view.state.doc)).toEqual(['heading:h', 'paragraph:p', `kindBlock:${nb}`]);
        const ops = sent(m);
        const cid = m.submitted[1].cid;
        expect(ops).toEqual([
            { kind: 'block.add', target: { bid: nb }, payload: { kind: 'widget', after: 'p', content: { url: 'https://x.org' },
                                                                 layout: { text: ['alt', 'caption'], flow: true, export: true } } },
            { kind: 'text.insert', target: { bid: nb, path: 'caption' }, payload: { after: 'START', text: 'A plot' } },
            { kind: 'text.mark', target: { bid: nb, path: 'caption' }, payload: { kind: 'bold', attrs: {}, start: ['$cid', cid, 2], end: ['$cid', cid, 5], on: true } },
        ]);
        const caption = m.container.querySelector(`[data-bid="${nb}"] textarea[data-path="caption"]`) as HTMLTextAreaElement;
        expect(caption.value).toBe('A plot');
        caption.value = 'A plot!';
        caption.dispatchEvent(new window.InputEvent('input', { bubbles: true }));
        expect(sent(m).at(-1)).toEqual({ kind: 'text.insert', target: { bid: nb, path: 'caption' }, payload: { after: ['$cid', cid, 5], text: '!' } });
        expect(shadowFaults(m)).toEqual([]);
    });

    it('is not drawn when its layout puts it out of the reading flow', () => {
        const off = { ...chart(), layout: { text: ['title'], flow: false, export: false } };
        const m = mount([block('h', 'heading', 'T'), off, block('p', 'paragraph', 'x')]);
        expect(ids(m.view.state.doc)).toEqual(['heading:h', 'paragraph:p']);
    });
});
