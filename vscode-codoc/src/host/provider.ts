/**
 * provider.ts — the `Codoc Tree` custom editor's host half (09-editor §5–6).
 *
 * The host does four things and nothing else: reads `projection.json` and
 * `status.json` and posts them to the webview when they change; appends the
 * webview's submissions to `.codoc/inbox/ide.jsonl`; tails
 * `.codoc/inbox/ide.ack.jsonl` and posts acks back; opens code locations and
 * runs the two terminal commands (`codoc realize`, `codoc sync`). It holds no
 * document model, no baseline, no diff.
 */
import * as fs from 'fs';
import * as fsp from 'fs/promises';
import * as path from 'path';
import * as vscode from 'vscode';

import type { Ack, Projection, Submission } from '../calc/projection';

export interface HostToWebview {
    kind: 'projection' | 'acks' | 'status' | 'reveal' | 'find' | 'history' | 'notice';
    proj?: Projection;
    history?: unknown;
    realizing?: string[];
    acks?: Ack[];
    status?: Record<string, unknown>;
    fid?: string;
    replace?: boolean;
    /** A sentence for the status bar: something the host could not do, and why. */
    text?: string;
}

export interface WebviewToHost {
    kind: 'ready' | 'submit' | 'open-cite' | 'realize' | 'sync' | 'open-file' | 'set-language' | 'history';
    subs?: Submission[];
    file?: string;
    symbol?: string;
    tag?: string;
}

const CLIENT = 'ide';

export class CodocEditorProvider implements vscode.CustomTextEditorProvider {
    static readonly viewType = 'codoc.tree-editor';
    private static panels = new Map<string, vscode.WebviewPanel>();

    constructor(private readonly context: vscode.ExtensionContext, private readonly codocCli: () => string | undefined) {}

    static reveal(fid: string): void {
        for (const p of CodocEditorProvider.panels.values()) void p.webview.postMessage({ kind: 'reveal', fid } satisfies HostToWebview);
    }

    static find(replace = false): void {
        for (const p of CodocEditorProvider.panels.values()) void p.webview.postMessage({ kind: 'find', replace } satisfies HostToWebview);
    }

    async resolveCustomTextEditor(document: vscode.TextDocument, panel: vscode.WebviewPanel): Promise<void> {
        const codocDir = path.dirname(document.uri.fsPath);
        const root = path.dirname(codocDir);
        panel.webview.options = { enableScripts: true, localResourceRoots: [vscode.Uri.joinPath(this.context.extensionUri, 'dist')] };
        panel.webview.html = this.html(panel.webview);
        CodocEditorProvider.panels.set(document.uri.toString(), panel);

        const post = (m: HostToWebview) => void panel.webview.postMessage(m);
        const postProjection = async () => {
            const proj = await readJson<Projection>(path.join(codocDir, 'projection.json'));
            if (proj) post({ kind: 'projection', proj, realizing: await realizingSet(codocDir) });
        };
        const postStatus = async () => {
            const st = await readJson<Record<string, unknown>>(path.join(codocDir, 'status.json'));
            const cfg = await readJson<{ doc_language?: string }>(path.join(codocDir, 'config.json'));
            if (st) post({ kind: 'status', status: { ...st, doc_language: cfg?.doc_language ?? 'en', actor: 'human:local' } });
        };

        // Acks: tail the ack file from its current end.
        const ackPath = path.join(codocDir, 'inbox', `${CLIENT}.ack.jsonl`);
        let ackOffset = await sizeOf(ackPath);
        const drainAcks = async () => {
            const { lines, end } = await readFrom(ackPath, ackOffset);
            ackOffset = end;
            const acks: Ack[] = [];
            for (const line of lines) {
                try { acks.push(JSON.parse(line)); } catch { /* torn line: the next drain sees it whole */ }
            }
            if (acks.length) post({ kind: 'acks', acks });
        };

        const watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(vscode.Uri.file(codocDir), '{projection.json,status.json,config.json,inbox/ide.ack.jsonl}'));
        const onChange = (uri: vscode.Uri) => {
            const name = path.basename(uri.fsPath);
            if (name === 'projection.json') void postProjection();
            else if (name === 'status.json' || name === 'config.json') void postStatus();
            else if (name.endsWith('.ack.jsonl')) void drainAcks();
        };
        const subs = [watcher, watcher.onDidChange(onChange), watcher.onDidCreate(onChange)];
        panel.onDidDispose(() => {
            subs.forEach(s => s.dispose());
            if (CodocEditorProvider.panels.get(document.uri.toString()) === panel) CodocEditorProvider.panels.delete(document.uri.toString());
        });

        panel.webview.onDidReceiveMessage(async (msg: WebviewToHost) => {
            switch (msg.kind) {
                case 'ready':
                    await postProjection();
                    await postStatus();
                    return;
                case 'submit':
                    if (msg.subs?.length) await appendSubmissions(codocDir, msg.subs);
                    return;
                case 'open-cite':
                    if (msg.file) await openSymbol(root, msg.file, msg.symbol ?? '');
                    return;
                case 'open-file':
                    if (msg.file) await vscode.window.showTextDocument(vscode.Uri.file(path.join(root, msg.file)), { preview: true });
                    return;
                case 'history': {
                    const h = await readJson<unknown>(path.join(codocDir, 'history.json'));
                    if (h) post({ kind: 'history', history: h });
                    return;
                }
                case 'set-language': {
                    // `.codoc/config.json` is the one tracked setting file; the daemon and
                    // every prompt read `doc_language` from it (codoc lang does the same).
                    if (!msg.tag) return;
                    const p = path.join(codocDir, 'config.json');
                    const cfg = (await readJson<Record<string, unknown>>(p)) ?? {};
                    // Atomically: this file is tracked, the daemon reads it on
                    // every inbox pass, and a torn read drops every role in it.
                    const tmp = `${p}.tmp`;
                    await fsp.writeFile(tmp, JSON.stringify({ ...cfg, doc_language: msg.tag }, null, 2) + '\n', 'utf8');
                    await fsp.rename(tmp, p);
                    await postStatus();
                    return;
                }
                case 'realize':
                case 'sync': {
                    const cli = this.codocCli();
                    if (!cli) { void vscode.window.showWarningMessage('codoc is not installed yet — run "codoc: Set up codoc".'); return; }
                    const term = vscode.window.createTerminal({ name: `codoc ${msg.kind}`, cwd: root });
                    term.show(true);
                    term.sendText(`${quote(cli)} ${msg.kind}`);
                    return;
                }
            }
        });
    }

    private html(webview: vscode.Webview): string {
        const nonce = Math.random().toString(36).slice(2) + Date.now().toString(36);
        const asset = (...p: string[]) => webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, 'dist', 'webview', ...p));
        return `<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8" />
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}'; font-src ${webview.cspSource}; img-src ${webview.cspSource};" />
<link rel="stylesheet" href="${asset('app.css')}" /><title>codoc</title></head>
<body><div id="app"></div><script nonce="${nonce}" src="${asset('app.js')}"></script></body></html>`;
    }
}

// ── files ────────────────────────────────────────────────────────────────────

async function readJson<T>(p: string): Promise<T | null> {
    try {
        return JSON.parse(await fsp.readFile(p, 'utf8')) as T;
    } catch {
        return null;
    }
}

async function sizeOf(p: string): Promise<number> {
    try { return (await fsp.stat(p)).size; } catch { return 0; }
}

async function readFrom(p: string, offset: number): Promise<{ lines: string[]; end: number }> {
    try {
        const fh = await fsp.open(p, 'r');
        try {
            const st = await fh.stat();
            // The daemon trims this file to its newest lines at session open, so
            // a file SHORTER than where we stopped is a rotation, not a quiet
            // moment: read it from the start rather than never reading it
            // again, which would leave every block held for the session.
            if (st.size < offset) offset = 0;
            if (st.size <= offset) return { lines: [], end: offset };
            const buf = Buffer.alloc(st.size - offset);
            await fh.read(buf, 0, buf.length, offset);
            const text = buf.toString('utf8');
            const last = text.lastIndexOf('\n');
            if (last < 0) return { lines: [], end: offset };
            return { lines: text.slice(0, last).split('\n').filter(Boolean), end: offset + Buffer.byteLength(text.slice(0, last + 1)) };
        } finally {
            await fh.close();
        }
    } catch {
        return { lines: [], end: offset };
    }
}

async function appendSubmissions(codocDir: string, subs: Submission[]): Promise<void> {
    const dir = path.join(codocDir, 'inbox');
    await fsp.mkdir(dir, { recursive: true });
    const body = subs.map(s => JSON.stringify({ cid: s.cid, kind: s.kind, target: s.target, payload: s.payload, batch: s.batch, plan: s.plan ?? null })).join('\n') + '\n';
    await fsp.appendFile(path.join(dir, `${CLIENT}.jsonl`), body, 'utf8');
}

async function realizingSet(codocDir: string): Promise<string[]> {
    const st = await readJson<{ state?: string }>(path.join(codocDir, 'status.json'));
    if (st?.state !== 'realizing') return [];
    const act = await readJson<{ features?: Record<string, { phase?: string }> }>(path.join(codocDir, 'activity.json'));
    return Object.entries(act?.features ?? {}).filter(([, v]) => v?.phase === 'editing' || v?.phase === 'reflecting').map(([k]) => k);
}

async function openSymbol(root: string, file: string, symbol: string): Promise<void> {
    const uri = vscode.Uri.file(path.join(root, file));
    const doc = await vscode.workspace.openTextDocument(uri);
    const editor = await vscode.window.showTextDocument(doc, { preview: true });
    const leaf = symbol.split('::').pop()?.split('.').pop();
    if (!leaf) return;
    const text = doc.getText();
    const m = new RegExp(`\\b(def|class|function|const|let|var|async function)\\s+${leaf.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`).exec(text)
        ?? new RegExp(`\\b${leaf.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`).exec(text);
    if (!m) return;
    const pos = doc.positionAt(m.index);
    editor.selection = new vscode.Selection(pos, pos);
    editor.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter);
}

function quote(p: string): string {
    return /\s/.test(p) ? `"${p}"` : p;
}

export function fileExists(p: string): boolean {
    try { fs.accessSync(p); return true; } catch { return false; }
}
