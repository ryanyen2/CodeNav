/**
 * credentials.test.ts — the PURE `.env` credential helpers (U5).
 *
 * Imports ONLY from `../setup/env-file` (never `vscode`, never
 * `../setup/credentials`) so it runs under `vitest.config.mjs` ("modules under
 * test must not import 'vscode'"). These guard the `.env` merge logic that the
 * credential bootstrap depends on:
 *   • upsertEnvLines — add/update keys, preserve unrelated lines/comments/order,
 *     never duplicate, quote values that need it;
 *   • providerEnvVars — the canonical env-var set per provider;
 *   • parseEnv — comment/blank-line handling + quoted-value round-trip.
 */
import { describe, it, expect } from 'vitest';
import { parseEnv, providerEnvVars, upsertEnvLines } from '../setup/env-file';

describe('upsertEnvLines', () => {
    it('adds a key to an empty file', () => {
        const out = upsertEnvLines('', { CODOC_PROVIDER: 'claude' });
        expect(out).toContain('CODOC_PROVIDER=claude');
        expect(parseEnv(out).CODOC_PROVIDER).toBe('claude');
    });

    it('updates an existing key in place without duplicating the line', () => {
        const existing = 'CODOC_PROVIDER=openai\n';
        const out = upsertEnvLines(existing, { CODOC_PROVIDER: 'claude' });
        const matches = out.split('\n').filter(l => l.startsWith('CODOC_PROVIDER='));
        expect(matches).toEqual(['CODOC_PROVIDER=claude']);
        expect(parseEnv(out).CODOC_PROVIDER).toBe('claude');
    });

    it('preserves unrelated lines + comments in order while updating one key', () => {
        const existing = [
            '# codoc config',
            'CODOC_MAX_TOKENS=16000',
            '',
            'OPENAI_API_KEY=sk-old',
            '# trailing note',
        ].join('\n') + '\n';
        const out = upsertEnvLines(existing, { OPENAI_API_KEY: 'sk-new' });
        const lines = out.split('\n');
        expect(lines[0]).toBe('# codoc config');
        expect(lines[1]).toBe('CODOC_MAX_TOKENS=16000');
        expect(lines[2]).toBe('');
        expect(lines[3]).toBe('OPENAI_API_KEY=sk-new');
        expect(lines[4]).toBe('# trailing note');
        // Unrelated keys untouched; the updated key not duplicated.
        expect(parseEnv(out).CODOC_MAX_TOKENS).toBe('16000');
        expect(out.split('\n').filter(l => l.startsWith('OPENAI_API_KEY='))).toHaveLength(1);
    });

    it('appends new keys after existing content', () => {
        const existing = 'CODOC_PROVIDER=openai\n';
        const out = upsertEnvLines(existing, { OPENAI_API_KEY: 'sk-x' });
        const parsed = parseEnv(out);
        expect(parsed.CODOC_PROVIDER).toBe('openai');
        expect(parsed.OPENAI_API_KEY).toBe('sk-x');
    });

    it('does not accumulate blank lines across repeated upserts', () => {
        let out = upsertEnvLines('', { CODOC_PROVIDER: 'openai' });
        out = upsertEnvLines(out, { OPENAI_API_KEY: 'sk-x' });
        out = upsertEnvLines(out, { CODOC_PROVIDER: 'claude' });
        expect(out.endsWith('\n')).toBe(true);
        expect(out).not.toContain('\n\n\n');
    });

    it('is a no-op when given no vars', () => {
        const existing = 'CODOC_PROVIDER=openai\n';
        expect(upsertEnvLines(existing, {})).toBe(existing);
    });
});

describe('providerEnvVars', () => {
    it('claude → only CODOC_PROVIDER, no key', () => {
        expect(providerEnvVars('claude')).toEqual({ CODOC_PROVIDER: 'claude' });
    });

    it('claude with a model adds CODOC_MODEL', () => {
        expect(providerEnvVars('claude', undefined, 'sonnet')).toEqual({
            CODOC_PROVIDER: 'claude',
            CODOC_MODEL: 'sonnet',
        });
    });

    it('openai → both CODOC_PROVIDER and OPENAI_API_KEY', () => {
        expect(providerEnvVars('openai', 'sk-x')).toEqual({
            CODOC_PROVIDER: 'openai',
            OPENAI_API_KEY: 'sk-x',
        });
    });

    it('openai with a model adds CODOC_MODEL', () => {
        expect(providerEnvVars('openai', 'sk-x', 'gpt-5.4-mini')).toEqual({
            CODOC_PROVIDER: 'openai',
            OPENAI_API_KEY: 'sk-x',
            CODOC_MODEL: 'gpt-5.4-mini',
        });
    });

    it('anthropic → both CODOC_PROVIDER and ANTHROPIC_API_KEY', () => {
        expect(providerEnvVars('anthropic', 'sk-ant-x')).toEqual({
            CODOC_PROVIDER: 'anthropic',
            ANTHROPIC_API_KEY: 'sk-ant-x',
        });
    });

    it('anthropic with a model adds CODOC_MODEL', () => {
        expect(providerEnvVars('anthropic', 'sk-ant-x', 'claude-sonnet-4-6')).toEqual({
            CODOC_PROVIDER: 'anthropic',
            ANTHROPIC_API_KEY: 'sk-ant-x',
            CODOC_MODEL: 'claude-sonnet-4-6',
        });
    });

    it('throws when a key-requiring provider is requested without a key', () => {
        expect(() => providerEnvVars('openai')).toThrow(/requires an API key/);
        expect(() => providerEnvVars('openai', '   ')).toThrow(/requires an API key/);
        expect(() => providerEnvVars('anthropic')).toThrow(/requires an API key/);
    });

    it('trims a surrounding-whitespace key', () => {
        expect(providerEnvVars('openai', '  sk-x  ')).toEqual({
            CODOC_PROVIDER: 'openai',
            OPENAI_API_KEY: 'sk-x',
        });
        expect(providerEnvVars('anthropic', '  sk-ant-x  ')).toEqual({
            CODOC_PROVIDER: 'anthropic',
            ANTHROPIC_API_KEY: 'sk-ant-x',
        });
    });
});

describe('parseEnv', () => {
    it('ignores comment and blank lines', () => {
        const parsed = parseEnv('# a comment\n\nCODOC_PROVIDER=claude\n\n# another\n');
        expect(parsed).toEqual({ CODOC_PROVIDER: 'claude' });
    });

    it('tolerates a leading export and trims whitespace', () => {
        const parsed = parseEnv('export CODOC_PROVIDER = openai\n');
        expect(parsed.CODOC_PROVIDER).toBe('openai');
    });

    it('round-trips a value that needs quoting (contains a space)', () => {
        const out = upsertEnvLines('', { CODOC_MODEL: 'a model with spaces' });
        // The value must be quoted on disk…
        expect(out).toContain('CODOC_MODEL="a model with spaces"');
        // …and parse back to the original.
        expect(parseEnv(out).CODOC_MODEL).toBe('a model with spaces');
    });

    it('round-trips a value with embedded double quotes', () => {
        const out = upsertEnvLines('', { CODOC_MODEL: 'say "hi" now' });
        expect(parseEnv(out).CODOC_MODEL).toBe('say "hi" now');
    });

    it('strips single quotes on read', () => {
        expect(parseEnv("CODOC_MODEL='quoted value'\n").CODOC_MODEL).toBe('quoted value');
    });

    it('later assignment to the same key wins', () => {
        expect(parseEnv('K=1\nK=2\n').K).toBe('2');
    });

    it('skips malformed lines without an =', () => {
        expect(parseEnv('NOT_A_PAIR\nK=v\n')).toEqual({ K: 'v' });
    });
});
