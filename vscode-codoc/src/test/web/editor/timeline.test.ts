// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';

import type { Projection } from '../../../web/calc/projection';
import type { HistoryWindow } from '../../../web/calc/window';
import { Timeline } from '../../../web/editor/timeline';

describe('the History scrubber', () => {
    it('sitting at now stays at now when the window grows', () => {
        const parent = document.createElement('div');
        document.body.append(parent);
        const dom = document.createElement('div');
        parent.append(dom);
        const tl = new Timeline(parent, () => dom);
        const p: Projection = { head: 9, frontier: {}, phases: {}, plans: [], bindings: { by_feature: {} }, claims: {}, blocks: [
            { id: 'p1', kind: 'paragraph', level: 0, rank: 'a', head: 1, fields: { text: { text: 'one', runs: [], spans: [], suggest_del: [] } } }] };
        const entry = (pos: number): HistoryWindow['ops'][number] => ({ pos, id: `s:${pos}`, at: `2026-09-18T00:00:0${pos}`,
            actor: 'human:gh:bob', kind: 'text.insert', batch: `b${pos}`, plan: null, caused_by: null, blocks: { p1: { text: `one${pos}` } }, inverse: [] });
        tl.setWindow({ start: 0, head: 2, before: { p1: { text: 'one' } }, ops: [entry(0), entry(1)] }, p);
        tl.toggle(true);
        expect(tl.past.hidden).toBe(true);                         // at "now"
        tl.setWindow({ start: 0, head: 3, before: { p1: { text: 'one' } }, ops: [entry(0), entry(1), entry(2)] }, p);
        expect(tl.past.hidden).toBe(true);                         // still now: nobody dragged the scrubber
        expect(dom.hidden).toBe(false);
    });
});
