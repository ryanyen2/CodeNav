/**
 * blame-decorations.ts — the History (blame) stance (W2).
 *
 * When the stance is on, every feature shows WHO last changed it, WHEN, and (on
 * hover) WHY — the "traces of history" a reader needs to trust the prose. The
 * data is the daemon's bounded per-feature history slice (sidecar
 * `feature_history`), forwarded as `payload.history`.
 *
 * Two decorations per feature, both gated on the `blame` pref so the resting
 * editor stays clean:
 *   - a trailing heading widget "You edited · 3h ago", tinted by author ROLE
 *     (human / agent / codoc) — colour here encodes authorship, not direction,
 *     so it uses its own muted role hues, never the direction palette.
 *   - a left attribution rail down the feature's description blocks in the same
 *     role hue, so a glance down the doc shows human vs agent authorship.
 * The widget's `title` carries the full timeline (who · when · why per entry), so
 * hovering reveals the trace without a popover — and CLICKING it opens the shared
 * provenance card (W8), which carries the rest of the chain the ledger keeps: the
 * directive this change implements, the prompt someone typed to ask for it, the session
 * they typed it in, and a way into the code the agent actually wrote. A tooltip could
 * only ever say who and when; "what did it change?" has a diff for an answer.
 *
 * Pure builder (unit-tested); the plugin rebuilds on a BLAME_UPDATED meta or a
 * doc change, else maps positions forward like every other decoration plugin.
 */
import { nextDecorations } from './decoration-policy';
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { Node as PMModelNode } from '@tiptap/pm/model';
import type { HistoryEntry } from '../../state/bindings-model';
import {
    actorLabel, actorRole, blameSummaryFrom, kindPhrase, relativeTime,
} from '../../state/blame-model';
import { featureTrace, traceBaseSha } from '../../state/provenance';
import { blameDescription, significantSpans } from '../../state/inline-blame';
import type { RevisionDirective, Timeline } from '../../state/revision-model';
import { ATOM_CHAR, paraDisplayText } from './display-text';
import { closeProvenanceCard, isProvenanceCardOpen, showProvenanceCard } from '../provenance-card';

export const BLAME_UPDATED = 'codocBlameUpdated';
const blameKey = new PluginKey('codocBlameDecorations');

export interface BlameDecorationsOptions {
    /** Blame stance on? Off ⇒ no decorations at all. */
    getEnabled: () => boolean;
    /** Per-feature history (newest first), keyed by fid. */
    getHistory: () => Record<string, HistoryEntry[]>;
    /** W8: the directives the history cites, so the label can answer WHY and not only
     *  who — the author's prompt, the session, the commit the code work started from. */
    getDirectives?: () => Record<string, RevisionDirective>;
    /** W9: the revision window, replayed for per-span authorship. */
    getTimeline?: () => Timeline | undefined;
    /** W8: the files bound to a feature, for the card's code-diff action. */
    getFiles?: (fid: string) => string[];
    /** W8: open the code an agent wrote for this feature, against `baseSha`. */
    onOpenDiff?: (fid: string, baseSha: string, files: string[]) => void;
    /** W8: open the coding session a change was asked for in. */
    onOpenSession?: (sessionId: string) => void;
    /** Injectable clock for deterministic relative-time in tests. */
    now?: () => number;
}

/** The multi-line hover trace for a feature: "You edited · 3h ago — clarified
 *  sessions" per entry, newest first. */
export function blameTooltip(history: HistoryEntry[], nowMs: number): string {
    return history.map(e => {
        const when = relativeTime(e.at, nowMs);
        const who = actorLabel(e.actor);
        const what = kindPhrase(e.kind);
        const why = e.rationale ? ` — ${e.rationale}` : '';
        return `${who} ${what}${when ? ' · ' + when : ''}${why}`;
    }).join('\n');
}

/** What clicking a blame label does. Passed through the builder so the widget stays a
 *  pure factory and the plugin owns the wiring. */
export interface BlameCardHandlers {
    /** The revision window, for per-span authorship (W9). Absent ⇒ no inline blame. */
    timeline?: Timeline;
    directives: Record<string, RevisionDirective>;
    files: (fid: string) => string[];
    openDiff?: (fid: string, baseSha: string, files: string[]) => void;
    openSession?: (sessionId: string) => void;
}

export function buildBlameDecorations(
    doc: PMModelNode,
    enabled: boolean,
    history: Record<string, HistoryEntry[]>,
    nowMs: number,
    handlers?: BlameCardHandlers,
): DecorationSet {
    if (!enabled) return DecorationSet.empty;
    const decos: Decoration[] = [];
    const paras: { fid: string; node: PMModelNode; pos: number }[] = [];
    // Group each feature heading with its following body paragraphs so the rail
    // spans the whole feature, matching hold/captured decorations' geometry.
    let activeFid: string | null = null;
    doc.forEach((node, pos) => {
        if (node.type.name === 'featureHeading') {
            const fid = node.attrs.fid as string | null;
            const hist = fid ? history[fid] : undefined;
            if (!fid || !hist || !hist.length) { activeFid = null; return; }
            activeFid = fid;
            const summary = blameSummaryFrom(hist, nowMs);
            decos.push(Decoration.node(pos, pos + node.nodeSize, { class: `ce-blame ce-blame-${summary.role}` }));
            decos.push(Decoration.widget(pos + node.nodeSize - 1, () => {
                // A BUTTON, not a span with a `title` (W8). The native tooltip could show
                // who and when and could never be acted on — and "what did the agent
                // actually write?" is a question whose answer is a diff, not a sentence.
                const chip = document.createElement('button');
                chip.type = 'button';
                chip.className = `ce-blame-who ce-blame-${summary.role}`;
                chip.contentEditable = 'false';
                chip.textContent = summary.line;
                chip.title = handlers
                    ? `${blameTooltip(hist, nowMs)}\n\nClick for why — and the code it changed.`
                    : blameTooltip(hist, nowMs);
                if (handlers) {
                    // Never steal the caret: a reader checking provenance mid-sentence
                    // must come back to where they were typing.
                    chip.addEventListener('mousedown', ev => ev.preventDefault());
                    chip.addEventListener('click', ev => {
                        ev.preventDefault();
                        ev.stopPropagation();
                        if (isProvenanceCardOpen()) { closeProvenanceCard(); return; }
                        const files = handlers.files(fid);
                        const baseSha = traceBaseSha(hist, handlers.directives);
                        const session = hist
                            .map(e => (e.caused_by ? handlers.directives[e.caused_by] : undefined))
                            .find(d => d?.session_id)?.session_id ?? '';
                        const actions = [];
                        if (baseSha && files.length && handlers.openDiff) {
                            actions.push({
                                label: `Open the code diff (${files.length})`,
                                title: 'Compare this feature\u2019s code against the commit '
                                    + 'the change that produced this prose started from',
                                run: () => handlers.openDiff?.(fid, baseSha, files),
                            });
                        }
                        if (session && handlers.openSession) {
                            actions.push({
                                label: 'Open the conversation',
                                title: 'The coding session this change was asked for in',
                                run: () => handlers.openSession?.(session),
                            });
                        }
                        showProvenanceCard({
                            anchor: chip,
                            head: summary.line,
                            rows: featureTrace(hist, handlers.directives, nowMs),
                            actions,
                        });
                    });
                }
                return chip;
            }, { side: 1, key: `blame-${fid}` }));
            return;
        }
        if (activeFid && node.type.name === 'paragraph') {
            paras.push({ fid: activeFid, node, pos });
        }
    });
    // Inline authorship — the point of the stance (W9). The node-level rail this replaces
    // said "somebody edited this feature", which is not a question anyone asks: a feature
    // is five paragraphs written by three parties in turn, and the reader is deciding
    // whether to trust ONE claim. It was also one of four rails competing for the same
    // `::before`, so turning History on used to erase the "recorded" and "queued" cues.
    if (handlers?.timeline) {
        for (const group of groupParagraphs(paras)) {
            decos.push(...blameSpansFor(group, handlers.timeline));
        }
    }
    return DecorationSet.create(doc, decos);
}

/** Paragraphs of one feature, in document order. */
function groupParagraphs(
    paras: { fid: string; node: PMModelNode; pos: number }[],
): { fid: string; blocks: { node: PMModelNode; pos: number }[] }[] {
    const out: { fid: string; blocks: { node: PMModelNode; pos: number }[] }[] = [];
    for (const p of paras) {
        const last = out[out.length - 1];
        if (last && last.fid === p.fid) last.blocks.push(p);
        else out.push({ fid: p.fid, blocks: [{ node: p.node, pos: p.pos }] });
    }
    return out;
}

/** The paragraph separator in DISPLAY space: `mdDisplayText` maps each newline of a
 *  stored description to one atom, so a paragraph break is two. Joining the live
 *  paragraphs the same way is what makes an offset computed against the stored text land
 *  on the right character of the rendered one. */
const PARA_SEP = ATOM_CHAR.repeat(2);

function blameSpansFor(
    group: { fid: string; blocks: { node: PMModelNode; pos: number }[] },
    timeline: Timeline,
): Decoration[] {
    const texts = group.blocks.map(b => paraDisplayText(b.node));
    const joined = texts.join(PARA_SEP);
    if (!joined.trim()) return [];
    const spans = significantSpans(blameDescription(joined, timeline, group.fid), joined.length);
    if (!spans.length) return [];

    // joined-offset → (block, offset within it). The +1 is the display-space contract:
    // a textblock's content starts one position after the block itself.
    const out: Decoration[] = [];
    for (const span of spans) {
        let cursor = 0;
        group.blocks.forEach((b, i) => {
            const start = cursor;
            const end = start + texts[i].length;
            cursor = end + PARA_SEP.length;
            const from = Math.max(span.from, start);
            const to = Math.min(span.to, end);
            if (to <= from) return;
            out.push(Decoration.inline(
                b.pos + 1 + (from - start), b.pos + 1 + (to - start),
                { class: `ce-blame-span ce-blame-${actorRole(span.actor)}`,
                  'data-blame-who': actorLabel(span.actor) },
            ));
        });
    }
    return out;
}

export const BlameDecorations = Extension.create<BlameDecorationsOptions>({
    name: 'blameDecorations',

    addOptions() {
        return { getEnabled: () => false, getHistory: () => ({}) };
    },

    addProseMirrorPlugins() {
        const enabled = (): boolean => this.options.getEnabled();
        const history = (): Record<string, HistoryEntry[]> => this.options.getHistory();
        const now = (): number => this.options.now?.() ?? Date.now();
        const handlers = (): BlameCardHandlers => ({
            timeline: this.options.getTimeline?.(),
            directives: this.options.getDirectives?.() ?? {},
            files: (fid: string) => this.options.getFiles?.(fid) ?? [],
            openDiff: this.options.onOpenDiff,
            openSession: this.options.onOpenSession,
        });
        return [
            new Plugin({
                key: blameKey,
                state: {
                    init: (_c, state) => buildBlameDecorations(state.doc, enabled(), history(), now(), handlers()),
                    // Structure-keyed: blame is drawn per feature from history, so
                    // typing a character changes no blame fact — mapping is not
                    // merely cheaper than rebuilding, it is the correct answer.
                    apply: (tr, old, _o, newState) => nextDecorations(
                        tr, old, !!tr.getMeta(BLAME_UPDATED),
                        () => buildBlameDecorations(newState.doc, enabled(), history(), now(), handlers()),
                    ),
                },
                props: { decorations(state) { return blameKey.getState(state); } },
            }),
        ];
    },
});

// Re-export so consumers can build the resting label without importing the model
// directly (keeps the blame surface behind one module).
export { actorRole };
