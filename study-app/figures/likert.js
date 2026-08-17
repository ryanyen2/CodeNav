// Figure: what people said, item by item, in both conditions.
//
// Stacked 100% bars rather than means with error bars. A mean of a seven point
// scale hides the shape that matters — four people at the middle and four split
// to the ends average to the same number, and they are not the same finding.
// Counts sit inside each band so the reader can check the arithmetic, which is
// what a reviewer does first.
//
// One panel per condition, sharing a row per item, so an item is read across.
import {
    el, text, svg, INK, RULE, SOFT, MUTED, TYPE, WIDTH,
    likertColors, onColor, CONDITION_LABEL,
} from './theme.js';
import { pairedEstimate } from './stats.js';

/**
 * @param {object} data
 *   items:      [{ id, text, reverse }]
 *   conditions: ['codoc','baseline']
 *   counts:     { [condition]: { [itemId]: number[] } }  index 0 = point 1
 *   points:     7
 *   ratings:    [{ code, condition, item, value }]  optional; adds panel (c)
 *
 * With `ratings`, a third panel shows the paired mean difference and its 95%
 * interval. The stacked panels say what people answered; only this one says
 * whether the two conditions differ, and by how much, with the uncertainty
 * attached. Reading a difference off two stacked bars by eye is guesswork, and
 * at a dozen people it is guesswork that is often wrong.
 */
export function likert(data, opts = {}) {
    const points = data.points || 7;
    const conditions = data.conditions || ['codoc', 'baseline'];
    const colors = likertColors(points);

    const rowH = opts.rowH || 15;
    const gap = 3;
    const labelW = opts.labelW || 250;
    const panelW = opts.panelW || 150;
    const padL = 8;
    const padT = 46;
    const padB = 34;

    const items = data.items;
    const n = items.length;
    // Panel (c) only exists when the per-participant ratings are supplied. A
    // difference cannot be computed from the counts alone: they have lost which
    // answer belonged to whom, which is exactly what pairing needs.
    const estimates = data.ratings ? items.map((it) => pairedEstimate(
        data.ratings.filter((r) => r.item === it.id),
        { a: conditions[0], b: conditions[1], key: 'value', seed: 20260816 },
    )) : null;
    const diffW = estimates ? (opts.diffW || 140) : 0;

    const width = padL + labelW + conditions.length * (panelW + 16)
        + (estimates ? diffW + 34 : 0) + 10;
    const height = padT + n * (rowH + gap) + padB;
    const root = svg(width, height, opts.title || 'Questionnaire responses');

    // The largest total across every bar, so both panels share a scale. Scaling
    // each panel to its own total would make a panel with fewer answers look the
    // same as a full one.
    let maxTotal = 0;
    for (const c of conditions) {
        for (const it of items) {
            const row = (data.counts[c] || {})[it.id] || [];
            maxTotal = Math.max(maxTotal, row.reduce((a, b) => a + b, 0));
        }
    }
    if (maxTotal === 0) maxTotal = 1;

    // ── the key ──
    const key = el('g', { transform: `translate(${padL + labelW},14)` });
    const swatch = 13;
    key.append(text('strongly disagree', {
        x: -6, y: 9, 'text-anchor': 'end', 'font-size': TYPE.caption, fill: MUTED,
    }));
    colors.forEach((c, i) => {
        key.append(el('rect', { x: i * swatch, y: 0, width: swatch, height: 9, fill: c }));
    });
    key.append(text('strongly agree', {
        x: colors.length * swatch + 6, y: 9, 'font-size': TYPE.caption, fill: MUTED,
    }));
    root.append(key);

    // ── rows ──
    items.forEach((it, i) => {
        const y = padT + i * (rowH + gap);
        // A banded background, so the eye tracks one item across both panels.
        if (i % 2 === 0) {
            root.append(el('rect', {
                x: padL, y: y - 1.5, width: width - padL - 6, height: rowH + 3, fill: SOFT,
            }));
        }
        // A gutter between the label column and the first bar, wide enough for
        // the reverse-key mark to sit in without landing on the data.
        root.append(text(it.text, {
            x: padL + labelW - 22, y: y + rowH - 3.5,
            'text-anchor': 'end', 'font-size': TYPE.label, fill: INK,
        }));
        // Reverse keyed items are marked on the figure. Without the mark a reader
        // comparing two rows would take a low bar as a bad result on both.
        if (it.reverse) {
            root.append(text('R', {
                x: padL + labelW - 14, y: y + rowH - 3.5,
                'font-size': TYPE.caption, fill: MUTED,
            }));
        }

        conditions.forEach((c, ci) => {
            const x0 = padL + labelW + ci * (panelW + 16);
            const row = (data.counts[c] || {})[it.id] || [];
            let x = x0;
            row.forEach((count, k) => {
                if (!count) return;
                const w = (count / maxTotal) * panelW;
                root.append(el('rect', {
                    x, y, width: w, height: rowH, fill: colors[k],
                }));
                // Only when it fits. A number wider than its band is unreadable
                // and lands on the neighbouring one.
                if (w >= 11) {
                    root.append(text(count, {
                        x: x + w / 2, y: y + rowH - 4,
                        'text-anchor': 'middle', 'font-size': TYPE.inBar,
                        fill: onColor(colors[k]),
                    }));
                }
                x += w;
            });
        });
    });

    // ── the difference panel ──
    if (estimates) {
        const x0 = padL + labelW + conditions.length * (panelW + 16) + 18;
        const finite = estimates.flatMap((e) => [e.low, e.high, e.mean]).filter((v) => v != null);
        const bound = Math.max(1, ...finite.map((v) => Math.abs(v)));
        const dx = (v) => x0 + ((v + bound) / (2 * bound)) * diffW;

        // Zero first, behind everything, so a bar crossing it stays readable.
        root.append(el('line', {
            x1: dx(0), y1: padT - 4, x2: dx(0), y2: padT + n * (rowH + gap) - gap + 2,
            stroke: RULE,
        }));

        estimates.forEach((e, i) => {
            const y = padT + i * (rowH + gap) + rowH / 2;
            if (e.low != null && e.high != null && !e.degenerate) {
                root.append(el('line', {
                    x1: dx(e.low), y1: y, x2: dx(e.high), y2: y, stroke: INK, 'stroke-width': 1.2,
                }));
                for (const end of [e.low, e.high]) {
                    root.append(el('line', {
                        x1: dx(end), y1: y - 3, x2: dx(end), y2: y + 3, stroke: INK,
                    }));
                }
            }
            if (e.mean != null) {
                root.append(el('circle', { cx: dx(e.mean), cy: y, r: 3, fill: INK }));
            }
            // Fewer than four pairs is not an interval, and saying so beats
            // drawing a dot that looks like one.
            if (e.n < 4) {
                root.append(text(`n=${e.n}`, {
                    x: x0 + diffW + 4, y: y + 3, 'font-size': TYPE.caption, fill: MUTED,
                }));
            }
        });

        const yAxis = padT + n * (rowH + gap) + 2;
        root.append(el('line', { x1: x0, y1: yAxis, x2: x0 + diffW, y2: yAxis, stroke: RULE }));
        // Always symmetric and always including zero, because zero is the value
        // a reader looks for first: it is where "no difference" sits, and an
        // unlabelled zero line makes every bar's position a guess.
        const step = bound > 2 ? Math.round(bound / 2) : 1;
        const ticks = [0];
        for (let v = step; v <= bound; v += step) ticks.unshift(-v), ticks.push(v);
        for (const v of ticks) {
            root.append(text(v, {
                x: dx(v), y: yAxis + 12, 'text-anchor': 'middle',
                'font-size': TYPE.tick, fill: v === 0 ? INK : MUTED,
            }));
        }
        root.append(text(`(${String.fromCharCode(97 + conditions.length)}) Mean difference & 95% CI`, {
            x: x0 + diffW / 2, y: yAxis + 26,
            'text-anchor': 'middle', 'font-size': TYPE.title, fill: INK,
        }));
    }

    // ── axes, one per panel ──
    const ticks = [0, Math.ceil(maxTotal / 2), maxTotal];
    conditions.forEach((c, ci) => {
        const x0 = padL + labelW + ci * (panelW + 16);
        const yAxis = padT + n * (rowH + gap) + 2;
        root.append(el('line', {
            x1: x0, y1: yAxis, x2: x0 + panelW, y2: yAxis, stroke: RULE,
        }));
        ticks.forEach((t) => {
            const x = x0 + (t / maxTotal) * panelW;
            root.append(el('line', { x1: x, y1: yAxis, x2: x, y2: yAxis + 3, stroke: RULE }));
            root.append(text(t, {
                x, y: yAxis + 12, 'text-anchor': 'middle',
                'font-size': TYPE.tick, fill: MUTED,
            }));
        });
        root.append(text(`(${String.fromCharCode(97 + ci)}) ${CONDITION_LABEL[c] || c}`, {
            x: x0 + panelW / 2, y: yAxis + 26,
            'text-anchor': 'middle', 'font-size': TYPE.title, fill: INK,
        }));
    });

    return root;
}

/**
 * Counts per point, from raw answers, for one condition.
 *
 * The scale is passed in rather than assumed. This figure draws the seven-point
 * blocks; the workload block is answered on twenty-one points and is reported
 * as a score with an interval instead, because twenty-one bands per row is not
 * a picture anybody reads. An answer off the scale's grid is
 * returned as a count rather than dropped, so drawing an item on the wrong
 * scale shows up as a number the caller can assert on rather than as a bar that
 * is merely short.
 *
 * @returns {{ counts: Record<string, number[]>, offScale: number }}
 */
export function tally(answersByParticipant, items, scale = { min: 1, max: 7, step: 1 }) {
    const step = scale.step || 1;
    const points = Math.round((scale.max - scale.min) / step) + 1;
    const counts = {};
    let offScale = 0;
    for (const it of items) {
        const row = new Array(points).fill(0);
        for (const a of answersByParticipant) {
            const v = a && a[it.id];
            if (typeof v !== 'number') continue;
            const k = (v - scale.min) / step;
            if (Number.isInteger(k) && k >= 0 && k < points) row[k] += 1;
            else offScale += 1;
        }
        counts[it.id] = row;
    }
    return { counts, offScale };
}

export const LIKERT_WIDTH = WIDTH;
