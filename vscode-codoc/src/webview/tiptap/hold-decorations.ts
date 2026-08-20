/**
 * hold-decorations.ts — the "queued for the agent" CHIP. Just the chip.
 *
 * A code-implying commit makes Loop B mint a realize directive, which lands the feature
 * in the doc-wins hold set (`sidecar.holds`; codoc/loop/edits.py:hold_set). The host
 * forwards that set as `payload.awaitingAI`, and this plugin hangs one quiet chip off
 * each held heading: what is queued, and the ✕ that withdraws it. Nothing here
 * classifies anything — the chip is a pure projection of the daemon's hold set.
 *
 * ## What was deleted, and why it is not missed
 *
 * This module used to draw a whole review surface of its own beside that chip: a margin
 * RAIL down every held paragraph and an UNDERLINE over the text the author had changed,
 * computed by its own `changedRange` word diff against `hold_detail.baseline`, all in
 * the sage "staged" hue.
 *
 * That underline is the human channel of `state/settlement.ts`, drawn a second time, in
 * a different colour, from a different baseline. Which meant:
 *
 *   • The author's own unlanded edit wore TWO marks — blue ink from the settlement
 *     layer and a green dotted underline from here — that agreed only by luck. They
 *     used different diffs (a contiguous word-snapped region vs per-sentence spans),
 *     so on any real edit they disagreed about WHERE the change was.
 *   • Green is the code channel's hue. Painting the author's pending words in it said
 *     "the codebase added this" about text the codebase had never seen.
 *   • With NO baseline the underline fell back to marking the author's **bold** runs —
 *     so a COMMENT, which queues a steer directive and carries no baseline at all, lit
 *     up random emphasis across the feature as though it were a pending change. That is
 *     the "why is my comment drawing green edits" the surface could not explain,
 *     because there was nothing to explain: it was marking text nobody had touched.
 *
 * The settlement model answers the same question once, from the projection the daemon
 * actually wrote, in the channel that owns it. So the diff half is gone and the ACTION
 * half — the one thing that was always this module's own — stays. The same split
 * `auto-edit-decorations` made when its diff moved out, for the same reason.
 *
 * ## The chip's ink is the ink of whoever is waiting
 *
 * A hold is "applied to the store, not yet in the code", and two different parties can
 * put a feature there: the author committed an edit, or the author accepted an agent's
 * plan. Those are the human and plan channels, and the chip takes the channel's colour
 * (`hold_detail.origin` → `.ce-pending-dot.human` / `.plan`) so the margin agrees with
 * the prose it sits beside instead of introducing a third hue for the same fact.
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { Node as PMModelNode } from '@tiptap/pm/model';
import type { HoldDetail } from '../../state/bindings-model';
import { icon } from '../icons';

export const HOLDS_UPDATED = 'codocHoldDecorationsUpdated';
const holdKey = new PluginKey('codocHoldDecorations');

export interface HoldDecorationsOptions {
    /** Feature ids awaiting AI realization (the daemon's hold set). */
    getHeld: () => Set<string>;
    /** Per-held-feature {kind, intent, origin} (a subset of getHeld) — the queued
     *  directive's plain-language gloss and whose words it is holding. Optional. */
    getDetail?: () => Record<string, HoldDetail>;
    /** Withdraw the queued realization for a feature (U6) — cancels the directive,
     *  keeps the prose. Wired to the ✕ on the chip; omitted ⇒ no ✕ (e.g. tests). */
    onWithdraw?: (fid: string) => void;
    /** W3: whether an agent session is live — a queued edit lands on its next turn
     *  automatically, so the nudge wording must not tell the user to run anything. */
    getSessionLive?: () => boolean;
}

/** The chip's hover sentence: what is queued, who it came from, and what makes it move.
 *  Exported so the test can read the wording rather than the DOM. */
export function pendingTitle(d: HoldDetail | undefined, sessionLive: boolean): string {
    const whose = d?.origin === 'plan'
        ? 'The plan you accepted is queued for the agent'
        : 'Your edit is queued for the agent';
    const landing = sessionLive
        ? ' — it lands on the next agent turn (nothing to run)'
        : ' — it is implemented when you run /codoc:sync in a Claude session '
          + '(or `codoc realize`)';
    return whose + landing + (d?.intent ? `: the agent will ${d.intent}.` : '.');
}

/**
 * One chip per held feature heading, carrying the ✕ that withdraws it.
 *
 * Exported for headless tests (no DOM needed to construct; the widget DOM factory only
 * runs when the view renders).
 */
export function buildHoldDecorations(
    doc: PMModelNode, held: Set<string>,
    onWithdraw?: (fid: string) => void,
    detail?: Record<string, HoldDetail>,
    sessionLive = false,
): DecorationSet {
    if (!held.size) return DecorationSet.empty;
    const decos: Decoration[] = [];
    doc.forEach((node, pos) => {
        if (node.type.name !== 'featureHeading') return;
        const fid = node.attrs.fid as string | null;
        if (!fid || !held.has(fid)) return;
        const d = detail?.[fid];
        const title = pendingTitle(d, sessionLive);
        // A styling hook on the heading; the chip carries the signal.
        decos.push(Decoration.node(pos, pos + node.nodeSize, { class: 'ce-realizing' }));
        decos.push(Decoration.widget(pos + node.nodeSize - 1, () => {
            const chip = document.createElement('span');
            chip.className = 'ce-pending-badge';
            chip.contentEditable = 'false';
            chip.title = title;
            // A FILLED DIAMOND: queued, not running. Inked by the channel that is
            // waiting — the author's blue, or the plan's gray.
            const dot = document.createElement('span');
            dot.className = 'ce-pending-dot ' + (d?.origin === 'plan' ? 'plan' : 'human');
            dot.append(icon('diamond-fill'));
            chip.append(dot);
            if (onWithdraw) {
                const x = document.createElement('button');
                x.type = 'button';
                x.className = 'ce-realize-withdraw';
                x.textContent = '✕';
                // The prose stays either way; whose prose it is depends on who queued
                // it, and calling an agent's accepted plan "your text" is the same slip
                // the ink used to make.
                x.title = d?.origin === 'plan'
                    ? 'Withdraw — cancel the queued build (the accepted wording stays)'
                    : 'Withdraw — cancel the queued change (keeps your text)';
                x.addEventListener('mousedown', ev => ev.preventDefault());
                x.addEventListener('click', ev => { ev.preventDefault(); ev.stopPropagation(); onWithdraw(fid); });
                chip.append(x);
            }
            return chip;
        }, { side: 1, key: `hold-${fid}-${d?.origin ?? 'human'}` }));
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
        const live = (): boolean => this.options.getSessionLive?.() ?? false;
        return [
            new Plugin({
                key: holdKey,
                state: {
                    init: (_c, state) => buildHoldDecorations(state.doc, getHeld(), onWithdraw, getDetail(), live()),
                    apply: (tr, old, _o, newState) => {
                        if (tr.getMeta(HOLDS_UPDATED) || tr.docChanged) {
                            return buildHoldDecorations(newState.doc, getHeld(), onWithdraw, getDetail(), live());
                        }
                        return old.map(tr.mapping, tr.doc);
                    },
                },
                props: { decorations(state) { return holdKey.getState(state); } },
            }),
        ];
    },
});
