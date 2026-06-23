/**
 * block-decorations.test.ts — the v6 block-edit round-trip logic (DOM-free).
 *
 * The widget rendering itself is DOM (verified visually in VS Code), but the
 * decision of WHAT to send the host on an edit is pure and pinned here: a content
 * change posts a stable-id edit with prev/new content; a revert-to-baseline posts
 * nothing; only LOWER-capable text media (diagram/latex) are editable.
 */
import { describe, it, expect } from 'vitest';
import { blockEditMsg, EDITABLE_KINDS } from '../webview/tiptap/block-decorations';
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
