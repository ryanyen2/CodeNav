/**
 * ask-decorations.ts — the `/codoc:ask` walkthrough, drawn in place.
 *
 * The overlay is deliberately the quietest layer in the editor. It carries NO
 * hue: every other decoration's colour means something in the lifecycle grammar
 * (blue = captured, amber = deletion, sage = staged, the proposal hues), and a
 * walkthrough is not a lifecycle state — it is a reading order somebody asked
 * for. So it says what it is with structure instead: a small ordinal chip beside
 * the heading, the procedure stage named once above its first step, one line of
 * note under the title, and a graphite wash on the sentence being pointed at.
 *
 * Four decorations per step, at most:
 *   group   a widget BEFORE the heading, only on the step that opens a run
 *   chip    a node decoration on the heading carrying `data-ask-label` (CSS ::before)
 *   note    a widget AFTER the heading — the note text + an optional jump to code
 *   quote   an inline wash over the span the note is about
 *
 * It never edits the doc, and it never blocks one. When a feature is mid-change
 * — the agent rewriting it, an unreviewed loop edit sitting on it, the author
 * typing in it — the quote wash is SUPPRESSED while the chip and note stay: two
 * different diffs washing the same words is the one thing that would make this
 * layer expensive to look at.
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { Node as PMModelNode } from '@tiptap/pm/model';
import type { AskStep, AskWalkthrough } from '../../state/ask-model';
import { findQuoteRange, groupOpeners, stepsByFid } from '../../state/ask-model';
import { paraDisplayText } from './display-text';

export const ASK_UPDATED = 'codocAskUpdated';
const askKey = new PluginKey('codocAsk');

export interface AskDecorationsOptions {
    getAsk: () => AskWalkthrough | null;
    /** The feature id of the step the reader is currently on ('' for none). */
    getCurrent: () => string;
    /** Features whose prose is already carrying a diff — the chip stays, the
     *  quote wash is dropped so two highlights never overlap the same words. */
    getSuppressed: () => Set<string>;
}

/** A textblock and where it starts. Char offset `i` of `paraDisplayText(node)`
 *  maps to document position `pos + 1 + i` (the display-space contract). */
interface Block { node: PMModelNode; pos: number }

/**
 * Every block a feature's quote may live in: its heading, then the paragraphs up
 * to the next heading. Paragraph-wise because a decoration lives inside one
 * block — which is also why the writer refuses a quote that straddles two.
 * Pure; unit-tested directly.
 */
export function featureQuoteBlocks(doc: PMModelNode): Map<string, Block[]> {
    const out = new Map<string, Block[]>();
    let current: Block[] | null = null;
    doc.forEach((node, pos) => {
        if (node.type.name === 'featureHeading') {
            const fid = (node.attrs.fid as string) || '';
            if (!fid) { current = null; return; }
            current = [{ node, pos }];
            out.set(fid, current);
            return;
        }
        if (current && node.isTextblock) current.push({ node, pos });
    });
    return out;
}

/**
 * The document range of `quote` within a feature's blocks, or null when the
 * prose has moved on. Searches block by block and takes the first hit — a quote
 * repeated in two paragraphs highlights the earlier one, which is the one the
 * reader reaches first.
 */
export function quoteRange(blocks: Block[], quote: string): { from: number; to: number } | null {
    for (const b of blocks) {
        const hit = findQuoteRange(paraDisplayText(b.node), quote);
        if (hit) return { from: b.pos + 1 + hit[0], to: b.pos + 1 + hit[1] };
    }
    return null;
}

function groupWidget(label: string): HTMLElement {
    const el = document.createElement('div');
    el.className = 'ce-ask-group';
    el.contentEditable = 'false';
    el.textContent = label;
    return el;
}

function build(
    doc: PMModelNode,
    walk: AskWalkthrough | null,
    current: string,
    suppressed: Set<string>,
): DecorationSet {
    if (!walk) return DecorationSet.empty;
    const byFid = stepsByFid(walk);
    const openers = groupOpeners(walk);
    const blocks = featureQuoteBlocks(doc);
    const decos: Decoration[] = [];

    doc.forEach((node, pos) => {
        if (node.type.name !== 'featureHeading') return;
        const fid = (node.attrs.fid as string) || '';
        const step = fid ? byFid.get(fid) : undefined;
        if (!step) return;

        const group = openers.get(fid);
        if (group) {
            decos.push(Decoration.widget(pos, () => groupWidget(group),
                                         { side: -1, key: `ask-g-${fid}:${group}` }));
        }

        const cls = current === fid ? 'ce-ask-head ce-ask-here' : 'ce-ask-head';
        decos.push(Decoration.node(pos, pos + node.nodeSize,
                                   { class: cls, 'data-ask-label': step.label }));

        // The note is NOT an inline widget any more. A widget here (between the
        // heading and the description) inserted a line and pushed the prose down —
        // a walkthrough that can stay up for a whole reading session should not
        // reflow the document it is a reading order OVER. It renders instead as a
        // card in the right whitespace, anchored to the quote, like a comment
        // (whole-doc-editor.ts:renderAskMargin). Only the highlight stays inline.

        if (step.quote && !suppressed.has(fid)) {
            const range = quoteRange(blocks.get(fid) ?? [], step.quote);
            if (range) {
                const qcls = current === fid ? 'ce-ask-quote ce-ask-quote-here' : 'ce-ask-quote';
                decos.push(Decoration.inline(range.from, range.to, { class: qcls }));
            }
        }
    });
    return DecorationSet.create(doc, decos);
}

export const AskDecorations = Extension.create<AskDecorationsOptions>({
    name: 'askDecorations',

    addOptions() {
        return {
            getAsk: () => null,
            getCurrent: () => '',
            getSuppressed: () => new Set<string>(),
        };
    },

    addProseMirrorPlugins() {
        const opts = (): AskDecorationsOptions => this.options;
        return [
            new Plugin({
                key: askKey,
                state: {
                    init: (_c, state) => {
                        const o = opts();
                        return build(state.doc, o.getAsk(), o.getCurrent(), o.getSuppressed());
                    },
                    apply: (tr, old, _o, newState) => {
                        // Text-keyed, like the other quote/word layers: the wash is
                        // located by SEARCHING the prose, so a keystroke really does
                        // invalidate it and decoration-policy's structure test would
                        // leave the highlight behind on the old words.
                        if (tr.getMeta(ASK_UPDATED) || tr.docChanged) {
                            const o = opts();
                            return build(newState.doc, o.getAsk(), o.getCurrent(), o.getSuppressed());
                        }
                        return old.map(tr.mapping, tr.doc);
                    },
                },
                props: { decorations(state) { return askKey.getState(state); } },
            }),
        ];
    },
});
