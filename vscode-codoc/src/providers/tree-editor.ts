/**
 * Codoc Tree Editor — a CustomTextEditorProvider that replaces the plain-text
 * view of tree.codoc with a webview-backed outline + detail pane. The .codoc
 * text file remains the source of truth; edits flow back through WorkspaceEdit
 * + document.save() so the daemon's watch / Loop B path is unchanged.
 *
 * Interactions:
 *   · Mouse click → select (in-place class swap, no tree re-render)
 *   · Double-click row title or detail title → inline edit; Esc cancels, Enter commits
 *   · Double-click description in detail pane → textarea; Esc cancels, ⌘Enter commits
 *   · Arrow Up/Down → move selection; Left → collapse / parent; Right → expand / first child
 *   · Enter → edit title; ⌘Enter → edit description; Space → toggle expand
 *   · Drag the ⋮⋮ handle to another row → reparents as child (MOVE_NODE)
 *   · "⇄ text" → reopen with VS Code's default text editor
 */

import * as vscode from 'vscode';
import { WorkspaceState } from '../state/workspace-state';
import { parseTreeCodoc } from '../state/tree-model';

interface UINode {
    id: string;
    title: string;
    description: string;
    parent_id: string | null;
    retired: boolean;
    realized: boolean;
    refCount: number;
    bindings: { file: string; symbol: string }[];
    proposal: null | {
        op: 'retire' | 'amend' | 'add' | 'move';
        eventId: string;
        tag: string;
        title?: string | null;
        description?: string | null;
    };
    line: number;
    depth: number;
    children: string[];
}

interface TreePayload {
    nodes: Record<string, UINode>;
    roots: string[];
    status: { state: string; pending: number };
    rootName: string;
}

export class CodocTreeEditorProvider implements vscode.CustomTextEditorProvider {
    public static readonly viewType = 'codoc.tree-editor';

    constructor(
        private readonly context: vscode.ExtensionContext,
        private readonly state: WorkspaceState,
    ) {}

    async resolveCustomTextEditor(
        document: vscode.TextDocument,
        panel: vscode.WebviewPanel,
        _token: vscode.CancellationToken,
    ): Promise<void> {
        panel.webview.options = { enableScripts: true };
        panel.webview.html = this.html(panel.webview);

        const post = (): void => {
            panel.webview.postMessage({ kind: 'doc', payload: this.buildPayload(document) });
        };

        const subs: vscode.Disposable[] = [
            vscode.workspace.onDidChangeTextDocument(e => {
                if (e.document.uri.toString() === document.uri.toString()) post();
            }),
            this.state.onDidChange(() => post()),
        ];
        panel.onDidDispose(() => { for (const s of subs) s.dispose(); });

        panel.webview.onDidReceiveMessage(async msg => {
            switch (msg.kind) {
                case 'ready':
                    post();
                    return;
                case 'edit-title':
                    await this.editTitle(document, msg.featureId, msg.newTitle);
                    return;
                case 'edit-description':
                    await this.editDescription(document, msg.featureId, msg.newDescription);
                    return;
                case 'move':
                    await this.editMove(document, msg.sourceId, msg.newParentId);
                    return;
                case 'open-text':
                    await vscode.commands.executeCommand('vscode.openWith', document.uri, 'default');
                    return;
                case 'open-binding': {
                    // <module>-level bindings have no symbol to jump to — just open the file.
                    const leafName = (msg.symbol || '').split('::').pop() ?? '';
                    const sym = (leafName === '__module__' || leafName === '<module>' || leafName === '‹module›')
                        ? '' : msg.symbol;
                    await vscode.commands.executeCommand('codoc.openRef', msg.file, sym);
                    return;
                }
                case 'verdict':
                    this.state.writeVerdict([msg.eventId], !!msg.accept);
                    return;
            }
        });
    }

    private buildPayload(document: vscode.TextDocument): TreePayload {
        const { features } = parseTreeCodoc(document.getText());
        const sidecar = this.state.sidecar;
        const status = this.state.status;

        const nodes: Record<string, UINode> = {};
        const roots: string[] = [];
        const childrenOf: Record<string, string[]> = {};
        const depthOf: Record<string, number> = {};

        for (const f of features) {
            if (!f.id) continue;
            const depth = f.parent_id && depthOf[f.parent_id] !== undefined ? depthOf[f.parent_id] + 1 : 0;
            depthOf[f.id] = depth;

            const binds = sidecar.by_feature[f.id] ?? [];
            const meta = sidecar.features[f.id];
            const prop = sidecar.proposals?.by_feature?.[f.id];

            nodes[f.id] = {
                id: f.id,
                title: f.title,
                description: f.description,
                parent_id: f.parent_id,
                retired: f.retired,
                realized: meta?.realized !== false,
                refCount: binds.length,
                bindings: binds,
                proposal: prop ? {
                    op: prop.op,
                    eventId: prop.event_id,
                    tag: prop.tag,
                    title: prop.title ?? null,
                    description: prop.description ?? null,
                } : null,
                line: f.line,
                depth,
                children: [],
            };

            if (f.parent_id) {
                (childrenOf[f.parent_id] ??= []).push(f.id);
            } else {
                roots.push(f.id);
            }
        }
        for (const [pid, kids] of Object.entries(childrenOf)) {
            if (nodes[pid]) nodes[pid].children = kids;
        }

        const rootName = (this.state.rootDir ?? '').split('/').filter(Boolean).pop() ?? 'workspace';

        return {
            nodes,
            roots,
            status: { state: status.state, pending: status.pending },
            rootName,
        };
    }

    private async editTitle(document: vscode.TextDocument, featureId: string, newTitle: string): Promise<void> {
        const { features } = parseTreeCodoc(document.getText());
        const f = features.find(x => x.id === featureId);
        if (!f) return;
        const lineText = document.lineAt(f.line).text;
        const m = /^(\s*[-~]\s+)(.*?)(\s*⟨f-[0-9a-f]+⟩)?\s*$/.exec(lineText);
        if (!m) return;
        const [, prefix, , idSuffix = ''] = m;
        const replaced = prefix + newTitle.trim() + idSuffix;
        if (replaced === lineText) return;

        const edit = new vscode.WorkspaceEdit();
        edit.replace(document.uri, document.lineAt(f.line).range, replaced);
        await vscode.workspace.applyEdit(edit);
        await document.save();
    }

    private async editDescription(document: vscode.TextDocument, featureId: string, newDescription: string): Promise<void> {
        const { features } = parseTreeCodoc(document.getText());
        const idx = features.findIndex(x => x.id === featureId);
        if (idx < 0) return;
        const f = features[idx];
        const next = features[idx + 1];
        const titleLine = document.lineAt(f.line).text;
        const titleIndent = (/^(\s*)/.exec(titleLine) ?? ['', ''])[1].length;

        const startLine = f.line + 1;
        let endLine = next ? next.line - 1 : document.lineCount - 1;
        for (let i = startLine; i <= endLine; i++) {
            const txt = document.lineAt(i).text;
            if (txt.startsWith('# ── pending changes')) { endLine = i - 1; break; }
            if (/^[+\-~] \s*[-~] /.test(txt)) { endLine = i - 1; break; }
        }

        let descIndent = ' '.repeat(titleIndent + 4);
        for (let i = startLine; i <= endLine; i++) {
            const txt = document.lineAt(i).text;
            if (txt.trim().length === 0) continue;
            const lead = /^(\s*)/.exec(txt);
            if (lead && lead[1].length > 0) { descIndent = lead[1]; break; }
        }

        const trimmedNew = newDescription.replace(/\s+$/g, '');
        const newBody = trimmedNew.length === 0 ? '' : trimmedNew
            .split('\n')
            .map(l => l.trim().length === 0 ? '' : descIndent + l.trimStart())
            .join('\n');

        const edit = new vscode.WorkspaceEdit();
        const hasExisting = startLine <= endLine;
        if (hasExisting) {
            const range = new vscode.Range(
                new vscode.Position(startLine, 0),
                new vscode.Position(endLine, document.lineAt(endLine).text.length),
            );
            edit.replace(document.uri, range, newBody);
        } else if (newBody.length > 0) {
            const pos = new vscode.Position(f.line, titleLine.length);
            edit.insert(document.uri, pos, '\n' + newBody);
        } else {
            return;
        }
        await vscode.workspace.applyEdit(edit);
        await document.save();
    }

    /** Move a feature (and its subtree) under a new parent (or to root if null). */
    private async editMove(document: vscode.TextDocument, sourceId: string, newParentId: string | null): Promise<void> {
        const text = document.getText();
        const { features } = parseTreeCodoc(text);
        const src = features.find(f => f.id === sourceId);
        if (!src) return;
        if (src.parent_id === newParentId) return;
        // Cycle guard.
        if (newParentId) {
            const blocked = new Set<string>([sourceId]);
            let grew = true;
            while (grew) {
                grew = false;
                for (const f of features) {
                    if (f.id && f.parent_id && blocked.has(f.parent_id) && !blocked.has(f.id)) {
                        blocked.add(f.id); grew = true;
                    }
                }
            }
            if (blocked.has(newParentId)) return;
        }

        const lines = text.split('\n');
        const isMarker = (l: string): boolean => /^\s*[-~]\s+/.test(l);
        const lead = (l: string): number => (/^(\s*)/.exec(l) ?? ['', ''])[1].length;
        const srcIndent = lead(lines[src.line]);

        // Subtree extent: continues while we see lines at deeper indent, until
        // a marker at indent ≤ src or the pending sentinel.
        let subtreeEnd = lines.length - 1;
        for (let i = src.line + 1; i < lines.length; i++) {
            if (lines[i].startsWith('# ── pending changes')) { subtreeEnd = i - 1; break; }
            if (isMarker(lines[i]) && lead(lines[i]) <= srcIndent) { subtreeEnd = i - 1; break; }
        }
        while (subtreeEnd > src.line && lines[subtreeEnd].trim() === '') subtreeEnd--;

        // Compute new indent.
        let newIndent = 0;
        if (newParentId) {
            const np = features.find(f => f.id === newParentId);
            if (!np) return;
            newIndent = lead(lines[np.line]) + 4;
        }
        const delta = newIndent - srcIndent;

        const moved = lines.slice(src.line, subtreeEnd + 1).map(l => {
            if (l.trim() === '') return l;
            if (delta > 0) return ' '.repeat(delta) + l;
            if (delta < 0) {
                const li = lead(l);
                const strip = Math.min(-delta, li);
                return l.slice(strip);
            }
            return l;
        });

        // Cut the subtree.
        const remaining = lines.slice(0, src.line).concat(lines.slice(subtreeEnd + 1));

        // Find insertion point: end of the new parent's subtree (or end of file
        // for root), in the post-cut buffer.
        let insertAt = remaining.length;
        if (newParentId) {
            const remText = remaining.join('\n');
            const remFeatures = parseTreeCodoc(remText).features;
            const np = remFeatures.find(f => f.id === newParentId);
            if (!np) return;
            const npIndent = lead(remaining[np.line]);
            insertAt = remaining.length;
            for (let i = np.line + 1; i < remaining.length; i++) {
                if (remaining[i].startsWith('# ── pending changes')) { insertAt = i; break; }
                if (isMarker(remaining[i]) && lead(remaining[i]) <= npIndent) { insertAt = i; break; }
            }
        }

        const out = remaining.slice(0, insertAt).concat(moved).concat(remaining.slice(insertAt));
        const edit = new vscode.WorkspaceEdit();
        const lastLine = document.lineAt(document.lineCount - 1);
        edit.replace(document.uri,
            new vscode.Range(0, 0, lastLine.lineNumber, lastLine.text.length),
            out.join('\n'));
        await vscode.workspace.applyEdit(edit);
        await document.save();
    }

    private html(webview: vscode.Webview): string {
        const nonce = String(Date.now()) + Math.random().toString(36).slice(2);
        const csp = webview.cspSource;
        return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${csp} 'unsafe-inline'; script-src 'nonce-${nonce}'; font-src ${csp};" />
<title>codoc</title>
<style nonce="${nonce}">${CSS}</style>
</head>
<body>
<div id="app"></div>
<script nonce="${nonce}">${JS}</script>
</body>
</html>`;
    }
}

// ─── Styles ──────────────────────────────────────────────────────────────────
const CSS = `
:root {
    --row-h: 28px;
    --indent: 20px;
    --pad-x: 18px;
    --rule: 1px solid color-mix(in srgb, var(--vscode-foreground) 12%, transparent);
    --fg-muted: color-mix(in srgb, var(--vscode-foreground) 55%, transparent);
    --hover-bg: color-mix(in srgb, var(--vscode-foreground) 5%, transparent);
    --select-bg: color-mix(in srgb, var(--vscode-list-activeSelectionBackground) 35%, transparent);
    --accent-refs: var(--vscode-charts-foreground, var(--vscode-foreground));
    --accent-plan: var(--vscode-charts-purple, #b58fff);
    --accent-amend: var(--vscode-charts-yellow, #e6c200);
    --accent-retire: var(--vscode-charts-red, #d96666);
    --accent-blue: var(--vscode-charts-blue, #6aa9ff);
    --accent-add: var(--vscode-charts-green, #66bb6a);
}

* { box-sizing: border-box; }
html, body { height: 100%; margin: 0; }
body {
    font: 13px/1.5 var(--vscode-font-family, system-ui);
    color: var(--vscode-foreground);
    background: var(--vscode-editor-background);
    overflow: hidden;
}
body.dragging { cursor: grabbing !important; }
body.dragging * { cursor: grabbing !important; }

#app { display: grid; grid-template-rows: 38px 1fr; height: 100vh; }

.toolbar {
    display: flex;
    align-items: center;
    gap: 18px;
    padding: 0 var(--pad-x);
    border-bottom: var(--rule);
    font: 11.5px var(--vscode-font-family);
    color: var(--fg-muted);
    user-select: none;
}
.toolbar .path { font-weight: 500; color: var(--vscode-foreground); letter-spacing: 0.01em; }
.toolbar .path .dim { color: var(--fg-muted); font-weight: 400; }
.toolbar .status { display: flex; align-items: center; gap: 7px; }
.toolbar .dot {
    width: 7px; height: 7px; border-radius: 50%;
    background: var(--accent-refs); opacity: 0.55;
    transition: background 200ms ease, opacity 200ms ease;
}
.toolbar .status.code_drift .dot { background: var(--accent-amend); opacity: 1; }
.toolbar .status.tree_dirty .dot { background: var(--accent-amend); opacity: 1; }
.toolbar .status.realizing .dot { background: var(--accent-plan); opacity: 1; animation: pulse 1.6s ease-in-out infinite; }
@keyframes pulse { 0%, 100% { opacity: 0.35; } 50% { opacity: 1; } }

.toolbar .spacer { flex: 1; }
.toolbar button.toggle {
    cursor: pointer;
    color: var(--fg-muted);
    background: none;
    border: 1px solid transparent;
    padding: 4px 10px;
    font: inherit;
    border-radius: 4px;
    transition: background 120ms ease, color 120ms ease, border-color 120ms ease;
}
.toolbar button.toggle:hover {
    background: var(--hover-bg);
    color: var(--vscode-foreground);
    border-color: color-mix(in srgb, var(--vscode-foreground) 14%, transparent);
}

.main {
    display: grid;
    grid-template-columns: minmax(360px, 46%) 1fr;
    overflow: hidden;
}

/* ── Tree pane ─────────────────────────────────────────────────────────── */
.tree { overflow: auto; padding: 8px 0 60px; outline: none; }
.tree .empty { padding: 24px var(--pad-x); color: var(--fg-muted); font-size: 12px; }

.row {
    display: flex;
    align-items: center;
    height: var(--row-h);
    padding-right: var(--pad-x);
    padding-left: calc(var(--indent) * var(--depth, 0) + 26px);
    cursor: default;
    user-select: none;
    position: relative;
    transition: background 80ms ease;
}
.row:hover { background: var(--hover-bg); }
.row.selected { background: var(--select-bg); }
.row.selected::before {
    content: "";
    position: absolute;
    left: 0; top: 4px; bottom: 4px;
    width: 2px;
    background: var(--accent-blue);
    border-radius: 1px;
}

/* Drag handle: invisible until row hover; grip cursor */
.drag-handle {
    position: absolute;
    left: calc(var(--indent) * var(--depth, 0) + 4px);
    width: 16px;
    text-align: center;
    color: var(--fg-muted);
    font-size: 11px;
    opacity: 0;
    cursor: grab;
    flex-shrink: 0;
    user-select: none;
    transition: opacity 100ms ease;
    line-height: var(--row-h);
}
.row:hover .drag-handle { opacity: 0.45; }
.row.selected .drag-handle { opacity: 0.6; }
.drag-handle:hover { opacity: 1 !important; }
.drag-handle:active { cursor: grabbing; }

/* Drop target: row that the dragged subtree will land under */
.row.drop-target {
    background: color-mix(in srgb, var(--accent-blue) 16%, transparent) !important;
    box-shadow:
        inset 2px 0 0 var(--accent-blue),
        inset 0 -1px 0 var(--accent-blue),
        inset 0 1px 0 var(--accent-blue);
}
.row.drop-target::before { background: var(--accent-blue) !important; }

.disclosure {
    width: 14px;
    text-align: center;
    color: var(--fg-muted);
    font-size: 10px;
    flex-shrink: 0;
    cursor: pointer;
    transition: color 120ms ease;
}
.disclosure:hover { color: var(--vscode-foreground); }
.disclosure.empty { visibility: hidden; }

.title {
    font-family: var(--vscode-editor-font-family, ui-monospace, monospace);
    font-size: 12.5px;
    margin-left: 8px;
    flex: 1 1 auto;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
    letter-spacing: -0.005em;
}
.row.retired .title { text-decoration: line-through; opacity: 0.55; }
.row.has-retire .title { text-decoration: line-through; color: var(--accent-retire); opacity: 0.7; }
.row.unrealized .title { font-style: italic; opacity: 0.78; }

.title input.t-edit {
    font: inherit;
    background: var(--vscode-input-background);
    color: var(--vscode-input-foreground);
    border: 1px solid var(--vscode-focusBorder, var(--accent-blue));
    border-radius: 3px;
    padding: 1px 6px;
    margin: -1px -6px;
    outline: none;
    width: calc(100% + 12px);
}

/* Amend inline: "→ new title" next to the live title */
.amend-inline {
    font-family: var(--vscode-editor-font-family);
    font-size: 11.5px;
    color: var(--accent-amend);
    margin-left: 12px;
    opacity: 0.88;
    flex-shrink: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 45%;
}

.row.has-amend  { background: color-mix(in srgb, var(--accent-amend) 5%, transparent); }
.row.has-amend:hover { background: color-mix(in srgb, var(--accent-amend) 9%, transparent); }
.row.has-retire { background: color-mix(in srgb, var(--accent-retire) 4%, transparent); }

.badge {
    width: 6px; height: 6px;
    border-radius: 50%;
    flex-shrink: 0;
    margin-left: 10px;
}
.badge.unrealized { background: var(--accent-plan); }
.badge.amend      { background: var(--accent-amend); }
.badge.retire     { background: var(--accent-retire); }

.refs-pill {
    font: 10.5px var(--vscode-editor-font-family);
    color: var(--accent-refs);
    opacity: 0.55;
    padding: 1px 7px;
    border-radius: 9px;
    background: color-mix(in srgb, var(--accent-refs) 8%, transparent);
    flex-shrink: 0;
    margin-left: 12px;
    cursor: pointer;
    transition: opacity 100ms ease;
}
.refs-pill:hover { opacity: 1; }

/* ── Detail pane ───────────────────────────────────────────────────────── */
.detail-host { overflow: hidden; }
.detail {
    border-left: var(--rule);
    overflow: auto;
    padding: 32px 36px 60px;
    height: 100%;
}
.detail.empty {
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--fg-muted);
    font-size: 12px;
}

.detail .crumbs {
    font: 11px var(--vscode-font-family);
    color: var(--fg-muted);
    margin-bottom: 14px;
    user-select: none;
}
.detail .crumbs .c { cursor: pointer; transition: color 100ms ease; }
.detail .crumbs .c:hover { color: var(--vscode-foreground); }
.detail .crumbs .sep { padding: 0 6px; opacity: 0.55; }

.detail .h-title {
    font: 500 19px/1.25 var(--vscode-editor-font-family);
    margin: 0;
    padding: 2px 0;
    letter-spacing: -0.01em;
    cursor: text;
}
.detail .h-title.unrealized { font-style: italic; opacity: 0.85; }
.detail .h-title.retired { text-decoration: line-through; opacity: 0.6; }
.detail input.t-edit-big {
    font: 500 19px/1.25 var(--vscode-editor-font-family);
    background: var(--vscode-input-background);
    color: var(--vscode-input-foreground);
    border: 1px solid var(--vscode-focusBorder, var(--accent-blue));
    border-radius: 4px;
    padding: 4px 8px;
    margin: -4px -8px;
    outline: none;
    width: calc(100% + 16px);
}

.detail .meta {
    margin-top: 10px;
    display: flex;
    gap: 8px;
    align-items: center;
    flex-wrap: wrap;
    font: 11px var(--vscode-font-family);
}
.pill {
    padding: 3px 9px;
    border-radius: 11px;
    background: color-mix(in srgb, var(--accent-refs) 9%, transparent);
    color: var(--accent-refs);
}
.pill.plan   { background: color-mix(in srgb, var(--accent-plan) 14%, transparent); color: var(--accent-plan); }
.pill.amend  { background: color-mix(in srgb, var(--accent-amend) 14%, transparent); color: var(--accent-amend); }
.pill.retire { background: color-mix(in srgb, var(--accent-retire) 14%, transparent); color: var(--accent-retire); }

/* AMEND title diff block under header */
.detail .amend-diff {
    margin-top: 14px;
    font: 13px var(--vscode-editor-font-family);
    display: flex;
    gap: 12px;
    align-items: baseline;
    flex-wrap: wrap;
    padding: 10px 14px;
    border-radius: 4px;
    background: color-mix(in srgb, var(--accent-amend) 7%, transparent);
    border-left: 2px solid var(--accent-amend);
}
.detail .amend-diff .label {
    font: 500 10px var(--vscode-font-family);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--accent-amend);
    opacity: 0.85;
}
.detail .amend-diff .old-title { text-decoration: line-through; opacity: 0.55; color: var(--fg-muted); }
.detail .amend-diff .arrow { color: var(--fg-muted); opacity: 0.55; }
.detail .amend-diff .new-title { color: var(--accent-amend); font-weight: 500; }

.detail .desc {
    margin-top: 24px;
    max-width: 70ch;
    font: 13.5px/1.7 var(--vscode-font-family);
    white-space: pre-wrap;
    cursor: text;
    padding: 4px 0;
}
.detail .desc.empty { color: var(--fg-muted); font-style: italic; }
.detail textarea.d-edit {
    width: 100%;
    max-width: 70ch;
    min-height: 140px;
    font: 13.5px/1.7 var(--vscode-font-family);
    background: var(--vscode-input-background);
    color: var(--vscode-input-foreground);
    border: 1px solid var(--vscode-focusBorder, var(--accent-blue));
    border-radius: 4px;
    padding: 12px 14px;
    outline: none;
    resize: vertical;
}
.detail .edit-hint {
    font: 10.5px var(--vscode-font-family);
    color: var(--fg-muted);
    margin-top: 6px;
}
.detail .edit-hint kbd {
    font: 10px var(--vscode-editor-font-family);
    background: color-mix(in srgb, var(--vscode-foreground) 8%, transparent);
    padding: 1px 5px;
    border-radius: 3px;
    margin: 0 2px;
}

/* AMEND description preview */
.detail .desc-proposed {
    max-width: 70ch;
    margin-top: 10px;
    font: 13.5px/1.7 var(--vscode-font-family);
    color: var(--vscode-foreground);
    padding: 12px 16px;
    border-left: 2px solid var(--accent-amend);
    background: color-mix(in srgb, var(--accent-amend) 6%, transparent);
    border-radius: 0 4px 4px 0;
    white-space: pre-wrap;
}

.detail h3.section {
    font: 500 10.5px var(--vscode-font-family);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--fg-muted);
    margin: 36px 0 10px;
}

.bindings .b-row {
    display: flex;
    align-items: baseline;
    gap: 10px;
    padding: 7px 8px;
    cursor: pointer;
    border-bottom: var(--rule);
    transition: background 80ms ease;
    border-radius: 3px;
}
.bindings .b-row:hover { background: var(--hover-bg); }
.bindings .b-row .sym {
    font: 12px var(--vscode-editor-font-family);
    color: var(--vscode-foreground);
}
.bindings .b-row .arrow { color: var(--fg-muted); }
.bindings .b-row .path {
    font: 11.5px var(--vscode-editor-font-family);
    color: var(--fg-muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.proposal-panel {
    margin-top: 36px;
    padding: 14px 18px;
    border-left: 2px solid var(--accent-amend);
    background: color-mix(in srgb, var(--accent-amend) 5%, transparent);
    border-radius: 0 4px 4px 0;
}
.proposal-panel.retire {
    border-left-color: var(--accent-retire);
    background: color-mix(in srgb, var(--accent-retire) 5%, transparent);
}
.proposal-panel .head {
    font: 500 10.5px var(--vscode-font-family);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--accent-amend);
    margin-bottom: 6px;
}
.proposal-panel.retire .head { color: var(--accent-retire); }
.proposal-panel .body {
    font: 13px/1.6 var(--vscode-font-family);
    color: var(--vscode-foreground);
}
.proposal-panel .tag {
    margin-top: 8px;
    font: 11px var(--vscode-font-family);
    color: var(--fg-muted);
}
.proposal-panel .actions {
    margin-top: 14px;
    display: flex;
    gap: 8px;
}
.proposal-panel button {
    font: 12px var(--vscode-font-family);
    padding: 5px 14px;
    border-radius: 3px;
    border: 1px solid color-mix(in srgb, var(--vscode-foreground) 18%, transparent);
    background: transparent;
    color: var(--vscode-foreground);
    cursor: pointer;
    transition: background 100ms ease, border-color 100ms ease;
}
.proposal-panel button:hover { background: var(--hover-bg); }
.proposal-panel button.primary {
    background: var(--accent-blue);
    color: var(--vscode-editor-background);
    border-color: var(--accent-blue);
    font-weight: 500;
}
.proposal-panel button.primary:hover { opacity: 0.92; }
`;

// ─── Webview script ───────────────────────────────────────────────────────
const JS = `
const vscode = acquireVsCodeApi();

let payload = { nodes: {}, roots: [], status: { state: 'in_sync', pending: 0 }, rootName: '' };
const expanded = new Set();
let selectedId = null;
let editingTitle = null;
let editingDesc = false;
let firstPayload = true;
let dragSourceId = null;

const app = document.getElementById('app');

function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
}

function leafSym(s) {
    const i = s.indexOf('::');
    const tail = i >= 0 ? s.slice(i + 2) : s;
    return tail === '__module__' ? '‹module›' : tail;
}

function statusLabel(s, n) {
    if (s === 'in_sync')    return 'in sync';
    if (s === 'code_drift') return n + ' proposal' + (n === 1 ? '' : 's');
    if (s === 'tree_dirty') return 'applying tree edits…';
    if (s === 'realizing')  return 'implementing…';
    return s;
}

function isDescendant(ancestorId, candidateId) {
    let cur = payload.nodes[candidateId];
    while (cur && cur.parent_id) {
        if (cur.parent_id === ancestorId) return true;
        cur = payload.nodes[cur.parent_id];
    }
    return false;
}

function cssEsc(s) {
    return (window.CSS && CSS.escape) ? CSS.escape(s) : String(s).replace(/["\\\\]/g, '\\\\$&');
}

function flatVisible() {
    const out = [];
    function walk(id) {
        const n = payload.nodes[id];
        if (!n) return;
        out.push(id);
        if (expanded.has(id)) for (const c of n.children) walk(c);
    }
    for (const r of payload.roots) walk(r);
    return out;
}

// ─── Render (split: toolbar / tree / detail) ───────────────────────────────
function renderAll() {
    app.replaceChildren();
    app.append(renderToolbar());
    const main = el('div', 'main');
    main.append(renderTree(), el('div', 'detail-host'));
    app.append(main);
    updateDetail();
}

function renderToolbar() {
    const t = el('div', 'toolbar');
    const p = el('div', 'path');
    p.append(el('span', 'dim', payload.rootName + ' / .codoc / '));
    p.append(document.createTextNode('tree.codoc'));
    t.append(p);

    const status = (payload.status && payload.status.state) || 'in_sync';
    const pending = (payload.status && payload.status.pending) || 0;
    const s = el('div', 'status ' + status);
    s.append(el('span', 'dot'));
    s.append(el('span', null, statusLabel(status, pending)));
    t.append(s);

    t.append(el('div', 'spacer'));

    const btn = el('button', 'toggle');
    btn.textContent = '⇄ text';
    btn.title = 'Open this file in the plain text editor';
    btn.onclick = () => vscode.postMessage({ kind: 'open-text' });
    t.append(btn);

    return t;
}

function renderTree() {
    const wrap = el('div', 'tree');
    wrap.tabIndex = 0;
    if (payload.roots.length === 0) {
        wrap.append(el('div', 'empty', 'No features yet. Run \`codoc init\` to bootstrap the tree.'));
        return wrap;
    }
    for (const id of payload.roots) appendRow(wrap, id);
    return wrap;
}

function appendRow(parent, id) {
    const n = payload.nodes[id];
    if (!n) return;

    const row = el('div', 'row');
    row.dataset.id = id;
    if (selectedId === id) row.classList.add('selected');
    if (n.retired) row.classList.add('retired');
    if (!n.realized) row.classList.add('unrealized');
    if (n.proposal && n.proposal.op === 'amend')  row.classList.add('has-amend');
    if (n.proposal && n.proposal.op === 'retire') row.classList.add('has-retire');
    row.style.setProperty('--depth', n.depth);

    // Drag handle (absolutely positioned at the left, visible on hover)
    const handle = el('span', 'drag-handle');
    handle.textContent = '⋮⋮';
    handle.draggable = true;
    handle.title = 'Drag to reparent under another feature';
    handle.ondragstart = (ev) => {
        dragSourceId = id;
        ev.dataTransfer.effectAllowed = 'move';
        ev.dataTransfer.setData('text/plain', id);
        document.body.classList.add('dragging');
        // Use the row as the drag image (offscreen clone trick)
        const ghost = row.cloneNode(true);
        ghost.style.position = 'absolute';
        ghost.style.top = '-9999px';
        ghost.style.left = '-9999px';
        ghost.style.opacity = '0.85';
        ghost.style.background = 'var(--vscode-editor-background)';
        document.body.append(ghost);
        try { ev.dataTransfer.setDragImage(ghost, 10, 12); } catch (_) {}
        setTimeout(() => ghost.remove(), 0);
        ev.stopPropagation();
    };
    handle.ondragend = () => {
        dragSourceId = null;
        document.body.classList.remove('dragging');
        document.querySelectorAll('.row.drop-target').forEach(r => r.classList.remove('drop-target'));
    };
    row.append(handle);

    // Disclosure
    const hasKids = n.children.length > 0;
    const isExp = expanded.has(id);
    const disc = el('span', 'disclosure' + (hasKids ? '' : ' empty'), hasKids ? (isExp ? '▾' : '▸') : '·');
    if (hasKids) disc.onclick = ev => { ev.stopPropagation(); toggle(id); };
    row.append(disc);

    // Title
    const titleWrap = el('span', 'title');
    titleWrap.textContent = n.title || '(untitled)';
    titleWrap.title = 'Double-click to edit';
    titleWrap.ondblclick = ev => { ev.stopPropagation(); setSelected(id); startEditTitle(id); };
    row.append(titleWrap);

    // Amend inline "→ new title"
    if (n.proposal && n.proposal.op === 'amend' && n.proposal.title && n.proposal.title !== n.title) {
        const diff = el('span', 'amend-inline', '→ ' + n.proposal.title);
        row.append(diff);
    }

    // Badges
    if (!n.realized)                                        row.append(el('span', 'badge unrealized'));
    if (n.proposal && n.proposal.op === 'amend')            row.append(el('span', 'badge amend'));
    if (n.proposal && n.proposal.op === 'retire')           row.append(el('span', 'badge retire'));

    // Refs pill
    if (n.refCount > 0) {
        const pill = el('span', 'refs-pill', n.refCount + (n.refCount === 1 ? ' ref' : ' refs'));
        pill.title = n.bindings.map(b => b.file + ' › ' + leafSym(b.symbol)).join('\\n');
        pill.onclick = ev => { ev.stopPropagation(); setSelected(id); };
        row.append(pill);
    }

    row.onclick = () => { if (!editingTitle && !editingDesc) setSelected(id); };

    // DnD drop target — drop on a row reparents the dragged subtree as a child.
    row.ondragover = (ev) => {
        if (!dragSourceId || dragSourceId === id || isDescendant(dragSourceId, id)) return;
        ev.preventDefault();
        ev.dataTransfer.dropEffect = 'move';
        row.classList.add('drop-target');
    };
    row.ondragleave = (ev) => {
        // Only clear when leaving the row itself (avoid flicker over child spans)
        if (!row.contains(ev.relatedTarget)) row.classList.remove('drop-target');
    };
    row.ondrop = (ev) => {
        ev.preventDefault();
        row.classList.remove('drop-target');
        if (dragSourceId && dragSourceId !== id && !isDescendant(dragSourceId, id)) {
            vscode.postMessage({ kind: 'move', sourceId: dragSourceId, newParentId: id });
        }
        dragSourceId = null;
    };

    parent.append(row);
    if (isExp) for (const c of n.children) appendRow(parent, c);
}

// ─── Surgical mutations (no full tree re-render) ───────────────────────────
function setSelected(id) {
    if (selectedId === id) return;
    document.querySelectorAll('.row.selected').forEach(r => r.classList.remove('selected'));
    selectedId = id;
    if (id) {
        const rowEl = document.querySelector('.row[data-id="' + cssEsc(id) + '"]');
        if (rowEl) {
            rowEl.classList.add('selected');
            rowEl.scrollIntoView({ block: 'nearest' });
        }
    }
    updateDetail();
}

function updateDetail() {
    const host = document.querySelector('.detail-host');
    if (!host) return;
    host.replaceChildren(renderDetail());
}

function renderDetail() {
    const n = selectedId ? payload.nodes[selectedId] : null;
    if (!n) return el('div', 'detail empty', 'Select a feature on the left.');
    const d = el('div', 'detail');

    // Breadcrumbs
    const trail = [];
    let cur = n;
    while (cur) { trail.unshift(cur); cur = cur.parent_id ? payload.nodes[cur.parent_id] : null; }
    if (trail.length > 1) {
        const crumbs = el('div', 'crumbs');
        trail.slice(0, -1).forEach((p, i) => {
            if (i > 0) crumbs.append(el('span', 'sep', '›'));
            const c = el('span', 'c', p.title || '(untitled)');
            c.onclick = () => setSelected(p.id);
            crumbs.append(c);
        });
        d.append(crumbs);
    }

    // Title (editable inline)
    if (editingTitle === n.id) {
        const inp = document.createElement('input');
        inp.className = 't-edit-big';
        inp.value = n.title;
        inp.onkeydown = ev => {
            ev.stopPropagation();
            if (ev.key === 'Enter') { ev.preventDefault(); commitTitle(n.id, inp.value); }
            else if (ev.key === 'Escape') { ev.preventDefault(); cancelTitle(); }
        };
        inp.onblur = () => { if (editingTitle === n.id) commitTitle(n.id, inp.value); };
        d.append(inp);
        queueMicrotask(() => { inp.focus(); inp.select(); });
    } else {
        const h = el('h1', 'h-title');
        if (!n.realized) h.classList.add('unrealized');
        if (n.retired)   h.classList.add('retired');
        h.textContent = n.title || '(untitled)';
        h.title = 'Double-click to edit  ·  Enter on selected row';
        h.ondblclick = () => startEditTitle(n.id);
        d.append(h);
    }

    // Meta pills
    const meta = el('div', 'meta');
    if (n.refCount > 0) meta.append(el('span', 'pill', n.refCount + ' binding' + (n.refCount === 1 ? '' : 's')));
    if (!n.realized)    meta.append(el('span', 'pill plan',  'unrealized'));
    if (n.proposal && n.proposal.op === 'amend')  meta.append(el('span', 'pill amend',  'amend pending'));
    if (n.proposal && n.proposal.op === 'retire') meta.append(el('span', 'pill retire', 'retire pending'));
    if (meta.children.length > 0) d.append(meta);

    // AMEND title diff block
    if (n.proposal && n.proposal.op === 'amend' && n.proposal.title && n.proposal.title !== n.title) {
        const dx = el('div', 'amend-diff');
        dx.append(el('span', 'label', 'title'));
        dx.append(el('span', 'old-title', n.title));
        dx.append(el('span', 'arrow', '→'));
        dx.append(el('span', 'new-title', n.proposal.title));
        d.append(dx);
    }

    // Description
    if (editingDesc) {
        const wrap = el('div');
        const ta = document.createElement('textarea');
        ta.className = 'd-edit';
        ta.value = n.description;
        ta.onkeydown = ev => {
            ev.stopPropagation();
            if (ev.key === 'Escape') { ev.preventDefault(); cancelDesc(); }
            else if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); commitDesc(n.id, ta.value); }
        };
        ta.onblur = () => { if (editingDesc) commitDesc(n.id, ta.value); };
        wrap.append(ta);
        const hint = el('div', 'edit-hint');
        hint.append(document.createTextNode('Commit with '));
        hint.append(el('kbd', null, '⌘'));
        hint.append(document.createTextNode(' '));
        hint.append(el('kbd', null, 'Enter'));
        hint.append(document.createTextNode('  ·  Cancel with '));
        hint.append(el('kbd', null, 'Esc'));
        wrap.append(hint);
        d.append(wrap);
        queueMicrotask(() => { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); });
    } else {
        const dd = el('div', 'desc' + (n.description ? '' : ' empty'));
        dd.textContent = n.description || 'No description — double-click to add one.';
        dd.title = 'Double-click to edit  ·  ⌘Enter on selected row';
        dd.ondblclick = () => startEditDesc();
        d.append(dd);
    }

    // AMEND description preview
    if (n.proposal && n.proposal.op === 'amend' && n.proposal.description && n.proposal.description !== n.description) {
        d.append(el('h3', 'section', 'Proposed description'));
        d.append(el('div', 'desc-proposed', n.proposal.description));
    }

    // Bindings list
    if (n.bindings.length > 0) {
        d.append(el('h3', 'section', 'Bindings'));
        const list = el('div', 'bindings');
        for (const b of n.bindings) {
            const r = el('div', 'b-row');
            r.append(el('span', 'sym', leafSym(b.symbol)));
            r.append(el('span', 'arrow', '›'));
            r.append(el('span', 'path', b.file));
            r.onclick = () => vscode.postMessage({ kind: 'open-binding', file: b.file, symbol: b.symbol });
            list.append(r);
        }
        d.append(list);
    }

    // Proposal panel (accept/reject)
    if (n.proposal) {
        const p = el('div', 'proposal-panel ' + n.proposal.op);
        const headLabel = n.proposal.op === 'amend' ? 'Amend proposed'
                        : n.proposal.op === 'retire' ? 'Retire proposed' : 'Pending change';
        p.append(el('div', 'head', headLabel));
        if (n.proposal.op === 'retire') {
            p.append(el('div', 'body', 'Mark this feature as retired and detach its bindings.'));
        }
        p.append(el('div', 'tag', n.proposal.tag));
        const acts = el('div', 'actions');
        const rej = el('button', null, 'Reject');
        rej.onclick = () => vscode.postMessage({ kind: 'verdict', eventId: n.proposal.eventId, accept: false });
        const acc = el('button', 'primary', 'Accept');
        acc.onclick = () => vscode.postMessage({ kind: 'verdict', eventId: n.proposal.eventId, accept: true });
        acts.append(rej, acc);
        p.append(acts);
        d.append(p);
    }

    return d;
}

// ─── State transitions ──────────────────────────────────────────────────────
function toggle(id) {
    if (expanded.has(id)) expanded.delete(id);
    else expanded.add(id);
    // Children show/hide → re-render the tree, keep selection + detail.
    const tree = document.querySelector('.tree');
    if (tree) {
        const replacement = renderTree();
        tree.replaceWith(replacement);
        replacement.focus({ preventScroll: true });
    }
}

function startEditTitle(id) {
    editingDesc = false;
    editingTitle = id;
    const rowEl = document.querySelector('.row[data-id="' + cssEsc(id) + '"]');
    const titleEl = rowEl && rowEl.querySelector('.title');
    if (!titleEl) { updateDetail(); return; }
    const inp = document.createElement('input');
    inp.className = 't-edit';
    inp.value = payload.nodes[id] ? payload.nodes[id].title : '';
    inp.onkeydown = (ev) => {
        ev.stopPropagation();
        if (ev.key === 'Enter')      { ev.preventDefault(); commitTitle(id, inp.value); }
        else if (ev.key === 'Escape'){ ev.preventDefault(); cancelTitle(); }
    };
    inp.onblur = () => { if (editingTitle === id) commitTitle(id, inp.value); };
    titleEl.replaceChildren(inp);
    inp.focus();
    inp.select();
    updateDetail();   // detail pane also shows inline title edit
}
function cancelTitle() {
    const id = editingTitle;
    editingTitle = null;
    restoreTitleSpan(id);
    updateDetail();
}
function commitTitle(id, newTitle) {
    if (editingTitle !== id) return;
    editingTitle = null;
    const trimmed = (newTitle || '').trim();
    const current = payload.nodes[id] ? payload.nodes[id].title : '';
    if (!trimmed || trimmed === current) {
        restoreTitleSpan(id);
        updateDetail();
        return;
    }
    if (payload.nodes[id]) payload.nodes[id].title = trimmed;
    restoreTitleSpan(id);
    updateDetail();
    vscode.postMessage({ kind: 'edit-title', featureId: id, newTitle: trimmed });
}
function restoreTitleSpan(id) {
    if (!id) return;
    const rowEl = document.querySelector('.row[data-id="' + cssEsc(id) + '"]');
    const titleEl = rowEl && rowEl.querySelector('.title');
    if (!titleEl) return;
    const n = payload.nodes[id];
    titleEl.replaceChildren(document.createTextNode(n ? (n.title || '(untitled)') : ''));
}

function startEditDesc() {
    editingTitle = null;
    editingDesc = true;
    updateDetail();
}
function cancelDesc() { editingDesc = false; updateDetail(); }
function commitDesc(id, newDesc) {
    editingDesc = false;
    const current = (payload.nodes[id] && payload.nodes[id].description) || '';
    if (newDesc === current) { updateDetail(); return; }
    if (payload.nodes[id]) payload.nodes[id].description = newDesc;
    updateDetail();
    vscode.postMessage({ kind: 'edit-description', featureId: id, newDescription: newDesc });
}

// ─── Keyboard navigation ────────────────────────────────────────────────────
function moveCursor(delta) {
    const visible = flatVisible();
    if (visible.length === 0) return;
    const idx = selectedId ? visible.indexOf(selectedId) : -1;
    const next = idx < 0 ? 0 : Math.max(0, Math.min(visible.length - 1, idx + delta));
    setSelected(visible[next]);
}
function expandOrDescend() {
    if (!selectedId) return;
    const n = payload.nodes[selectedId];
    if (!n || n.children.length === 0) return;
    if (!expanded.has(selectedId)) toggle(selectedId);
    else setSelected(n.children[0]);
}
function collapseOrAscend() {
    if (!selectedId) return;
    const n = payload.nodes[selectedId];
    if (!n) return;
    if (expanded.has(selectedId) && n.children.length > 0) toggle(selectedId);
    else if (n.parent_id) setSelected(n.parent_id);
}

document.addEventListener('keydown', (ev) => {
    const tag = document.activeElement && document.activeElement.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA') return;
    switch (ev.key) {
        case 'ArrowDown':  ev.preventDefault(); moveCursor(+1); return;
        case 'ArrowUp':    ev.preventDefault(); moveCursor(-1); return;
        case 'ArrowRight': ev.preventDefault(); expandOrDescend(); return;
        case 'ArrowLeft':  ev.preventDefault(); collapseOrAscend(); return;
        case 'Enter':
            ev.preventDefault();
            if (!selectedId) return;
            if (ev.metaKey || ev.ctrlKey) startEditDesc();
            else startEditTitle(selectedId);
            return;
        case ' ':
            if (selectedId) { ev.preventDefault(); toggle(selectedId); }
            return;
        case 'Escape':
            if (editingTitle) { ev.preventDefault(); cancelTitle(); }
            else if (editingDesc) { ev.preventDefault(); cancelDesc(); }
            return;
    }
});

// Clicking the tree container itself (empty space) keeps it focused for kb nav.
document.addEventListener('mousedown', (ev) => {
    const inTree = ev.target && (ev.target.closest && ev.target.closest('.tree'));
    if (inTree) {
        setTimeout(() => document.querySelector('.tree')?.focus({ preventScroll: true }), 0);
    }
});

// ─── Message bus ────────────────────────────────────────────────────────────
window.addEventListener('message', ev => {
    const msg = ev.data;
    if (msg.kind !== 'doc') return;
    payload = msg.payload;
    if (selectedId && !payload.nodes[selectedId]) selectedId = null;
    for (const id of [...expanded]) if (!payload.nodes[id]) expanded.delete(id);
    if (firstPayload) {
        firstPayload = false;
        for (const r of payload.roots) expanded.add(r);
        if (selectedId == null) selectedId = payload.roots[0] ?? null;
    }
    // External edits invalidate any in-flight inline editor.
    editingTitle = null;
    editingDesc = false;
    renderAll();
    setTimeout(() => document.querySelector('.tree')?.focus({ preventScroll: true }), 0);
});

vscode.postMessage({ kind: 'ready' });
`;
