/**
 * env-file.ts: the text of the repository's `.env`, edited in place.
 *
 * codoc's Python side loads the repository-root `.env` in every process it
 * runs as: the daemon, the hooks and the MCP server, most of which the
 * extension does not start. So the provider a person picks during setup is
 * written there rather than passed in an environment (setup/credentials.ts).
 * These two functions say which variables a provider needs and merge them into
 * existing `.env` text without disturbing anything else in it.
 *
 * No `vscode` import and no file access, so vitest can load it.
 */

/** The LLM provider codoc's own passes run on. */
export type CodocProvider = 'claude' | 'openai' | 'anthropic';

/**
 * Merge `vars` into `.env` text. A key already present is rewritten on its own
 * line; a new key is appended. Every other line, comment and blank line keeps
 * its place. The result ends with one newline.
 */
export function upsertEnvLines(existingText: string, vars: Record<string, string>): string {
    const keys = Object.keys(vars);
    if (keys.length === 0) return existingText;

    const pending = new Set(keys);
    const lines = existingText.length > 0 ? existingText.split(/\r?\n/) : [];
    // A final newline leaves an empty last element. Drop it, or each upsert
    // would add a blank line.
    if (/\r?\n$/.test(existingText)) lines.pop();

    const updated = lines.map(rawLine => {
        const trimmed = rawLine.trim();
        if (trimmed.length === 0 || trimmed.startsWith('#')) return rawLine;
        const body = trimmed.startsWith('export ') ? trimmed.slice('export '.length).trimStart() : trimmed;
        const eq = body.indexOf('=');
        if (eq <= 0) return rawLine;
        const key = body.slice(0, eq).trim();
        if (!pending.has(key)) return rawLine;
        pending.delete(key);
        return `${key}=${quoteValue(vars[key])}`;
    });
    for (const key of keys) {
        if (pending.has(key)) updated.push(`${key}=${quoteValue(vars[key])}`);
    }
    return updated.join('\n') + '\n';
}

/**
 * The variables a provider needs: `CODOC_PROVIDER`, the API key for `openai`
 * and `anthropic` (required; `claude` reuses the Claude Code login and takes
 * none), and `CODOC_MODEL` when a model is given.
 */
export function providerEnvVars(provider: CodocProvider, key?: string, model?: string): Record<string, string> {
    const vars: Record<string, string> = { CODOC_PROVIDER: provider };
    if (provider === 'openai' || provider === 'anthropic') {
        if (!key || key.trim().length === 0) {
            throw new Error(`providerEnvVars('${provider}', …) requires an API key.`);
        }
        vars[provider === 'openai' ? 'OPENAI_API_KEY' : 'ANTHROPIC_API_KEY'] = key.trim();
    }
    if (model && model.trim().length > 0) vars.CODOC_MODEL = model.trim();
    return vars;
}

/** A value as it is written after `KEY=`: bare when it is a plain token,
 *  otherwise double-quoted with `\` and `"` escaped. */
function quoteValue(value: string): string {
    if (value.length === 0) return '';
    if (/^[A-Za-z0-9_./:@+-]+$/.test(value)) return value;
    return `"${value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
}
