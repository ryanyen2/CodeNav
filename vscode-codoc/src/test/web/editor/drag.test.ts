// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';

import { moveBlock } from '../../../web/editor/commands';
import { block, ids, kinds, mount, perform, posIn, select, shadowFaults, texts, type Mounted } from './harness';

describe('moving a block', () => {
    it('takes what the heading dominates with it, as one block.move', () => {
        const m = mount([block('h1', 'heading', 'A'), block('p1', 'paragraph', 'a-body'), block('h2', 'heading', 'B'), block('p2', 'paragraph', 'b-body')]);
        perform(m, s => moveBlock(s, 'h2', 'START'));
        m.handle.flush();
        expect(ids(m.view.state.doc)).toEqual(['heading:h2', 'paragraph:p2', 'heading:h1', 'paragraph:p1']);
        expect(kinds(m.submitted)).toEqual(['block.move']);
        expect(shadowFaults(m)).toEqual([]);
    });
});

// The handle is a CSS `::before` on the block, so a drag is DOM events and no
// decoration. These deliver the events a browser does.
class FakeDataTransfer {
    private d = new Map<string, string>();
    effectAllowed = 'uninitialized';
    dropEffect = 'none';
    files: File[] = [];
    get types(): string[] { return [...this.d.keys()]; }
    setData(t: string, v: string): void { this.d.set(t, v); }
    getData(t: string): string { return this.d.get(t) ?? ''; }
    clearData(t?: string): void { if (t) this.d.delete(t); else this.d.clear(); }
}

function domOf(m: Mounted, bid: string): Element {
    const el = m.view.dom.querySelector(`[data-bid="${bid}"]`);
    if (!el) throw new Error(`no DOM for ${bid}`);
    return el;
}

function dragEvent(type: string, target: Element, dt: FakeDataTransfer, clientY = 10): Event {
    const ev = new window.Event(type, { bubbles: true, cancelable: true });
    Object.defineProperty(ev, 'dataTransfer', { value: dt });
    Object.defineProperty(ev, 'clientX', { value: 5 });
    Object.defineProperty(ev, 'clientY', { value: clientY });
    target.dispatchEvent(ev);
    return ev;
}

/** A drag as Chrome delivers it: mousedown in the handle strip (x is negative,
 *  which is where the `::before` sits), dragstart on the block, dragover and
 *  drop on the target, dragend on the source. */
function dragBlock(m: Mounted, from: string, to: string): FakeDataTransfer {
    const src = domOf(m, from);
    src.dispatchEvent(new window.MouseEvent('mousedown',
        { bubbles: true, cancelable: true, button: 0, clientX: -30, clientY: 5 }));
    const dt = new FakeDataTransfer();
    dragEvent('dragstart', src, dt);
    const tgt = domOf(m, to);
    dragEvent('dragover', tgt, dt);
    dragEvent('drop', tgt, dt);
    dragEvent('dragend', src, dt);
    return dt;
}

describe('the drag handle', () => {
    it('moves the block, and the payload survives ProseMirror\'s own dragstart', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one'), block('p2', 'paragraph', 'two')]);
        const dt = dragBlock(m, 'p1', 'p2');
        expect(dt.getData('application/x-codoc-bid')).toBe('p1');
        expect(ids(m.view.state.doc)).toEqual(['heading:h', 'paragraph:p2', 'paragraph:p1']);
        expect(m.view.dragging).toBeNull();               // ProseMirror did not take the drag as its own
        m.handle.flush();
        expect(kinds(m.submitted)).toEqual(['block.move']);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('with words selected elsewhere, is still a block move and not a move of those words', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one'), block('p2', 'paragraph', 'two')]);
        select(m.view, posIn(m.view, 'p2', 0), posIn(m.view, 'p2', 3));        // "two" selected
        dragBlock(m, 'p1', 'p2');
        expect(texts(m.view.state.doc)).toEqual(['T', 'two', 'one']);
        expect(m.view.dragging).toBeNull();
        m.handle.flush();
        expect(kinds(m.submitted)).toEqual(['block.move']);
        expect(shadowFaults(m)).toEqual([]);
    });

    it('does nothing when the pointer went down in the text rather than the handle', () => {
        const m = mount([block('h', 'heading', 'T'), block('p1', 'paragraph', 'one'), block('p2', 'paragraph', 'two')]);
        const src = domOf(m, 'p1');
        src.dispatchEvent(new window.MouseEvent('mousedown',
            { bubbles: true, cancelable: true, button: 0, clientX: 20, clientY: 5 }));
        const dt = new FakeDataTransfer();
        dragEvent('dragstart', src, dt);
        expect(dt.getData('application/x-codoc-bid')).toBe('');
        expect((src as HTMLElement).draggable).toBe(false);
    });
});
