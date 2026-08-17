/**
 * ask-decorations.test.ts — the geometry that maps a walkthrough's quote to a
 * document range, and the search projection find uses. Both must agree with the
 * display-space contract (a codeRef atom is one document position) or a highlight
 * lands on the wrong words.
 */
import { describe, it, expect } from 'vitest';
import { Schema } from '@tiptap/pm/model';
import { featureQuoteBlocks, quoteRange } from '../webview/tiptap/ask-decorations';
import { searchBlocks } from '../webview/tiptap/find-decorations';

// A schema mirroring the real one closely enough for position math: a heading
// with an fid, paragraphs, and a codeRef atom that occupies one position.
const schema = new Schema({
    nodes: {
        doc: { content: 'block+' },
        text: { group: 'inline' },
        codeRef: {
            group: 'inline', inline: true, atom: true,
            attrs: { label: { default: '' }, file: { default: '' }, symbol: { default: '' } },
            toDOM: () => ['span', 0],
        },
        paragraph: { group: 'block', content: 'inline*', toDOM: () => ['p', 0] },
        featureHeading: {
            group: 'block', content: 'inline*',
            attrs: { fid: { default: null } }, toDOM: () => ['h1', 0],
        },
    },
});

const heading = (fid: string, text: string) => schema.nodes.featureHeading.create({ fid }, schema.text(text));
const para = (...inline: any[]) => schema.nodes.paragraph.create(null, inline);
const ref = (label: string) => schema.nodes.codeRef.create({ label });

describe('featureQuoteBlocks + quoteRange', () => {
    it('finds a quote in a description paragraph at the right document range', () => {
        const doc = schema.nodes.doc.create(null, [
            heading('f1', 'Strip furniture'),
            para(schema.text('Runs first so the header is gone.')),
        ]);
        const blocks = featureQuoteBlocks(doc);
        const range = quoteRange(blocks.get('f1')!, 'the header is gone')!;
        expect(doc.textBetween(range.from, range.to)).toBe('the header is gone');
    });

    it('finds a quote in the title', () => {
        const doc = schema.nodes.doc.create(null, [
            heading('f1', 'Strip page furniture'),
            para(schema.text('body')),
        ]);
        const range = quoteRange(featureQuoteBlocks(doc).get('f1')!, 'page furniture')!;
        expect(doc.textBetween(range.from, range.to)).toBe('page furniture');
    });

    it('anchors correctly across a codeRef atom (display-space contract)', () => {
        // "see " (4) + [chip] (1) + " then done" — a match after the chip must skip
        // exactly one position for it, not zero and not the chip's label length.
        const doc = schema.nodes.doc.create(null, [
            heading('f1', 'T'),
            para(schema.text('see '), ref('convert'), schema.text(' then done')),
        ]);
        const range = quoteRange(featureQuoteBlocks(doc).get('f1')!, 'then done')!;
        expect(doc.textBetween(range.from, range.to)).toBe('then done');
    });

    it('does not find a quote that straddles two paragraphs', () => {
        const doc = schema.nodes.doc.create(null, [
            heading('f1', 'T'),
            para(schema.text('First ends.')),
            para(schema.text('Second begins.')),
        ]);
        expect(quoteRange(featureQuoteBlocks(doc).get('f1')!, 'First ends. Second begins.')).toBeNull();
    });
});

describe('searchBlocks', () => {
    it('yields one block per heading and paragraph, attributed to the feature', () => {
        const doc = schema.nodes.doc.create(null, [
            heading('f1', 'One'),
            para(schema.text('alpha')),
            heading('f2', 'Two'),
            para(schema.text('beta')),
        ]);
        const blocks = searchBlocks(doc);
        expect(blocks.map(b => [b.fid, b.field, b.text])).toEqual([
            ['f1', 'title', 'One'],
            ['f1', 'description', 'alpha'],
            ['f2', 'title', 'Two'],
            ['f2', 'description', 'beta'],
        ]);
    });

    it('base maps char 0 of a block to the position just inside it', () => {
        const doc = schema.nodes.doc.create(null, [heading('f1', 'One'), para(schema.text('alpha'))]);
        const desc = searchBlocks(doc).find(b => b.field === 'description')!;
        expect(doc.textBetween(desc.base, desc.base + 5)).toBe('alpha');
    });
});
