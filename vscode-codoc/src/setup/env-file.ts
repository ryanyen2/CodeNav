/**
 * env-file.ts — PURE, vscode-free `.env` read/merge logic (vitest-testable).
 *
 * The credential bootstrap (KTD7) mirrors the chosen provider + any OpenAI
 * fallback key into the repo-root `.env`, because the hooks / MCP server / the
 * `codoc watch` daemon are SEPARATE processes (the hooks/MCP are spawned by
 * Claude Code, not the extension), so an env injected only into the extension's
 * child can't reach them. codoc reads `.env` via `load_dotenv(override=True)` at
 * import, so the `.env` is the canonical mirror those processes read.
 *
 * Every transformation that can be expressed without `vscode` or touching disk
 * lives here so `src/test/**` can exercise it:
 *   • parseEnv        — text → a key→value map (ignores comments / blank lines).
 *   • upsertEnvLines  — merge vars into existing `.env` text, PRESERVING unrelated
 *                       lines, comments and ordering; updating in place, never
 *                       duplicating a key; quoting values that need it.
 *   • providerEnvVars — the canonical env-var set for a chosen provider.
 *
 * The thin `vscode` wiring (SecretStorage, input boxes, reading/writing the file)
 * lives in `credentials.ts`, which imports these.
 *
 * No `import 'vscode'` here, ever — `vitest.config.mjs` runs `src/test/**`
 * against these and the modules under test must not pull in the vscode host shim.
 */

/** The provider codoc's reflection LLM runs on. */
export type CodocProvider = 'claude' | 'openai' | 'anthropic';

/**
 * Parse `.env` text into a key→value map. Comment lines (`# …`) and blank lines
 * are ignored; a leading `export ` is tolerated; surrounding quotes on the value
 * are stripped (the inverse of {@link quoteValue}). Lines without an `=` are
 * skipped. Later assignments to the same key win (matching dotenv semantics).
 *
 * @param text the raw `.env` file contents (may be empty).
 */
export function parseEnv(text: string): Record<string, string> {
    const out: Record<string, string> = {};
    for (const rawLine of text.split(/\r?\n/)) {
        const line = rawLine.trim();
        if (line.length === 0 || line.startsWith('#')) continue;
        const body = line.startsWith('export ') ? line.slice('export '.length).trimStart() : line;
        const eq = body.indexOf('=');
        if (eq <= 0) continue; // no key, or empty key
        const key = body.slice(0, eq).trim();
        if (key.length === 0) continue;
        out[key] = unquoteValue(body.slice(eq + 1).trim());
    }
    return out;
}

/**
 * Merge `vars` into existing `.env` text. Keys already present are updated IN
 * PLACE (same line position, no duplicate); keys not present are appended after
 * the existing content. Unrelated lines, comments, and ordering are preserved
 * exactly. Values that contain whitespace or shell-special characters are quoted
 * (round-trips through {@link parseEnv}).
 *
 * @param existingText the current `.env` contents (`''` for a fresh file).
 * @param vars the key→value pairs to add or update.
 * @returns the merged `.env` text (newline-terminated).
 */
export function upsertEnvLines(existingText: string, vars: Record<string, string>): string {
    const keys = Object.keys(vars);
    if (keys.length === 0) return existingText;

    const pending = new Set(keys);
    // Split on newlines but DON'T drop the structure — we rebuild line by line.
    const hadContent = existingText.length > 0;
    const lines = hadContent ? existingText.split(/\r?\n/) : [];

    // A trailing empty element from a final newline; track + restore it so we
    // don't accumulate blank lines across repeated upserts.
    const hadTrailingNewline = hadContent && /\r?\n$/.test(existingText);
    if (hadTrailingNewline) lines.pop();

    const updated = lines.map(rawLine => {
        const trimmed = rawLine.trim();
        if (trimmed.length === 0 || trimmed.startsWith('#')) return rawLine;
        const body = trimmed.startsWith('export ') ? trimmed.slice('export '.length).trimStart() : trimmed;
        const eq = body.indexOf('=');
        if (eq <= 0) return rawLine;
        const key = body.slice(0, eq).trim();
        if (pending.has(key)) {
            pending.delete(key);
            return `${key}=${quoteValue(vars[key])}`;
        }
        return rawLine;
    });

    // Append any keys not already present, in insertion order.
    for (const key of keys) {
        if (pending.has(key)) updated.push(`${key}=${quoteValue(vars[key])}`);
    }

    return updated.join('\n') + '\n';
}

/**
 * The canonical env-var set for a chosen reflection provider.
 *   • claude    → `{ CODOC_PROVIDER: 'claude' }` (keyless — reuses Claude Code auth).
 *   • openai    → `{ CODOC_PROVIDER: 'openai', OPENAI_API_KEY: <key> }`.
 *   • anthropic → `{ CODOC_PROVIDER: 'anthropic', ANTHROPIC_API_KEY: <key> }`.
 * `CODOC_MODEL` is added when a model override is given.
 *
 * @param provider the chosen provider.
 * @param key the API key — REQUIRED when `provider` is `openai` or `anthropic`,
 *            ignored for the keyless `claude` provider.
 * @param model an optional `CODOC_MODEL` override.
 */
export function providerEnvVars(
    provider: CodocProvider,
    key?: string,
    model?: string,
): Record<string, string> {
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

/**
 * Quote a value for a `.env` line when it contains whitespace or characters a
 * shell / dotenv parser would otherwise mishandle. Plain values (keys, simple
 * tokens like `sk-…`) are emitted bare. Double quotes inside a quoted value are
 * backslash-escaped.
 */
function quoteValue(value: string): string {
    if (value.length === 0) return '';
    // Bare is safe only for values with no whitespace, quotes, or shell-specials.
    if (/^[A-Za-z0-9_./:@+-]+$/.test(value)) return value;
    return `"${value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
}

/** Strip a single layer of surrounding single/double quotes and unescape. */
function unquoteValue(value: string): string {
    if (value.length >= 2) {
        const q = value[0];
        if ((q === '"' || q === "'") && value[value.length - 1] === q) {
            const inner = value.slice(1, -1);
            return q === '"'
                ? inner.replace(/\\"/g, '"').replace(/\\\\/g, '\\')
                : inner;
        }
    }
    return value;
}
