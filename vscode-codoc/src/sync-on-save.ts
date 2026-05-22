import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { ServerState } from './state/server';

export function findCodocRoot(filePath: string): string | null {
    let dir = path.dirname(filePath);
    let parent = path.dirname(dir);
    while (parent !== dir) {
        if (fs.existsSync(path.join(dir, '.codoc', 'codoc.db'))) return dir;
        if (fs.existsSync(path.join(dir, '.codoc'))) return dir;
        dir = parent;
        parent = path.dirname(dir);
    }
    return null;
}

// Replace only the changed line block so the cursor doesn't jump to line 0.
async function applyMinimalEdit(document: vscode.TextDocument, newContent: string): Promise<void> {
    const oldLines = document.getText().split('\n');
    const newLines = newContent.split('\n');

    // Find first differing line.
    let first = 0;
    while (first < oldLines.length && first < newLines.length && oldLines[first] === newLines[first]) first++;
    if (first === oldLines.length && first === newLines.length) return;

    // Find last differing line (scanning from end).
    let oldLast = oldLines.length - 1;
    let newLast = newLines.length - 1;
    while (oldLast > first && newLast > first && oldLines[oldLast] === newLines[newLast]) {
        oldLast--;
        newLast--;
    }

    const startPos = new vscode.Position(first, 0);
    const endPos = new vscode.Position(oldLast, oldLines[oldLast]?.length ?? 0);
    const replacement = newLines.slice(first, newLast + 1).join('\n');

    const edit = new vscode.WorkspaceEdit();
    edit.replace(document.uri, new vscode.Range(startPos, endPos), replacement);
    await vscode.workspace.applyEdit(edit);
}

// Debounce state (per extension lifetime — one .codoc file edited at a time).
let _debounceTimer: ReturnType<typeof setTimeout> | null = null;

export function scheduleSyncCodocFile(
    document: vscode.TextDocument,
    server: ServerState,
    diagnostics: vscode.DiagnosticCollection,
): void {
    if (_debounceTimer) clearTimeout(_debounceTimer);
    _debounceTimer = setTimeout(() => {
        _debounceTimer = null;
        onSaveCodocFile(document, server, diagnostics);
    }, 500);
}

export async function onSaveCodocFile(
    document: vscode.TextDocument,
    server: ServerState,
    diagnostics: vscode.DiagnosticCollection,
): Promise<void> {
    if (document.languageId !== 'codoc') return;
    if (!server.connected || !server.client) return;

    // Clear stale diagnostics for this file on each sync attempt.
    diagnostics.delete(document.uri);

    try {
        const result = await server.client.syncFile();

        if (result.status === 'ok' || result.status === 'partial') {
            const count = result.applied.length;
            if (count > 0) {
                vscode.window.showInformationMessage(
                    `codoc: ${count} ${count === 1 ? 'change' : 'changes'} applied`,
                );
            }
            // Refresh any open .codoc files using minimal edits.
            const rootDir = server.rootDir;
            if (result.files && rootDir) {
                for (const [filename, content] of Object.entries(result.files)) {
                    const fileUri = vscode.Uri.file(path.join(rootDir, '.codoc', 'tree', filename));
                    const openDoc = vscode.workspace.textDocuments.find(
                        d => d.uri.fsPath === fileUri.fsPath,
                    );
                    if (openDoc) {
                        await applyMinimalEdit(openDoc, content);
                    }
                }
            }
        } else if (result.status === 'stale_buffer') {
            const choice = await vscode.window.showWarningMessage(
                'codoc: tree buffer is stale. Render now?',
                'Render',
                'Cancel',
            );
            if (choice === 'Render' && server.client) {
                await server.client.renderTree();
                vscode.window.showInformationMessage('codoc: tree rendered — reload .codoc files to see changes');
            }
        } else if (result.status === 'parse_error') {
            // Show as inline diagnostics, not toasts.
            const byFile = new Map<string, vscode.Diagnostic[]>();
            for (const err of result.errors) {
                const targetFile = err.file
                    ? path.join(server.rootDir ?? '', '.codoc', 'tree', err.file)
                    : document.uri.fsPath;
                const line = err.line !== null ? err.line - 1 : 0;
                const diag = new vscode.Diagnostic(
                    new vscode.Range(Math.max(0, line), 0, Math.max(0, line), 999),
                    `codoc: ${err.message}`,
                    vscode.DiagnosticSeverity.Error,
                );
                diag.source = 'codoc';
                const key = targetFile;
                if (!byFile.has(key)) byFile.set(key, []);
                byFile.get(key)!.push(diag);
            }
            for (const [file, diags] of byFile) {
                diagnostics.set(vscode.Uri.file(file), diags);
            }
        }
    } catch (e: unknown) {
        // Server offline — show a subtle status hint, don't toast.
        const msg = e instanceof Error ? e.message : String(e);
        console.error(`codoc sync failed: ${msg}`);
    }
}
