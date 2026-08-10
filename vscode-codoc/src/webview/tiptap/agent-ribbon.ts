/**
 * agent-ribbon.ts — the inline "what the agent is doing" ribbon (P2b).
 *
 * Under a feature heading the agent is actively working, ONE quiet line says who is
 * there and what they are doing right now — "Claude · reading sessions.py". The
 * steps are derived from activity.json (`featureSteps`, state/activity-model) and
 * arrive as `payload.sync.steps[fid]`; this plugin renders only the CURRENT one (the
 * last step) as a widget decoration. The earlier steps are history the agent has
 * already moved past, and stacking them under every heading turned a liveness cue
 * into a block of text — the line simply re-labels itself as the agent moves on.
 *
 * The "who" is the same role the presence avatar resolves (state/presence.ts's
 * roleName/roleInk), tinting the line to match. When a feature's steps empty out (the
 * agent finished), the line doesn't just vanish — it ticks to a check, holds briefly,
 * then collapses out (motion.ts's collapseRowOut).
 *
 * STATUS axis (no hue beyond the existing role-ink family): motion is CSS/anime.js,
 * gated on prefers-reduced-motion throughout. The widget is inert (no doc mutation),
 * keyed on the rendered line so ProseMirror reuses the DOM across reconciles (no flicker).
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { Node as PMModelNode } from '@tiptap/pm/model';
import type { AgentStep } from '../protocol';
import { roleName, roleInk } from '../../state/presence';
import { collapseRowOut } from '../motion';

export const STEPS_UPDATED = 'codocStepsUpdated';
const RIBBON_CLEAR = 'codocRibbonClear';
const stepsKey = new PluginKey('codocAgentRibbon');

/** The finish shape: the line ticks to a check, holds SUMMARY_HOLD_MS so the reader
 *  registers that this feature is done, then collapses over ~ROW_COLLAPSE_MS. */
const ROW_COLLAPSE_MS = 180;
const SUMMARY_HOLD_MS = 700;

export interface AgentRibbonOptions {
    getSteps: () => Record<string, AgentStep[]>;
    getRole: () => string;
}

/** The step the ribbon shows: the last one, which `featureSteps` marks active. */
export function currentStep(steps: AgentStep[]): AgentStep | null {
    return steps.length ? steps[steps.length - 1] : null;
}

/** A stable key for a feature's ribbon so PM reuses the widget DOM across reconciles
 *  (only rebuilds when the RENDERED line changes → no flicker / re-animation; an
 *  earlier step ticking to done is history the line no longer shows). */
export function ribbonKey(fid: string, steps: AgentStep[]): string {
    const cur = currentStep(steps);
    return fid + '|' + (cur ? (cur.done ? '✓' : '▸') + cur.label : '');
}

/** fids whose steps just emptied (the agent finished that feature) — non-empty in
 *  `prev`, empty/absent in `cur`. Pure — unit-tested directly. */
export function justFinished(prev: Record<string, AgentStep[]>, cur: Record<string, AgentStep[]>): string[] {
    return Object.keys(prev).filter(fid => (prev[fid]?.length ?? 0) > 0 && !(cur[fid]?.length));
}

/** The one line: `● Claude · reading sessions.py`. */
function ribbonDom(steps: AgentStep[], role: string): HTMLElement {
    const cur = currentStep(steps);
    const wrap = document.createElement('div');
    wrap.className = 'ce-ribbon' + (cur?.done ? ' done' : '') + (cur?.kind ? ` kind-${cur.kind}` : '');
    wrap.contentEditable = 'false';
    wrap.dataset.ink = roleInk(role);
    const mark = document.createElement('span');
    mark.className = 'ce-ribbon-mark';
    const who = document.createElement('span');
    who.className = 'ce-ribbon-who';
    who.textContent = roleName(role);
    wrap.append(mark, who);
    if (cur) {
        const label = document.createElement('span');
        label.className = 'ce-ribbon-label';
        label.textContent = cur.label;
        wrap.append(label);
        wrap.title = `${roleName(role)} is here: ${cur.label}`;
    }
    return wrap;
}

/** The settled widget for a feature that just finished: the same line with its mark
 *  ticked to a check, held SUMMARY_HOLD_MS so the reader sees the work land, then
 *  collapsed away (collapseRowOut reads offsetHeight, so this must run after layout). */
function ribbonSummaryDom(steps: AgentStep[], role: string): HTMLElement {
    const last = currentStep(steps);
    const wrap = ribbonDom(last ? [{ ...last, done: true }] : [], role);
    window.setTimeout(
        () => collapseRowOut(wrap, () => wrap.remove(), { duration: ROW_COLLAPSE_MS }),
        SUMMARY_HOLD_MS,
    );
    return wrap;
}

interface RibbonState {
    set: DecorationSet;
    /** The steps map as of the last STEPS_UPDATED — the finished-feature baseline. */
    lastSteps: Record<string, AgentStep[]>;
    /** fid → its last-known steps, for a feature currently collapsing to its summary. */
    collapsing: Record<string, AgentStep[]>;
}

function build(
    doc: PMModelNode,
    steps: Record<string, AgentStep[]>,
    collapsing: Record<string, AgentStep[]>,
    role: string,
): DecorationSet {
    const decos: Decoration[] = [];
    doc.forEach((node, pos) => {
        if (node.type.name !== 'featureHeading') return;
        const fid = node.attrs.fid as string | null;
        if (!fid) return;
        // widget sits just after the heading (before its description prose)
        const at = pos + node.nodeSize;
        const fsteps = steps[fid];
        if (fsteps && fsteps.length) {
            decos.push(Decoration.widget(at, () => ribbonDom(fsteps, role), {
                key: ribbonKey(fid, fsteps),
                side: -1,
                ignoreSelection: true,
            }));
        } else if (collapsing[fid]?.length) {
            decos.push(Decoration.widget(at, () => ribbonSummaryDom(collapsing[fid], role), {
                key: 'collapse:' + ribbonKey(fid, collapsing[fid]),
                side: -1,
                ignoreSelection: true,
            }));
        }
    });
    return DecorationSet.create(doc, decos);
}

export const AgentRibbon = Extension.create<AgentRibbonOptions>({
    name: 'agentRibbon',

    addOptions() {
        return { getSteps: () => ({}), getRole: () => 'claude' };
    },

    addProseMirrorPlugins() {
        const getSteps = (): Record<string, AgentStep[]> => this.options.getSteps();
        const getRole = (): string => this.options.getRole();
        return [
            new Plugin<RibbonState>({
                key: stepsKey,
                state: {
                    init: (_c, state): RibbonState => {
                        const steps = getSteps();
                        return { set: build(state.doc, steps, {}, getRole()), lastSteps: { ...steps }, collapsing: {} };
                    },
                    apply: (tr, value, _o, newState): RibbonState => {
                        let { lastSteps, collapsing } = value;
                        const cleared = tr.getMeta(RIBBON_CLEAR) as string[] | undefined;
                        if (cleared) { collapsing = { ...collapsing }; cleared.forEach(f => delete collapsing[f]); }
                        if (tr.getMeta(STEPS_UPDATED)) {
                            const cur = getSteps();
                            const finished = justFinished(lastSteps, cur);
                            if (finished.length) {
                                collapsing = { ...collapsing };
                                finished.forEach(f => { collapsing[f] = lastSteps[f]; });
                            }
                            lastSteps = { ...cur };
                            return { set: build(newState.doc, cur, collapsing, getRole()), lastSteps, collapsing };
                        }
                        if (!cleared && !tr.docChanged) {
                            return { set: value.set.map(tr.mapping, tr.doc), lastSteps, collapsing };
                        }
                        return { set: build(newState.doc, getSteps(), collapsing, getRole()), lastSteps, collapsing };
                    },
                },
                props: { decorations(state) { return stepsKey.getState(state)?.set; } },
                // Schedule a one-shot clear per collapsing feature once its summary has held
                // long enough, so the widget doesn't linger (it would re-animate the collapse
                // on the next unrelated rebuild otherwise). Mirrors reveal-decorations.ts's timer.
                view(view) {
                    const timed = new Set<string>();
                    const tick = () => {
                        const st = stepsKey.getState(view.state);
                        if (!st) return;
                        for (const fid of Object.keys(st.collapsing)) {
                            if (timed.has(fid)) continue;
                            timed.add(fid);
                            const dur = SUMMARY_HOLD_MS + ROW_COLLAPSE_MS + 60;
                            window.setTimeout(() => {
                                if (view.isDestroyed) return;
                                timed.delete(fid);
                                view.dispatch(view.state.tr.setMeta(RIBBON_CLEAR, [fid]));
                            }, dur);
                        }
                    };
                    tick();
                    return { update: tick };
                },
            }),
        ];
    },
});
