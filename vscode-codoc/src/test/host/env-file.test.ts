import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

import { ignoreEnvFile, providerEnvVars, upsertEnvLines } from '../../host/setup/env-file';

describe('upsertEnvLines', () => {
    it('adds a key to an empty file', () => {
        expect(upsertEnvLines('', { CODOC_PROVIDER: 'claude' })).toBe('CODOC_PROVIDER=claude\n');
    });

    it('rewrites an existing key on its own line, once', () => {
        expect(upsertEnvLines('CODOC_PROVIDER=openai\n', { CODOC_PROVIDER: 'claude' })).toBe('CODOC_PROVIDER=claude\n');
    });

    it('rewrites a key written with `export` and spaces', () => {
        expect(upsertEnvLines('export CODOC_PROVIDER = openai\n', { CODOC_PROVIDER: 'claude' })).toBe('CODOC_PROVIDER=claude\n');
    });

    it('keeps unrelated lines, comments and blank lines where they are', () => {
        const existing = ['# codoc config', 'CODOC_MAX_TOKENS=16000', '', 'OPENAI_API_KEY=sk-old', '# trailing note'].join('\n') + '\n';
        expect(upsertEnvLines(existing, { OPENAI_API_KEY: 'sk-new' }).split('\n')).toEqual(
            ['# codoc config', 'CODOC_MAX_TOKENS=16000', '', 'OPENAI_API_KEY=sk-new', '# trailing note', '']);
    });

    it('appends a new key after what is there', () => {
        expect(upsertEnvLines('CODOC_PROVIDER=openai\n', { OPENAI_API_KEY: 'sk-x' })).toBe('CODOC_PROVIDER=openai\nOPENAI_API_KEY=sk-x\n');
    });

    it('does not accumulate blank lines across repeated upserts', () => {
        let out = upsertEnvLines('', { CODOC_PROVIDER: 'openai' });
        out = upsertEnvLines(out, { OPENAI_API_KEY: 'sk-x' });
        out = upsertEnvLines(out, { CODOC_PROVIDER: 'claude' });
        expect(out).toBe('CODOC_PROVIDER=claude\nOPENAI_API_KEY=sk-x\n');
    });

    it('returns the text unchanged when given no vars', () => {
        expect(upsertEnvLines('CODOC_PROVIDER=openai\n', {})).toBe('CODOC_PROVIDER=openai\n');
    });

    it('quotes a value with spaces and escapes quotes inside it', () => {
        expect(upsertEnvLines('', { CODOC_MODEL: 'a model with spaces' })).toBe('CODOC_MODEL="a model with spaces"\n');
        expect(upsertEnvLines('', { CODOC_MODEL: 'say "hi" now' })).toBe('CODOC_MODEL="say \\"hi\\" now"\n');
    });
});

describe('providerEnvVars', () => {
    it('claude needs no key', () => {
        expect(providerEnvVars('claude')).toEqual({ CODOC_PROVIDER: 'claude' });
    });

    it('openai and anthropic carry their key, trimmed', () => {
        expect(providerEnvVars('openai', '  sk-x  ')).toEqual({ CODOC_PROVIDER: 'openai', OPENAI_API_KEY: 'sk-x' });
        expect(providerEnvVars('anthropic', ' sk-ant-x ')).toEqual({ CODOC_PROVIDER: 'anthropic', ANTHROPIC_API_KEY: 'sk-ant-x' });
    });

    it('refuses a key provider without a key', () => {
        expect(() => providerEnvVars('openai')).toThrow(/requires an API key/);
        expect(() => providerEnvVars('openai', '   ')).toThrow(/requires an API key/);
        expect(() => providerEnvVars('anthropic')).toThrow(/requires an API key/);
    });
});

describe('ignoreEnvFile', () => {
    // No global excludes file: a developer's own ignore of `.env` would hide the write.
    const env = { ...process.env, GIT_CONFIG_GLOBAL: '/dev/null', GIT_CONFIG_NOSYSTEM: '1' };
    const repo = () => {
        const dir = mkdtempSync(join(tmpdir(), 'codoc-env-'));
        spawnSync('git', ['init', '-q', dir], { env });
        return dir;
    };

    it('adds .env to .gitignore once, keeping what is there', () => {
        const dir = repo();
        writeFileSync(join(dir, '.gitignore'), 'node_modules');
        const saved = { ...process.env };
        Object.assign(process.env, env);
        try {
            ignoreEnvFile(dir);
            ignoreEnvFile(dir);
        } finally {
            process.env = saved;
        }
        expect(readFileSync(join(dir, '.gitignore'), 'utf8')).toBe('node_modules\n.env\n');
    });

    it('writes nothing outside a git repository', () => {
        const dir = mkdtempSync(join(tmpdir(), 'codoc-env-'));
        ignoreEnvFile(dir);
        expect(existsSync(join(dir, '.gitignore'))).toBe(false);
    });
});
