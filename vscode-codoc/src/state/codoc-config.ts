/**
 * codoc-config.ts — reading `.codoc/config.json` (authored workspace settings).
 *
 * Deliberately free of any `vscode` import so it is unit-testable: this file answers
 * "what language is this tree authored in?", which is the question the toolbar
 * switcher's whole behaviour depends on, and a switch that silently keeps reporting
 * the old value is indistinguishable from a switch that did not write anything. The
 * vscode-dependent WRITE path lives in doc-language.ts.
 */

import * as fsSync from 'fs';
import * as path from 'path';
import { languageName } from '../webview/doc-lang';

export const CONFIG_FILENAME = 'config.json';

/**
 * The tree's authoring language, from the config file.
 *
 * Reads the CONFIG and not the sidecar, which matters more than it looks: the
 * sidecar's copy is written by the daemon, so sourcing it there meant a switch made
 * in the webview landed in config.json while the immediate repost still read the
 * daemon's older sidecar — the button wrote the right thing and appeared to do
 * nothing, and with no daemon running it never caught up at all.
 *
 * `sidecarCode` is the fallback for a workspace whose config predates this setting,
 * and 'en' behind that — which is what every tree was before it existed. Synchronous
 * because `buildPayload` is, and this is one small read per repaint.
 */
export function readDocLanguage(
    codocDirFsPath: string,
    sidecarCode?: string,
): { code: string; name: string } {
    let code = '';
    try {
        const raw = fsSync.readFileSync(
            path.join(codocDirFsPath, CONFIG_FILENAME), 'utf-8');
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed.doc_language === 'string') {
            code = parsed.doc_language.trim();
        }
    } catch {
        // Missing or malformed: fall through to the sidecar / default, the same
        // tolerance doclang.read_config has. A hand-mangled settings file must
        // degrade to a default, never break the editor.
    }
    const resolved = code || sidecarCode || 'en';
    return { code: resolved, name: languageName(resolved) };
}
