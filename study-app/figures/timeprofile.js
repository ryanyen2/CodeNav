// Figure: where the time went, across the session rather than in total.
//
// A single bar of "40% reading, 30% prompting" says nothing about whether the
// reading came first or ran all the way through, and the difference between
// those two is most of what a way of working is. So the session is stretched
// onto a common 0-to-1 axis and the share of each action is drawn at each slice.
//
// Stretching is what makes sessions comparable when one took 40 minutes and
// another 70. It also destroys absolute duration, which is why the caption says
// share and the panel title carries the median length.
import {
    el, text, svg, INK, RULE, MUTED, TYPE,
    ACTION_COLOR, ACTION_ORDER, ACTION_LABEL, CONDITION_LABEL,
} from './theme.js';

/**
 * Share of each action in each slice of normalized session time.
 *
 * @param sessions [{ actions: [{ action, t, ms }] }]
 * @param bins how many slices
 * @returns { bins, actions, share: { [action]: number[] } }
 */
export function timeShare(sessions, bins = 20, { includeIdle = false } = {}) {
    const present = new Set();
    const totals = Array.from({ length: bins }, () => ({}));

    for (const s of sessions) {
        const acts = (s.actions || []).filter((a) => includeIdle || a.a !== 'IDLE');
        if (acts.length < 2) continue;
        const t0 = acts[0].t;
        const t1 = acts[acts.length - 1].t + (acts[acts.length - 1].ms || 0);
        const span = t1 - t0;
        if (span <= 0) continue;

        for (const a of acts) {
            // Weight by duration where there is one, and by a nominal moment
            // where there is not. Counting a two second glance the same as a
            // four minute read would make the figure a count of events wearing
            // the clothes of a time figure.
            const dur = a.ms && a.ms > 0 ? a.ms : 1500;
            const from = (a.t - t0) / span;
            const to = Math.min(1, (a.t - t0 + dur) / span);
            present.add(a.a);
            // Spread the action across every slice it actually covers.
            const first = Math.max(0, Math.min(bins - 1, Math.floor(from * bins)));
            const last = Math.max(first, Math.min(bins - 1, Math.ceil(to * bins) - 1));
            for (let b = first; b <= last; b += 1) {
                const lo = Math.max(from, b / bins);
                const hi = Math.min(to, (b + 1) / bins);
                const overlap = Math.max(0, hi - lo);
                if (overlap <= 0) continue;
                // Per session, so one long session cannot dominate the shape.
                totals[b][a.a] = (totals[b][a.a] || 0)
                    + (overlap / (1 / bins)) / sessions.length;
            }
        }
    }

    const actions = ACTION_ORDER.filter((a) => present.has(a));
    const share = Object.fromEntries(actions.map((a) => [a, new Array(bins).fill(0)]));
    totals.forEach((slice, b) => {
        const sum = actions.reduce((acc, a) => acc + (slice[a] || 0), 0) || 1;
        for (const a of actions) share[a][b] = (slice[a] || 0) / sum;
    });
    return { bins, actions, share };
}

/**
 * @param panels [{ condition, profile, n, medianMinutes }]
 */
export function timeProfile(panels, opts = {}) {
    const w = opts.panelW || 300;
    const h = opts.panelH || 120;
    const padL = 34;
    const padT = 26;
    const gapY = 46;

    const width = padL + w + 150;
    const height = padT + panels.length * (h + gapY);
    const root = svg(width, height, opts.title || 'Where the time went');

    panels.forEach((panel, pi) => {
        const top = padT + pi * (h + gapY);
        const { profile } = panel;
        const bins = profile.bins;

        root.append(text(`${CONDITION_LABEL[panel.condition] || panel.condition}`, {
            x: padL, y: top - 8, 'font-size': TYPE.title, 'font-weight': '600', fill: INK,
        }));
        root.append(text(
            `n = ${panel.n}${panel.medianMinutes ? `, median ${panel.medianMinutes} min` : ''}`, {
                x: padL + w, y: top - 8, 'text-anchor': 'end',
                'font-size': TYPE.caption, fill: MUTED,
            }));

        // Stack from the bottom, in reading order, so the same action is in the
        // same place in both panels.
        const x = (b) => padL + (b / (bins - 1)) * w;
        const base = new Array(bins).fill(0);
        for (const action of profile.actions) {
            const vals = profile.share[action];
            const pts = [];
            for (let b = 0; b < bins; b += 1) pts.push([x(b), top + h - base[b] * h]);
            for (let b = bins - 1; b >= 0; b -= 1) {
                pts.push([x(b), top + h - (base[b] + vals[b]) * h]);
            }
            root.append(el('path', {
                d: `M${pts.map(([px, py]) => `${px.toFixed(1)},${py.toFixed(1)}`).join('L')}Z`,
                fill: ACTION_COLOR[action] || '#ccc',
            }));
            for (let b = 0; b < bins; b += 1) base[b] += vals[b];
        }

        // Axes.
        root.append(el('line', { x1: padL, y1: top + h, x2: padL + w, y2: top + h, stroke: RULE }));
        for (const [frac, label] of [[0, 'start'], [0.5, 'half'], [1, 'end']]) {
            root.append(text(label, {
                x: padL + frac * w, y: top + h + 12,
                'text-anchor': frac === 0 ? 'start' : frac === 1 ? 'end' : 'middle',
                'font-size': TYPE.tick, fill: MUTED,
            }));
        }
        for (const [frac, label] of [[0, '0%'], [0.5, '50%'], [1, '100%']]) {
            root.append(text(label, {
                x: padL - 5, y: top + h - frac * h + 3,
                'text-anchor': 'end', 'font-size': TYPE.tick, fill: MUTED,
            }));
        }
    });

    // One legend for both panels, in the same order as the stack.
    const shown = [...new Set(panels.flatMap((p) => p.profile.actions))]
        .sort((a, b) => ACTION_ORDER.indexOf(a) - ACTION_ORDER.indexOf(b));
    const legend = el('g', { transform: `translate(${padL + w + 14},${padT})` });
    shown.forEach((a, i) => {
        legend.append(el('rect', {
            x: 0, y: i * 14, width: 9, height: 9, fill: ACTION_COLOR[a] || '#ccc',
        }));
        legend.append(text(ACTION_LABEL[a] || a, {
            x: 14, y: i * 14 + 8, 'font-size': TYPE.caption, fill: INK,
        }));
    });
    root.append(legend);

    return root;
}
