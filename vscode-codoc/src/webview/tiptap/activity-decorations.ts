/**
 * activity-decorations.ts — live "the agent is working here" feedback in the doc
 * pane, driven by the Claude Code hooks: PreToolUse/PostToolUse touches and the
 * MCP reflect/attach calls write per-feature phases (editing → reflecting → done)
 * into `.codoc/activity.json`; the host folds them into `sync.phase` and the
 * webview decorates the matching feature headings.
 *
 * This is the STATUS axis (no hue — colour stays reserved for direction): a
 * small pulsing dot after the heading while the agent edits the feature's code,
 * a hollow dot while it reflects the work back. `done` (and a closed epoch)
 * clears the decoration. Animation respects prefers-reduced-motion (CSS).
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { Node as PMModelNode } from '@tiptap/pm/model';
import type { FeaturePhase } from '../../state/activity-model';

export const PHASES_UPDATED = 'codocPhasesUpdated';
const phaseKey = new PluginKey('codocActivityPhases');

export interface ActivityDecorationsOptions {
    getPhases: () => Record<string, FeaturePhase>;
}

function buildPhaseDecorations(doc: PMModelNode, phases: Record<string, FeaturePhase>): DecorationSet {
    const decos: Decoration[] = [];
    doc.forEach((node, pos) => {
        if (node.type.name !== 'featureHeading') return;
        const fid = node.attrs.fid as string | null;
        if (!fid) return;
        const phase = phases[fid];
        if (phase !== 'editing' && phase !== 'reflecting') return; // done/absent → quiet
        decos.push(Decoration.node(pos, pos + node.nodeSize, { class: `ce-phase-${phase}` }));
    });
    return DecorationSet.create(doc, decos);
}

export const ActivityDecorations = Extension.create<ActivityDecorationsOptions>({
    name: 'activityDecorations',

    addOptions() {
        return { getPhases: () => ({}) };
    },

    addProseMirrorPlugins() {
        const getPhases = (): Record<string, FeaturePhase> => this.options.getPhases();
        return [
            new Plugin({
                key: phaseKey,
                state: {
                    init: (_c, state) => buildPhaseDecorations(state.doc, getPhases()),
                    apply: (tr, old, _o, newState) => {
                        if (tr.getMeta(PHASES_UPDATED) || tr.docChanged) {
                            return buildPhaseDecorations(newState.doc, getPhases());
                        }
                        return old.map(tr.mapping, tr.doc);
                    },
                },
                props: { decorations(state) { return phaseKey.getState(state); } },
            }),
        ];
    },
});
