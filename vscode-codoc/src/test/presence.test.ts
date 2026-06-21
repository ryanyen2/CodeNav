/**
 * presence.test.ts — the pure logic of agent presence (P3 / spec §B). The DOM (the floating
 * avatar, glide, trail, tree twin) is EDH-only; this covers the mappings the whole feature
 * reads off — phase→glyph/verb, the realize-progress whisper, the multi-agent stack/collapse,
 * the off-screen clamp math, and the derivation from the live sync signal.
 */
import { describe, it, expect } from 'vitest';
import {
    phaseGlyph, phaseVerb, realizeWhisper, presenceWhisper, deriveAgentPresences,
    clampToViewport, overlayAnchor, roleName, roleInk, Rect,
} from '../state/presence';

describe('phase → glyph / verb (§B.1 / §B.4)', () => {
    it('maps each phase to its icon-registry glyph', () => {
        expect(phaseGlyph('editing')).toBe('pen-nib');
        expect(phaseGlyph('reflecting')).toBe('arrows-clockwise');
        expect(phaseGlyph('read')).toBe('eye');
        expect(phaseGlyph('done')).toBe('arrows-clockwise');
    });
    it('maps each phase to its present-continuous lowercase verb', () => {
        expect(phaseVerb('editing')).toBe('implementing');
        expect(phaseVerb('reflecting')).toBe('syncing the tree');
        expect(phaseVerb('read')).toBe('reading');
        expect(phaseVerb('done')).toBe('done');
    });
});

describe('realizeWhisper — folds sync.realize into the verb', () => {
    it('shows done/total · current title when realizing', () => {
        expect(realizeWhisper('implementing', { done: 3, total: 5, current: 'Persist drafts' }))
            .toBe('implementing 3/5 · Persist drafts');
    });
    it('omits the title fragment when there is no current title', () => {
        expect(realizeWhisper('implementing', { done: 1, total: 2, current: '' }))
            .toBe('implementing 1/2');
    });
    it('degrades to the bare verb when realize is undefined or has no total', () => {
        expect(realizeWhisper('implementing')).toBe('implementing');
        expect(realizeWhisper('implementing', { done: 0, total: 0, current: 'x' })).toBe('implementing');
    });
});

describe('presenceWhisper — the full label (§B.4 copy table)', () => {
    it('editing folds in realize progress', () => {
        expect(presenceWhisper('Claude', 'editing', 'Drafts', { done: 2, total: 4, current: 'Drafts' }))
            .toBe('Claude · implementing 2/4 · Drafts');
    });
    it('editing without realize is just the verb', () => {
        expect(presenceWhisper('Claude', 'editing', 'Drafts')).toBe('Claude · implementing');
    });
    it('reflecting / read / done read per the table', () => {
        expect(presenceWhisper('Codex', 'reflecting', 'Drafts')).toBe('Codex · syncing the tree');
        expect(presenceWhisper('Claude', 'read', 'Loop A')).toBe('Claude · reading Loop A');
        expect(presenceWhisper('Claude', 'done', 'Drafts')).toBe('Claude · done');
    });
});

describe('roleName / roleInk — the avatar identity + tint', () => {
    it('names known roles and Title-cases unknown ones', () => {
        expect(roleName('claude-code')).toBe('Claude');
        expect(roleName('codex')).toBe('Codex');
        expect(roleName('mystery')).toBe('Mystery');
    });
    it('maps the role to its --ink-* class suffix, defaulting to claude', () => {
        expect(roleInk('codex')).toBe('codex');
        expect(roleInk('gemini')).toBe('gemini');
        expect(roleInk('claude-code')).toBe('claude');
        expect(roleInk('unknown')).toBe('claude');
    });
});

describe('deriveAgentPresences — from the live sync signal (§B.1)', () => {
    it('parks the avatar on the editing feature (editing wins over read)', () => {
        const p = deriveAgentPresences({ 'f-a': 'editing', 'f-b': 'reflecting' }, ['f-c']);
        expect(p).toHaveLength(1);
        expect(p[0]).toMatchObject({ fid: 'f-a', phase: 'editing', name: 'Claude' });
    });
    it('falls back to reflecting, then to a bare read', () => {
        expect(deriveAgentPresences({ 'f-b': 'reflecting' }, [])[0]).toMatchObject({ fid: 'f-b', phase: 'reflecting' });
        expect(deriveAgentPresences({}, ['f-c'])[0]).toMatchObject({ fid: 'f-c', phase: 'read' });
    });
    it('is empty when nothing is active', () => {
        expect(deriveAgentPresences({}, [])).toEqual([]);
    });
    it('honours a non-default role', () => {
        expect(deriveAgentPresences({ 'f-a': 'editing' }, [], 'codex')[0]).toMatchObject({ role: 'codex', name: 'Codex' });
    });
});

describe('clampToViewport — off-screen avatar (§B.5)', () => {
    it('pins to the top with ↑ when the target is above the viewport', () => {
        expect(clampToViewport(-50, 0, 600)).toEqual({ y: 8, chevron: '↑' });
    });
    it('pins to the bottom with ↓ when the target is below the viewport', () => {
        expect(clampToViewport(900, 0, 600)).toEqual({ y: 592, chevron: '↓' });
    });
    it('passes the target through with no chevron when in view', () => {
        expect(clampToViewport(300, 0, 600)).toEqual({ y: 300, chevron: null });
    });
    it('honours a custom pad', () => {
        expect(clampToViewport(-1, 0, 600, 20)).toEqual({ y: 20, chevron: '↑' });
    });
});

describe('overlayAnchor — the scroll-drift fix (§B.2)', () => {
    // The overlay (.doc-host) is fixed; the heading rect changes as the surface scrolls. The
    // avatar top = heading.top - overlay.top + centring — so it must TRACK the heading 1:1.
    const overlay: Rect = { top: 100, left: 50, right: 650, height: 800 };
    const heading = (top: number): Rect => ({ top, left: 60, right: 300, height: 24 });

    it('centres the avatar on the heading mid-line, just past its right edge', () => {
        // heading at viewport top=200 → overlay-relative top = 200-100 + 24/2 - 16/2 = 104
        // left = (300-50)+8 = 258, clamped under maxRight (650-50-28=572)
        expect(overlayAnchor(heading(200), overlay)).toEqual({ top: 104, left: 258 });
    });

    it('TRACKS the heading 1:1 as the surface scrolls (no drift)', () => {
        const atTop = overlayAnchor(heading(200), overlay).top;
        // scroll the doc up 150px → the heading's viewport top drops to 50
        const scrolled = overlayAnchor(heading(50), overlay).top;
        expect(atTop - scrolled).toBe(150);   // avatar moved exactly with the heading
    });

    it('clamps left to maxRight so the avatar never overflows the overlay', () => {
        const wide = { ...heading(200), right: 99999 };
        expect(overlayAnchor(wide, overlay).left).toBe(650 - 50 - 28); // maxRight
    });

    it('honours custom avatarSize / gap', () => {
        // top = 200-100 + 30/2 - 20/2 = 105 ; left = (300-50)+12 = 262
        expect(overlayAnchor({ top: 200, left: 60, right: 300, height: 30 }, overlay, { avatarSize: 20, gap: 12 }))
            .toEqual({ top: 105, left: 262 });
    });
});
