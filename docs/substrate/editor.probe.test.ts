/**
 * Review-4 probes for the editor's recorder, keymap fall-through, the `## `
 * input rule, undo over the window and the timeline reader. Every test asserts
 * the CORRECT behaviour, so a failing test is a confirmed fault.
 *
 *   cd <worktree>/vscode-codoc && npx vitest run --config <scratchpad>/vitest.probe.config.ts
 */
import { baseKeymap } from '@tiptap/pm/commands';
import { Fragment, Slice, type Node as PMNode } from '@tiptap/pm/model';
import { EditorState, TextSelection, type Transaction } from '@tiptap/pm/state';
import { describe, expect, it } from 'vitest';

const SRC = '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src';
import { docFrom } from '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/calc/adopt';
import { type OpBody, type Projection, type ProjectedBlock } from '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/calc/projection';
import { Recorder, reconcile } from '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/calc/recorder';
import { Shadow } from '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/calc/shadow';
import { nextUndo, textAt, type HistoryWindow, type WindowEntry } from '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/calc/window';
import { makeHeading, splitBlock } from '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/editor/commands';
import { codocSchema } from '/Users/r4yen/Desktop/Research/CoDoc/repos/CodeNav-substrate/vscode-codoc/src/editor/schema';
void SRC;

const schema = codocSchema();

function block(id: string, kind: 'heading' | 'paragraph', text: string, level = 1): ProjectedBlock {
    const n = Array.from(text).length;
    return { id, kind, level, rank: id, head: 1,
             fields: { text: { text, runs: n ? [{ op: `s:${id}`, k0: 0, start: 0, end: n, actor: 'human:a', at: '1', plan: null }] : [],
                               spans: [], suggest_del: [] } } };
}
const proj = (blocks: ProjectedBlock[]): Projection =>
    ({ head: 9, frontier: {}, blocks, phases: {}, plans: [], bindings: { by_feature: {}, by_file: {} }, claims: {} });

let n = 0;
const cid = () => `c${n++}`;
const env = (shadows: Map<string, Shadow>) => ({ shadows, nextBid: () => `n${n++}`, nextCid: cid });
const sel = (state: EditorState, from: number, to = from) => state.apply(state.tr.setSelection(TextSelection.create(state.doc, from, to)));
const ids = (doc: PMNode) => { const out: string[] = []; doc.forEach(x => out.push(`${x.type.name}:${x.attrs.bid}`)); return out; };

/**
 * editor.ts `record()` (lines 132–163), verbatim in its logic: handled steps
 * through the Recorder, then the confined fallback over every block with a
 * shadow that an unhandled step's map touches in the FINAL doc.
 */
function recordLikeEditor(tr: Transaction, shadows: Map<string, Shadow>): OpBody[] {
    const recorder = new Recorder(shadows, cid);
    const rec = recorder.record(tr.steps, tr.docs);
    if (rec.unhandled.length) {
        const touched = new Set<string>();
        for (const step of rec.unhandled) {
            step.getMap().forEach((_a, _b, fromB, toB) => {
                tr.doc.nodesBetween(Math.max(0, fromB - 1), Math.min(tr.doc.content.size, toB + 1), (nd, _pos, parent) => {
                    if (parent === tr.doc && nd.attrs.bid && shadows.has(nd.attrs.bid)) touched.add(nd.attrs.bid);
                    return false;
                });
            });
        }
        for (const bid of touched) {
            let node: PMNode | null = null;
            tr.doc.forEach(nd => { if (nd.attrs.bid === bid) node = nd; });
            if (node) rec.ops.push(...reconcile(node, shadows.get(bid)!, cid).ops);
        }
    }
    return rec.ops;
}

const mentions = (ops: OpBody[], bid: string) => ops.filter(o => o.target.bid === bid || o.target.into === bid);

describe('gestures the keymap does not own fall through to ProseMirror', () => {
    it('T1: Enter with a non-empty selection is recorded as a split, not a duplicated bid', () => {
        const { doc, shadows } = docFrom(schema, proj([block('h', 'heading', 'T'), block('p', 'paragraph', 'hello world')]));
        const state = sel(EditorState.create({ schema, doc }), 4 + 2, 4 + 5);   // "he|llo| world"
        expect(splitBlock(state, env(shadows))).toBeNull();                      // editor.ts:167 returns false → baseKeymap Enter
        let out: Transaction | null = null;
        baseKeymap.Enter(state, tr => { out = tr; });
        const tr = out!;
        const ops = recordLikeEditor(tr, shadows);
        const bids = ids(tr.doc);
        expect(new Set(bids).size).toBe(bids.length);                          // one bid per block
        expect(ops.map(o => o.kind)).toContain('block.split');
    });

    it('T2: a selection across two paragraphs replaced by typing records the second paragraph', () => {
        const { doc, shadows } = docFrom(schema, proj([block('a', 'paragraph', 'one two'), block('b', 'paragraph', 'three four')]));
        const state = sel(EditorState.create({ schema, doc }), 1 + 4, 9 + 1 + 5);    // "one |two" .. "three| four"
        const tr = state.tr.insertText('X');                                       // baseKeymap / typing: one ReplaceStep across the boundary
        const ops = recordLikeEditor(tr, shadows);
        // Block b left the document; the store must be told, or the projection resurrects it with its full text.
        expect(ids(tr.doc)).toEqual(['paragraph:a']);
        expect(mentions(ops, 'b').length).toBeGreaterThan(0);
        // And " four" was b's text, written by whoever wrote b; it must not be re-minted as the person's insert.
        const inserted = ops.filter(o => o.kind === 'text.insert').map(o => o.payload.text).join('');
        expect(inserted).toBe('X');
    });

    it('T3: select-all + Backspace does not silently drop every block but the first', () => {
        const { doc, shadows } = docFrom(schema, proj([block('h', 'heading', 'Title'), block('p', 'paragraph', 'body'), block('h2', 'heading', 'Other')]));
        const state = sel(EditorState.create({ schema, doc }), 1, doc.content.size - 1);
        let out: Transaction | null = null;
        baseKeymap.Backspace(state, tr => { out = tr; });
        const tr = out!;
        const ops = recordLikeEditor(tr, shadows);
        // Either the gesture is refused (nothing changes) or every block that vanished is recorded.
        const gone = ['p', 'h2'].filter(b => !ids(tr.doc).some(x => x.endsWith(`:${b}`)));
        for (const b of gone) expect(mentions(ops, b).length, `block ${b} vanished with no op`).toBeGreaterThan(0);
    });

    it('T4: pasting a paragraph with bold and a link keeps the marks in the store', () => {
        const { doc, shadows } = docFrom(schema, proj([block('p', 'paragraph', 'start end')]));
        const state = sel(EditorState.create({ schema, doc }), 1 + 6);            // "start |end"
        const pasted = schema.nodes.paragraph.create(null, [
            schema.text('see ', []),
            schema.text('the docs', [schema.marks.link.create({ href: 'https://example.org' })]),
            schema.text(' now', [schema.marks.bold.create()]),
        ]);
        const tr = state.tr.replaceSelection(new Slice(Fragment.from(pasted), 1, 1));   // what pasteHTML of <p>…</p> produces
        const ops = recordLikeEditor(tr, shadows);
        expect(tr.doc.child(0).textContent).toBe('start see the docs nowend');
        const marks = ops.filter(o => o.kind === 'text.mark').map(o => o.payload.kind);
        expect(marks.sort()).toEqual(['bold', 'link']);
    });

    it('T5: pasting several paragraphs records a block per paragraph and keeps the tail', () => {
        const { doc, shadows } = docFrom(schema, proj([block('p', 'paragraph', 'head tail')]));
        const state = sel(EditorState.create({ schema, doc }), 1 + 5);            // "head |tail"
        const paras = [schema.nodes.paragraph.create(null, schema.text('first')), schema.nodes.paragraph.create(null, schema.text('second'))];
        const tr = state.tr.replaceSelection(new Slice(Fragment.from(paras), 1, 1));
        const ops = recordLikeEditor(tr, shadows);
        expect(tr.doc.childCount).toBe(2);
        expect(tr.doc.child(1).textContent).toBe('secondtail');
        // The second block exists on screen; it must exist in the store (a block.add) or the next projection erases it.
        expect(ops.some(o => o.kind === 'block.add'), 'no block.add for the pasted paragraph').toBe(true);
    });

    it('T5b: pasting several paragraphs does not record the text after the caret as deleted', () => {
        const { doc, shadows } = docFrom(schema, proj([block('p', 'paragraph', 'head tail')]));
        const state = sel(EditorState.create({ schema, doc }), 1 + 5);
        const paras = [schema.nodes.paragraph.create(null, schema.text('first')), schema.nodes.paragraph.create(null, schema.text('second'))];
        const tr = state.tr.replaceSelection(new Slice(Fragment.from(paras), 1, 1));
        const ops = recordLikeEditor(tr, shadows);
        console.log('T5b ops', JSON.stringify(ops));
        expect(ops.filter(o => o.kind === 'text.delete').length).toBe(0);
    });

    it('T1b: what the fallback records for the fall-through Enter', () => {
        const { doc, shadows } = docFrom(schema, proj([block('h', 'heading', 'T'), block('p', 'paragraph', 'hello world')]));
        const state = sel(EditorState.create({ schema, doc }), 4 + 2, 4 + 5);
        let out: Transaction | null = null;
        baseKeymap.Enter(state, tr => { out = tr; });
        const tr = out!;
        const ops = recordLikeEditor(tr, shadows);
        console.log('T1b doc', ids(tr.doc), JSON.stringify(ops));
        expect(shadows.get('p')!.text()).toBe(tr.doc.child(1).textContent);
    });
});

describe('the `## ` input rule', () => {
    /**
     * editor.ts:186–196, verbatim. prosemirror-inputrules calls the handler with the
     * state BEFORE the typed character is inserted and `match` over textBefore + typed
     * text, so `match[0]` ("## ") is one character longer than what the doc holds ("##").
     */
    function headingRule(state: EditorState, shadows: Map<string, Shadow>, match: RegExpMatchArray) {
        const g = makeHeading(state, env(shadows), match[1].length, 0);
        if (!g) return null;
        const b = state.doc.resolve(state.selection.from);
        const bid = b.node(1).attrs.bid as string;
        const sh = shadows.get(bid)!;
        const nn = match[0].length;
        const refs = sh.delete(0, nn);
        g.ops.unshift({ kind: 'text.delete', target: { bid, path: 'text' }, payload: { from: refs[0], to: refs[refs.length - 1] } });
        return { tr: g.tr.delete(b.before(1) + 1, b.before(1) + 1 + nn), ops: g.ops };
    }

    it('T6a: typing "## " before existing text keeps that text', () => {
        const { doc, shadows } = docFrom(schema, proj([block('p', 'paragraph', '##abc')]));   // the person typed "##" at the start of "abc"
        const state = sel(EditorState.create({ schema, doc }), 3);                          // caret after "##"; the space is being typed
        const g = headingRule(state, shadows, /^(#{1,6})\s$/.exec('## ')!)!;
        expect(g.tr.doc.child(0).textContent).toBe('abc');
        expect(shadows.get('p')!.text()).toBe('abc');
        expect((g.ops[0].payload as { to: unknown }).to).toEqual(['s:p', 1]);              // deletes exactly the two hashes
    });

    it('T6b: typing "## " in an otherwise empty paragraph does not reach into the next block', () => {
        const { doc, shadows } = docFrom(schema, proj([block('p', 'paragraph', '##'), block('q', 'paragraph', 'next')]));
        const state = sel(EditorState.create({ schema, doc }), 3);
        const g = headingRule(state, shadows, /^(#{1,6})\s$/.exec('## ')!)!;
        expect(ids(g.tr.doc)).toEqual(['heading:p', 'paragraph:q']);
        expect(g.tr.doc.child(1).textContent).toBe('next');
    });
});

describe('undo and the timeline reader', () => {
    const e = (pos: number, actor: string, batch: string, blocks: Record<string, string>, kind = 'text.insert'): WindowEntry =>
        ({ pos, id: `s:${pos}`, at: String(pos), actor, kind, batch, plan: null, caused_by: null, blocks,
           inverse: [{ kind: 'x', target: {}, payload: { pos } }] as never });

    it('T7: a batch straddling the window edge is not half-undone when another actor holds the first slot', () => {
        // b1 has ops at 9 (outside), 11 and 12 (inside); the window starts at 10 with the loop's op.
        const w: HistoryWindow = { start: 10, head: 13, before: {}, ops: [
            e(10, 'loop', 'L', { p: 'a' }), e(11, 'human:local', 'b1', { p: 'ab' }), e(12, 'human:local', 'b1', { p: 'abc' }),
        ] };
        expect(nextUndo(w, 'human:local')).toBeNull();
    });

    it('T8: a block added inside the window is absent at positions before its add', () => {
        // Exactly what codoc/calculus/window.py publishes for: add h1, insert "T", add p1, insert "one", limit=2
        // (probe_substrate4.py::test_W3): `before` carries p1 as "" because _touched(block.add) clones it.
        const w: HistoryWindow = { start: 2, head: 4, before: { p1: '' }, ops: [
            e(2, 'human:a', 'b', { p1: '' }, 'block.add'), e(3, 'human:a', 'b', { p1: 'one' }),
        ] };
        expect(textAt(w, 'p1', -1, 'one')).toBeNull();      // before the window p1 did not exist
    });

    it('T8b: a block split off inside the window is absent before its split (with `before` fixed)', () => {
        // The working tree's window.py now omits born blocks from `before`; textAt still only knows block.add.
        const w: HistoryWindow = { start: 3, head: 5, before: { p1: 'hello world' }, ops: [
            e(3, 'human:a', 'b', { p1: 'hell', p2: 'o world' }, 'block.split'), e(4, 'human:a', 'b', { p2: 'o world!' }),
        ] };
        expect(textAt(w, 'p2', -1, 'o world!')).toBeNull();
    });
});
