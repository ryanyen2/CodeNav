/**
 * icons.test.ts — guards the lifecycle iconography sprite (P0 / spec §C.1). Node-env, so
 * the pure registry + string builder are tested directly (the DOM `icon()` wrapper is
 * verified visually in the EDH gate). The icons carry the lifecycle vocabulary the whole
 * redesign reads off, so a missing/malformed glyph is a real regression.
 */
import { describe, it, expect } from 'vitest';
import { iconSvg, iconMaskDataUri, IconName } from '../webview/icons';

const ALL: IconName[] = [
    'circle-dashed', 'diamond', 'diamond-fill', 'pen-nib', 'arrows-clockwise',
    'warning-diamond', 'check-circle', 'x-circle', 'paper-plane-tilt',
    'arrow-bend-down-left', 'magnifying-glass', 'eye',
];

describe('icons — the §C.1 lifecycle sprite', () => {
    it('every named glyph resolves to a well-formed currentColor <svg> with path data', () => {
        for (const name of ALL) {
            const svg = iconSvg(name);
            expect(svg.startsWith('<svg')).toBe(true);
            expect(svg.endsWith('</svg>')).toBe(true);
            expect(svg).toContain('fill="currentColor"');     // hue rides CSS `color`
            expect(svg).toContain('viewBox="0 0 256 256"');    // the canonical Phosphor box
            expect(svg).toContain('aria-hidden="true"');       // meaning rides the host label
            expect(svg).toMatch(/<path d="M[\d.,-]/);          // real path data, not empty
            expect(svg).toContain('class="ce-icon"');          // base sizing class
        }
    });

    it('an extra className composes onto the base ce-icon class', () => {
        expect(iconSvg('diamond', { className: 'foo' })).toContain('class="ce-icon foo"');
    });

    it('a title is emitted and XML-escaped (the only interpolated field)', () => {
        const svg = iconSvg('eye', { title: 'a & b < c > "d"' });
        expect(svg).toContain('<title>a &amp; b &lt; c &gt; &quot;d&quot;</title>');
    });

    it('the captured/pending/divergent ramp maps to distinct shapes (not the same glyph)', () => {
        // shape = family: a viewer must read captured ≠ pending ≠ divergent at a glance.
        const captured = iconSvg('circle-dashed');
        const pending = iconSvg('diamond');
        const divergent = iconSvg('warning-diamond');
        expect(captured).not.toEqual(pending);
        expect(pending).not.toEqual(divergent);
        expect(captured).not.toEqual(divergent);
    });
});

describe('iconMaskDataUri — single-sources the CSS phase-glyph masks (P0 polish §C.4)', () => {
    it('produces a percent-encoded svg data-URI carrying the glyph path', () => {
        const uri = iconMaskDataUri('pen-nib');
        expect(uri.startsWith('url("data:image/svg+xml,')).toBe(true);
        expect(uri.endsWith('")')).toBe(true);
        expect(uri).toContain('%3Csvg');     // < encoded
        expect(uri).toContain('%3Cpath');    // the path is present
        expect(uri).not.toContain('<');      // no raw angle brackets leak into the url()
    });
    it('the editing + reflecting masks are the SAME glyphs icons.ts ships (no drift)', () => {
        // these are the two the CSS pseudo-elements read via --phase-glyph-*; a drift here
        // would desync the heading glyph from the icon registry.
        expect(iconMaskDataUri('pen-nib')).not.toEqual(iconMaskDataUri('arrows-clockwise'));
        expect(iconMaskDataUri('pen-nib')).toContain('256 256'); // canonical viewBox
    });
});
