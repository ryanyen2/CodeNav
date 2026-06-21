/**
 * icons.ts — the lifecycle iconography sprite (P0 / spec §C.1).
 *
 * Phosphor Icons (MIT) supply ONE shape family whose weight axis (thin → regular →
 * fill) maps onto our lifecycle intensity ramp: captured = thin, pending = regular,
 * resolving = fill. We do NOT ship the runtime dep or a webfont — the ~12 raw `d`
 * paths the spec names are inlined below (tree-shaken to exactly what we draw) and
 * rendered as `currentColor` SVG so HUE stays the existing direction tokens and SIZE
 * is pure CSS. A viewer learns "dashed circle = mine and local, diamond = sent,
 * filled pen = the AI is on it" in one frame.
 *
 * The pure registry + `iconSvg` string builder are unit-tested (icons.test.ts); the
 * DOM helper `icon()` is a thin wrapper that parses that string into an <svg> element.
 */

/** Every lifecycle glyph we draw, named per the spec §C.1 table. Each entry is the
 *  Phosphor name at the weight the table calls for — the weight IS the intensity ramp,
 *  so e.g. `circle-dashed` is only ever drawn thin (captured/unrealized) here. */
export type IconName =
    | 'circle-dashed'        // captured / unrealized (thin)
    | 'diamond'              // pending / staged (regular)
    | 'diamond-fill'         // queued — sent (fill)
    | 'pen-nib'              // resolving: agent mid-edit (fill)
    | 'arrows-clockwise'     // resolving: agent syncing the tree (regular)
    | 'warning-diamond'      // divergent (regular)
    | 'check-circle'         // accept / landed (fill)
    | 'x-circle'             // reject / dismissed (regular)
    | 'paper-plane-tilt'     // hand-to-agent / sent (regular)
    | 'arrow-bend-down-left' // code→doc spark (regular)
    | 'magnifying-glass'     // search (thin)
    | 'eye';                 // agent reading (regular)

/** The inner path markup for each glyph — extracted verbatim from `@phosphor-icons/core`
 *  (MIT) at the spec's weight, on the canonical 256×256 viewBox. `fill="currentColor"` is
 *  applied on the wrapping <svg>, so every glyph inherits its lifecycle hue from CSS. */
const PATHS: Record<IconName, string> = {
    'circle-dashed':
        '<path d="M155.87,36.06a4,4,0,0,1-3.87,3,4.29,4.29,0,0,1-1-.13,92,92,0,0,0-46,0,4,4,0,0,1-2-7.74,100.09,100.09,0,0,1,50,0A4,4,0,0,1,155.87,36.06ZM56.65,57.94a100.18,100.18,0,0,0-25,43.29,4,4,0,0,0,7.71,2.14,92.06,92.06,0,0,1,23-39.82,4,4,0,1,0-5.7-5.61ZM39.36,152.62a4,4,0,0,0-7.71,2.14,100.08,100.08,0,0,0,25,43.31,4,4,0,1,0,5.71-5.61A91.91,91.91,0,0,1,39.36,152.62ZM151,217.09a92,92,0,0,1-46,0,4,4,0,0,0-2,7.75,100,100,0,0,0,50,0,4,4,0,1,0-2-7.74Zm70.58-67.25a4,4,0,0,0-4.92,2.79,92.12,92.12,0,0,1-23,39.82,4,4,0,1,0,5.7,5.61,100.18,100.18,0,0,0,25-43.29A4,4,0,0,0,221.58,149.84Zm-4.94-46.46a4,4,0,0,0,7.71-2.14,100.08,100.08,0,0,0-25-43.31,4,4,0,1,0-5.71,5.61A91.91,91.91,0,0,1,216.64,103.38Z"/>',
    'diamond':
        '<path d="M235.33,116.72,139.28,20.66a16,16,0,0,0-22.56,0l-96,96.06a16,16,0,0,0,0,22.56l96.05,96.06h0a16,16,0,0,0,22.56,0l96.05-96.06a16,16,0,0,0,0-22.56ZM128,224h0L32,128,128,32,224,128Z"/>',
    'diamond-fill':
        '<path d="M240,128a15.85,15.85,0,0,1-4.67,11.28l-96.05,96.06a16,16,0,0,1-22.56,0h0l-96-96.06a16,16,0,0,1,0-22.56l96.05-96.06a16,16,0,0,1,22.56,0l96.05,96.06A15.85,15.85,0,0,1,240,128Z"/>',
    'pen-nib':
        '<path d="M243.31,81.36,174.63,12.68a16,16,0,0,0-22.63,0L123.56,41.12l-58,21.76A16,16,0,0,0,55.36,75.23L34.59,199.83a4,4,0,0,0,6.77,3.49l57-57a23.85,23.85,0,0,1-2.29-12.08,24,24,0,1,1,13.6,23.4l-57,57a4,4,0,0,0,3.49,6.77l124.61-20.77a16,16,0,0,0,12.35-10.16l21.77-58.07L243.31,104a16,16,0,0,0,0-22.63ZM208,116.68,139.32,48l24-24L232,92.68Z"/>',
    'arrows-clockwise':
        '<path d="M224,48V96a8,8,0,0,1-8,8H168a8,8,0,0,1,0-16h28.69L182.06,73.37a79.56,79.56,0,0,0-56.13-23.43h-.45A79.52,79.52,0,0,0,69.59,72.71,8,8,0,0,1,58.41,61.27a96,96,0,0,1,135,.79L208,76.69V48a8,8,0,0,1,16,0ZM186.41,183.29a80,80,0,0,1-112.47-.66L59.31,168H88a8,8,0,0,0,0-16H40a8,8,0,0,0-8,8v48a8,8,0,0,0,16,0V179.31l14.63,14.63A95.43,95.43,0,0,0,130,222.06h.53a95.36,95.36,0,0,0,67.07-27.33,8,8,0,0,0-11.18-11.44Z"/>',
    'warning-diamond':
        '<path d="M128,72a8,8,0,0,1,8,8v56a8,8,0,0,1-16,0V80A8,8,0,0,1,128,72ZM116,172a12,12,0,1,0,12-12A12,12,0,0,0,116,172Zm124-44a15.85,15.85,0,0,1-4.67,11.28l-96.05,96.06a16,16,0,0,1-22.56,0h0l-96-96.06a16,16,0,0,1,0-22.56l96.05-96.06a16,16,0,0,1,22.56,0l96.05,96.06A15.85,15.85,0,0,1,240,128Zm-16,0L128,32,32,128,128,224h0Z"/>',
    'check-circle':
        '<path d="M128,24A104,104,0,1,0,232,128,104.11,104.11,0,0,0,128,24Zm45.66,85.66-56,56a8,8,0,0,1-11.32,0l-24-24a8,8,0,0,1,11.32-11.32L112,148.69l50.34-50.35a8,8,0,0,1,11.32,11.32Z"/>',
    'x-circle':
        '<path d="M165.66,101.66,139.31,128l26.35,26.34a8,8,0,0,1-11.32,11.32L128,139.31l-26.34,26.35a8,8,0,0,1-11.32-11.32L116.69,128,90.34,101.66a8,8,0,0,1,11.32-11.32L128,116.69l26.34-26.35a8,8,0,0,1,11.32,11.32ZM232,128A104,104,0,1,1,128,24,104.11,104.11,0,0,1,232,128Zm-16,0a88,88,0,1,0-88,88A88.1,88.1,0,0,0,216,128Z"/>',
    'paper-plane-tilt':
        '<path d="M227.32,28.68a16,16,0,0,0-15.66-4.08l-.15,0L19.57,82.84a16,16,0,0,0-2.49,29.8L102,154l41.3,84.87A15.86,15.86,0,0,0,157.74,248q.69,0,1.38-.06a15.88,15.88,0,0,0,14-11.51l58.2-191.94c0-.05,0-.1,0-.15A16,16,0,0,0,227.32,28.68ZM157.83,231.85l-.05.14,0-.07-40.06-82.3,48-48a8,8,0,0,0-11.31-11.31l-48,48L24.08,98.25l-.07,0,.14,0L216,40Z"/>',
    'arrow-bend-down-left':
        '<path d="M232,56A104.11,104.11,0,0,1,128,160H51.31l34.35,34.34a8,8,0,0,1-11.32,11.32l-48-48a8,8,0,0,1,0-11.32l48-48a8,8,0,0,1,11.32,11.32L51.31,144H128a88.1,88.1,0,0,0,88-88,8,8,0,0,1,16,0Z"/>',
    'magnifying-glass':
        '<path d="M226.83,221.17l-52.7-52.7a84.1,84.1,0,1,0-5.66,5.66l52.7,52.7a4,4,0,0,0,5.66-5.66ZM36,112a76,76,0,1,1,76,76A76.08,76.08,0,0,1,36,112Z"/>',
    'eye':
        '<path d="M247.31,124.76c-.35-.79-8.82-19.58-27.65-38.41C194.57,61.26,162.88,48,128,48S61.43,61.26,36.34,86.35C17.51,105.18,9,124,8.69,124.76a8,8,0,0,0,0,6.5c.35.79,8.82,19.57,27.65,38.4C61.43,194.74,93.12,208,128,208s66.57-13.26,91.66-38.34c18.83-18.83,27.3-37.61,27.65-38.4A8,8,0,0,0,247.31,124.76ZM128,192c-30.78,0-57.67-11.19-79.93-33.25A133.47,133.47,0,0,1,25,128,133.33,133.33,0,0,1,48.07,97.25C70.33,75.19,97.22,64,128,64s57.67,11.19,79.93,33.25A133.46,133.46,0,0,1,231.05,128C223.84,141.46,192.43,192,128,192Zm0-112a48,48,0,1,0,48,48A48.05,48.05,0,0,0,128,80Zm0,80a32,32,0,1,1,32-32A32,32,0,0,1,128,160Z"/>',
};

export interface IconOpts {
    /** Extra class(es) on the <svg> — sizing + colour live in CSS, never inline. */
    className?: string;
    /** Native tooltip on the glyph. */
    title?: string;
}

/** The raw `<svg>…</svg>` markup string for a glyph (pure — unit-tested). Always carries
 *  the `ce-icon` base class (size + vertical rhythm), `fill="currentColor"` (hue from CSS),
 *  and `aria-hidden` (the meaning rides the host element's title/label, not the glyph). */
export function iconSvg(name: IconName, opts: IconOpts = {}): string {
    const cls = opts.className ? `ce-icon ${opts.className}` : 'ce-icon';
    const title = opts.title ? `<title>${escapeXml(opts.title)}</title>` : '';
    return `<svg class="${cls}" viewBox="0 0 256 256" fill="currentColor" `
        + `aria-hidden="true" focusable="false">${title}${PATHS[name]}</svg>`;
}

/** Build a live <svg> element for a glyph. Parses `iconSvg` via a template so the inline
 *  markup never touches innerHTML on a live DOM node directly (CSP-clean; the SVG is a
 *  static literal, no interpolation into the path data). */
export function icon(name: IconName, opts: IconOpts = {}): SVGElement {
    const tpl = document.createElement('template');
    tpl.innerHTML = iconSvg(name, opts);
    return tpl.content.firstElementChild as SVGElement;
}

/** A `url("data:…")` CSS value for a glyph, for `mask-image` (P0/§C.4). Single-sources the
 *  path data from PATHS so a CSS pseudo-element (no DOM widget to set inline style on) can
 *  draw the SAME glyph as `icon()` without a hand-copied data-URI that could drift. The mask
 *  uses the silhouette, so colour comes from the element's `background` (currentColor-style).
 *  Pure (unit-tested); the webview sets these as `--phase-glyph-*` vars at startup. */
export function iconMaskDataUri(name: IconName): string {
    const svg = `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 256 256'>${PATHS[name]}</svg>`;
    // Minimal percent-encoding for an inline svg data-URI (the path data has no #/&; encode
    // the few reserved chars a data-URI needs).
    const enc = svg.replace(/"/g, "'").replace(/</g, '%3C').replace(/>/g, '%3E').replace(/#/g, '%23');
    return `url("data:image/svg+xml,${enc}")`;
}

/** Minimal XML escaping for the optional <title> text (the only interpolated field). */
function escapeXml(s: string): string {
    return s.replace(/[&<>"]/g, c => (
        c === '&' ? '&amp;' : c === '<' ? '&lt;' : c === '>' ? '&gt;' : '&quot;'
    ));
}
