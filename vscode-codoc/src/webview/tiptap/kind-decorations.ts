/**
 * kind-decorations.ts — INFERRED KIND CHIP (B-U3 / R4): a small Diátaxis-lite
 * `kind` hint rendered as a chip below each feature title.
 *
 * The kind is a pure structural heuristic derived Python-side (the sidecar's
 * `feature_kind` slice) — `overview` for a binding-less realized theme parent,
 * `reference` for a code-bound feature. Suppressed/`unclassified`/`retired` kinds
 * are filtered host-side, so only meaningful hints reach this plugin (absent ⇒ no
 * chip). Structure is OFFERED, never mandated — free prose stays the default.
 *
 * This is a DECORATION, never a doc edit — tree.doc.json / tree.codoc stay
 * byte-identical (R10). The chip is an inert widget right after the feature heading
 * (side:1, so it sits below the title before the description), keyed by fid so it
 * survives doc remaps. Shape/label = kind; colour stays the muted `--vscode-badge-*`
 * token (no new hue — colour is reserved for direction per the 2026-06-09 convention).
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { Node as PMModelNode } from '@tiptap/pm/model';

export const KIND_UPDATED = 'codocKindUpdated';
const kindKey = new PluginKey('codocKind');

export interface KindDecorationsOptions {
    /** feature id → its kind hint (`overview` | `reference`). Absent/empty → no chip. */
    getKind: (fid: string) => string;
}

// Human-readable label + glyph per kind (shape = kind, no hue). A theme parent reads
// as an "overview"; a code-bound feature as a "reference".
const KIND_LABEL: Record<string, { glyph: string; label: string; title: string }> = {
    overview: { glyph: '◇', label: 'overview', title: 'A theme parent grouping child features (no code of its own).' },
    reference: { glyph: '◆', label: 'reference', title: 'A code-bound feature.' },
};

function kindChip(kind: string): HTMLElement | null {
    const meta = KIND_LABEL[kind];
    if (!meta) return null;
    const chip = document.createElement('span');
    chip.className = 'ce-kind-chip ce-kind-' + kind;
    chip.contentEditable = 'false';
    chip.title = meta.title;
    const g = document.createElement('span');
    g.className = 'ce-kind-glyph';
    g.textContent = meta.glyph;
    chip.append(g, document.createTextNode(meta.label));
    return chip;
}

function buildKindDecorations(
    doc: PMModelNode,
    getKind: (fid: string) => string,
): DecorationSet {
    const decos: Decoration[] = [];
    doc.forEach((node, pos) => {
        if (node.type.name !== 'featureHeading') return;
        const fid = node.attrs.fid as string | null;
        if (!fid) return;
        const kind = (getKind(fid) ?? '').trim();
        if (!kind || !KIND_LABEL[kind]) return;
        decos.push(Decoration.widget(pos + node.nodeSize, () => {
            const wrap = document.createElement('div');
            wrap.className = 'ce-kind-row';
            wrap.contentEditable = 'false';
            const chip = kindChip(kind);
            if (chip) wrap.append(chip);
            return wrap;
        }, { side: 1, key: 'kind-' + fid }));
    });
    return DecorationSet.create(doc, decos);
}

export const KindDecorations = Extension.create<KindDecorationsOptions>({
    name: 'kindDecorations',

    addOptions() {
        return { getKind: () => '' };
    },

    addProseMirrorPlugins() {
        const getKind = (fid: string): string => this.options.getKind(fid);
        return [
            new Plugin({
                key: kindKey,
                state: {
                    init: (_c, state) => buildKindDecorations(state.doc, getKind),
                    apply: (tr, old, _o, newState) => {
                        if (tr.getMeta(KIND_UPDATED) || tr.docChanged) {
                            return buildKindDecorations(newState.doc, getKind);
                        }
                        return old.map(tr.mapping, tr.doc);
                    },
                },
                props: { decorations(state) { return kindKey.getState(state); } },
            }),
        ];
    },
});
