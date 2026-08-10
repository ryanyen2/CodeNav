import { describe, it, expect } from 'vitest';
import { featureState, stateBadge, type FeatureSignals } from '../state/feature-state';

describe('featureState — one state, ranked by lifecycle', () => {
    it('is settled when nothing is happening', () => {
        expect(featureState({ realized: true })).toBe('settled');
    });

    it('live agent work outranks everything — it explains why the rest is moving', () => {
        const all: FeatureSignals = {
            activeMode: 'write', proposalOp: 'amend', sent: true, staged: true, realized: false,
        };
        expect(featureState(all)).toBe('working');
    });

    it('a pending proposal outranks the states that need nothing from you', () => {
        expect(featureState({ proposalOp: 'amend', sent: true, staged: true, realized: false }))
            .toBe('proposed');
    });

    it('sent outranks staged, staged outranks planned', () => {
        expect(featureState({ sent: true, staged: true, realized: false })).toBe('sent');
        expect(featureState({ staged: true, realized: false })).toBe('staged');
        expect(featureState({ realized: false })).toBe('planned');
    });

    it('a read touch is still "working"', () => {
        expect(featureState({ activeMode: 'read' })).toBe('working');
    });
});

describe('stateBadge — at most one glyph, always explained', () => {
    it('draws nothing for the two states the row itself carries', () => {
        expect(stateBadge('planned')).toBeNull();
        expect(stateBadge('settled')).toBeNull();
    });

    it('working is a CSS dot (motion carries "now"), not a glyph', () => {
        const b = stateBadge('working', { activeMode: 'write' })!;
        expect(b.icon).toBeNull();
        expect(b.cls).toBe('working write');
    });

    it('distinguishes reading from changing in the hover text', () => {
        expect(stateBadge('working', { activeMode: 'write' })!.title).toMatch(/changing/);
        expect(stateBadge('working', { activeMode: 'read' })!.title).toMatch(/reading/);
    });

    it('every drawn state says what to do about it', () => {
        expect(stateBadge('proposed')!.title).toMatch(/accept or reject/);
        expect(stateBadge('sent')!.title).toMatch(/nothing to do/);
        expect(stateBadge('staged')!.title).toMatch(/⌘S/);
    });

    it('names WHAT is queued, so "has code but waiting" stops reading as a contradiction', () => {
        const q = { queuedIntent: 'add a QUERY verb helper' };
        expect(stateBadge('sent', q)!.title).toMatch(/It will add a QUERY verb helper\./);
        expect(stateBadge('staged', q)!.title).toMatch(/agent will add a QUERY verb helper once you send it/);
    });

    it('falls back to the generic wording when no gloss was recorded', () => {
        expect(stateBadge('sent')!.title).toMatch(/change the code to match/);
        expect(stateBadge('staged')!.title).toMatch(/⌘S/);
    });

    it('divergence is a reason inside "proposed", not a seventh badge', () => {
        const b = stateBadge('proposed', { divergent: true })!;
        expect(b.cls).toBe('proposed');
        expect(b.title).toMatch(/while implementing another of your edits/);
    });

    it('sent and staged are distinct SHAPES, so the state survives without hue', () => {
        expect(stateBadge('sent')!.icon).toBe('diamond-fill');
        expect(stateBadge('staged')!.icon).toBe('circle-dashed');
        expect(stateBadge('proposed')!.icon).toBe('diamond');
    });
});
