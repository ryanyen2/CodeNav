// Figure: who wrote what, and to which surface.
//
// This is the figure the thesis lives or dies on. The claim is that codoc makes
// the description a *shared* artifact — something both the person and the agent
// write, and therefore something either can rely on. The claim fails in two
// visible ways, and the figure has to be able to show both: a description only
// the agent touches (the person has been written out) and a description only the
// person touches (the agent is ignoring it, so it is a diary, not a channel).
//
// Drawn as one dot per participant with a median line, not as a mean with error
// bars. At a dozen people a mean is a summary of a sample small enough to just
// show, and a strip plot cannot hide a bimodal split the way a mean can.
import {
    el, text, svg, INK, RULE, SOFT, MUTED, TYPE, CONDITION_LABEL, CONDITION_COLOR,
} from './theme.js';

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

const median = (xs) => {
    const v = xs.filter((x) => x != null).sort((a, b) => a - b);
    if (!v.length) return null;
    const m = Math.floor(v.length / 2);
    return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
};

/**
 * @param rows       output of authorship()
 * @param measures   [{ key, label, max, unit }]
 * @param conditions ['codoc','baseline']
 */
export function provenance(rows, opts = {}) {
    const conditions = opts.conditions || ['codoc', 'baseline'];
    const measures = opts.measures || [
        { key: 'docWrites', label: 'Writes to the description', kind: 'count' },
        { key: 'humanShareOfDoc', label: 'Share of those written by the person', kind: 'share' },
        { key: 'agentDoc', label: 'Writes to the description by the agent', kind: 'count' },
        { key: 'humanCode', label: 'Writes to code by the person', kind: 'count' },
        { key: 'agentCode', label: 'Writes to code by the agent', kind: 'count' },
    ];

    const labelW = opts.labelW || 220;
    const plotW = opts.plotW || 230;
    const rowH = 46;   // tall enough that a row's own axis labels stay inside it
    const padL = 8;
    const padT = 22;
    const padB = 30;

    const width = padL + labelW + plotW + 76;
    const height = padT + measures.length * rowH + padB;
    const root = svg(width, height, opts.title || 'Who wrote what');

    conditions.forEach((c, i) => {
        root.append(el('circle', {
            cx: padL + labelW + 8 + i * 90, cy: padT - 12, r: 3.5, fill: CONDITION_COLOR[c],
        }));
        root.append(text(CONDITION_LABEL[c] || c, {
            x: padL + labelW + 16 + i * 90, y: padT - 8.5,
            'font-size': TYPE.caption, fill: INK,
        }));
    });

    measures.forEach((m, mi) => {
        const y = padT + mi * rowH;
        if (mi % 2 === 0) {
            root.append(el('rect', {
                x: padL, y, width: width - padL - 6, height: rowH, fill: SOFT,
            }));
        }
        root.append(text(m.label, {
            x: padL + labelW - 10, y: y + rowH / 2 + 3.5,
            'text-anchor': 'end', 'font-size': TYPE.label, fill: INK,
        }));

        const values = conditions.map((c) =>
            rows.filter((r) => r.condition === c).map((r) => r[m.key]).filter((v) => v != null));
        const hi = m.kind === 'share' ? 1 : Math.max(1, ...values.flat());
        const x = (v) => padL + labelW + (v / hi) * plotW;

        root.append(el('line', {
            x1: padL + labelW, y1: y + rowH - 14, x2: padL + labelW + plotW, y2: y + rowH - 14,
            stroke: RULE,
        }));
        for (const [frac, lab] of (m.kind === 'share'
            ? [[0, '0'], [0.5, 'half'], [1, 'all']]
            : [[0, '0'], [1, String(hi)]])) {
            root.append(text(lab, {
                x: padL + labelW + frac * plotW, y: y + rowH - 4,
                'text-anchor': frac === 1 ? 'end' : frac === 0 ? 'start' : 'middle',
                'font-size': TYPE.tick, fill: MUTED,
            }));
        }

        conditions.forEach((c, ci) => {
            const vs = values[ci];
            const lane = y + 12 + ci * 10;
            for (const v of vs) {
                root.append(el('circle', {
                    cx: x(v), cy: lane, r: 3, fill: CONDITION_COLOR[c],
                    'fill-opacity': 0.5,
                }));
            }
            const md = median(vs);
            if (md != null) {
                root.append(el('line', {
                    x1: x(md), y1: lane - 6, x2: x(md), y2: lane + 6,
                    stroke: CONDITION_COLOR[c], 'stroke-width': 2,
                }));
            }
            // Say how many are behind the dots. Overlapping dots at a small n
            // otherwise read as fewer people than were actually run.
            root.append(text(`n=${vs.length}`, {
                x: padL + labelW + plotW + 8, y: lane + 3.5,
                'font-size': TYPE.caption, fill: MUTED,
            }));
        });
    });

    return root;
}
