/**
 * setup-orchestration.test.ts — the PURE setup ORDER + decision logic (U4).
 *
 * Imports ONLY from `../setup/setup-flow` (never `vscode`, never `../extension`)
 * so it runs under `vitest.config.mjs` ("modules under test must not import
 * 'vscode'"). These guard the orchestration order `extension.ts` drives:
 *   • the canonical step sequence (ensureUv → provision → credentials → init →
 *     startDaemon);
 *   • the correctness invariant — credentials precede init (init's LLM bootstrap
 *     needs a configured provider);
 *   • the `needsSetup` first-run decision.
 */
import { describe, it, expect } from 'vitest';
import {
    SETUP_STEPS, SetupStepId, credentialsPrecedeInit, needsSetup, setupStepIds,
} from '../setup/setup-flow';

describe('SETUP_STEPS order', () => {
    it('is exactly ensure-uv → provision → credentials → init → start-daemon', () => {
        const expected: SetupStepId[] = ['ensure-uv', 'provision', 'credentials', 'init', 'start-daemon'];
        expect(setupStepIds()).toEqual(expected);
    });

    it('every step carries a non-empty human-facing label', () => {
        for (const step of SETUP_STEPS) {
            expect(step.label.trim().length).toBeGreaterThan(0);
        }
    });

    it('has no duplicate step ids', () => {
        const ids = setupStepIds();
        expect(new Set(ids).size).toBe(ids.length);
    });
});

describe('credentialsPrecedeInit (the correctness invariant)', () => {
    it('is true for the canonical order — credentials before init', () => {
        expect(credentialsPrecedeInit()).toBe(true);
    });

    it('is true on the live SETUP_STEPS data structure', () => {
        const credIdx = SETUP_STEPS.findIndex(s => s.id === 'credentials');
        const initIdx = SETUP_STEPS.findIndex(s => s.id === 'init');
        expect(credIdx).toBeGreaterThanOrEqual(0);
        expect(initIdx).toBeGreaterThanOrEqual(0);
        expect(credIdx).toBeLessThan(initIdx);
    });

    it('is false if init were ordered before credentials', () => {
        const reordered = [
            { id: 'init' as const, label: 'init' },
            { id: 'credentials' as const, label: 'creds' },
        ];
        expect(credentialsPrecedeInit(reordered)).toBe(false);
    });

    it('is false when either step is missing', () => {
        expect(credentialsPrecedeInit([{ id: 'init', label: 'init' }])).toBe(false);
        expect(credentialsPrecedeInit([{ id: 'credentials', label: 'creds' }])).toBe(false);
        expect(credentialsPrecedeInit([])).toBe(false);
    });
});

describe('needsSetup', () => {
    it('is true for a fresh repo — no .codoc/ and nothing provisioned', () => {
        expect(needsSetup(false, false)).toBe(true);
    });

    it('is false once .codoc/ exists (already initialized)', () => {
        expect(needsSetup(true, false)).toBe(false);
        expect(needsSetup(true, true)).toBe(false);
    });

    it('is false once executables are cached (already provisioned)', () => {
        expect(needsSetup(false, true)).toBe(false);
    });
});
