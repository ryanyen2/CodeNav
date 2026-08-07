/**
 * decoration-cost.perf.test.ts — what one keystroke costs.
 *
 * Every decoration plugin in the editor is written the same way:
 *
 *     if (tr.getMeta(X_UPDATED) || tr.docChanged) return buildEverything(newState.doc, …)
 *     return old.map(tr.mapping, tr.doc)
 *
 * `tr.docChanged` is true for every character typed, so the cheap branch — the
 * one ProseMirror provides precisely for this, which repositions the existing
 * decorations through the transaction's mapping — runs only when the document
 * did NOT change, which is when nobody needs it. Typing instead walks the whole
 * document once per plugin and allocates a fresh Decoration for every feature.
 *
 * This measures that directly, so the fix is judged against a number rather than
 * against an argument about it.
 */
import { describe, it, expect } from 'vitest';
import { Node as PMNodeType } from '@tiptap/pm/model';
import { EditorState } from '@tiptap/pm/state';
import { codocSchema } from '../webview/tiptap/schema';
import { buildBlameDecorations } from '../webview/tiptap/blame-decorations';
import { buildHoldDecorations } from '../webview/tiptap/hold-decorations';
import type { PMNode } from '../state/pm-doc';

const schema = codocSchema();

/** A tree the size of a real one: N features, each with two body paragraphs. */
function bigDoc(features: number): PMNode {
    const content: unknown[] = [];
    for (let i = 0; i < features; i++) {
        content.push({
            type: 'featureHeading',
            attrs: { fid: `f-${i}`, localId: `l-${i}`, level: (i % 3) + 1, version: `v${i}` },
            content: [{ type: 'text', text: `Feature number ${i}` }],
        });
        content.push({
            type: 'paragraph', attrs: { ownerId: `f-${i}` },
            content: [{ type: 'text', text: `What feature ${i} is for, in a sentence that runs on a while.` }],
        });
        content.push({
            type: 'paragraph', attrs: { ownerId: `f-${i}` },
            content: [{ type: 'text', text: `How feature ${i} is used, with a little more prose after it.` }],
        });
    }
    return { type: 'doc', content } as unknown as PMNode;
}

function history(features: number): Record<string, { actor: string; at: string; summary: string }[]> {
    const out: Record<string, { actor: string; at: string; summary: string }[]> = {};
    for (let i = 0; i < features; i++) {
        out[`f-${i}`] = [{ actor: i % 2 ? 'human' : 'loop', at: '1700000000000-0', summary: 'wrote it' }];
    }
    return out;
}

/** The hot path, honestly framed.
 *
 * Most layers early-return `DecorationSet.empty` when they have nothing to show
 * (History off, no blocks, no threads), so they cost nothing while you type. The
 * layer that is active *precisely* while you type is the hold/pending one: you
 * edit a feature, the edit becomes a pending draft, and from then until hand-off
 * `held.size > 0` on every keystroke. So this measures one held feature in a doc
 * of N — the cost of typing a character into a tree you are already editing.
 */
function measureHold(features: number): { ms: number; blocks: number } {
    const doc = PMNodeType.fromJSON(schema, bigDoc(features) as never);
    const state = EditorState.create({ schema, doc });
    const held = new Set(['f-0']);          // exactly one feature is being edited
    const detail = { 'f-0': { baseline: 'What feature 0 is for, in a sentence that runs on a while.' } };

    const tr = state.tr.insertText('x', 2);
    const REPS = 200;
    for (let i = 0; i < 40; i++) buildHoldDecorations(tr.doc, held, undefined, detail as never, true);

    const t0 = performance.now();
    for (let i = 0; i < REPS; i++) buildHoldDecorations(tr.doc, held, undefined, detail as never, true);
    return { ms: (performance.now() - t0) / REPS, blocks: tr.doc.childCount };
}

describe('the per-keystroke cost of a decoration layer', () => {
    it('a layer showing ONE feature still walks the whole document', () => {
        const rows = [50, 200, 600].map(n => ({ n, ...measureHold(n) }));

        for (const r of rows) {
            // eslint-disable-next-line no-console
            console.log(`[perf] hold layer, 1 held feature of ${String(r.n).padStart(3)} ` +
                        `(${r.blocks} blocks) — ${r.ms.toFixed(3)}ms per keystroke`);
        }

        // The shape that matters: the work is identical in every case — decorate
        // one feature — yet it costs more on a bigger tree, because finding that
        // feature rescans every block. Cost should track the EDIT, not the tree.
        const small = rows[0], large = rows[rows.length - 1];
        expect(large.ms).toBeGreaterThan(small.ms * 3);
    }, 30_000);

    it('a structural layer rebuilds every feature to redraw none of them', () => {
        const n = 300;
        const doc = PMNodeType.fromJSON(schema, bigDoc(n) as never);
        const state = EditorState.create({ schema, doc });
        const hist = history(n) as never;
        const built = buildBlameDecorations(state.doc, true, hist, 1_700_000_100_000);
        expect(built.find().length).toBeGreaterThan(n);

        const tr = state.tr.insertText('x', 2);
        const REPS = 200;
        for (let i = 0; i < 40; i++) { buildBlameDecorations(tr.doc, true, hist, 0); built.map(tr.mapping, tr.doc); }

        let t0 = performance.now();
        for (let i = 0; i < REPS; i++) buildBlameDecorations(tr.doc, true, hist, 1_700_000_100_000);
        const rebuildMs = (performance.now() - t0) / REPS;

        t0 = performance.now();
        for (let i = 0; i < REPS; i++) built.map(tr.mapping, tr.doc);
        const mapMs = (performance.now() - t0) / REPS;

        // eslint-disable-next-line no-console
        console.log(`[perf] blame layer, ${n} features — rebuild ${rebuildMs.toFixed(3)}ms ` +
                    `vs map ${mapMs.toFixed(3)}ms per keystroke (${(rebuildMs / mapMs).toFixed(1)}×)`);

        // Typing a character changes no blame fact, so mapping the existing
        // decorations is not merely faster — it is the correct answer.
        expect(rebuildMs).toBeGreaterThan(mapMs);
    }, 30_000);
});
