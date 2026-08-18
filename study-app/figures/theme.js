// The look of every figure that goes in the paper.
//
// One rule runs through this file: colour, size and typeface are set as SVG
// attributes, never through a stylesheet. Serializing the element is then enough
// to get a file that stands on its own in a LaTeX document, with no CSS to carry
// along, no rasterized layer, and text still text — so it stays selectable in
// the PDF and can be re-typeset if a reviewer asks for a bigger font.
//
// The dashboard draws the same figures it exports. A figure that looks one way
// on screen and another in the paper is a figure nobody checked.

export const FONT = 'Helvetica, Arial, sans-serif';

export const TYPE = { tick: 9, label: 10, title: 11, inBar: 8.5, caption: 8.5 };

export const INK = '#1a1a18';
export const RULE = '#cfd4da';
export const SOFT = '#f2f4f6';
export const MUTED = '#6b7280';

/** ACM column widths, in points at 72dpi. */
export const WIDTH = { single: 3.33 * 72, double: 7.0 * 72 };

/**
 * The conditions, named by what they are rather than by which one is ours.
 *
 * A figure captioned "ours vs theirs" invites the reader to discount it, and the
 * dashboard uses the same labels so nobody holds a mapping in their head while
 * reading a chart during a session.
 */
export const CONDITION_LABEL = { codoc: 'codoc', baseline: 'CLAUDE.md' };
export const CONDITION_COLOR = { codoc: '#4a90d9', baseline: '#e8b93c' };

/**
 * The action colours, grouped by surface rather than spread around the wheel.
 *
 * Everything touching the description is green, code is blue, tests purple,
 * agent orange, runs brown. Within a group, reading is pale and writing is
 * solid, so a glance at the time profile says whether a band is attention or
 * change without reading the legend.
 */
export const ACTION_COLOR = {
    READ_DOC: '#a8ceb9', EDIT_DOC: '#2f6f4e', AGENT_DOC: '#6aa583',
    READ_CODE: '#a9c4de', EDIT_CODE: '#2f5f8c', AGENT_EDIT: '#5f8fbd',
    READ_TEST: '#c3b4d9', EDIT_TEST: '#6b4fa0',
    READ_OUTPUT: '#a9d5d2', EDIT_OUTPUT: '#3f7f7a',
    PROMPT: '#e0a06a', ACCEPT: '#c2763a', REJECT: '#8c4a2f', ASK: '#eebb90',
    RUN_TEST: '#b39b7a', RUN_BUILD: '#8a7250',
    IDLE: '#e6e4e0',
};

/** Reading order for a stacked figure: attending, then changing, then checking. */
export const ACTION_ORDER = [
    'READ_DOC', 'READ_CODE', 'READ_TEST', 'READ_OUTPUT',
    'EDIT_DOC', 'EDIT_CODE', 'EDIT_TEST', 'EDIT_OUTPUT',
    'PROMPT', 'AGENT_DOC', 'AGENT_EDIT', 'ASK', 'ACCEPT', 'REJECT',
    'RUN_TEST', 'RUN_BUILD', 'IDLE',
];

export const ACTION_LABEL = {
    READ_DOC: 'Read description', READ_CODE: 'Read code', READ_TEST: 'Read tests',
    EDIT_DOC: 'Wrote description', EDIT_CODE: 'Wrote code', EDIT_TEST: 'Wrote tests',
    READ_OUTPUT: 'Read a sample or its output', EDIT_OUTPUT: 'Edited a sample',
    PROMPT: 'Prompted', AGENT_DOC: 'Agent wrote description', AGENT_EDIT: 'Agent wrote code',
    ASK: 'Asked for a walkthrough', ACCEPT: 'Accepted', REJECT: 'Rejected',
    RUN_TEST: 'Ran tests', RUN_BUILD: 'Ran the project', IDLE: 'Away',
};

/**
 * A seven step ramp for a seven point scale, orange at disagree and blue at
 * agree, pale in the middle.
 *
 * The ends stay separable in greyscale, which is how a good share of reviewers
 * will read it, and they are pulled in from the extremes so white numerals
 * inside a bar remain legible.
 */
export function likertColors(points = 7) {
    // Brown at disagree, cream at the middle, teal at agree. The ends differ
    // enough in lightness to stay separable in greyscale, which is how a good
    // share of reviewers will read it, and the middle is pale so a neutral
    // answer does not draw the eye the way a coloured one would.
    const low = ['#a8542a', '#c98a5e', '#e3c39a'];
    const mid = '#efece3';
    const high = ['#a8c4b0', '#5f9c8a', '#2f7d6e'];
    if (points === 7) return [...low, mid, ...high];
    const all = [...low, mid, ...high];
    return Array.from({ length: points }, (_, i) =>
        all[Math.round((i / (points - 1)) * (all.length - 1))]);
}

/** Readable text on a given fill. */
export function onColor(fill) {
    const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(fill);
    if (!m) return INK;
    const [r, g, b] = [1, 2, 3].map((i) => parseInt(m[i], 16));
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.6 ? INK : '#ffffff';
}

// ── building SVG without a framework ─────────────────────────────────────────

const NS = 'http://www.w3.org/2000/svg';

export function el(name, attrs = {}, children = []) {
    const node = document.createElementNS(NS, name);
    for (const [k, v] of Object.entries(attrs)) {
        if (v !== null && v !== undefined) node.setAttribute(k, String(v));
    }
    for (const c of [].concat(children)) {
        if (c) node.append(typeof c === 'string' ? document.createTextNode(c) : c);
    }
    return node;
}

/** Text with the font baked in, because the export carries no stylesheet. */
export function text(str, attrs = {}) {
    return el('text', {
        'font-family': FONT, 'font-size': TYPE.label, fill: INK, ...attrs,
    }, String(str));
}

export function svg(width, height, title) {
    // No xmlns attribute here on purpose: the element is already in the SVG
    // namespace via createElementNS, so the serializer emits one. Setting it as
    // a literal attribute as well produced "Attribute xmlns redefined" and every
    // exported figure failed to parse.
    const node = el('svg', {
        width, height, viewBox: `0 0 ${width} ${height}`, 'font-family': FONT,
    });
    // Named for a screen reader and for anyone opening the file on its own.
    if (title) node.append(el('title', {}, title));
    // White rather than transparent: a transparent figure dropped on a dark slide
    // loses every dark label on it.
    node.append(el('rect', { x: 0, y: 0, width, height, fill: '#ffffff' }));
    return node;
}
