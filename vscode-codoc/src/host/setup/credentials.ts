/**
 * credentials.ts: the setup step that picks the LLM provider.
 *
 * The person chooses one of three: Claude Code, which reuses their existing
 * login and needs no key, an OpenAI API key, or an Anthropic API key.
 * Dismissing the picker selects Claude Code. The choice, and the key when
 * there is one, is written to the repository-root `.env`, which is where every
 * codoc process reads it from (setup/env-file.ts). Before a key is written,
 * `.env` is added to `.gitignore` unless git already ignores it. The key is
 * never passed on a command line.
 */
import * as vscode from 'vscode';
import * as cp from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';

import { output } from '../output';
import { ignoreEnvFile, providerEnvVars, upsertEnvLines, type CodocProvider } from './env-file';

const CLAUDE_BILLING_CAVEAT =
    'codoc will reuse your Claude Code login (no separate API key). '
    + 'Headless Claude usage bills against your Claude subscription, and codoc pauses if your monthly credits run out. '
    + 'Switch to an API key any time with "codoc: Set up codoc".';

const CLAUDE_BILLING_URL = 'https://support.claude.com/en/articles/11145838-using-claude-code-with-your-pro-or-max-plan';

/** Whether the `claude` CLI is on PATH and answers `--version` within five
 *  seconds. It does not check that the person is logged in; a failed login
 *  shows up when codoc first calls the model. */
function claudeCliPresent(): Promise<boolean> {
    return new Promise<boolean>(resolve => {
        let child: cp.ChildProcess;
        try {
            child = cp.spawn('claude', ['--version'], { shell: false });
        } catch {
            resolve(false);
            return;
        }
        const timer = setTimeout(() => {
            try { child.kill(); } catch { /* already gone */ }
            resolve(false);
        }, 5000);
        child.on('error', () => { clearTimeout(timer); resolve(false); });
        child.on('close', code => { clearTimeout(timer); resolve(code === 0); });
    });
}

/** Merge `vars` into the repository-root `.env`, creating it if absent. The
 *  write is a rename, so a process reading the file never sees half of it. */
function writeEnvVars(rootDir: string, vars: Record<string, string>): void {
    const target = path.join(rootDir, '.env');
    let existing = '';
    try {
        existing = fs.readFileSync(target, 'utf8');
    } catch {
        // no .env yet
    }
    const tmp = `${target}.tmp.${process.pid}`;
    fs.writeFileSync(tmp, upsertEnvLines(existing, vars), { encoding: 'utf8' });
    fs.renameSync(tmp, target);
    output().appendLine(`codoc: wrote ${Object.keys(vars).join(', ')} to ${target}`);
}

function validateKey(prefix: string, label: string): (value: string) => string | undefined {
    return value => {
        const v = value.trim();
        if (v.length === 0) return `An ${label} API key is required (or press Escape to add one later).`;
        if (!v.startsWith(prefix)) return `That does not look like an ${label} key (expected it to start with "${prefix}").`;
        if (v.length < 20) return 'That key looks too short to be valid.';
        return undefined;
    };
}

/**
 * Ask which provider to use and write the answer to `.env`. When the person
 * picks an API-key provider and then dismisses the key prompt, nothing is
 * written and setup carries on without a provider.
 */
export async function bootstrapCredentials(rootDir: string): Promise<void> {
    const claudeAvailable = await claudeCliPresent();

    type Choice = vscode.QuickPickItem & { id: CodocProvider };
    const items: Choice[] = [
        {
            id: 'claude',
            label: '$(sparkle) Use Claude Code (no API key)',
            description: claudeAvailable ? 'Recommended' : 'Claude Code CLI not detected on PATH',
            detail: 'Reuses your existing Claude Code login. Usage bills against your Claude subscription.',
        },
        { id: 'openai', label: '$(key) Use an OpenAI API key', detail: 'Run codoc on OpenAI with your own key.' },
        { id: 'anthropic', label: '$(key) Use an Anthropic API key', detail: 'Run codoc on the Anthropic API with your own key.' },
    ];
    const pick = await vscode.window.showQuickPick(items, {
        title: 'codoc: choose a provider',
        placeHolder: 'Which model provider should codoc use? (Esc selects Claude Code)',
        ignoreFocusOut: true,
    });
    const chosen: CodocProvider = pick?.id ?? 'claude';

    if (chosen === 'claude') {
        writeEnvVars(rootDir, providerEnvVars('claude'));
        if (!claudeAvailable) {
            void vscode.window.showWarningMessage(
                'codoc will use Claude Code, but the `claude` CLI was not found on PATH. '
                + 'Install Claude Code, or run "codoc: Set up codoc" again and pick an API key.');
        } else {
            void vscode.window.showInformationMessage(CLAUDE_BILLING_CAVEAT, 'Learn more').then(choice => {
                if (choice === 'Learn more') void vscode.env.openExternal(vscode.Uri.parse(CLAUDE_BILLING_URL));
            });
        }
        return;
    }

    const openai = chosen === 'openai';
    const label = openai ? 'OpenAI' : 'Anthropic';
    const key = await vscode.window.showInputBox({
        title: `codoc: ${label} API key`,
        prompt: `Paste an ${label} API key for codoc.`,
        password: true,
        ignoreFocusOut: true,
        placeHolder: openai ? 'sk-…' : 'sk-ant-…',
        validateInput: validateKey(openai ? 'sk-' : 'sk-ant-', label),
    });
    if (key === undefined || key.trim().length === 0) {
        output().appendLine(`codoc: no ${chosen} key given. codoc has no provider until one is added.`);
        return;
    }
    ignoreEnvFile(rootDir);
    writeEnvVars(rootDir, providerEnvVars(chosen, key.trim()));
}
