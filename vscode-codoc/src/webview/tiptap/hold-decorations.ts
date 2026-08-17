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
import { alignParas, mdDisplayText, paraDisplayText } from './display-text';
import { icon } from '../icons';

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
    /** W3: whether an agent session is live — a queued edit lands on its next turn
     *  automatically, so the nudge wording must not tell the user to run anything. */
    getSessionLive?: () => boolean;
}

/** The contiguous changed region of `current` vs `baseline`, snapped out to whole words
 *  (so we never underline half a word). Returns char offsets into `current`, or null when
 *  they are identical or the change is a pure deletion (nothing to underline in current).
 *  A cheap, deterministic word-aware prefix/suffix trim — it highlights one region from the
 *  first to the last difference, which is exactly enough to "spot what changed". Exported
 *  for the headless test. */
export function changedRange(baseline: string, current: string): { start: number; end: number } | null {
    if (baseline === current) return null;
    const n = current.length, m = baseline.length;
    let p = 0;
    while (p < n && p < m && current[p] === baseline[p]) p++;
    let s = 0;
    while (s < n - p && s < m - p && current[n - 1 - s] === baseline[m - 1 - s]) s++;
    let start = p, end = n - s;
    if (start >= end) return null; // pure deletion — no added/changed span in `current`
    // Snap OUT to word boundaries so half a Latin word is never underlined — but a
    // CJK character is a word of its own (those scripts write without spaces), so
    // expansion stops at one instead of swallowing the paragraph. Before this, a
    // five-word English insertion into a Chinese description underlined the whole
    // node: every char back to the previous space was "the same word".
    const isWord = (c: string): boolean =>
        !!c && !/[\s\u2E80-\u9FFF\uF900-\uFAFF\u3040-\u30FF\uAC00-\uD7AF\u3000-\u303F\uFF00-\uFFEF]/u.test(c);
    while (start > 0 && isWord(current[start - 1])) start--;   // grow left to the word start
    while (end < n && isWord(current[end])) end++;             // grow right to the word end
    return { start, end };
}

/** Build the badge decorations: one node decoration + one trailing chip widget per
 *  held feature heading. The chip carries a ✕ to withdraw the realization when
 *  `onWithdraw` is given. Exported for headless tests (no DOM needed to construct;
 *  the widget DOM factory only runs when the view renders). */
export function buildHoldDecorations(
    doc: PMModelNode, held: Set<string>,
    onWithdraw?: (fid: string) => void,
    detail?: Record<string, HoldDetail>,
    sessionLive = false,
): DecorationSet {
    if (!held.size) return DecorationSet.empty;
    const decos: Decoration[] = [];
    // The doc is a FLAT sequence of featureHeading + paragraph blocks; a feature's
    // description is the paragraph(s) between its heading and the next heading.
    // Group each held heading with its body paragraphs FIRST, so the baseline
    // pairing can align whole paragraph lists (display-text.alignParas) instead
    // of pairing blindly by index — one inserted/removed paragraph must not
    // shift every later underline onto the wrong neighbour.
    interface Para { node: PMModelNode; pos: number }
    interface Group { fid: string; headNode: PMModelNode; headPos: number; paras: Para[] }
    const groups: Group[] = [];
    let g: Group | null = null;
    doc.forEach((node, pos) => {
        if (node.type.name === 'featureHeading') {
            const fid = node.attrs.fid as string | null;
            g = fid && held.has(fid) ? { fid, headNode: node, headPos: pos, paras: [] } : null;
            if (g) groups.push(g);
            return;
        }
        if (g && node.type.name === 'paragraph' && node.content.size > 0) g.paras.push({ node, pos });
    });
    for (const grp of groups) {
        const { fid, headNode: node, headPos: pos } = grp;
        // Heading: a calm PENDING marker — a dashed hollow pulsing dot meaning
        // "this edit is QUEUED for the agent; it is NOT running". It is implemented
        // only when you run /codoc:sync; the active "realizing" shimmer is a separate
        // signal (activity-decorations.ts, driven by the agent mid-sync). Hover shows
        // the plain-language gloss of what's queued; the ✕ withdraws it.
        const gloss = detail?.[fid]?.intent;
        decos.push(Decoration.node(pos, pos + node.nodeSize, { class: 'ce-realizing' }));
        decos.push(Decoration.widget(pos + node.nodeSize - 1, () => {
            const chip = document.createElement('span');
            chip.className = 'ce-pending-badge';
            chip.contentEditable = 'false';
            // Session-aware (W3): with a live agent session the queue drains on its
            // next turn by itself — telling the user to run something would be wrong.
            chip.title = (sessionLive
                ? 'Pending — queued for the agent; it lands on the next agent turn '
                  + '(nothing to run)'
                : 'Pending — this edit is queued for the agent and is implemented when '
                  + 'you run /codoc:sync in a Claude session (or `codoc realize`)')
                + (gloss ? `: the agent will ${gloss}.` : '.');
            // pending = phase 2 → a FILLED DIAMOND glyph (§C.1 "open/fill = phase"): the
            // captured note crystallised into a task and was sent — "◆ queued."
            const dot = document.createElement('span');
            dot.className = 'ce-pending-dot';
            dot.append(icon('diamond-fill'));
            chip.append(dot);
            if (onWithdraw) {
                const x = document.createElement('button');
                x.type = 'button';
                x.className = 'ce-realize-withdraw';
                x.textContent = '✕';
                x.title = 'Withdraw — cancel the queued change (keeps your text)';
                x.addEventListener('mousedown', ev => ev.preventDefault());
                x.addEventListener('click', ev => { ev.preventDefault(); ev.stopPropagation(); onWithdraw(fid); });
                chip.append(x);
            }
            return chip;
        }, { side: 1, key: 'hold-' + fid }));

        // Body blocks → the pending-intent rail + the changed-text underline. The
        // baseline pairs with the current paragraphs by ALIGNMENT (not index) and
        // both sides diff in display space, so chips and inserted paragraphs no
        // longer skew the underline.
        const bl = detail?.[fid]?.baseline ?? '';
        const baseParas = bl
            ? bl.split(/\n+/).map(s => mdDisplayText(s.trim())).filter(Boolean)
            : null;
        const curTexts = grp.paras.map(p => paraDisplayText(p.node));
        const pairing = baseParas ? alignParas(baseParas, curTexts) : null;
        grp.paras.forEach((p, k) => {
            // Hover title = the plain-language gloss of WHAT codoc understood, so the
            // author can confirm recognition (not just that *something* is queued); it
            // falls back to a generic line when only a live intent holds the feature.
            const landing = sessionLive ? 'Lands on the next agent turn.' : 'Awaiting /codoc:sync.';
            const title = gloss
                ? `Queued for the agent: ${gloss}. ${landing}`
                : `Queued for the agent — ${landing.toLowerCase()}`;
            decos.push(Decoration.node(p.pos, p.pos + p.node.nodeSize, { class: 'ce-pending-rail', title }));
            const contentEnd = p.pos + p.node.nodeSize - 1; // last valid inline position
            if (baseParas && pairing) {
                // Underline the text the author actually CHANGED vs the pre-edit baseline —
                // the in-situ "what's pending" highlight. An unpaired (inserted) paragraph
                // diffs against '' and underlines whole, which is the truthful reading.
                const bi = pairing[k];
                const r = changedRange(bi == null ? '' : baseParas[bi], curTexts[k]);
                if (r) {
                    const from = p.pos + 1 + r.start;
                    const to = Math.min(contentEnd, p.pos + 1 + r.end);
                    if (to > from) decos.push(Decoration.inline(from, to, { class: 'ce-intent-underline' }));
                }
            } else {
                // No baseline (ADD / steer / legacy directive) → fall back to underlining the
                // bold "focus" runs (the author's emphasis).
                p.node.forEach((child, offset) => {
                    if (child.isText && child.marks.some(m => m.type.name === 'bold')) {
                        decos.push(Decoration.inline(p.pos + 1 + offset, p.pos + 1 + offset + child.nodeSize, { class: 'ce-intent-underline' }));
                    }
                });
            }
        });
    }
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
