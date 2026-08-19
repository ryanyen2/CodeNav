/**
 * grammar.test.ts — guards U3's disagreement grammar: colour=direction, shape=kind,
 * and the mappings are total + lossless vs the old 6-colour op-type system.
 */
import { describe, it, expect } from 'vitest';
import {
    directionColorVar, directionActions, directionLabel, directionOrigin, directionNote,
    kindGlyph, verdictHints,
    type Direction, type Kind,
} from '../state/grammar';

const DIRECTIONS: Direction[] = ['code-ahead', 'doc-ahead', 'yours'];
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

describe('the author\'s own deferred edit is not the codebase', () => {
    // A contended edit is parked as a pending proposal carrying the AUTHOR's words
    // (loop_b._resolve_content). Every sidecar proposal used to read `code-ahead`, so
    // the verdict strip printed "from code" over a sentence the reader had just typed.
    it('never says the change came from code', () => {
        expect(directionOrigin('yours')).not.toMatch(/from code/);
        expect(directionOrigin('yours')).toMatch(/your version/);
        expect(directionOrigin('yours')).toMatch(/review/);
    });

    it('is resolved by the human, like code-ahead — not withdrawn like doc-ahead', () => {
        expect(directionActions('yours')).toEqual(['Reject', 'Accept']);
    });

    it('keeps the two-hue grammar: the reader resolves it, so it takes the review hue', () => {
        expect(directionColorVar('yours')).toBe(directionColorVar('code-ahead'));
    });

    it('explains why the words are un-applied, and promises nothing was lost', () => {
        expect(directionNote('yours')).toMatch(/same lines/);
        expect(directionNote('yours')).toMatch(/nothing of yours was thrown away/i);
    });

    it('does not describe accepting as matching code that already exists', () => {
        // `record`-grade for both, but the SENTENCE differs: one adopts the codebase's
        // wording, the other re-applies the reader's own over the text that beat it.
        const mine = verdictHints('yours', 'record');
        expect(mine.accept).toMatch(/your wording/);
        expect(mine.accept).not.toBe(verdictHints('code-ahead', 'record').accept);
        expect(mine.reject).toMatch(/your version/);
    });
});

describe('U3 — kind axis (shape)', () => {
    it('maps every kind to a distinct glyph (total, lossless)', () => {
        const glyphs = KINDS.map(kindGlyph);
        expect(glyphs.every(g => g.length > 0)).toBe(true);
        expect(new Set(glyphs).size).toBe(KINDS.length); // no two kinds share a glyph
    });
});
