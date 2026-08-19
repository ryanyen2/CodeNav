/**
 * node-marker.ts — the accumulated marker at the end of a feature's heading.
 *
 * Drawn from the SAME claims the prose is drawn from (`state/settlement.claimsFor`),
 * which is the point rather than an optimisation: a badge computed from its own set of
 * inputs is a badge that will eventually disagree with the text under it, and there is
 * no way for a reader to tell which of the two is lying. Here there is one source, so
 * the margin can only ever be a summary of what is visibly on the page.
 *
 * The marker is up to three glyphs and never more — whose, whether it was planned,
 * whether the build drifted (see node-status.ts for what each slot means and when it
 * is dropped). A settled feature draws nothing at all, which is most of a document.
 *
 * ## Why this is not `feature-state.ts`
 *
 * That module ranks a lifecycle and picks ONE state, and for what it models that is
 * right — `working` / `proposed` / `sent` / `staged` are stages of one progression and
 * showing four at once made the row a legend nobody had. The settlement channels are
 * not stages of each other. A node that was planned, then built, and built DIFFERENTLY
 * carries three facts that a rank would reduce to one, dropping precisely the two that
 * make the reader look. Both models are kept, each where its shape is true.
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { Node as PMModelNode } from '@tiptap/pm/model';
import { claimsFor } from '../../state/settlement';
import { nodeStatus, statusGlyphs, statusTitle, isSettled, type Fulfilment } from '../../state/node-status';
import type { FeatureStages } from '../../state/settlement-stages';
import { liveFeatures } from './settlement-decorations';

export const MARKERS_UPDATED = 'codocMarkersUpdated';
const markerKey = new PluginKey('codocNodeMarkers');

export interface NodeMarkerOptions {
    getStages: () => ReadonlyMap<string, FeatureStages>;
    getCommitted?: () => ReadonlySet<string>;
    /** Claims that reached the code and have not been acknowledged yet — the one part
     *  of the marker that cannot be read off the page, because it IS the moment the
     *  difference disappears (state/fulfilment.ts). */
    getFulfilments?: () => ReadonlyMap<string, readonly Fulfilment[]>;
    /** Injected so the marker is testable and so a History reconstruction can ask what
     *  the margin said at a past moment rather than at this one. */
    now?: () => number;
}

function markerElement(glyphs: ReturnType<typeof statusGlyphs>, title: string): HTMLElement {
    const box = document.createElement('span');
    box.className = 'ce-mark';
    box.contentEditable = 'false';
    box.title = title;
    // One accessible name for the group rather than three unlabelled dots: a screen
    // reader announcing "bullet bullet plus" is worse than silence.
    box.setAttribute('role', 'img');
    box.setAttribute('aria-label', title);
    for (const g of glyphs) {
        const el = document.createElement('span');
        el.className = g.cls;
        if (g.text) el.textContent = g.text;
        box.append(el);
    }
    return box;
}

export function buildNodeMarkers(
    doc: PMModelNode,
    stages: ReadonlyMap<string, FeatureStages>,
    committed: ReadonlySet<string> = new Set(),
    landed: ReadonlyMap<string, readonly Fulfilment[]> = new Map(),
    now = 0,
): DecorationSet {
    if (!stages.size && !landed.size) return DecorationSet.empty;
    const decos: Decoration[] = [];
    for (const f of liveFeatures(doc)) {
        const st = stages.get(f.key);
        const claims = st ? claimsFor({ ...st, live: f.text, committed: committed.has(f.key) }) : [];
        const status = nodeStatus(claims, [...(landed.get(f.key) ?? [])], now);
        if (isSettled(status)) continue;
        const glyphs = statusGlyphs(status);
        const title = statusTitle(status);
        decos.push(Decoration.widget(
            f.titlePos + 1 + titleSize(doc, f.titlePos),
            () => markerElement(glyphs, title),
            {
                side: 1,
                // Keyed by what is drawn, so an unchanged marker is reused across
                // rebuilds. A remount restarts the pulse, and a pulse that restarts on
                // every keystroke reads as a new event each time.
                key: `mk-${f.key}-${status.human}-${status.plan}-${status.diff}`,
            },
        ));
    }
    return DecorationSet.create(doc, decos);
}

/** The heading node's content size at `pos` — where the marker hangs, after the title. */
function titleSize(doc: PMModelNode, pos: number): number {
    return doc.nodeAt(pos)?.content.size ?? 0;
}

export const NodeMarkers = Extension.create<NodeMarkerOptions>({
    name: 'nodeMarkers',
    addOptions() {
        return { getStages: () => new Map() };
    },
    addProseMirrorPlugins() {
        const o = this.options;
        const build = (doc: PMModelNode): DecorationSet => buildNodeMarkers(
            doc, o.getStages(), o.getCommitted?.() ?? new Set(),
            o.getFulfilments?.() ?? new Map(), o.now?.() ?? Date.now(),
        );
        return [new Plugin({
            key: markerKey,
            state: {
                init: (_c, state) => build(state.doc),
                // Text-derived, like the settlement layer it summarises: a keystroke can
                // change whether a feature has an unsent edit, so it really does
                // invalidate. Cheap for the same reason — only features the host names
                // in `getStages` are examined at all.
                apply: (tr, old) => (tr.docChanged || tr.getMeta(MARKERS_UPDATED))
                    ? build(tr.doc) : old,
            },
            props: { decorations(state) { return this.getState(state); } },
        })];
    },
});
