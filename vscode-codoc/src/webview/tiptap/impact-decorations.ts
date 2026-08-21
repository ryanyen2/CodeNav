/**
 * impact-decorations.ts — "what happens if I change this?" on the feature it is about.
 *
 * The fourth of Sillito's question groups is the one a reader asks with their hands on
 * the keyboard: *if I change this, what breaks?* The dependency graph has always known
 * the answer — Loop A computes an `impacted` set on every pass and spends it on the LLM
 * prompt — and no description states it, because a description should not: a list of
 * one's own callers is the inventory-of-machinery defect the altitude rule exists to
 * stop, and it goes stale the moment somebody adds a caller. So it is drawn, from the
 * `feature_impact` sidecar slice, as a derived index beside the prose rather than inside
 * it.
 *
 * **Invisible until the heading is hovered.** A permanently-lit "4 dependents" on every
 * heading is a second document competing with the first, and the fact is worthless except
 * in the seconds before an edit. This follows `.ce-realize-withdraw`'s precedent exactly:
 * the chip is `opacity: 0` and the heading's `:hover` / `:focus-within` reveals it.
 *
 * **The count is the chip; the evidence is the card.** A number alone is a claim to take
 * on trust, so clicking opens the dependents by name, each with the symbols that actually
 * reach in, and each a link that navigates there — because "what breaks" is only useful
 * if you can go read it.
 *
 * A DECORATION, never a doc edit: the widget is inert and `contentEditable=false`, so
 * `renderTreeFromDoc` is byte-identical whether it is drawn or not (R10).
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { Node as PMModelNode } from '@tiptap/pm/model';
import type { ImpactEntry } from '../../state/bindings-model';
import { showCard } from './comment-decorations';

export const IMPACT_UPDATED = 'codocImpactUpdated';
const impactKey = new PluginKey('codocImpact');

/** How many dependents the card lists before it stops and says how many are left. A
 *  hub feature has dozens, and a card that scrolls is a panel — the reader wanted the
 *  shape of the risk, and the tree itself is where the full list belongs. */
export const IMPACT_CARD_ROWS = 8;

export interface ImpactDecorationsOptions {
    /** feature id → the features that depend on it, heaviest coupling first. */
    getImpact: (fid: string) => ImpactEntry[];
    /** Navigate to a feature (the card's rows are links). */
    onNavigate?: (fid: string) => void;
}

/** The chip's label. Plural-correct, and phrased as the answer to the question rather
 *  than as a metric: "3 dependents" is a statistic, "3 features depend on this" is the
 *  sentence the reader was about to ask for. */
export function impactLabel(n: number): string {
    return n === 1 ? '1 feature depends on this' : `${n} features depend on this`;
}

/** One card row's evidence line: the symbols in the DEPENDENT that reach into the
 *  subject. Truncated with an honest tail, never a bare ellipsis — a reader has to be
 *  able to tell "these three" from "three of eleven". */
export function viaLine(entry: ImpactEntry): string {
    const shown = entry.via.slice(0, 3);
    const rest = entry.count - shown.length;
    return rest > 0 ? `${shown.join(', ')} +${rest} more` : shown.join(', ');
}

function buildCard(
    entries: ImpactEntry[],
    onNavigate?: (fid: string) => void,
): HTMLElement {
    const card = document.createElement('div');
    card.className = 'ce-hovercard ce-impact-card';   // same card chrome as the hover preview
    const head = document.createElement('div');
    head.className = 'ce-impact-card-head';
    head.textContent = impactLabel(entries.length);
    card.append(head);
    for (const e of entries.slice(0, IMPACT_CARD_ROWS)) {
        const row = document.createElement(onNavigate ? 'button' : 'div');
        row.className = 'ce-impact-row';
        const title = document.createElement('span');
        title.className = 'ce-impact-row-title';
        title.textContent = e.title;
        const via = document.createElement('span');
        via.className = 'ce-impact-row-via';
        via.textContent = viaLine(e);
        row.append(title, via);
        if (onNavigate) {
            (row as HTMLButtonElement).type = 'button';
            row.addEventListener('click', ev => {
                ev.preventDefault();
                ev.stopPropagation();
                onNavigate(e.feature_id);
            });
        }
        card.append(row);
    }
    if (entries.length > IMPACT_CARD_ROWS) {
        const more = document.createElement('div');
        more.className = 'ce-impact-more';
        more.textContent = `+${entries.length - IMPACT_CARD_ROWS} more`;
        card.append(more);
    }
    return card;
}

function chip(
    entries: ImpactEntry[],
    onNavigate?: (fid: string) => void,
): HTMLElement {
    const span = document.createElement('span');
    span.className = 'ce-impact-chip';
    span.contentEditable = 'false';
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'ce-impact-count';
    btn.textContent = String(entries.length);
    btn.title = impactLabel(entries.length) + ' — click to see which';
    // The opening mousedown must not move the caret into the heading, or clicking the
    // chip would count as an edit position and the settle pipeline would see a cursor
    // parked in a title it never typed in.
    btn.addEventListener('mousedown', ev => ev.preventDefault());
    btn.addEventListener('click', ev => {
        ev.preventDefault();
        ev.stopPropagation();
        showCard(btn, buildCard(entries, onNavigate), { pinned: true });
    });
    span.append(btn);
    return span;
}

function buildImpactDecorations(
    doc: PMModelNode,
    getImpact: (fid: string) => ImpactEntry[],
    onNavigate?: (fid: string) => void,
): DecorationSet {
    const decos: Decoration[] = [];
    doc.forEach((node, pos) => {
        if (node.type.name !== 'featureHeading') return;
        const fid = node.attrs.fid as string | null;
        if (!fid) return;
        // A node the author is still typing has no fid the graph knows, and a node
        // nothing depends on gets nothing drawn — the absence IS the answer.
        const entries = getImpact(fid);
        if (!entries.length) return;
        decos.push(Decoration.widget(
            pos + node.nodeSize - 1,
            () => chip(entries, onNavigate),
            // Keyed by the count so a changed graph rebuilds the widget: keying on fid
            // alone lets ProseMirror treat old and new as equal and keep a stale number.
            { side: 1, key: `impact-${fid}:${entries.length}` }));
    });
    return DecorationSet.create(doc, decos);
}

export const ImpactDecorations = Extension.create<ImpactDecorationsOptions>({
    name: 'impactDecorations',

    addOptions() {
        return { getImpact: () => [] };
    },

    addProseMirrorPlugins() {
        const getImpact = (fid: string): ImpactEntry[] => this.options.getImpact(fid);
        const onNavigate = this.options.onNavigate;
        return [
            new Plugin({
                key: impactKey,
                state: {
                    init: (_c, state) =>
                        buildImpactDecorations(state.doc, getImpact, onNavigate),
                    apply: (tr, old, _o, newState) => {
                        if (tr.getMeta(IMPACT_UPDATED) || tr.docChanged) {
                            return buildImpactDecorations(newState.doc, getImpact, onNavigate);
                        }
                        return old.map(tr.mapping, tr.doc);
                    },
                },
                props: { decorations(state) { return impactKey.getState(state); } },
            }),
        ];
    },
});

export { buildImpactDecorations };
