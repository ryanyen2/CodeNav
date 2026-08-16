// Figure: who wrote what, and to which surface.
//
// This is the figure the thesis lives or dies on. The claim is that codoc makes
// the description a *shared* artifact — something both the person and the agent
// write, and therefore something either can rely on. The claim fails in two
// visible ways, and the figure has to be able to show both: a description only
// the agent touches (the person has been written out) and a description only the
// person touches (the agent is ignoring it, so it is a diary, not a channel).
//
// Every observation is drawn, coloured by condition, with a black mean and its
// interval over the top and a dashed line at each condition's own mean. At a
// dozen people a mean is a summary of a sample small enough to just show, and
// dots cannot hide a bimodal split, or a single person carrying a result, the
// way a bar can.
import {
    el, text, svg, INK, RULE, SOFT, MUTED, TYPE, CONDITION_LABEL, CONDITION_COLOR,
} from './theme.js';
import { mean, studentizedCI } from './stats.js';

const HUMAN_DOC = ['EDIT_DOC'];
const AGENT_DOC = ['AGENT_DOC'];
const HUMAN_CODE = ['EDIT_CODE', 'EDIT_TEST'];
const AGENT_CODE = ['AGENT_EDIT'];

/** Per participant, per condition, how many writes of each kind. */
export function authorship(sessions) {
    const count = (acts, names) => acts.filter((a) => names.includes(a.a)).length;
    return sessions.map((s) => {
        const acts = s.actions || [];
        const humanDoc = count(acts, HUMAN_DOC);
        const agentDoc = count(acts, AGENT_DOC);
        const doc = humanDoc + agentDoc;
        return {
            code: s.code,
            condition: s.condition,
            humanDoc,
            agentDoc,
            humanCode: count(acts, HUMAN_CODE),
            agentCode: count(acts, AGENT_CODE),
            docWrites: doc,
            // Undefined rather than 0.5 when nobody wrote to it at all. A
            // description nobody touched has no author split, and plotting it at
            // the midpoint would invent a balanced one.
            humanShareOfDoc: doc > 0 ? humanDoc / doc : null,
        };
    });
}

export const DEFAULT_MEASURES = Object.freeze([
    { key: 'docWrites', label: 'Writes to the description', kind: 'count' },
    { key: 'humanShareOfDoc', label: 'Share of those by the person', kind: 'share' },
    { key: 'humanCode', label: 'Writes to code by the person', kind: 'count' },
    { key: 'agentCode', label: 'Writes to code by the agent', kind: 'count' },
]);

/**
 * @param rows output of authorship()
 */
export function provenance(rows, opts = {}) {
    const conditions = opts.conditions || ['codoc', 'baseline'];
    const measures = opts.measures || DEFAULT_MEASURES;

    const labelW = opts.labelW || 160;
    const plotW = opts.plotW || 300;
    const laneH = 16;
    const padL = 8;
    const padT = 30;
    const padB = 16;
    const rowH = conditions.length * laneH + 22;

    const width = padL + labelW + plotW + 32;
    const height = padT + measures.length * rowH + padB;
    const root = svg(width, height, opts.title || 'Who wrote what');

    // ── the key ──
    let kx = padL + labelW;
    for (const c of conditions) {
        root.append(el('circle', { cx: kx + 4, cy: 10, r: 4, fill: CONDITION_COLOR[c] }));
        root.append(text(CONDITION_LABEL[c] || c, {
            x: kx + 13, y: 13.5, 'font-size': TYPE.caption, fill: INK,
        }));
        kx += 76;
    }
    root.append(el('circle', { cx: kx + 4, cy: 10, r: 3.4, fill: INK }));
    root.append(text('Mean & 95% CI', { x: kx + 13, y: 13.5, 'font-size': TYPE.caption, fill: INK }));

    measures.forEach((m, mi) => {
        const y = padT + mi * rowH;
        if (mi % 2 === 1) {
            root.append(el('rect', {
                x: padL, y, width: width - padL - 6, height: rowH, fill: SOFT,
            }));
        }
        root.append(text(m.label, {
            x: padL + labelW - 12, y: y + rowH / 2 - 1,
            'text-anchor': 'end', 'font-size': TYPE.label, fill: INK,
        }));

        const valuesFor = (c) => rows.filter((r) => r.condition === c)
            .map((r) => r[m.key]).filter((v) => v != null);
        const hi = m.kind === 'share' ? 1 : Math.max(1, ...conditions.flatMap(valuesFor));
        const x = (v) => padL + labelW + (v / hi) * plotW;
        const ticks = m.kind === 'share'
            ? [0, 0.25, 0.5, 0.75, 1]
            : [...new Set([0, hi / 4, hi / 2, (hi * 3) / 4, hi].map((t) => Math.round(t)))];

        // Gridlines behind everything, so a value can be read off a dot.
        for (const t of ticks) {
            root.append(el('line', {
                x1: x(t), y1: y + 4, x2: x(t), y2: y + rowH - 16, stroke: RULE,
            }));
        }

        conditions.forEach((c, ci) => {
            const vs = valuesFor(c);
            const lane = y + 13 + ci * laneH;
            // Jittered only across the lane, never along the value axis: moving a
            // dot sideways would move it away from the number it stands for.
            vs.forEach((v, i) => {
                root.append(el('circle', {
                    cx: x(v), cy: lane + ((i % 3) - 1) * 2.3, r: 3.4,
                    fill: CONDITION_COLOR[c], 'fill-opacity': 0.75,
                }));
            });
            // Each condition's own mean, dashed, full height of the row, so it is
            // visible whether that condition sits above or below the other.
            const m0 = mean(vs);
            if (m0 != null) {
                root.append(el('line', {
                    x1: x(m0), y1: y + 4, x2: x(m0), y2: y + rowH - 16,
                    stroke: CONDITION_COLOR[c], 'stroke-dasharray': '3 2', 'stroke-width': 1.4,
                }));
            }
            const est = studentizedCI(vs, { seed: 20260816 });
            if (est && est.low != null && !est.degenerate) {
                root.append(el('line', {
                    x1: x(est.low), y1: lane, x2: x(est.high), y2: lane,
                    stroke: INK, 'stroke-width': 1.2,
                }));
                for (const end of [est.low, est.high]) {
                    root.append(el('line', {
                        x1: x(end), y1: lane - 3, x2: x(end), y2: lane + 3, stroke: INK,
                    }));
                }
            }
            if (m0 != null) {
                root.append(el('circle', { cx: x(m0), cy: lane, r: 3.2, fill: INK }));
            }
            // Overlapping dots at a small n read as fewer people than were run.
            root.append(text(`n=${vs.length}`, {
                x: padL + labelW + plotW + 5, y: lane + 3.5,
                'font-size': TYPE.caption, fill: MUTED,
            }));
        });

        // An axis per row, because the rows are on different scales.
        const yAxis = y + rowH - 14;
        root.append(el('line', {
            x1: padL + labelW, y1: yAxis, x2: padL + labelW + plotW, y2: yAxis, stroke: RULE,
        }));
        for (const t of ticks) {
            root.append(text(m.kind === 'share' ? `${Math.round(t * 100)}%` : t, {
                x: x(t), y: yAxis + 10, 'text-anchor': 'middle',
                'font-size': TYPE.tick, fill: MUTED,
            }));
        }
    });

    return root;
}
