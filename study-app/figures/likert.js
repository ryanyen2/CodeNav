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

/**
 * @param {object} data
 *   items:      [{ id, text, reverse }]
 *   conditions: ['codoc','baseline']
 *   counts:     { [condition]: { [itemId]: number[] } }  index 0 = point 1
 *   points:     7
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
    const width = padL + labelW + conditions.length * (panelW + 16) + 10;
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

/** Counts per point, from raw answers, for one condition. */
export function tally(answersByParticipant, items, points = 7) {
    const out = {};
    for (const it of items) {
        const row = new Array(points).fill(0);
        for (const a of answersByParticipant) {
            const v = a && a[it.id];
            if (typeof v === 'number' && v >= 1 && v <= points) row[v - 1] += 1;
        }
        out[it.id] = row;
    }
    return out;
}

export const LIKERT_WIDTH = WIDTH;
