/**
 * glance-decorations.ts — GLANCE MODE (B-U2 / R3): collapse every feature to its
 * one-line pitch so the whole tree is skimmable; expanding (toggling glance off)
 * restores the full prose.
 *
 * This is a DECORATION, never a doc edit — the underlying tree.doc.json / tree.codoc
 * are byte-identical whether glance is on or off (R10). The doc-view sets `body.glance`
 * (CSS hides the description paragraphs beneath each heading); this plugin drops a single
 * inert widget — the pitch line — right after each feature heading so the collapsed row
 * still reads as a one-liner. When a feature's pitch equals its title (the B-U1 fallback)
 * the row still collapses with NO extra placeholder: we omit the redundant pitch widget
 * (the heading already shows that text), so there's no doubled line.
 *
 * The pitch text comes from the sidecar (FeatureMeta.pitch), threaded in by the host
 * payload; the widget is inert (no clicks), so it can't desync the doc. Reduced-motion
 * is handled by the global `body.vscode-reduce-motion` gate in CSS (no animation here).
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { Node as PMModelNode } from '@tiptap/pm/model';

export const GLANCE_UPDATED = 'codocGlanceUpdated';
const glanceKey = new PluginKey('codocGlance');

export interface GlanceDecorationsOptions {
    /** glance on/off — when off the plugin emits the empty decoration set. */
    isGlance: () => boolean;
    /** feature id → its one-line pitch (FeatureMeta.pitch). Empty/absent → no widget. */
    getPitch: (fid: string) => string;
}

function pitchWidget(text: string): HTMLElement {
    const span = document.createElement('div');
    span.className = 'ce-glance-pitch';
    span.textContent = text;
    span.contentEditable = 'false';
    return span;
}

function buildGlanceDecorations(
    doc: PMModelNode,
    isGlance: boolean,
    getPitch: (fid: string) => string,
): DecorationSet {
    if (!isGlance) return DecorationSet.empty;
    const decos: Decoration[] = [];
    doc.forEach((node, pos) => {
        if (node.type.name !== 'featureHeading') return;
        const fid = node.attrs.fid as string | null;
        if (!fid) return;
        const pitch = (getPitch(fid) ?? '').trim();
        // Omit the widget when the pitch is empty OR equals the heading text (fallback
        // case) — the row still collapses, just with no doubled line (no placeholder).
        const title = (node.textContent ?? '').trim();
        if (!pitch || pitch === title) return;
        // A widget AFTER the heading node — inert, side:1 so it sits below the title.
        decos.push(Decoration.widget(pos + node.nodeSize, () => pitchWidget(pitch), { side: 1 }));
    });
    return DecorationSet.create(doc, decos);
}

export const GlanceDecorations = Extension.create<GlanceDecorationsOptions>({
    name: 'glanceDecorations',

    addOptions() {
        return { isGlance: () => false, getPitch: () => '' };
    },

    addProseMirrorPlugins() {
        const isGlance = (): boolean => this.options.isGlance();
        const getPitch = (fid: string): string => this.options.getPitch(fid);
        return [
            new Plugin({
                key: glanceKey,
                state: {
                    init: (_c, state) => buildGlanceDecorations(state.doc, isGlance(), getPitch),
                    apply: (tr, old, _o, newState) => {
                        if (tr.getMeta(GLANCE_UPDATED) || tr.docChanged) {
                            return buildGlanceDecorations(newState.doc, isGlance(), getPitch);
                        }
                        return old.map(tr.mapping, tr.doc);
                    },
                },
                props: { decorations(state) { return glanceKey.getState(state); } },
            }),
        ];
    },
});
