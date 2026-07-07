/**
 * block-decorations.test.ts — the v6 block-edit round-trip logic (DOM-free).
 *
 * The widget rendering itself is DOM (verified visually in VS Code), but the
 * decision of WHAT to send the host on an edit is pure and pinned here: a content
 * change posts a stable-id edit with prev/new content; a revert-to-baseline posts
 * nothing; only LOWER-capable text media (diagram/latex) are editable.
 */
import { describe, it, expect } from 'vitest';
import { blockEditMsg, EDITABLE_KINDS, parsePdfEnvelope, parseUrlEnvelope } from '../webview/tiptap/block-decorations';
import type { UIBlock } from '../webview/protocol';

const diagram: UIBlock = {
    id: 'blk-1', kind: 'diagram', content: 'flowchart TB\n  a --> b',
    lifecycle: 'persistent', provenance: 'derived', ord: 0,
};

describe('block-edit message', () => {
    it('builds an edit carrying the stable id + prev/new content', () => {
        const msg = blockEditMsg(diagram, 'f-auth', 'flowchart TB\n  a');
        expect(msg).toEqual({
            block_id: 'blk-1', feature_id: 'f-auth', kind: 'diagram',
            action: 'edit', content: 'flowchart TB\n  a', prev_content: 'flowchart TB\n  a --> b',
        });
    });

    it('returns null when the content is unchanged (edit-then-revert is a no-op)', () => {
        expect(blockEditMsg(diagram, 'f-auth', diagram.content)).toBeNull();
    });

    it('only diagram/latex are editable; consult media (url/image) are read-only', () => {
        expect(EDITABLE_KINDS.has('diagram')).toBe(true);
        expect(EDITABLE_KINDS.has('latex')).toBe(true);
        expect(EDITABLE_KINDS.has('url')).toBe(false);
        expect(EDITABLE_KINDS.has('image')).toBe(false);
    });
});

describe('url block envelope parsing', () => {
    it('a bare (not-yet-lifted) url is not an envelope', () => {
        expect(parseUrlEnvelope('https://docs.example/spec')).toBeNull();
    });

    it('parses a lifted envelope carrying title + excerpt', () => {
        const content = JSON.stringify({
            url: 'https://docs.example/spec', title: 'Spec Doc',
            excerpt: 'the important bit', status: 'ok',
        });
        expect(parseUrlEnvelope(content)).toEqual({
            url: 'https://docs.example/spec', title: 'Spec Doc',
            excerpt: 'the important bit', status: 'ok',
        });
    });

    it('malformed JSON-looking content falls back to null (renders as a bare link)', () => {
        expect(parseUrlEnvelope('{not valid json')).toBeNull();
    });

    it('a JSON object without a url field is not treated as an envelope', () => {
        expect(parseUrlEnvelope(JSON.stringify({ foo: 'bar' }))).toBeNull();
    });
});

describe('pdf block envelope parsing', () => {
    it('a bare (not-yet-lifted) ref is not an envelope', () => {
        expect(parsePdfEnvelope('.codoc/media/blk-1.pdf')).toBeNull();
    });

    it('parses a lifted envelope carrying pages + excerpt', () => {
        const content = JSON.stringify({
            ref: '.codoc/media/blk-1.pdf', pages: 3, excerpt: 'design notes', status: 'ok',
        });
        expect(parsePdfEnvelope(content)).toEqual({
            ref: '.codoc/media/blk-1.pdf', pages: 3, excerpt: 'design notes', status: 'ok',
        });
    });

    it('a JSON object without a ref field is not treated as an envelope', () => {
        expect(parsePdfEnvelope(JSON.stringify({ foo: 'bar' }))).toBeNull();
    });
});
