/**
 * grammar.test.ts — guards U3's disagreement grammar: colour=direction, shape=kind,
 * and the mappings are total + lossless vs the old 6-colour op-type system.
 */
import { describe, it, expect } from 'vitest';
import {
    directionColorVar, directionActions, directionLabel, kindGlyph,
    type Direction, type Kind,
} from '../state/grammar';

const DIRECTIONS: Direction[] = ['code-ahead', 'doc-ahead'];
const KINDS: Kind[] = ['amend', 'add', 'move', 'retire'];

describe('U3 — direction axis (colour + actions)', () => {
    it('uses exactly two directional hues', () => {
        const colors = new Set(DIRECTIONS.map(directionColorVar));
        expect(colors).toEqual(new Set(['var(--dir-review)', 'var(--dir-await)']));
    });

    it('code-ahead is resolved by the human (Reject/Accept)', () => {
        expect(directionActions('code-ahead')).toEqual(['Reject', 'Accept']);
    });

    it('doc-ahead offers the human only Withdraw — apply belongs to the AI side', () => {
        expect(directionActions('doc-ahead')).toEqual(['Withdraw']);
    });

    it('carries a non-colour direction label for colourblind parity (R8)', () => {
        expect(directionLabel('code-ahead')).toMatch(/code/);
        expect(directionLabel('doc-ahead')).toMatch(/your/);
    });
});

describe('U3 — kind axis (shape)', () => {
    it('maps every kind to a distinct glyph (total, lossless)', () => {
        const glyphs = KINDS.map(kindGlyph);
        expect(glyphs.every(g => g.length > 0)).toBe(true);
        expect(new Set(glyphs).size).toBe(KINDS.length); // no two kinds share a glyph
    });
});
