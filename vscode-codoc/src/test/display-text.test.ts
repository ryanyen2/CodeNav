/**
 * display-text.test.ts — the display-space diff contract (the misplaced-decoration fix).
 *
 * Two root causes pinned here:
 *   1. A codeRef chip occupies ONE doc position but contributed zero chars via
 *      textContent and ~30 via its markdown form — so chip paragraphs either lost
 *      their diff underline entirely (the old bail) or would have mis-anchored it.
 *      Display space maps every atom to one char: offsets ≡ doc positions, always.
 *   2. Baseline↔current paragraphs paired by INDEX — one inserted/removed paragraph
 *      shifted every later diff onto the wrong neighbour. alignParas pairs by
 *      exact-match anchoring instead.
 */
import { describe, it, expect } from 'vitest';
import { Node as PMModelNode } from '@tiptap/pm/model';
import { codocSchema } from '../webview/tiptap/schema';
import { ATOM_CHAR, alignParas, mdDisplayText, paraDisplayText } from '../webview/tiptap/display-text';
import { featureBlocks } from '../state/edit-baseline';
import { buildSettlementDecorations } from '../webview/tiptap/settlement-decorations';
import type { PMNode } from '../state/pm-doc';

const schema = codocSchema();

const chip = { type: 'codeRef', attrs: { label: 'dispatch()', file: 'fanout.py', symbol: 'dispatch' } };
const text = (t: string): PMNode => ({ type: 'text', text: t });
const para = (...content: PMNode[]): PMNode => ({ type: 'paragraph', content });
const heading = (fid: string, title: string): PMNode => ({
    type: 'featureHeading',
    attrs: { fid, level: 0, retired: false, realized: true },
    content: [text(title)],
});
const docOf = (...blocks: PMNode[]): PMModelNode =>
    schema.nodeFromJSON({ type: 'doc', content: blocks });

function attrsOf(d: unknown): { class?: string; title?: string } | undefined {
    return (d as { type?: { attrs?: { class?: string; title?: string } } }).type?.attrs;
}

describe('alignParas — exact-match anchored paragraph pairing', () => {
    it('pairs identical lists 1:1', () => {
        expect(alignParas(['A', 'B'], ['A', 'B'])).toEqual([0, 1]);
    });
    it('marks an inserted middle paragraph null and keeps later pairs anchored', () => {
        expect(alignParas(['A', 'B'], ['A', 'X', 'B'])).toEqual([0, null, 1]);
    });
    it('skips a deleted baseline paragraph instead of shifting later pairs', () => {
        expect(alignParas(['A', 'X', 'B'], ['A', 'B'])).toEqual([0, 2]);
    });
    it('pairs an in-place edit directly', () => {
        expect(alignParas(['A', 'B', 'C'], ['A', 'Bee', 'C'])).toEqual([0, 1, 2]);
    });
    it('handles an edit followed by a trailing insertion', () => {
        expect(alignParas(['A', 'B'], ['A', 'Bee', 'New'])).toEqual([0, 1, null]);
    });
});

describe('display projections', () => {
    it('mdDisplayText collapses a codoc ref to one atom char', () => {
        expect(mdDisplayText('See [dispatch()](codoc:fanout.py#dispatch) here'))
            .toBe(`See ${ATOM_CHAR} here`);
        expect(mdDisplayText('line one\nline two')).toBe(`line one${ATOM_CHAR}line two`);
    });

    it('paraDisplayText yields one char per doc position, chips included', () => {
        const p = schema.nodeFromJSON(para(text('See '), chip, text(' for fan-out.')));
        expect(paraDisplayText(p)).toBe(`See ${ATOM_CHAR} for fan-out.`);
        expect(paraDisplayText(p).length).toBe(p.content.size);
    });
});

// (The `hold underline` block that stood here is gone with the underline itself. It
// pinned two properties — a chip is ONE position, and paragraphs pair by CONTENT — for
// a second diff hold-decorations kept of the author's pending edit. Both properties are
// pinned below against the layer that draws that ink now, which is the point of having
// one model rather than two that agree by luck.)

describe('the human channel — chips and alignment, through the real decoration layer', () => {
    /** The host half of the stages, from a baseline doc: `projected` in DISPLAY space,
     *  which is the whole point of the contract these tests guard. */
    const stagesOf = (baseJson: PMNode) => {
        const out = new Map<string, { projected: { title: string; paras: string[] } }>();
        for (const [fid, ft] of featureBlocks(baseJson)) {
            out.set(fid, { projected: { title: mdDisplayText(ft.title), paras: ft.paras.map(mdDisplayText) } });
        }
        return out;
    };
    const humanAdds = (doc: PMModelNode, baseJson: PMNode): string[] =>
        buildSettlementDecorations(doc, stagesOf(baseJson)).find()
            .filter(d => (attrsOf(d)?.class ?? '').includes('human'))
            .map(d => doc.textBetween(d.from, d.to, ' ', '¤'));

    it('marks an added word in a paragraph that cites code (a chip is ONE position)', () => {
        // The bug class this pins: a chip contributes zero chars via textContent and
        // ~30 via its markdown, so a diff run in either space mis-anchors — or, with the
        // old defence, was dropped entirely and the paragraph showed no mark at all.
        const baseJson = { type: 'doc', content: [
            heading('f-a', 'Fan-out'),
            para(text('See '), chip, text(' for fan-out.')),
        ] } as PMNode;
        const doc = docOf(
            heading('f-a', 'Fan-out'),
            para(text('See '), chip, text(' for resilient fan-out.')),
        );
        expect(humanAdds(doc, baseJson)).toContain('resilient ');
    });

    it('draws NO ghost for what you deleted — the human channel is ink only', () => {
        // You removed those words; showing them back to you is the surface narrating
        // your own typing. The claim still exists (the margin marker reads it), so this
        // asserts the RENDERING, which is where the rule belongs.
        const baseJson = { type: 'doc', content: [
            heading('f-a', 'Fan-out'),
            para(text('Alpha beta gamma.')),
        ] } as PMNode;
        const doc = docOf(heading('f-a', 'Fan-out'), para(text('Alpha gamma.')));
        const decos = buildSettlementDecorations(doc, stagesOf(baseJson)).find();
        expect(decos).toHaveLength(0);
    });

    it('…while the code channel DOES, because somebody else took the words out', () => {
        const doc = docOf(heading('f-a', 'Fan-out'), para(text('Alpha gamma.')));
        const stages = new Map([['f-a', {
            projected: { title: 'Fan-out', paras: ['Alpha gamma.'] },
            code: { layerId: 'e-1', prev: { title: 'Fan-out', paras: ['Alpha beta gamma.'] } },
        }]]);
        const decos = buildSettlementDecorations(doc, stages).find();
        expect(decos.length).toBeGreaterThan(0);
    });

    it('keeps the mark anchored when a paragraph is inserted above', () => {
        const baseJson = { type: 'doc', content: [
            heading('f-a', 'Fan-out'),
            para(text('Alpha beta.')),
            para(text('Gamma delta.')),
        ] } as PMNode;
        const doc = docOf(
            heading('f-a', 'Fan-out'),
            para(text('Alpha beta.')),
            para(text('Fresh thought.')),
            para(text('Gamma delta plus.')),
        );
        const adds = humanAdds(doc, baseJson);
        expect(adds.some(s => s.includes('plus'))).toBe(true);
        expect(adds.some(s => s.includes('Fresh thought.'))).toBe(true);
        // The unchanged first paragraph carries nothing — paragraphs pair by CONTENT,
        // so an insertion above does not shift every later diff onto its neighbour.
        expect(adds.some(s => s.includes('Alpha'))).toBe(false);
    });
});
