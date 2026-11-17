// @vitest-environment jsdom
/**
 * A block of a kind this editor has no node type for: drawn as a card whose
 * Text fields (from the block's layout) are editable text areas.
 */
import { describe, expect, it } from 'vitest';

import { TextSelection } from '@tiptap/pm/state';

import { textPaths, type ProjectedBlock } from '../../../web/calc/projection';
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

    it('keeps focus and the caret, by character, when its text changes underneath', () => {
        const m = mount([block('h', 'heading', 'T'), chart()]);
        document.body.append(m.container);
        const a = area(m, 'title');
        a.focus();
        a.setSelectionRange(2, 2);                                          // "Q3|"
        m.handle.adopt(proj([block('h', 'heading', 'T'), chart(2, '', [{ op: 'b:1', text: 'Big ', actor: 'human:bob' },
                                                                         { op: 'k:1', text: 'Q3 sales', actor: 'human:a' }])], { head: 20 }));
        expect(area(m, 'title')).toBe(a);
        expect(a.value).toBe('Big Q3 sales');
        expect(document.activeElement).toBe(a);
        expect([a.selectionStart, a.selectionEnd]).toEqual([6, 6]);        // still after "Q3"
    });

    it('holds the block while an IME composition is open, and records it when it ends', () => {
        const m = mount([block('h', 'heading', 'T'), chart()]);
        edit(m, 'title', 'Q3 sales你', { isComposing: true });
        m.handle.adopt(proj([block('h', 'heading', 'T'), chart(2, '', [{ op: 'k:1', text: 'Q3 sales', actor: 'human:a' },
                                                                         { op: 'b:1', text: '!', actor: 'human:bob' }])], { head: 20 }));
        expect(area(m, 'title').value).toBe('Q3 sales你');
        area(m, 'title').dispatchEvent(new window.CompositionEvent('compositionend', { bubbles: true }));
        expect(sent(m)).toEqual([{ kind: 'text.insert', target: { bid: 'k', path: 'title' }, payload: { after: ['k:1', 7], text: '你' } }]);
    });

    it('has no Text fields without a layout', () => {
        expect(textPaths({ id: 'i', kind: 'image', level: 0, rank: 'i', head: 1,
                           fields: { caption: { text: 'c', runs: [], spans: [], suggest_del: [] } } })).toEqual([]);
    });
});

describe('a pasted `codoc:` fence with no `$layout`', () => {
    const fence = '```codoc:image\n{"asset": "a.png", "caption": "A **cat**"}\n```';

    it('sends no layout, and splits its fields by the layout the projection shows for the kind', () => {
        const img: ProjectedBlock = { id: 'i', kind: 'image', level: 0, rank: 'i', head: 1, layout: { text: ['alt', 'caption'], flow: true, export: true },
                                      fields: { asset: 'b.png', alt: { text: '', runs: [], spans: [], suggest_del: [] }, caption: { text: '', runs: [], spans: [], suggest_del: [] } } };
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', ''), img]);
        m.view.dispatch(m.view.state.tr.setSelection(TextSelection.create(m.view.state.doc, posIn(m.view, 'p', 0))));
        pasteText(m, fence);
        const ops = sent(m);
        expect(ops[0].payload).toEqual({ kind: 'image', after: 'p', content: { asset: 'a.png' } });
        expect(ops.slice(1).map(o => [o.kind, o.target.path])).toEqual([['text.insert', 'caption'], ['text.mark', 'caption']]);
    });

    it('splits by the layout the projection says the kind registers, and still sends no layout', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', '')],
                        { kinds: { image: { text: ['alt', 'caption'], flow: true, export: true } } });
        m.view.dispatch(m.view.state.tr.setSelection(TextSelection.create(m.view.state.doc, posIn(m.view, 'p', 0))));
        pasteText(m, fence);
        const ops = sent(m);
        expect(ops[0].payload).toEqual({ kind: 'image', after: 'p', content: { asset: 'a.png' } });
        expect(ops.slice(1).map(o => [o.kind, o.target.path])).toEqual([['text.insert', 'caption'], ['text.mark', 'caption']]);
    });

    it('sends no layout and every field as content when the page knows no layout for the kind', () => {
        const m = mount([block('h', 'heading', 'T'), block('p', 'paragraph', '')]);
        m.view.dispatch(m.view.state.tr.setSelection(TextSelection.create(m.view.state.doc, posIn(m.view, 'p', 0))));
        pasteText(m, fence);
        expect(sent(m)).toEqual([{ kind: 'block.add', target: { bid: ids(m.view.state.doc)[2].split(':')[1] },
                                   payload: { kind: 'image', after: 'p', content: { asset: 'a.png', caption: 'A **cat**' } } }]);
    });
});

describe('copying a card', () => {
    it('puts its `codoc:` fence with `$layout` on the clipboard, and pasting it here adds the same card', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one'), chart(), block('p2', 'paragraph', 'two')]);
        m.view.dispatch(m.view.state.tr.setSelection(TextSelection.create(m.view.state.doc, posIn(m.view, 'p1', 0), posIn(m.view, 'p2', 3))));
        const data: Record<string, string> = {};
        const clipboardData = { clearData: () => undefined, setData: (t: string, v: string) => { data[t] = v; }, getData: (t: string) => data[t] ?? '' };
        m.view.dom.dispatchEvent(Object.assign(new window.Event('copy', { bubbles: true, cancelable: true }), { clipboardData }));
        expect(data['text/plain']).toContain('```codoc:chart\n{"$layout":{"export":true,"flow":true,"text":["title","caption"]},"spec":{"series":[1,2,3],"type":"bar"},"title":"Q3 sales"}\n```');
        m.view.dispatch(m.view.state.tr.setSelection(TextSelection.create(m.view.state.doc, posIn(m.view, 'p2', 3))));
        m.view.dom.dispatchEvent(Object.assign(new window.Event('paste', { bubbles: true, cancelable: true }), { clipboardData }));
        const card = sent(m).find(o => o.kind === 'block.add' && o.payload.kind === 'chart')!;
        expect(card.payload).toMatchObject({ kind: 'chart', content: { spec: { type: 'bar', series: [1, 2, 3] } },
                                             layout: { text: ['title', 'caption'], flow: true, export: true } });
        expect(m.container.querySelectorAll('[data-kind="chart"]').length).toBe(2);
        expect(shadowFaults(m)).toEqual([]);
    });
});
