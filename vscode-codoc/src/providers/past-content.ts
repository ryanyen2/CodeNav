/**
 * past-content.ts — read-only access to a file as it was at a commit (W8).
 *
 * The tree records which commit each realize directive started from (`Directive.base_sha`),
 * and which files the work bound code in. That is enough to answer the question the
 * change ledger could previously only gesture at: *show me what the agent actually
 * wrote*. This provider is the "before" side of that diff.
 *
 * ## Why a content provider and not the git extension's API
 *
 * `vscode.git` exposes a repository API, and a `git:` URI scheme, and both are
 * reasonable — but both make a diff depend on another extension being installed,
 * activated, and having finished scanning the repository. A reader clicking "open the
 * code diff" gets an error whose cause is somebody else's activation timing. Shelling
 * out to `git show` is the same call the Python side already makes for its own
 * provenance, costs one process, and fails in exactly one way we can explain.
 *
 * The scheme is read-only by construction: `provideTextDocumentContent` is the entire
 * surface, so a document opened this way cannot be saved back over history.
 */
import * as cp from 'child_process';
import * as path from 'path';
import * as vscode from 'vscode';

export const PAST_SCHEME = 'codoc-past';

const GIT_TIMEOUT_MS = 8_000;

interface PastRef { root: string; sha: string }

/** The URI for `relPath` as of `sha`. The path segment carries the real filename so
 *  VS Code picks the right language for syntax highlighting and names the diff tab
 *  something a person recognises. */
export function pastUri(root: string, sha: string, relPath: string): vscode.Uri {
    return vscode.Uri.from({
        scheme: PAST_SCHEME,
        path: '/' + relPath.replace(/^\/+/, ''),
        query: JSON.stringify({ root, sha } satisfies PastRef),
    });
}

function gitShow(root: string, sha: string, relPath: string): Promise<string> {
    return new Promise(resolve => {
        cp.execFile('git', ['-C', root, 'show', `${sha}:${relPath}`],
            { timeout: GIT_TIMEOUT_MS, maxBuffer: 16 * 1024 * 1024 },
            (err, stdout) => {
                if (!err) { resolve(stdout); return; }
                // The overwhelmingly common "failure" is a file that did not exist at
                // that commit — which is not an error, it is the answer: the agent
                // created it. An empty left-hand side renders as a pure addition, which
                // is exactly right, so say so rather than showing a stack trace.
                resolve('');
            });
    });
}

export function registerPastContentProvider(context: vscode.ExtensionContext): void {
    const provider: vscode.TextDocumentContentProvider = {
        async provideTextDocumentContent(uri: vscode.Uri): Promise<string> {
            let ref: PastRef;
            try {
                ref = JSON.parse(uri.query) as PastRef;
            } catch {
                return '';
            }
            if (!ref?.root || !ref?.sha) return '';
            return gitShow(ref.root, ref.sha, uri.path.replace(/^\//, ''));
        },
    };
    context.subscriptions.push(
        vscode.workspace.registerTextDocumentContentProvider(PAST_SCHEME, provider));
}

/**
 * Open `relPath` as a diff between `sha` and the working tree.
 *
 * Returns false when the file is outside the workspace root — the same containment
 * guard `codoc.openRef` applies, for the same reason: `relPath` reaches here from a
 * control file, and a `..` in it must not become a read of somebody's home directory.
 */
export async function openPastDiff(
    root: string, sha: string, relPath: string, label: string,
): Promise<boolean> {
    const abs = path.resolve(root, relPath);
    const rel = path.relative(path.resolve(root), abs);
    if (!rel || rel.startsWith('..') || path.isAbsolute(rel)) return false;
    const title = `${path.basename(relPath)} — ${label} (${sha.slice(0, 8)} ↔ working tree)`;
    await vscode.commands.executeCommand(
        'vscode.diff', pastUri(root, sha, rel), vscode.Uri.file(abs), title,
        { preview: true, viewColumn: vscode.ViewColumn.Beside } satisfies vscode.TextDocumentShowOptions);
    return true;
}
