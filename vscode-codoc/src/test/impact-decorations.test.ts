import { describe, it, expect } from 'vitest';
import { Schema } from '@tiptap/pm/model';
import {
    IMPACT_CARD_ROWS,
    buildImpactDecorations,
    impactLabel,
    viaLine,
} from '../webview/tiptap/impact-decorations';
import { emptySidecar, impactForFeature, SidecarData, ImpactEntry } from '../state/bindings-model';

// The group-4 answer ("what happens if I change this?") on the surface that shows it.
// These pin the three properties that make the chip trustworthy rather than decorative:
// nothing is drawn where nothing depends on the feature, the truncation is stated, and a
// changed graph invalidates the widget instead of leaving a stale number on the heading.

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
function entry(id: string, count = 1, via: string[] = ['x']): ImpactEntry {
    return { feature_id: id, title: `Feature ${id}`, count, via };
}

describe('impactLabel', () => {
    it('reads as the answer to the question, not as a metric', () => {
        expect(impactLabel(1)).toBe('1 feature depends on this');
        expect(impactLabel(4)).toBe('4 features depend on this');
    });
});

describe('viaLine', () => {
    it('names the symbols that reach in', () => {
        expect(viaLine(entry('f1', 2, ['a.login', 'a.logout']))).toBe('a.login, a.logout');
    });

    it('says how many it left off rather than trailing an ellipsis', () => {
        // count is the true total; `via` is already capped upstream at five.
        const e = entry('f1', 11, ['a', 'b', 'c', 'd', 'e']);
        expect(viaLine(e)).toBe('a, b, c +8 more');
    });
});

describe('buildImpactDecorations', () => {
    const doc = schema.nodes.doc.create(null, [
        heading('f1', 'Sign-in'),
        para('body'),
        heading('f2', 'Theme'),
        para('body'),
    ]);

    it('draws one chip per feature something depends on', () => {
        const set = buildImpactDecorations(doc, fid => (fid === 'f1' ? [entry('f9')] : []));
        const decos = set.find();
        expect(decos.length).toBe(1);
        expect(decos[0].spec.key).toBe('impact-f1:1');
    });

    it('draws nothing where nothing depends on the feature', () => {
        // The absence IS the answer — a "0 dependents" chip on every leaf would be the
        // inventory the prose exists to spare the reader.
        expect(buildImpactDecorations(doc, () => []).find()).toEqual([]);
    });

    it('ignores a heading with no fid — a node the author is still typing', () => {
        const typing = schema.nodes.doc.create(null, [heading('', 'New'), para('body')]);
        expect(buildImpactDecorations(typing, () => [entry('f9')]).find()).toEqual([]);
    });

    it('sits inside the heading it is about, at its end', () => {
        const set = buildImpactDecorations(doc, fid => (fid === 'f1' ? [entry('f9')] : []));
        const [deco] = set.find();
        const headingNode = doc.child(0);
        expect(deco.from).toBe(headingNode.nodeSize - 1);   // 0 + size - 1: last inside slot
    });

    it('keys on the count so a changed graph cannot leave a stale number', () => {
        const one = buildImpactDecorations(doc, () => [entry('f9')]).find()[0];
        const two = buildImpactDecorations(doc, () => [entry('f9'), entry('f8')]).find()[0];
        expect(one.spec.key).not.toBe(two.spec.key);
    });
});

describe('impactForFeature', () => {
    const sidecar: SidecarData = {
        ...emptySidecar(),
        features: { 'f-core': { title: 'Store', parent_id: null } },
        feature_impact: {
            'f-core': [
                { feature_id: 'f-loop', title: 'The two loops', count: 3, via: ['loop_a.run'] },
            ],
        },
    };

    it('returns the ranked dependents', () => {
        expect(impactForFeature(sidecar, 'f-core').map(r => r.title)).toEqual(['The two loops']);
    });

    it('is empty for a feature nothing depends on, and on a pre-v6 sidecar', () => {
        expect(impactForFeature(sidecar, 'f-loop')).toEqual([]);
        expect(impactForFeature({ ...emptySidecar(), version: 5 }, 'f-core')).toEqual([]);
    });
});

describe('the card bound', () => {
    it('stops at a readable number of rows', () => {
        // A hub feature has dozens of dependents; a card that scrolls is a panel, and the
        // reader asked for the shape of the risk.
        expect(IMPACT_CARD_ROWS).toBeLessThanOrEqual(10);
    });
});
