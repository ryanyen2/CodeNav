import * as fs from 'node:fs';
import * as path from 'node:path';
import { describe, expect, it } from 'vitest';

import { parseBlocks, parseInline, type BlockSpec } from '../../../web/calc/markdown';
import type { Span } from '../../../web/calc/projection';

// Written by the Python codecs (codoc.calculus.block_md / inline_md); see the file's `_comment`.
const cases = JSON.parse(fs.readFileSync(path.join(__dirname, '../../fixtures/markdown-cases.json'), 'utf8')) as {
    blocks: Array<{ name: string; md: string; headings: boolean; expect: BlockSpec[] }>;
    inline: Array<{ name: string; md: string; text: string; spans: Span[] }>;
};

describe('parseBlocks reads what block_md.parse reads', () => {
    for (const c of cases.blocks) it(c.name, () => expect(parseBlocks(c.md, c.headings)).toEqual(c.expect));
});

describe('parseInline reads what inline_md.parse reads', () => {
    for (const c of cases.inline) it(c.name, () => expect(parseInline(c.md)).toEqual({ text: c.text, spans: c.spans }));
});
