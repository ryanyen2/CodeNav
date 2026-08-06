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
 * The "who" is the same role the presence avatar resolves (state/presence.ts's
 * roleName/roleInk), tinting the ribbon to match. New rows entering the list get a
 * brief entrance stagger (existing rows never re-animate); when a feature's steps
 * empty out (the agent finished), the ribbon doesn't just vanish — it collapses its
 * step rows (motion.ts's collapseRowOut) down to the settled head line, holds
 * briefly, then clears.
 *
 * STATUS axis (no hue beyond the existing role-ink family): motion is CSS/anime.js,
 * gated on prefers-reduced-motion throughout. The widget is inert (no doc mutation),
 * keyed on the step labels so ProseMirror reuses the DOM across reconciles (no flicker).
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

/** Per-row entrance stagger; the collapse-to-summary hold shape (rows shrink over
 *  ~ROW_COLLAPSE_MS each, the settled head line then holds SUMMARY_HOLD_MS before
 *  the widget itself clears). */
const STEP_STEP_MS = 40;
const ROW_COLLAPSE_MS = 180;
const SUMMARY_HOLD_MS = 900;

export interface AgentRibbonOptions {
    getSteps: () => Record<string, AgentStep[]>;
    getRole: () => string;
}

/** A stable key for a feature's ribbon so PM reuses the widget DOM across reconciles
 *  (only rebuilds when the steps actually change → no flicker / re-animation). */
export function ribbonKey(fid: string, steps: AgentStep[]): string {
    return fid + '|' + steps.map(s => (s.done ? '✓' : '▸') + s.label).join('§');
}

/** fids whose steps just emptied (the agent finished that feature) — non-empty in
 *  `prev`, empty/absent in `cur`. Pure — unit-tested directly. */
export function justFinished(prev: Record<string, AgentStep[]>, cur: Record<string, AgentStep[]>): string[] {
    return Object.keys(prev).filter(fid => (prev[fid]?.length ?? 0) > 0 && !(cur[fid]?.length));
}

function ribbonDom(steps: AgentStep[], role: string, prevCount: number): HTMLElement {
    const wrap = document.createElement('div');
    wrap.className = 'ce-ribbon';
    wrap.contentEditable = 'false';
    wrap.dataset.ink = roleInk(role);
    const head = document.createElement('div');
    head.className = 'ce-ribbon-head';
    const mark = document.createElement('span');
    mark.className = 'ce-ribbon-mark';
    const who = document.createElement('span');
    who.className = 'ce-ribbon-who';
    who.textContent = roleName(role);
    head.append(mark, who);
    wrap.appendChild(head);
    for (let i = 0; i < steps.length; i++) {
        const s = steps[i];
        const row = document.createElement('div');
        row.className = 'ce-ribbon-step' + (s.done ? ' done' : ' active')
            + (s.kind ? ` kind-${s.kind}` : '');
        // only rows past the previously-rendered count get the entrance animation —
        // a step flipping active→done must never re-trigger its own entrance.
        if (i < prevCount) row.style.animation = 'none';
        else row.style.animationDelay = `${(i - prevCount) * STEP_STEP_MS}ms`;
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

/** The settled widget for a feature that just finished: the same head + step rows,
 *  but every row immediately starts collapsing to 0 (deferred a frame so the node
 *  has real layout once ProseMirror inserts it — collapseRowOut reads offsetHeight).
 *  Leaves the head line — agent name + tint — as the held summary. */
function ribbonSummaryDom(steps: AgentStep[], role: string): HTMLElement {
    const wrap = ribbonDom(steps, role, steps.length); // no entrance — every row was already seen
    requestAnimationFrame(() => {
        wrap.querySelectorAll<HTMLElement>('.ce-ribbon-step').forEach(row => {
            collapseRowOut(row, () => row.remove(), { duration: ROW_COLLAPSE_MS });
        });
    });
    return wrap;
}

interface RibbonState {
    set: DecorationSet;
    /** The steps map as of the last STEPS_UPDATED — the entrance-stagger baseline. */
    lastSteps: Record<string, AgentStep[]>;
    /** fid → its last-known steps, for a feature currently collapsing to its summary. */
    collapsing: Record<string, AgentStep[]>;
}

function build(
    doc: PMModelNode,
    steps: Record<string, AgentStep[]>,
    prevSteps: Record<string, AgentStep[]>,
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
            const prevCount = prevSteps[fid]?.length ?? 0;
            decos.push(Decoration.widget(at, () => ribbonDom(fsteps, role, prevCount), {
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
                        return { set: build(state.doc, steps, steps, {}, getRole()), lastSteps: { ...steps }, collapsing: {} };
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
                            const prevSteps = lastSteps;
                            lastSteps = { ...cur };
                            return { set: build(newState.doc, cur, prevSteps, collapsing, getRole()), lastSteps, collapsing };
                        }
                        if (!cleared && !tr.docChanged) {
                            return { set: value.set.map(tr.mapping, tr.doc), lastSteps, collapsing };
                        }
                        return { set: build(newState.doc, getSteps(), lastSteps, collapsing, getRole()), lastSteps, collapsing };
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
                            const rows = st.collapsing[fid].length;
                            const dur = rows * ROW_COLLAPSE_MS + SUMMARY_HOLD_MS;
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
