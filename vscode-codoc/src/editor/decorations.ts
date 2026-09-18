/**
 * decorations.ts — paints and phases become ProseMirror decorations.
 *
 * Paints are computed in scalar offsets per block (calc/claims.ts); here they
 * are placed at document positions by walking each block's inline content once,
 * converting scalar offsets to UTF-16 positions. Removed text (a red ground
 * claim) is a zero-width widget carrying the words the code dropped. The
 * margin marker is a widget at the heading's start carrying the phase class.
 */
import type { Node as PMNode } from '@tiptap/pm/model';
import { Decoration, DecorationSet } from '@tiptap/pm/view';

import { PHASE_CHANNEL, PHASE_LABEL, type Paint } from '../calc/claims';
import type { Phase } from '../calc/projection';

/** UTF-16 offset inside a textblock for a scalar offset (hard breaks count 1). */
export function unitOffset(block: PMNode, scalar: number): number {
    let units = 0;
    let seen = 0;
    for (let i = 0; i < block.childCount && seen < scalar; i++) {
        const child = block.child(i);
        if (child.isText) {
            const cps = Array.from(child.text ?? '');
            const take = Math.min(cps.length, scalar - seen);
            units += cps.slice(0, take).join('').length;
            seen += take;
        } else {
            units += child.nodeSize;
            seen += 1;
        }
    }
    return units;
}

export function buildDecorations(doc: PMNode, paints: Map<string, Paint[]>, phases: Record<string, Phase>,
                                 realizing: ReadonlySet<string>, only?: ReadonlySet<string>): DecorationSet {
    return DecorationSet.create(doc, decorationsFor(doc, paints, phases, realizing, only));
}


/** The decorations for ``only`` blocks, or for every block. Separate from the
 *  set so a redraw can replace one block's decorations in the set it already
 *  has: building three thousand of them on every keystroke, ack and adopt was
 *  most of what typing in a large tree cost. */
export function decorationsFor(doc: PMNode, paints: Map<string, Paint[]>, phases: Record<string, Phase>,
                               realizing: ReadonlySet<string>, only?: ReadonlySet<string>): Decoration[] {
    const decos: Decoration[] = [];
    doc.forEach((block, pos) => {
        const bid = block.attrs.bid as string | null;
        if (only && (!bid || !only.has(bid))) return;
        if (!bid) return;
        if (block.type.name === 'heading') {
            const phase: Phase = realizing.has(bid) ? 'realizing' : (phases[bid] ?? 'synced');
            if (phase !== 'synced') {
                decos.push(Decoration.widget(pos + 1, () => {
                    const el = document.createElement('span');
                    el.className = `codoc-marker codoc-marker-${phase} codoc-ch-${PHASE_CHANNEL[phase]}`;
                    el.title = PHASE_LABEL[phase];
                    el.setAttribute('aria-label', PHASE_LABEL[phase]);
                    return el;
                }, { side: -1, key: `marker:${bid}:${phase}` }));
            }
        }
        for (const p of paints.get(bid) ?? []) {
            const from = pos + 1 + unitOffset(block, p.start);
            if (p.cls === 'code-removed') {
                decos.push(Decoration.widget(from, () => {
                    const el = document.createElement('span');
                    el.className = 'codoc-paint codoc-code-removed';
                    el.textContent = p.text ?? '';
                    el.title = p.title;
                    el.setAttribute('aria-description', p.title);
                    return el;
                }, { side: 0, key: `removed:${bid}:${p.start}:${p.text?.length ?? 0}` }));
                continue;
            }
            const to = pos + 1 + unitOffset(block, p.end);
            if (to > from) {
                decos.push(Decoration.inline(from, to, { class: `codoc-paint codoc-${p.cls}`, title: p.title, 'aria-description': p.title }));
            }
        }
    });
    return decos;
}


/** Where each of ``bids`` sits in the document, as a `[from, to)` range. */
export function blockRanges(doc: PMNode, bids: ReadonlySet<string>): Array<{ from: number; to: number }> {
    const out: Array<{ from: number; to: number }> = [];
    doc.forEach((block, pos) => {
        if (bids.has(block.attrs.bid as string)) out.push({ from: pos, to: pos + block.nodeSize });
    });
    return out;
}
