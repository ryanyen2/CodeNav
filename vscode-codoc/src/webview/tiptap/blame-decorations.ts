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
 * The widget's `title` carries the full timeline (who · when · why per entry),
 * so hovering reveals the trace without a popover.
 *
 * Pure builder (unit-tested); the plugin rebuilds on a BLAME_UPDATED meta or a
 * doc change, else maps positions forward like every other decoration plugin.
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { Node as PMModelNode } from '@tiptap/pm/model';
import type { HistoryEntry } from '../../state/bindings-model';
import {
    actorLabel, actorRole, blameSummaryFrom, kindPhrase, relativeTime,
} from '../../state/blame-model';

export const BLAME_UPDATED = 'codocBlameUpdated';
const blameKey = new PluginKey('codocBlameDecorations');

export interface BlameDecorationsOptions {
    /** Blame stance on? Off ⇒ no decorations at all. */
    getEnabled: () => boolean;
    /** Per-feature history (newest first), keyed by fid. */
    getHistory: () => Record<string, HistoryEntry[]>;
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

export function buildBlameDecorations(
    doc: PMModelNode,
    enabled: boolean,
    history: Record<string, HistoryEntry[]>,
    nowMs: number,
): DecorationSet {
    if (!enabled) return DecorationSet.empty;
    const decos: Decoration[] = [];
    // Group each feature heading with its following body paragraphs so the rail
    // spans the whole feature, matching hold/captured decorations' geometry.
    let activeFid: string | null = null;
    let activeRole = 'human';
    doc.forEach((node, pos) => {
        if (node.type.name === 'featureHeading') {
            const fid = node.attrs.fid as string | null;
            const hist = fid ? history[fid] : undefined;
            if (!fid || !hist || !hist.length) { activeFid = null; return; }
            activeFid = fid;
            const summary = blameSummaryFrom(hist, nowMs);
            activeRole = summary.role;
            decos.push(Decoration.node(pos, pos + node.nodeSize, { class: `ce-blame ce-blame-${summary.role}` }));
            decos.push(Decoration.widget(pos + node.nodeSize - 1, () => {
                const chip = document.createElement('span');
                chip.className = `ce-blame-who ce-blame-${summary.role}`;
                chip.contentEditable = 'false';
                chip.textContent = summary.line;
                chip.title = blameTooltip(hist, nowMs);
                return chip;
            }, { side: 1, key: `blame-${fid}` }));
            return;
        }
        if (activeFid && node.type.name === 'paragraph' && node.content.size > 0) {
            decos.push(Decoration.node(pos, pos + node.nodeSize, { class: `ce-blame-rail ce-blame-${activeRole}` }));
        }
    });
    return DecorationSet.create(doc, decos);
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
        return [
            new Plugin({
                key: blameKey,
                state: {
                    init: (_c, state) => buildBlameDecorations(state.doc, enabled(), history(), now()),
                    apply: (tr, old, _o, newState) => {
                        if (tr.getMeta(BLAME_UPDATED) || tr.docChanged) {
                            return buildBlameDecorations(newState.doc, enabled(), history(), now());
                        }
                        return old.map(tr.mapping, tr.doc);
                    },
                },
                props: { decorations(state) { return blameKey.getState(state); } },
            }),
        ];
    },
});

// Re-export so consumers can build the resting label without importing the model
// directly (keeps the blame surface behind one module).
export { actorRole };
