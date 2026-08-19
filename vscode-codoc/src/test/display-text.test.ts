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
import { buildHoldDecorations } from '../webview/tiptap/hold-decorations';
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

describe('hold underline — chip paragraphs diff precisely (formerly blacked out)', () => {
    it('underlines the changed words at the right doc positions in a paragraph with a chip', () => {
        const doc = docOf(
            heading('f-a', 'Fan-out'),
            para(text('See '), chip, text(' for fan-out later.')),
        );
        const set = buildHoldDecorations(doc, new Set(['f-a']), undefined, {
            'f-a': {
                kind: 'amend',
                intent: 'update fan-out',
                baseline: 'See [dispatch()](codoc:fanout.py#dispatch) for fan-out now.',
            },
        });
        const underline = set.find().find(d => attrsOf(d)?.class === 'ce-intent-underline');
        expect(underline).toBeTruthy();
        expect(doc.textBetween(underline!.from, underline!.to, ' ', '¤')).toBe('later.');
    });

    it('keeps the underline on the edited paragraph when a new paragraph is inserted above it', () => {
        const doc = docOf(
            heading('f-a', 'Fan-out'),
            para(text('Alpha beta.')),
            para(text('Brand new paragraph.')),
            para(text('Gamma delta plus.')),
        );
        const set = buildHoldDecorations(doc, new Set(['f-a']), undefined, {
            'f-a': { kind: 'amend', intent: 'x', baseline: 'Alpha beta.\n\nGamma delta.' },
        });
        const spans = set.find()
            .filter(d => attrsOf(d)?.class === 'ce-intent-underline')
            .map(d => doc.textBetween(d.from, d.to, ' ', '¤'));
        // The inserted paragraph reads as fully new; the edited one underlines its
        // changed tail — NOT the whole paragraph (the old index-pairing bug diffed
        // it against the wrong baseline neighbour).
        expect(spans).toContain('Brand new paragraph.');
        expect(spans).toContain('delta plus.');
        expect(spans).not.toContain('Gamma delta plus.');
    });
});

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
