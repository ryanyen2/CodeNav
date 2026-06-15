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
import { providerEnvVars, upsertEnvLines } from './env-file';

/** SecretStorage key for the canonical OpenAI fallback key. */
export const SECRET_OPENAI_KEY = 'codoc.openaiApiKey';

/** The chosen reflection provider, or 'none' if the user dismissed the key prompt. */
export type BootstrapResult = 'claude' | 'openai' | 'none';

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

/**
 * Bootstrap codoc's reflection credentials. The common, keyless path: when the
 * `claude` CLI is available, write `CODOC_PROVIDER=claude` into `.env`, surface
 * the subscription-billing caveat, and return `'claude'` — no key prompt.
 *
 * Otherwise fall back to OpenAI: prompt for a key (validated, password-masked),
 * store it canonically in SecretStorage, MIRROR it to `.env` as
 * `CODOC_PROVIDER=openai` + `OPENAI_API_KEY=…` (KTD7), and return `'openai'`. If
 * the user dismisses the prompt, return `'none'` (the caller surfaces a friendly
 * "add a key later" path).
 *
 * @param context the extension context (for SecretStorage).
 * @param rootDir the workspace root holding `.env`.
 */
export async function bootstrapCredentials(
    context: vscode.ExtensionContext,
    rootDir: string,
): Promise<BootstrapResult> {
    if (await probeClaudeAuth()) {
        writeEnvVars(rootDir, providerEnvVars('claude'));
        // Non-blocking caveat with a link to the support article.
        void vscode.window
            .showInformationMessage(CLAUDE_BILLING_CAVEAT, 'Learn more')
            .then(choice => {
                if (choice === 'Learn more') {
                    void vscode.env.openExternal(vscode.Uri.parse(CLAUDE_BILLING_URL));
                }
            });
        return 'claude';
    }

    // Fallback: no Claude CLI → ask for an OpenAI key.
    const key = await vscode.window.showInputBox({
        title: 'codoc — OpenAI API key',
        prompt: "Claude Code wasn't found, so codoc's reflection will use OpenAI. Paste an OpenAI API key.",
        password: true,
        ignoreFocusOut: true,
        placeHolder: 'sk-…',
        validateInput: validateOpenAiKey,
    });

    if (key === undefined || key.trim().length === 0) {
        outputChannel().appendLine('codoc: no OpenAI key provided — reflection will stay unconfigured until a key is added.');
        return 'none';
    }

    const trimmed = key.trim();
    await context.secrets.store(SECRET_OPENAI_KEY, trimmed);
    writeEnvVars(rootDir, providerEnvVars('openai', trimmed));
    return 'openai';
}

/**
 * Re-mirror the canonical OpenAI key from SecretStorage into the repo-root `.env`
 * (as the openai provider). Called by U4's `context.secrets.onDidChange` listener
 * so an external key change takes effect for the separate hook/MCP/daemon
 * processes. A no-op when no key is stored (the keyless Claude path).
 *
 * @param context the extension context (for SecretStorage).
 * @param rootDir the workspace root holding `.env`.
 */
export async function syncCredentialsToEnv(
    context: vscode.ExtensionContext,
    rootDir: string,
): Promise<void> {
    const key = await context.secrets.get(SECRET_OPENAI_KEY);
    if (!key || key.trim().length === 0) return; // nothing stored → keyless path, leave .env alone
    writeEnvVars(rootDir, providerEnvVars('openai', key.trim()));
}
