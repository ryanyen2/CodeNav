/**
 * Codoc Tree Editor — a CustomTextEditorProvider that replaces the plain-text
 * view of tree.codoc with a webview: a feature-tree nav pane (left) beside one
 * continuous documentation article (right), every feature a section with its
 * citations woven inline. The .codoc text file remains the source of truth;
 * edits flow back through WorkspaceEdit + document.save() so the daemon's
 * watch / Loop B path is unchanged.
 *
 * This host module builds the DocPayload (tree nodes + ordered doc sections via
 * `layoutDoc` + live sync state from activity.json) and serves the bundled
 * webview client (dist/webview/doc-view.{js,css}). All rendering, scroll-sync,
 * keyboard nav and inline editing live in src/webview/doc-view.ts.
 */

import * as vscode from 'vscode';
import { WorkspaceState } from '../state/workspace-state';
import { parseTreeCodoc } from '../state/tree-model';
import { layoutDoc } from '../state/doc-layout';
import { activeFeatureModes, featurePhases } from '../state/activity-model';
import type { DocPayload, UINode, SyncState } from '../webview/protocol';

export class CodocTreeEditorProvider implements vscode.CustomTextEditorProvider {
    public static readonly viewType = 'codoc.tree-editor';

    constructor(
        private readonly context: vscode.ExtensionContext,
        private readonly state: WorkspaceState,
    ) {}

    private rev = 0;

    async resolveCustomTextEditor(
        document: vscode.TextDocument,
        panel: vscode.WebviewPanel,
        _token: vscode.CancellationToken,
    ): Promise<void> {
        panel.webview.options = {
            enableScripts: true,
            localResourceRoots: [vscode.Uri.joinPath(this.context.extensionUri, 'dist')],
        };
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
                case 'verdict': {
                    const ids: string[] = Array.isArray(msg.eventIds)
                        ? msg.eventIds
                        : (msg.eventId ? [msg.eventId] : []);
                    if (ids.length) this.state.writeVerdict(ids, !!msg.accept);
                    return;
                }
            }
        });
    }

    private buildPayload(document: vscode.TextDocument): DocPayload {
        const { features } = parseTreeCodoc(document.getText());
        const sidecar = this.state.sidecar;
        const status = this.state.status;
        const activity = this.state.activity;
        const activeModes = activeFeatureModes(activity, sidecar);
        // Effective phase: an explicit signal (editing/reflecting/done) wins;
        // otherwise a feature whose bound file is being written reads as 'editing'
        // so it shimmers immediately, before the hook's explicit mark lands.
        const phases = featurePhases(activity);
        for (const [fid, mode] of activeModes) {
            if (mode === 'write' && !phases.has(fid)) phases.set(fid, 'editing');
        }

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
                depth,
                children: [],
                activeMode: activeModes.get(f.id) ?? null,
            };

            if (f.parent_id) {
                (childrenOf[f.parent_id] ??= []).push(f.id);
            } else {
                roots.push(f.id);
            }
        }
        // ── Inject ADD/MOVE proposals as inline ghost rows at their destination
        //    parent (RETIRE/AMEND already decorate their live node via `proposal`). ─
        const byEvent = sidecar.proposals?.by_event ?? {};
        for (const [eventId, p] of Object.entries(byEvent)) {
            const parentId = p.parent_id ?? null;
            const depth = parentId && depthOf[parentId] !== undefined ? depthOf[parentId] + 1 : 0;
            depthOf[eventId] = depth;
            const movedTitle = p.op === 'move' && p.feature_id ? (nodes[p.feature_id]?.title ?? p.title ?? '') : '';
            nodes[eventId] = {
                id: eventId,
                title: p.op === 'move' ? (movedTitle || p.title || '(moved)') : (p.title || '(new feature)'),
                parent_id: parentId,
                retired: false,
                realized: true,
                refCount: 0,
                bindings: [],
                proposal: { op: p.op, eventId, tag: p.tag, title: p.title ?? null, description: p.description ?? null },
                isProposal: true,
                proposalOp: p.op,
                depth,
                children: [],
                activeMode: null,
            };
            if (parentId && nodes[parentId]) {
                (childrenOf[parentId] ??= []).push(eventId);
            } else {
                roots.push(eventId);
            }
        }

        for (const [pid, kids] of Object.entries(childrenOf)) {
            if (nodes[pid]) nodes[pid].children = kids;
        }

        // All pending proposal event ids → toolbar Accept-all / Reject-all.
        const pendingEventIds = [
            ...Object.values(sidecar.proposals?.by_feature ?? {}).map(p => p.event_id),
            ...Object.keys(byEvent),
        ];

        const rootName = (this.state.rootDir ?? '').split('/').filter(Boolean).pop() ?? 'workspace';

        const siblingOrder = vscode.workspace.getConfiguration('codoc')
            .get<'dependency' | 'tree'>('docSiblingOrder', 'dependency');
        const sections = layoutDoc(features, sidecar, { siblingOrder, activeModes, phases });

        const sync: SyncState = {
            state: status.state,
            pending: status.pending,
            activeWrite: [...activeModes].filter(([, m]) => m === 'write').map(([id]) => id),
            activeRead: [...activeModes].filter(([, m]) => m === 'read').map(([id]) => id),
            phase: Object.fromEntries(phases),
            realize: this.parseRealizeProgress(status.detail),
        };

        return {
            nodes,
            roots,
            sections,
            status: { state: status.state, pending: status.pending },
            sync,
            rootName,
            pendingEventIds,
            rev: ++this.rev,
        };
    }

    /** Loop B / realize may stamp "done/total" progress into status.detail
     *  (e.g. "implementing 2/5: <title>"). Best-effort parse for the doc header. */
    private parseRealizeProgress(detail: string): SyncState['realize'] {
        const m = /(\d+)\s*\/\s*(\d+)(?:\s*[:\-]\s*(.*))?/.exec(detail || '');
        if (!m) return undefined;
        return { done: Number(m[1]), total: Number(m[2]), current: (m[3] ?? '').trim() };
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
        const asset = (...p: string[]): vscode.Uri =>
            webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, 'dist', 'webview', ...p));
        const scriptUri = asset('doc-view.js');
        const styleUri = asset('doc-view.css');
        return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${csp} 'unsafe-inline'; script-src 'nonce-${nonce}'; font-src ${csp}; img-src ${csp};" />
<link rel="stylesheet" href="${styleUri}" />
<title>codoc</title>
</head>
<body>
<div id="app"></div>
<script nonce="${nonce}" src="${scriptUri}"></script>
</body>
</html>`;
    }
}
