/**
 * ui-state.test.ts — guards U5's pure (de)serialization. The persist/restore wiring + caret
 * ordering + resize reposition are verified in the EDH gate (U7).
 */
import { describe, it, expect } from 'vitest';
import { serializeUiState, deserializeUiState } from '../webview/ui-state';

const sample = {
    selectedId: 'f-abc123',
    expanded: ['f-a', 'f-b'],
    caretPos: 42,
    treeScroll: 120,
    docScroll: 880,
};

describe('U5 — UI state round-trip (R6)', () => {
    it('round-trips losslessly through serialize → deserialize', () => {
        expect(deserializeUiState(serializeUiState(sample))).toEqual({ v: 1, ...sample });
    });
    it('stamps the version on serialize', () => {
        expect(serializeUiState(sample).v).toBe(1);
    });
});

describe('U5 — tolerant deserialize (no throw, safe defaults)', () => {
    it('returns null for a null / non-object prior state', () => {
        expect(deserializeUiState(null)).toBeNull();
        expect(deserializeUiState(undefined)).toBeNull();
        expect(deserializeUiState('nope')).toBeNull();
    });
    it('returns null for a legacy/unknown version (caller falls back to defaults)', () => {
        expect(deserializeUiState({ selectedId: 'f-x' })).toBeNull(); // no v
        expect(deserializeUiState({ v: 2, selectedId: 'f-x' })).toBeNull();
    });
    it('fills safe defaults for a partial v:1 state', () => {
        expect(deserializeUiState({ v: 1 })).toEqual({
            v: 1, selectedId: null, expanded: [], caretPos: 0, treeScroll: 0, docScroll: 0,
        });
    });
    it('drops non-string expanded entries and non-finite numbers', () => {
        const r = deserializeUiState({ v: 1, expanded: ['f-a', 7, null], caretPos: NaN, treeScroll: 'x' });
        expect(r?.expanded).toEqual(['f-a']);
        expect(r?.caretPos).toBe(0);
        expect(r?.treeScroll).toBe(0);
    });
    it('ignores extra unknown fields', () => {
        const r = deserializeUiState({ v: 1, selectedId: 'f-1', surprise: true });
        expect(r?.selectedId).toBe('f-1');
        expect(r).not.toHaveProperty('surprise');
    });
});
