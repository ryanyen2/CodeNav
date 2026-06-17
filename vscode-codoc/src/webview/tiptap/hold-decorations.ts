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

export const HOLDS_UPDATED = 'codocHoldsUpdated';
const holdKey = new PluginKey('codocHoldDecorations');

export interface HoldDecorationsOptions {
    /** Feature ids awaiting AI realization (the daemon's hold set). */
    getHeld: () => Set<string>;
}

/** Build the badge decorations: one node decoration + one trailing chip widget per
 *  held feature heading. Exported for headless tests (no DOM needed to construct;
 *  the widget DOM factory only runs when the view renders). */
export function buildHoldDecorations(doc: PMModelNode, held: Set<string>): DecorationSet {
    if (!held.size) return DecorationSet.empty;
    const decos: Decoration[] = [];
    doc.forEach((node, pos) => {
        if (node.type.name !== 'featureHeading') return;
        const fid = node.attrs.fid as string | null;
        if (!fid || !held.has(fid)) return;
        decos.push(Decoration.node(pos, pos + node.nodeSize, { class: 'ce-realizing' }));
        decos.push(Decoration.widget(pos + node.nodeSize - 1, () => {
            const chip = document.createElement('span');
            chip.className = 'ce-realize-badge';
            chip.textContent = 'realizing';
            chip.title = 'Awaiting AI realization — your edit is queued for the agent to implement. '
                + 'It clears on its own once the change lands in code.';
            chip.contentEditable = 'false';
            return chip;
        }, { side: 1, key: 'hold-' + fid }));
    });
    return DecorationSet.create(doc, decos);
}

export const HoldDecorations = Extension.create<HoldDecorationsOptions>({
    name: 'holdDecorations',

    addOptions() {
        return { getHeld: () => new Set<string>() };
    },

    addProseMirrorPlugins() {
        const getHeld = (): Set<string> => this.options.getHeld();
        return [
            new Plugin({
                key: holdKey,
                state: {
                    init: (_c, state) => buildHoldDecorations(state.doc, getHeld()),
                    apply: (tr, old, _o, newState) => {
                        if (tr.getMeta(HOLDS_UPDATED) || tr.docChanged) {
                            return buildHoldDecorations(newState.doc, getHeld());
                        }
                        return old.map(tr.mapping, tr.doc);
                    },
                },
                props: { decorations(state) { return holdKey.getState(state); } },
            }),
        ];
    },
});
