// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';

import type { OpBody, TextField } from '../../../web/calc/projection';
import { Margin } from '../../../web/editor/comments';
import { block, mount, proj } from './harness';

describe('the margin', () => {
    // Every settle anywhere publishes a projection, so cards are updated while
    // a person is typing into one.
    it('keeps what a person is writing in a card when a projection arrives', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one two'), block('p2', 'paragraph', 'three')]);
        const page = document.createElement('div');
        document.body.append(page);
        const posted: OpBody[] = [];
        const margin = new Margin(page, () => m.view,
            { submit: ops => posted.push(...ops), build: () => undefined, newBid: () => 'c-new', featureOf: () => 'h' });
        const p = proj([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one two'), block('p2', 'paragraph', 'three'),
            { id: 'c1', kind: 'comment', level: 0, rank: 'z', head: 1, fields: { thread: 'c1', anchor: 'p2', anchor_text: 'three',
              scope: 'code', status: 'open', body: { text: 'tighten this', spans: [],
              runs: [{ op: 'o', k0: 0, start: 0, end: 12, actor: 'human:gh:bob', at: '1', plan: null }] } } }]);
        (p.blocks[2].fields.text as TextField).spans.push({ kind: 'comment', attrs: { block: 'c1' }, start: 0, end: 5 });
        margin.update(p);
        margin.compose('p1', 'one', ['s:x', 0], ['s:x', 2]);
        const draft = () => page.querySelector('.codoc-card.is-draft');
        const reply = () => page.querySelector('.codoc-card:not(.is-draft)');
        (draft()!.querySelector('textarea') as HTMLTextAreaElement).value = 'please split this sentence';
        (reply()!.querySelector('textarea') as HTMLTextAreaElement).value = 'agreed, and rename it';
        const draftEl = draft(), replyEl = reply();
        margin.update(p);                                          // someone else typed elsewhere
        expect((draft()?.querySelector('textarea') as HTMLTextAreaElement | null)?.value).toBe('please split this sentence');
        expect((reply()?.querySelector('textarea') as HTMLTextAreaElement | null)?.value).toBe('agreed, and rename it');
        // The cards are the same elements: a card is keyed by its thread and
        // is not rebuilt while the thread is unchanged.
        expect(draft()).toBe(draftEl);
        expect(reply()).toBe(replyEl);
        expect(posted).toEqual([]);
    });
});
