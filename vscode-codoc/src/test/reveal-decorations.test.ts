import { describe, it, expect } from 'vitest';
import { Schema } from '@tiptap/pm/model';
import { featureBodyRanges, newlyResolved, paragraphRanges } from '../webview/tiptap/reveal-decorations';
import type { FeaturePhase } from '../state/activity-model';

// A minimal schema with a featureHeading node carrying an `fid`, plus paragraphs —
// enough to exercise the pure body-range geometry without the full editor.
const schema = new Schema({
    nodes: {
        doc: { content: 'block+' },
        text: { group: 'inline' },
        paragraph: { group: 'block', content: 'inline*', toDOM: () => ['p', 0] },
        featureHeading: {
            group: 'block',
            content: 'inline*',
            attrs: { fid: { default: null }, level: { default: 1 } },
            toDOM: () => ['h1', 0],
        },
    },
});

function heading(fid: string, text: string) {
    return schema.nodes.featureHeading.create({ fid }, schema.text(text));
}
function para(text: string) {
    return schema.nodes.paragraph.create(null, schema.text(text));
}

describe('featureBodyRanges', () => {
    it('spans from after a heading to before the next heading', () => {
        const doc = schema.nodes.doc.create(null, [
            heading('f1', 'One'),
            para('alpha beta'),
            para('gamma'),
            heading('f2', 'Two'),
            para('delta'),
        ]);
        const ranges = featureBodyRanges(doc);
        expect(ranges.map(r => r.fid)).toEqual(['f1', 'f2']);
        // f1 body covers both its paragraphs; the extracted text matches
        expect(doc.textBetween(ranges[0].from, ranges[0].to, ' ').trim()).toBe('alpha beta gamma');
        expect(doc.textBetween(ranges[1].from, ranges[1].to, ' ').trim()).toBe('delta');
    });

    it('drops a heading with an empty body (immediately followed by another heading)', () => {
        const doc = schema.nodes.doc.create(null, [
            heading('f1', 'One'),
            heading('f2', 'Two'),
            para('body'),
        ]);
        expect(featureBodyRanges(doc).map(r => r.fid)).toEqual(['f2']);
    });

    it('ignores headings with no fid', () => {
        const doc = schema.nodes.doc.create(null, [heading('', 'No id'), para('x')]);
        expect(featureBodyRanges(doc)).toEqual([]);
    });
});

describe('paragraphRanges', () => {
    it('returns one range per paragraph within [from, to)', () => {
        const doc = schema.nodes.doc.create(null, [
            heading('f1', 'One'),
            para('alpha beta'),
            para('gamma'),
            heading('f2', 'Two'),
            para('delta'),
        ]);
        const [f1] = featureBodyRanges(doc);
        const ranges = paragraphRanges(doc, f1.from, f1.to);
        expect(ranges).toHaveLength(2);
        expect(ranges.map(r => doc.textBetween(r.from, r.to))).toEqual(['alpha beta', 'gamma']);
    });

    it('excludes paragraphs outside the range', () => {
        const doc = schema.nodes.doc.create(null, [
            heading('f1', 'One'),
            para('alpha'),
            heading('f2', 'Two'),
            para('beta'),
        ]);
        const [f1, f2] = featureBodyRanges(doc);
        expect(paragraphRanges(doc, f1.from, f1.to).map(r => doc.textBetween(r.from, r.to))).toEqual(['alpha']);
        expect(paragraphRanges(doc, f2.from, f2.to).map(r => doc.textBetween(r.from, r.to))).toEqual(['beta']);
    });

    it('returns nothing for an empty range', () => {
        const doc = schema.nodes.doc.create(null, [heading('f1', 'One'), heading('f2', 'Two'), para('x')]);
        expect(paragraphRanges(doc, 0, 0)).toEqual([]);
    });
});

describe('newlyResolved', () => {
    const P = (o: Record<string, FeaturePhase>) => o;
    it('flags a feature leaving an active phase', () => {
        expect(newlyResolved(P({ f1: 'editing' }), P({ f1: 'done' }))).toEqual(['f1']);
        expect(newlyResolved(P({ f1: 'reflecting' }), P({}))).toEqual(['f1']);
    });
    it('does not flag a feature still active or never active', () => {
        expect(newlyResolved(P({ f1: 'editing' }), P({ f1: 'editing' }))).toEqual([]);
        expect(newlyResolved(P({ f1: 'done' }), P({ f1: 'done' }))).toEqual([]);
    });
});
