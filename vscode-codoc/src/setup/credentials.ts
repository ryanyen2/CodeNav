/**
 * credentials.ts — vscode-facing credential bootstrap (U5).
 *
 * Default codoc's reflection LLM to the user's EXISTING Claude credentials (no
 * separate API key — the keyless common case), falling back to an OpenAI key
 * when Claude isn't usable. This is what makes "just install the extension" need
 * zero key in the common case.
 *
 *   probeClaudeAuth     — is the `claude` CLI available (so the keyless path works)?
 *   bootstrapCredentials — pick the provider, write `.env`, prompt+store an OpenAI
 *                          key on fallback. Returns the chosen provider.
 *   syncCredentialsToEnv — re-mirror SecretStorage → `.env` (for U4's secrets
 *                          onDidChange listener, so a key change takes effect).
 *
 * KTD7: the OpenAI fallback key is canonical in `SecretStorage`, MIRRORED to a
 * gitignored repo-root `.env`, because the hooks / MCP / `codoc watch` daemon are
 * separate processes (hooks/MCP are spawned by Claude Code, not the extension) and
 * read `.env` via codoc's `load_dotenv` — an env injected only into the
 * extension's child process can't reach them. The key NEVER goes on argv; it lives
 * only in SecretStorage + the `.env` file.
 *
 * All pure `.env` transformation logic lives in `./env-file` so it stays
 * vitest-testable; this file is the vscode wiring and is intentionally NOT covered
 * by vitest (it imports `vscode`).
 */

import * as vscode from 'vscode';
import * as cp from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { parseEnv, providerEnvVars, upsertEnvLines, type CodocProvider } from './env-file';

/** SecretStorage key for the canonical OpenAI key. */
export const SECRET_OPENAI_KEY = 'codoc.openaiApiKey';

/** SecretStorage key for the canonical Anthropic key. */
export const SECRET_ANTHROPIC_KEY = 'codoc.anthropicApiKey';

/** The chosen reflection provider, or 'none' if the user dismissed the key prompt. */
export type BootstrapResult = 'claude' | 'openai' | 'anthropic' | 'none';

/** Claude headless subscription-billing began 2026-06-15; surface this on the claude path. */
const CLAUDE_BILLING_CAVEAT =
    "codoc's reflection will reuse your Claude Code login (no separate API key). " +
    'Heads up: headless Claude usage bills against your Claude subscription as of 2026-06-15, ' +
    'and reflection pauses if your monthly credits run out — switch to an OpenAI key any time via ' +
    '"codoc: Set up codoc" if that happens.';

/** The support article documenting the subscription-billing model + credit behaviour. */
const CLAUDE_BILLING_URL = 'https://support.claude.com/en/articles/11145838-using-claude-code-with-your-pro-or-max-plan';

/** Get (or lazily create) the shared "codoc" OutputChannel (same name provision.ts uses). */
let _channel: vscode.OutputChannel | undefined;
function outputChannel(): vscode.OutputChannel {
    if (!_channel) _channel = vscode.window.createOutputChannel('codoc');
    return _channel;
}

/**
 * Probe whether the `claude` CLI is available (and so plausibly authed) so we can
 * use the keyless reflection path. The bar is binary presence: `claude --version`
 * exiting 0. A deeper auth/login check is an Open Question — the runtime
 * `_complete_claude` (codoc/config.py) already surfaces auth/billing failures
 * clearly, and a fast static probe of the login state is unreliable across CLI
 * versions, so binary presence is the gate for now.
 *
 * @returns `true` when `claude --version` succeeds.
 */
export function probeClaudeAuth(): Promise<boolean> {
    return new Promise<boolean>(resolve => {
        let child: cp.ChildProcess;
        try {
            child = cp.spawn('claude', ['--version'], { shell: false });
        } catch {
            resolve(false);
            return;
        }
        let settled = false;
        const done = (ok: boolean): void => {
            if (settled) return;
            settled = true;
            resolve(ok);
        };
        // Guard against a hung CLI — a presence probe must be fast.
        const timer = setTimeout(() => {
            try { child.kill(); } catch { /* already gone */ }
            done(false);
        }, 5000);
        child.on('error', () => { clearTimeout(timer); done(false); });
        child.on('close', code => { clearTimeout(timer); done(code === 0); });
    });
}

/** Absolute path to the repo-root `.env`. */
function envPath(rootDir: string): string {
    return path.join(rootDir, '.env');
}

/**
 * Merge `vars` into the repo-root `.env` (creating it if absent), preserving any
 * unrelated lines/comments. Written atomically (tmp → rename) so a concurrent
 * reader never sees a half-written file. The key only ever reaches disk here and
 * in SecretStorage — never on argv.
 */
function writeEnvVars(rootDir: string, vars: Record<string, string>): void {
    const target = envPath(rootDir);
    let existing = '';
    try {
        existing = fs.readFileSync(target, 'utf8');
    } catch {
        existing = ''; // no .env yet
    }
    const merged = upsertEnvLines(existing, vars);
    const tmp = `${target}.tmp.${process.pid}`;
    fs.writeFileSync(tmp, merged, { encoding: 'utf8' });
    fs.renameSync(tmp, target);
    outputChannel().appendLine(`codoc: wrote ${Object.keys(vars).join(', ')} to ${target}`);
}

/** Validate an OpenAI key input: non-empty and key-shaped (`sk-…`). */
function validateOpenAiKey(value: string): string | undefined {
    const v = value.trim();
    if (v.length === 0) return 'An OpenAI API key is required (or press Escape to add one later).';
    if (!v.startsWith('sk-')) return 'That does not look like an OpenAI key (expected it to start with "sk-").';
    if (v.length < 20) return 'That key looks too short to be valid.';
    return undefined;
}

/** Validate an Anthropic key input: non-empty and key-shaped (`sk-ant-…`). */
function validateAnthropicKey(value: string): string | undefined {
    const v = value.trim();
    if (v.length === 0) return 'An Anthropic API key is required (or press Escape to add one later).';
    if (!v.startsWith('sk-ant-')) return 'That does not look like an Anthropic key (expected it to start with "sk-ant-").';
    if (v.length < 20) return 'That key looks too short to be valid.';
    return undefined;
}

/** Read the current `CODOC_PROVIDER` from the repo-root `.env`, if any. */
function readProviderFromEnv(rootDir: string): CodocProvider | undefined {
    try {
        const provider = parseEnv(fs.readFileSync(envPath(rootDir), 'utf8')).CODOC_PROVIDER;
        if (provider === 'claude' || provider === 'openai' || provider === 'anthropic') return provider;
    } catch { /* no .env yet */ }
    return undefined;
}

/**
 * Bootstrap codoc's reflection credentials by letting the user CHOOSE a provider:
 *
 *   1. Claude Code (recommended, keyless) — reuses the existing Claude Code login,
 *      no API key. Writes `CODOC_PROVIDER=claude` and surfaces the subscription-
 *      billing caveat. This is the default and the dismiss-the-picker fallback, so
 *      "just install the extension" needs zero key.
 *   2. OpenAI API key — prompt (validated, masked), store in SecretStorage, mirror
 *      to `.env` as `CODOC_PROVIDER=openai` + `OPENAI_API_KEY=…` (KTD7).
 *   3. Anthropic API key — same, as `CODOC_PROVIDER=anthropic` + `ANTHROPIC_API_KEY=…`.
 *
 * Returns the chosen provider, or `'none'` if a key prompt was dismissed (the
 * caller surfaces a friendly "add a key later" path).
 *
 * @param context the extension context (for SecretStorage).
 * @param rootDir the workspace root holding `.env`.
 */
export async function bootstrapCredentials(
    context: vscode.ExtensionContext,
    rootDir: string,
): Promise<BootstrapResult> {
    const claudeAvailable = await probeClaudeAuth();

    type Choice = vscode.QuickPickItem & { id: CodocProvider };
    const items: Choice[] = [
        {
            id: 'claude',
            label: '$(sparkle) Use Claude Code (no API key)',
            description: claudeAvailable ? 'Recommended' : 'Claude Code CLI not detected on PATH',
            detail: "Reuses your existing Claude Code login — codoc's reflection bills against your Claude subscription.",
        },
        {
            id: 'openai',
            label: '$(key) Use an OpenAI API key',
            detail: 'Run codoc reflection on OpenAI (e.g. gpt-5.4-mini) with your own key.',
        },
        {
            id: 'anthropic',
            label: '$(key) Use an Anthropic API key',
            detail: 'Run codoc reflection on the Anthropic API (Claude) with your own key.',
        },
    ];

    const pick = await vscode.window.showQuickPick(items, {
        title: 'codoc — choose a reflection provider',
        placeHolder: 'How should codoc run its reflection LLM? (Esc → keyless Claude Code)',
        ignoreFocusOut: true,
    });

    // Dismissed → default to the zero-key Claude Code path.
    const chosen: CodocProvider = pick?.id ?? 'claude';

    if (chosen === 'claude') {
        writeEnvVars(rootDir, providerEnvVars('claude'));
        if (!claudeAvailable) {
            void vscode.window.showWarningMessage(
                "codoc will use Claude Code, but the `claude` CLI wasn't found on PATH. " +
                'Install Claude Code (or re-run "codoc: Set up codoc" and pick an API key) if reflection fails.',
            );
        } else {
            // Non-blocking billing caveat with a link to the support article.
            void vscode.window
                .showInformationMessage(CLAUDE_BILLING_CAVEAT, 'Learn more')
                .then(choice => {
                    if (choice === 'Learn more') {
                        void vscode.env.openExternal(vscode.Uri.parse(CLAUDE_BILLING_URL));
                    }
                });
        }
        return 'claude';
    }

    // OpenAI / Anthropic → prompt for the matching key.
    const isOpenAi = chosen === 'openai';
    const key = await vscode.window.showInputBox({
        title: isOpenAi ? 'codoc — OpenAI API key' : 'codoc — Anthropic API key',
        prompt: isOpenAi
            ? "Paste an OpenAI API key for codoc's reflection."
            : "Paste an Anthropic API key for codoc's reflection.",
        password: true,
        ignoreFocusOut: true,
        placeHolder: isOpenAi ? 'sk-…' : 'sk-ant-…',
        validateInput: isOpenAi ? validateOpenAiKey : validateAnthropicKey,
    });

    if (key === undefined || key.trim().length === 0) {
        outputChannel().appendLine(
            `codoc: no ${chosen} key provided — reflection will stay unconfigured until a key is added.`,
        );
        return 'none';
    }

    const trimmed = key.trim();
    await context.secrets.store(isOpenAi ? SECRET_OPENAI_KEY : SECRET_ANTHROPIC_KEY, trimmed);
    writeEnvVars(rootDir, providerEnvVars(chosen, trimmed));
    return chosen;
}

/**
 * Re-mirror the canonical API key from SecretStorage into the repo-root `.env`,
 * matching whatever provider `.env` currently names. Called by U4's
 * `context.secrets.onDidChange` listener so an external key change takes effect
 * for the separate hook/MCP/daemon processes. A no-op on the keyless Claude path
 * (or when no matching key is stored), so it never clobbers the chosen provider.
 *
 * @param context the extension context (for SecretStorage).
 * @param rootDir the workspace root holding `.env`.
 */
export async function syncCredentialsToEnv(
    context: vscode.ExtensionContext,
    rootDir: string,
): Promise<void> {
    const provider = readProviderFromEnv(rootDir);
    if (provider === 'anthropic') {
        const key = await context.secrets.get(SECRET_ANTHROPIC_KEY);
        if (key && key.trim().length > 0) writeEnvVars(rootDir, providerEnvVars('anthropic', key.trim()));
        return;
    }
    // Default / openai: mirror the OpenAI key if one is stored.
    const key = await context.secrets.get(SECRET_OPENAI_KEY);
    if (!key || key.trim().length === 0) return; // nothing stored → keyless path, leave .env alone
    writeEnvVars(rootDir, providerEnvVars('openai', key.trim()));
}
