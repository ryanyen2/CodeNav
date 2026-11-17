import * as fs from 'node:fs';
import * as path from 'node:path';
import { describe, expect, it } from 'vitest';

import { fenceLayout, parseBlocks, parseInline, renderInline, type BlockSpec } from '../../../web/calc/markdown';
import type { Span } from '../../../web/calc/projection';

// Written by the Python codecs (codoc.calculus.block_md / inline_md); see the file's `_comment`.
const cases = JSON.parse(fs.readFileSync(path.join(__dirname, '../../fixtures/markdown-cases.json'), 'utf8')) as {
    blocks: Array<{ name: string; md: string; headings: boolean; expect: BlockSpec[] }>;
    inline: Array<{ name: string; md: string; text: string; spans: Span[] }>;
    render: Array<{ name: string; text: string; spans: Span[]; md: string }>;
};

describe('parseBlocks reads what block_md.parse reads', () => {
    for (const c of cases.blocks) it(c.name, () => expect(parseBlocks(c.md, c.headings)).toEqual(c.expect));
});

describe('parseInline reads what inline_md.parse reads', () => {
    for (const c of cases.inline) it(c.name, () => expect(parseInline(c.md)).toEqual({ text: c.text, spans: c.spans }));
});

describe('renderInline writes what inline_md.render writes', () => {
    for (const c of cases.render) {
        it(c.name, () => {
            expect(renderInline(c.text, c.spans)).toBe(c.md);
            expect(parseInline(c.md).text).toBe(c.text);
        });
    }
});

describe('a fence\'s `$layout`, read as doc.Layout.from_json reads it', () => {
    it('takes a list of field names, defaults flow and export, and refuses anything else', () => {
        expect(fenceLayout({ $layout: { text: ['alt', 'caption', 'alt'] } })).toEqual({ text: ['alt', 'caption'], flow: true, export: true });
        expect(fenceLayout({ $layout: { text: ['body'], flow: false, export: false } })).toEqual({ text: ['body'], flow: false, export: false });
        expect(fenceLayout({ $layout: {} })).toEqual({ text: [], flow: true, export: true });
        expect(fenceLayout({ $layout: { text: 'caption' } })).toBeNull();
        expect(fenceLayout({ $layout: { text: [1] } })).toBeNull();
        expect(fenceLayout({ caption: 'x' })).toBeNull();
    });
});
