/**
 * hold-decorations.ts — the calm "being realized" badge in the doc pane (U3).
 *
 * In the single-surface model the human just edits; the daemon's `classify.py`
 * decides per-edit whether it implies code. A code-implying commit makes Loop B
 * mint a realize directive, which lands the feature in the doc-wins hold set
 * (`sidecar.holds`; see codoc/loop/edits.py:hold_set). The host forwards that set
 * as `payload.awaitingAI`; this plugin decorates each held feature heading with a
 * quiet badge meaning "code is catching up" — it clears on its own when the agent
 * realizes the change (the feature leaves the hold set). No client-side
 * classification: the badge is a pure projection of the daemon's hold set.
 *
 * This is the DURABLE state axis. It composes with activity-decorations.ts (the
 * transient editing/reflecting shimmer while the agent is actively on the
 * feature): a held feature wears the badge throughout, and additionally shimmers
 * while the agent is mid-edit. Calm + low-motion by design (CSS honors
 * prefers-reduced-motion); colour stays reserved for direction, so the badge is
 * a neutral chip, not a hue.
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { Node as PMModelNode } from '@tiptap/pm/model';
import type { HoldDetail } from '../../state/bindings-model';

export const HOLDS_UPDATED = 'codocHoldsUpdated';
const holdKey = new PluginKey('codocHoldDecorations');

export interface HoldDecorationsOptions {
    /** Feature ids awaiting AI realization (the daemon's hold set). */
    getHeld: () => Set<string>;
    /** Per-held-feature {kind, intent} (a subset of getHeld) — the queued directive's
     *  plain-language gloss, used as the pending rail's hover title. Optional. */
    getDetail?: () => Record<string, HoldDetail>;
    /** Withdraw the queued realization for a feature (U6) — cancels the directive,
     *  keeps the prose. Wired to the ✕ on the badge; omitted ⇒ no ✕ (e.g. tests). */
    onWithdraw?: (fid: string) => void;
}

/** Build the badge decorations: one node decoration + one trailing chip widget per
 *  held feature heading. The chip carries a ✕ to withdraw the realization when
 *  `onWithdraw` is given. Exported for headless tests (no DOM needed to construct;
 *  the widget DOM factory only runs when the view renders). */
export function buildHoldDecorations(
    doc: PMModelNode, held: Set<string>,
    onWithdraw?: (fid: string) => void,
    detail?: Record<string, HoldDetail>,
): DecorationSet {
    if (!held.size) return DecorationSet.empty;
    const decos: Decoration[] = [];
    // The doc is a FLAT sequence of featureHeading + paragraph blocks; a feature's
    // description is the paragraph(s) between its heading and the next heading. We walk
    // the top-level nodes statefully so a held heading's body blocks get the pending
    // rail + underline, not just the heading.
    let activeFid: string | null = null;
    doc.forEach((node, pos) => {
        if (node.type.name === 'featureHeading') {
            const fid = node.attrs.fid as string | null;
            if (!fid || !held.has(fid)) { activeFid = null; return; }
            activeFid = fid;
            // Heading: the calm node tint + the trailing "realizing" chip (the durable
            // "live" cue; the substance lives on the rail's hover + the underline).
            decos.push(Decoration.node(pos, pos + node.nodeSize, { class: 'ce-realizing' }));
            decos.push(Decoration.widget(pos + node.nodeSize - 1, () => {
                const chip = document.createElement('span');
                chip.className = 'ce-realize-badge';
                chip.contentEditable = 'false';
                chip.title = 'Awaiting AI realization — your edit is queued for the agent to implement. '
                    + 'It clears on its own once the change lands in code.';
                const label = document.createElement('span');
                label.className = 'ce-realize-label';
                label.textContent = 'realizing';
                chip.append(label);
                if (onWithdraw) {
                    const x = document.createElement('button');
                    x.type = 'button';
                    x.className = 'ce-realize-withdraw';
                    x.textContent = '✕';
                    x.title = 'Withdraw — cancel the AI realization (keeps your text)';
                    x.addEventListener('mousedown', ev => ev.preventDefault());
                    x.addEventListener('click', ev => { ev.preventDefault(); ev.stopPropagation(); onWithdraw(fid); });
                    chip.append(x);
                }
                return chip;
            }, { side: 1, key: 'hold-' + fid }));
            return;
        }
        // A body block of the current held feature → the pending-intent rail. Its hover
        // title is the plain-language gloss of WHAT codoc understood, so the author can
        // confirm recognition (not just that *something* is queued); it falls back to a
        // generic line when only a live intent holds the feature (no queued directive).
        if (activeFid && node.type.name === 'paragraph' && node.content.size > 0) {
            const gloss = detail?.[activeFid]?.intent;
            const title = gloss
                ? `Queued for the agent: ${gloss}. Awaiting /codoc:sync.`
                : 'Queued for the agent — awaiting /codoc:sync.';
            decos.push(Decoration.node(pos, pos + node.nodeSize, { class: 'ce-pending-rail', title }));
            // Underline the bold runs (the author's emphasised "focus" spans) — the exact
            // text codoc treats as the actionable request, so a misread is visible at the
            // word. No bold ⇒ the rail alone marks the block.
            node.forEach((child, offset) => {
                if (child.isText && child.marks.some(m => m.type.name === 'bold')) {
                    const from = pos + 1 + offset;
                    decos.push(Decoration.inline(from, from + child.nodeSize, { class: 'ce-intent-underline' }));
                }
            });
        }
    });
    return DecorationSet.create(doc, decos);
}

export const HoldDecorations = Extension.create<HoldDecorationsOptions>({
    name: 'holdDecorations',

    addOptions() {
        return { getHeld: () => new Set<string>(), getDetail: () => ({}) };
    },

    addProseMirrorPlugins() {
        const getHeld = (): Set<string> => this.options.getHeld();
        const getDetail = (): Record<string, HoldDetail> => this.options.getDetail?.() ?? {};
        const onWithdraw = this.options.onWithdraw;
        return [
            new Plugin({
                key: holdKey,
                state: {
                    init: (_c, state) => buildHoldDecorations(state.doc, getHeld(), onWithdraw, getDetail()),
                    apply: (tr, old, _o, newState) => {
                        if (tr.getMeta(HOLDS_UPDATED) || tr.docChanged) {
                            return buildHoldDecorations(newState.doc, getHeld(), onWithdraw, getDetail());
                        }
                        return old.map(tr.mapping, tr.doc);
                    },
                },
                props: { decorations(state) { return holdKey.getState(state); } },
            }),
        ];
    },
});
