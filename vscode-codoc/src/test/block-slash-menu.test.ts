/**
 * block-slash-menu.test.ts — U5: the slash-menu filter + the block-create message
 * (the pure, DOM-free core of typed-media authoring).
 */
import { describe, it, expect } from 'vitest';
import { filterBlockKinds, buildBlockCreate, BLOCK_KINDS } from '../webview/tiptap/block-slash-menu';

describe('filterBlockKinds', () => {
    it('empty query returns the full catalog in order', () => {
        expect(filterBlockKinds('').map(k => k.kind)).toEqual(['diagram', 'image', 'latex', 'url']);
    });

    it('fuzzy-matches by label, best first', () => {
        expect(filterBlockKinds('di')[0].kind).toBe('diagram');
        expect(filterBlockKinds('form')[0].kind).toBe('latex');   // "Formula"
        expect(filterBlockKinds('link')[0].kind).toBe('url');
    });

    it('also matches the kind key, and drops non-matches', () => {
        const r = filterBlockKinds('latex');
        expect(r[0].kind).toBe('latex');
        expect(filterBlockKinds('zzzz')).toEqual([]);
    });

    it('every catalog kind has a glyph + hint', () => {
        for (const k of BLOCK_KINDS) {
            expect(k.glyph).toBeTruthy();
            expect(k.hint).toBeTruthy();
        }
    });
});

describe('buildBlockCreate', () => {
    it('mints an add block-edit with a blk- id on the given feature', () => {
        const msg = buildBlockCreate('diagram', 'f-auth');
        expect(msg.action).toBe('add');
        expect(msg.feature_id).toBe('f-auth');
        expect(msg.kind).toBe('diagram');
        expect(msg.block_id).toMatch(/^blk-/);
        expect(msg.content).toBe('');            // diagram fills via lift
    });

    it('carries an initial ref for url/image', () => {
        expect(buildBlockCreate('url', 'f-a', 'https://x.example').content).toBe('https://x.example');
    });
});
