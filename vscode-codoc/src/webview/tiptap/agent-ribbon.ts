/**
 * agent-ribbon.ts — the inline "what the agent is doing" ribbon (P2b).
 *
 * Under a feature heading the agent is actively working, a quiet ribbon lists its
 * steps — "reading agent.py", "editing models.py", "running pytest" — each ticking
 * to a check as the agent moves on, the live counterpart of the prototype's
 * "codoc · Bug Fixer · reading… → root cause…". The steps are derived from
 * activity.json (`featureSteps`, state/activity-model) and arrive as
 * `payload.sync.steps[fid]`; this plugin only renders them as a widget decoration.
 *
 * STATUS axis (no hue): the working glyph + checks are foreground-tinted; motion is a
 * CSS pulse gated on prefers-reduced-motion. The widget is inert (no doc mutation),
 * keyed on the step labels so ProseMirror reuses the DOM across reconciles (no flicker).
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { Node as PMModelNode } from '@tiptap/pm/model';
import type { AgentStep } from '../protocol';

export const STEPS_UPDATED = 'codocStepsUpdated';
const stepsKey = new PluginKey('codocAgentRibbon');

export interface AgentRibbonOptions {
    getSteps: () => Record<string, AgentStep[]>;
}

/** A stable key for a feature's ribbon so PM reuses the widget DOM across reconciles
 *  (only rebuilds when the steps actually change → no flicker / re-animation). */
export function ribbonKey(fid: string, steps: AgentStep[]): string {
    return fid + '|' + steps.map(s => (s.done ? '✓' : '▸') + s.label).join('§');
}

function ribbonDom(steps: AgentStep[]): HTMLElement {
    const wrap = document.createElement('div');
    wrap.className = 'ce-ribbon';
    wrap.contentEditable = 'false';
    const head = document.createElement('div');
    head.className = 'ce-ribbon-head';
    head.innerHTML = `<span class="ce-ribbon-mark"></span><span class="ce-ribbon-who">codoc</span>`;
    wrap.appendChild(head);
    for (const s of steps) {
        const row = document.createElement('div');
        row.className = 'ce-ribbon-step' + (s.done ? ' done' : ' active');
        const tick = document.createElement('span');
        tick.className = 'ce-ribbon-tick';
        const label = document.createElement('span');
        label.className = 'ce-ribbon-label';
        label.textContent = s.label;
        row.append(tick, label);
        wrap.appendChild(row);
    }
    return wrap;
}

function build(doc: PMModelNode, steps: Record<string, AgentStep[]>): DecorationSet {
    const decos: Decoration[] = [];
    doc.forEach((node, pos) => {
        if (node.type.name !== 'featureHeading') return;
        const fid = node.attrs.fid as string | null;
        if (!fid) return;
        const fsteps = steps[fid];
        if (!fsteps || !fsteps.length) return;
        // widget sits just after the heading (before its description prose)
        const at = pos + node.nodeSize;
        decos.push(Decoration.widget(at, () => ribbonDom(fsteps), {
            key: ribbonKey(fid, fsteps),
            side: -1,
            ignoreSelection: true,
        }));
    });
    return DecorationSet.create(doc, decos);
}

export const AgentRibbon = Extension.create<AgentRibbonOptions>({
    name: 'agentRibbon',

    addOptions() {
        return { getSteps: () => ({}) };
    },

    addProseMirrorPlugins() {
        const getSteps = (): Record<string, AgentStep[]> => this.options.getSteps();
        return [
            new Plugin({
                key: stepsKey,
                state: {
                    init: (_c, state) => build(state.doc, getSteps()),
                    apply: (tr, old, _o, newState) => {
                        if (tr.getMeta(STEPS_UPDATED) || tr.docChanged) {
                            return build(newState.doc, getSteps());
                        }
                        return old.map(tr.mapping, tr.doc);
                    },
                },
                props: { decorations(state) { return stepsKey.getState(state); } },
            }),
        ];
    },
});
