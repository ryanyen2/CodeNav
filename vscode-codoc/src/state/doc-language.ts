/**
 * doc-language.ts — the authoring language of the tree, host side.
 *
 * The setting lives in `.codoc/config.json` (`{"doc_language": "zh-Hans"}`), which
 * unlike the rest of `.codoc/` is TRACKED in git: the language has to travel with
 * the repo, or a contributor's daemon starts writing English prose into somebody
 * else's Chinese tree. `codoc/doclang.py` is the authority on what the setting
 * means; this module only reads and writes it.
 *
 * Why the host may read-modify-write this file when it must NOT do that to
 * edits.json (see the U9 note in tree-editor.ts): the daemon never writes
 * config.json, only reads it. The only writers are explicit human actions — this
 * switcher and `codoc lang` — so there is no lock to miss and last-write-wins is
 * both correct and what a person would expect from two windows.
 *
 * Changing it affects prose codoc ORIGINATES from here on. It does not retranslate
 * the tree, and it does not change what an amend to an existing node comes out in:
 * that follows the node's own language, so a bilingual tree stays bilingual.
 */

import * as vscode from 'vscode';

// The config filename and the (vscode-free, unit-tested) reader live in
// codoc-config.ts; re-exported so callers have one import for the setting.
export { CONFIG_FILENAME, readDocLanguage } from './codoc-config';
import { CONFIG_FILENAME } from './codoc-config';

// The language table itself lives in the webview-safe module (it has no vscode
// dependency and the webview needs it too), so there is exactly one list and the
// host cannot offer a language the UI does not know how to label.
export { DOC_LANGUAGE_CHOICES } from '../webview/doc-lang';

/** Read `.codoc/config.json`, or `{}` when it is missing or unparseable — the same
 *  tolerance `doclang.read_config` has, and for the same reason: a hand-mangled
 *  settings file must degrade to defaults, never break the editor. */
export async function readCodocConfig(
    codocDir: vscode.Uri,
): Promise<Record<string, unknown>> {
    try {
        const bytes = await vscode.workspace.fs.readFile(
            vscode.Uri.joinPath(codocDir, CONFIG_FILENAME));
        const parsed = JSON.parse(Buffer.from(bytes).toString('utf-8'));
        return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
    } catch {
        return {};
    }
}

/**
 * Set `doc_language`, preserving every other key.
 *
 * Merge rather than replace so a setting written by a newer codoc is not dropped by
 * an older editor, and tmp→rename so a reader never sees a half-written file. Two
 * spaces + a trailing newline + `ensure_ascii`-free output match what
 * `doclang.write_config` produces, so the two writers cannot fight over formatting
 * in a file that is meant to be reviewed in a diff.
 */
export async function writeDocLanguage(codocDir: vscode.Uri, code: string): Promise<void> {
    const merged: Record<string, unknown> = {
        ...(await readCodocConfig(codocDir)), doc_language: code,
    };
    const ordered: Record<string, unknown> = {};
    for (const key of Object.keys(merged).sort()) ordered[key] = merged[key];
    const body = Buffer.from(JSON.stringify(ordered, null, 2) + '\n', 'utf-8');

    const dest = vscode.Uri.joinPath(codocDir, CONFIG_FILENAME);
    const tmp = vscode.Uri.joinPath(codocDir, `.${CONFIG_FILENAME}.${process.pid}.tmp`);
    await vscode.workspace.fs.writeFile(tmp, body);
    try {
        await vscode.workspace.fs.rename(tmp, dest, { overwrite: true });
    } catch (err) {
        // Leave no orphan tmp behind if the rename fails (a read-only checkout, a
        // vanished directory) — the caller surfaces the original error.
        await vscode.workspace.fs.delete(tmp).then(undefined, () => undefined);
        throw err;
    }
}
