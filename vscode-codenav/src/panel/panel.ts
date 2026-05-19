import * as vscode from 'vscode';
import { ServerState } from '../state/server';
import { TransactionResponse, FeatureResponse } from '../api/client';

type WebviewMsg =
    | { type: 'refresh' }
    | { type: 'accept'; hlc: string }
    | { type: 'reject'; hlc: string }
    | { type: 'label'; hlc: string; label: string }
    | { type: 'loadFeature'; uuid: string }
    | { type: 'navigateBinding'; file: string; symbolPath: string | null; tsQuery: string | null; occurrenceIndex: number }
    | { type: 'amend'; featureUuid: string; newIntent: string }
    | { type: 'rename'; featureUuid: string; newSlug: string }
    | { type: 'retire'; featureUuid: string };

export class CodocPanel {
    static current: CodocPanel | undefined;
    private readonly panel: vscode.WebviewPanel;
    private disposables: vscode.Disposable[] = [];
    private _cursorDebounce: ReturnType<typeof setTimeout> | null = null;

    static createOrShow(context: vscode.ExtensionContext, server: ServerState): void {
        if (CodocPanel.current) {
            CodocPanel.current.panel.reveal(vscode.ViewColumn.Two);
            return;
        }
        const panel = vscode.window.createWebviewPanel(
            'codocPanel',
            'codoc',
            vscode.ViewColumn.Two,
            { enableScripts: true, retainContextWhenHidden: true },
        );
        CodocPanel.current = new CodocPanel(panel, server);
    }

    private constructor(panel: vscode.WebviewPanel, private server: ServerState) {
        this.panel = panel;
        panel.webview.html = buildHtml();
        panel.webview.onDidReceiveMessage(this.handleMessage.bind(this), null, this.disposables);
        panel.onDidDispose(() => this.dispose(), null, this.disposables);

        server.onReady(() => this.refresh());

        vscode.window.onDidChangeTextEditorSelection(
            (e) => this.onCursorChange(e),
            null,
            this.disposables,
        );
    }

    async reflect(): Promise<void> {
        const client = this.server.client;
        if (!client) return;
        await client.reflect();
        await this.refresh();
    }

    async bootstrap(): Promise<void> {
        const client = this.server.client;
        if (!client) return;
        await client.bootstrap();
        await this.refresh();
    }

    private async refresh(): Promise<void> {
        const client = this.server.client;
        if (!client) {
            this.send({ type: 'status', connected: false, rootDir: null });
            return;
        }
        this.send({ type: 'status', connected: true, rootDir: this.server.rootDir });
        const [proposals, tree] = await Promise.all([
            client.listPending().catch(() => [] as TransactionResponse[]),
            client.getTree().catch(() => [] as FeatureResponse[]),
        ]);
        this.send({ type: 'proposals', items: proposals });
        this.send({ type: 'tree', items: tree });
    }

    private async handleMessage(msg: WebviewMsg): Promise<void> {
        const client = this.server.client;
        if (!client) return;

        switch (msg.type) {
            case 'refresh':
                await this.refresh();
                break;

            case 'accept':
                await client.acceptTx(msg.hlc).catch(err => this.showError(err));
                await this.refreshProposals();
                break;

            case 'reject':
                await client.rejectTx(msg.hlc).catch(err => this.showError(err));
                await this.refreshProposals();
                break;

            case 'label':
                await client.labelTx(msg.hlc, msg.label).catch(err => this.showError(err));
                await this.refreshProposals();
                break;

            case 'loadFeature': {
                const [feature, bindings, history] = await Promise.all([
                    client.getFeature(msg.uuid),
                    client.getFeatureBindings(msg.uuid),
                    client.getFeatureHistory(msg.uuid),
                ]);
                this.send({ type: 'feature', data: feature, bindings, history });
                break;
            }

            case 'navigateBinding':
                await this.navigateToBinding(msg.file, msg.symbolPath, msg.tsQuery, msg.occurrenceIndex);
                break;

            case 'amend': {
                await client.amend(msg.featureUuid, msg.newIntent).catch(err => this.showError(err));
                const [updated, bindings, history] = await Promise.all([
                    client.getFeature(msg.featureUuid),
                    client.getFeatureBindings(msg.featureUuid),
                    client.getFeatureHistory(msg.featureUuid),
                ]);
                this.send({ type: 'feature', data: updated, bindings, history });
                break;
            }

            case 'rename':
                await client.rename(msg.featureUuid, msg.newSlug).catch(err => this.showError(err));
                await this.refresh();
                break;

            case 'retire':
                await client.retire(msg.featureUuid).catch(err => this.showError(err));
                await this.refresh();
                break;
        }
    }

    private async refreshProposals(): Promise<void> {
        const client = this.server.client;
        if (!client) return;
        const proposals = await client.listPending().catch(() => [] as TransactionResponse[]);
        this.send({ type: 'proposals', items: proposals });
    }

    private async navigateToBinding(
        file: string,
        symbolPath: string | null,
        tsQuery: string | null,
        occurrenceIndex: number,
    ): Promise<void> {
        const rootDir = this.server.rootDir;
        if (!rootDir) return;

        const absUri = vscode.Uri.file(`${rootDir}/${file}`);

        const client = this.server.client;
        if (client) {
            try {
                const pos = await client.resolveAnchor(file, symbolPath, tsQuery, occurrenceIndex);
                if (pos) {
                    const doc = await vscode.workspace.openTextDocument(absUri);
                    const editor = await vscode.window.showTextDocument(doc, vscode.ViewColumn.One);
                    const range = new vscode.Range(pos.start_line, 0, pos.end_line, 0);
                    editor.revealRange(range, vscode.TextEditorRevealType.InCenter);
                    editor.selection = new vscode.Selection(range.start, range.start);
                    return;
                }
            } catch { /* fall through */ }
        }

        let doc: vscode.TextDocument;
        try { doc = await vscode.workspace.openTextDocument(absUri); } catch { return; }
        const editor = await vscode.window.showTextDocument(doc, vscode.ViewColumn.One);

        if (symbolPath) {
            const leaf = symbolPath.split('.').pop() ?? symbolPath;
            for (let i = 0; i < doc.lineCount; i++) {
                const line = doc.lineAt(i).text;
                if (line.includes(`def ${leaf}`) || line.includes(`class ${leaf}`) || line.includes(`function ${leaf}`)) {
                    const pos = new vscode.Position(i, 0);
                    editor.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter);
                    editor.selection = new vscode.Selection(pos, pos);
                    return;
                }
            }
        }
    }

    private onCursorChange(e: vscode.TextEditorSelectionChangeEvent): void {
        if (!['python', 'typescript', 'javascript'].includes(e.textEditor.document.languageId)) return;
        if (this._cursorDebounce) clearTimeout(this._cursorDebounce);
        this._cursorDebounce = setTimeout(() => this._doCursorChange(e), 400);
    }

    private async _doCursorChange(e: vscode.TextEditorSelectionChangeEvent): Promise<void> {
        if (!this.server.client) return;
        let symbols: vscode.DocumentSymbol[] | undefined;
        try {
            symbols = await vscode.commands.executeCommand<vscode.DocumentSymbol[]>(
                'vscode.executeDocumentSymbolProvider', e.textEditor.document.uri,
            );
        } catch { return; }
        if (!symbols) return;

        const line = e.selections[0].active.line;
        const symbol = findSymbolAtLine(symbols, line);
        if (!symbol) return;

        this.send({ type: 'cursorSymbol', symbolName: symbol.name, file: vscode.workspace.asRelativePath(e.textEditor.document.uri) });
    }

    private send(data: unknown): void {
        this.panel.webview.postMessage(data);
    }

    private showError(err: unknown): void {
        vscode.window.showErrorMessage(`codoc: ${String(err)}`);
    }

    dispose(): void {
        if (this._cursorDebounce) clearTimeout(this._cursorDebounce);
        CodocPanel.current = undefined;
        this.panel.dispose();
        this.disposables.forEach(d => d.dispose());
        this.disposables = [];
    }
}

function findSymbolAtLine(symbols: vscode.DocumentSymbol[], line: number): vscode.DocumentSymbol | null {
    for (const sym of symbols) {
        if (sym.range.start.line <= line && line <= sym.range.end.line) {
            return findSymbolAtLine(sym.children, line) ?? sym;
        }
    }
    return null;
}

function buildHtml(): string {
    return /* html */`<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>codoc</title>
<style>
:root {
  --accent:      #e8a020;
  --accent-bg:   rgba(232,160,32,0.10);
  --stable:      #52a87a;
  --strained:    #d4882a;
  --drafting:    #6b9fd4;
  --severed:     #c94545;
  --stub:        #555a65;
  --deprecated:  #3f4249;
  --k-introduce: #52a87a;
  --k-absorb:    #6b9fd4;
  --k-evict:     #c94545;
  --k-reattr:    #d4882a;
  --k-fracture:  #9b72cf;
  --mono: 'JetBrains Mono','Fira Code','Cascadia Code',ui-monospace,'Courier New',monospace;
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: var(--mono);
  font-size: 12px;
  line-height: 1.5;
  color: var(--vscode-foreground);
  background: var(--vscode-editor-background);
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
  -webkit-font-smoothing: antialiased;
}

/* ── scrollbars ─────────────────────────────────── */
::-webkit-scrollbar { width: 4px; height: 4px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--vscode-panel-border); border-radius: 2px; }

/* ── header ─────────────────────────────────────── */
.hdr {
  display: flex;
  align-items: center;
  height: 32px;
  padding: 0 10px 0 12px;
  border-bottom: 1px solid var(--vscode-panel-border);
  flex-shrink: 0;
  gap: 8px;
}
.hdr-logo {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--accent);
  flex-shrink: 0;
}
.hdr-dot { color: var(--vscode-panel-border); font-size: 10px; }
.hdr-status {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  min-width: 0;
  font-size: 10px;
  color: var(--vscode-descriptionForeground);
}
.pip {
  width: 5px; height: 5px;
  border-radius: 50%;
  background: var(--severed);
  flex-shrink: 0;
  transition: background 0.4s;
}
.pip.ok { background: var(--stable); }
.hdr-path { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.btn-icon {
  background: none; border: none;
  color: var(--vscode-descriptionForeground);
  cursor: pointer; padding: 4px 6px;
  font-size: 14px; line-height: 1;
  border-radius: 2px;
  transition: color 0.15s, background 0.15s;
}
.btn-icon:hover { color: var(--vscode-foreground); background: var(--vscode-list-hoverBackground); }

/* ── tabs ───────────────────────────────────────── */
.tabs {
  display: flex;
  border-bottom: 1px solid var(--vscode-panel-border);
  flex-shrink: 0;
  padding: 0 6px;
}
.tab {
  display: flex; align-items: center; gap: 6px;
  padding: 7px 10px;
  font-size: 10px; font-weight: 600;
  letter-spacing: 0.07em; text-transform: uppercase;
  color: var(--vscode-descriptionForeground);
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  cursor: pointer; user-select: none;
  transition: color 0.15s;
  white-space: nowrap;
}
.tab:hover { color: var(--vscode-foreground); }
.tab.active { color: var(--accent); border-bottom-color: var(--accent); }
.cnt {
  background: var(--accent-bg); color: var(--accent);
  border-radius: 8px; padding: 0 5px;
  font-size: 9px; font-weight: 700; min-width: 16px; text-align: center;
  transition: opacity 0.3s;
}
.cnt.zero { opacity: 0.35; }

/* ── panel ──────────────────────────────────────── */
.panel { display: none; flex: 1; overflow: hidden; flex-direction: column; }
.panel.active { display: flex; }
.empty { padding: 40px; text-align: center; font-size: 11px; color: var(--vscode-descriptionForeground); opacity: 0.45; }

/* ── queue ──────────────────────────────────────── */
.queue { flex: 1; overflow-y: auto; padding: 4px 0; }

.prop {
  border-bottom: 1px solid var(--vscode-panel-border);
  animation: rise 0.18s ease both;
}
.prop:last-child { border-bottom: none; }
@keyframes rise {
  from { opacity: 0; transform: translateY(-3px); }
  to   { opacity: 1; transform: none; }
}

.prop-row {
  display: flex; align-items: center; gap: 9px;
  padding: 6px 12px;
  cursor: pointer;
  transition: background 0.1s;
}
.prop-row:hover { background: var(--vscode-list-hoverBackground); }

.kbadge {
  font-size: 9px; font-weight: 700;
  letter-spacing: 0.07em; text-transform: uppercase;
  padding: 2px 7px; border-radius: 2px;
  flex-shrink: 0; min-width: 80px; text-align: center;
}
.k-introduce   { background: rgba(82,168,122,0.13);  color: var(--k-introduce); }
.k-absorb      { background: rgba(107,159,212,0.13); color: var(--k-absorb); }
.k-evict       { background: rgba(201,69,69,0.13);   color: var(--k-evict); }
.k-reattribute { background: rgba(212,136,42,0.13);  color: var(--k-reattr); }
.k-fracture    { background: rgba(155,114,207,0.13); color: var(--k-fracture); }
.k-coalesce    { background: rgba(107,159,212,0.13); color: var(--k-absorb); }
.k-rename-infer      { background: rgba(107,159,212,0.08); color: var(--drafting); }
.k-retire-reflective { background: rgba(63,66,73,0.4);     color: var(--stub); }

.prop-slug { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11px; }
.prop-hlc  { font-size: 9px; color: var(--vscode-descriptionForeground); opacity: 0.4; flex-shrink: 0; font-variant-numeric: tabular-nums; }
.lbadge    { font-size: 9px; padding: 1px 5px; border-radius: 2px; background: var(--accent-bg); color: var(--accent); flex-shrink: 0; }
.chevron   { font-size: 10px; color: var(--vscode-descriptionForeground); flex-shrink: 0; transition: transform 0.15s; }
.prop.open .chevron { transform: rotate(90deg); }

.prop-body {
  display: none;
  padding: 0 12px 10px;
  border-top: 1px solid var(--vscode-panel-border);
}
.prop.open .prop-body { display: block; }

.payload {
  background: var(--vscode-input-background);
  border: 1px solid var(--vscode-panel-border);
  padding: 7px 9px; margin-bottom: 8px;
  font-size: 10px; border-radius: 2px;
  overflow: auto; max-height: 130px;
  white-space: pre; color: var(--vscode-foreground);
}

.acts { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }

.btn {
  background: var(--vscode-button-background);
  color: var(--vscode-button-foreground);
  border: none; padding: 4px 10px;
  font-family: var(--mono); font-size: 11px; font-weight: 500;
  cursor: pointer; border-radius: 2px;
  transition: background 0.1s, opacity 0.1s;
  letter-spacing: 0.02em;
}
.btn:hover  { background: var(--vscode-button-hoverBackground); }
.btn:active { opacity: 0.8; }
.btn-ghost {
  background: transparent;
  color: var(--vscode-foreground);
  border: 1px solid var(--vscode-panel-border);
}
.btn-ghost:hover { background: var(--vscode-list-hoverBackground); }
.btn-del {
  background: rgba(201,69,69,0.12);
  color: var(--severed);
  border: 1px solid rgba(201,69,69,0.22);
}
.btn-del:hover { background: rgba(201,69,69,0.22); }

.lsel {
  background: var(--vscode-input-background);
  color: var(--vscode-foreground);
  border: 1px solid var(--vscode-panel-border);
  padding: 3px 7px;
  font-family: var(--mono); font-size: 10px;
  border-radius: 2px; cursor: pointer;
}
.lsel:focus { outline: 1px solid var(--accent); }

/* ── features ───────────────────────────────────── */
.feat-layout { display: flex; flex: 1; overflow: hidden; }

.ftree {
  width: 196px; flex-shrink: 0;
  overflow-y: auto;
  border-right: 1px solid var(--vscode-panel-border);
  padding: 4px 0;
}

.tnode {
  display: flex; align-items: center; gap: 5px;
  padding: 3px 6px;
  cursor: pointer; font-size: 11px;
  white-space: nowrap; overflow: hidden;
  transition: background 0.1s;
}
.tnode:hover { background: var(--vscode-list-hoverBackground); }
.tnode.sel { background: var(--accent-bg); color: var(--accent); }

.tc { color: var(--vscode-panel-border); font-size: 10px; letter-spacing: -1px; flex-shrink: 0; }

.sq {
  width: 6px; height: 6px; flex-shrink: 0;
  border: 1.5px solid var(--stub);
  transition: border-color 0.2s, background 0.2s;
}
.sq.stable     { background: var(--stable);   border-color: var(--stable); }
.sq.strained   { background: var(--strained); border-color: var(--strained); }
.sq.drafting   { border-color: var(--drafting); }
.sq.stub       { border-color: var(--stub); }
.sq.severed    { background: var(--severed); border-color: var(--severed); }
.sq.deprecated { border-color: var(--deprecated); opacity: 0.5; }

.tslug { overflow: hidden; text-overflow: ellipsis; flex: 1; }
.tcnt  { font-size: 9px; color: var(--vscode-descriptionForeground); opacity: 0.45; flex-shrink: 0; }

/* ── feature detail ─────────────────────────────── */
.fdetail { flex: 1; overflow-y: auto; padding: 16px; min-width: 0; }

.d-empty {
  height: 100%; display: flex; align-items: center; justify-content: center;
  font-size: 11px; color: var(--vscode-descriptionForeground); opacity: 0.4;
}

.d-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
.d-slug { font-size: 14px; font-weight: 700; letter-spacing: -0.01em; }

.spill {
  font-size: 9px; font-weight: 700;
  letter-spacing: 0.08em; text-transform: uppercase;
  padding: 2px 7px; border-radius: 2px;
}
.s-stable     { background: rgba(82,168,122,0.13);  color: var(--stable); }
.s-strained   { background: rgba(212,136,42,0.13);  color: var(--strained); }
.s-drafting   { background: rgba(107,159,212,0.13); color: var(--drafting); }
.s-stub       { background: rgba(85,90,101,0.18);   color: var(--stub); }
.s-severed    { background: rgba(201,69,69,0.13);   color: var(--severed); }
.s-deprecated { background: rgba(63,66,73,0.25);    color: var(--vscode-descriptionForeground); }

.div { border: none; border-top: 1px solid var(--vscode-panel-border); margin: 12px 0; }

.sec { margin-bottom: 14px; }
.sec-lbl {
  font-size: 9px; letter-spacing: 0.1em; text-transform: uppercase;
  color: var(--vscode-descriptionForeground); opacity: 0.55;
  margin-bottom: 5px;
}

.intent-txt { font-size: 12px; line-height: 1.65; }
.intent-ed {
  display: none; width: 100%;
  background: var(--vscode-input-background);
  color: var(--vscode-input-foreground);
  border: 1px solid var(--vscode-panel-border);
  padding: 7px 8px;
  font-family: var(--mono); font-size: 12px;
  border-radius: 2px; resize: vertical; min-height: 60px;
  line-height: 1.55;
}
.intent-ed:focus { outline: 1px solid var(--accent); border-color: var(--accent); }
.save-row { display: none; gap: 6px; margin-top: 6px; }

.blist { list-style: none; }
.bitem {
  display: flex; align-items: center; gap: 7px;
  padding: 5px 0; font-size: 11px;
  border-bottom: 1px solid var(--vscode-panel-border);
}
.bitem:last-child { border-bottom: none; }
.barrow { color: var(--accent); font-size: 11px; cursor: pointer; flex-shrink: 0; user-select: none; }
.bfile {
  color: var(--vscode-textLink-foreground); cursor: pointer;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  max-width: 130px; flex-shrink: 0;
}
.bfile:hover { text-decoration: underline; }
.bsym { color: var(--vscode-descriptionForeground); opacity: 0.6; font-size: 10px; overflow: hidden; text-overflow: ellipsis; flex: 1; }

.hlist { list-style: none; }
.hitem {
  display: flex; gap: 10px; align-items: baseline;
  padding: 4px 0; font-size: 10px;
  border-bottom: 1px solid var(--vscode-panel-border);
  color: var(--vscode-descriptionForeground);
}
.hitem:last-child { border-bottom: none; }
.hkind { color: var(--vscode-foreground); font-weight: 600; font-size: 10px; }

.abar { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.rinput {
  display: none;
  background: var(--vscode-input-background);
  color: var(--vscode-input-foreground);
  border: 1px solid var(--vscode-panel-border);
  padding: 4px 8px;
  font-family: var(--mono); font-size: 11px;
  border-radius: 2px; width: 148px;
}
.rinput:focus { outline: 1px solid var(--accent); border-color: var(--accent); }

/* ── conflicts ──────────────────────────────────── */
.conflicts {
  flex: 1; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 5px;
  color: var(--vscode-descriptionForeground);
}
.conflicts-icon { font-size: 26px; opacity: 0.2; margin-bottom: 4px; }
.conflicts strong { font-size: 12px; opacity: 0.55; }
.conflicts span   { font-size: 10px; opacity: 0.4; }
</style>
</head>
<body>

<header class="hdr">
  <span class="hdr-logo">◈ codoc</span>
  <span class="hdr-dot">·</span>
  <div class="hdr-status">
    <div class="pip" id="pip"></div>
    <span class="hdr-path" id="statusTxt">connecting…</span>
  </div>
  <button class="btn-icon" onclick="refresh()" title="Refresh (⌘R)">↻</button>
</header>

<nav class="tabs">
  <div class="tab active" id="tab-queue" onclick="switchTab('queue')">
    Queue <span class="cnt zero" id="qcnt">0</span>
  </div>
  <div class="tab" id="tab-features" onclick="switchTab('features')">Features</div>
  <div class="tab" id="tab-conflicts" onclick="switchTab('conflicts')">Conflicts</div>
</nav>

<section class="panel active" id="panel-queue">
  <div class="queue" id="queueEl"><div class="empty">No pending proposals.</div></div>
</section>

<section class="panel" id="panel-features">
  <div class="feat-layout">
    <div class="ftree" id="ftreeEl"><div class="empty">No features.</div></div>
    <div class="fdetail" id="fdetailEl"><div class="d-empty">Select a feature →</div></div>
  </div>
</section>

<section class="panel" id="panel-conflicts">
  <div class="conflicts">
    <div class="conflicts-icon">⌥</div>
    <strong>Branch conflict resolution</strong>
    <span>Coming in Phase 3 — CRDT branch/merge with interactive diff UI.</span>
  </div>
</section>

<script>
const vscode = acquireVsCodeApi();
const $ = id => document.getElementById(id);
let S = { proposals: [], tree: [], selected: null };

function refresh() { vscode.postMessage({ type: 'refresh' }); }

function switchTab(t) {
  ['queue','features','conflicts'].forEach(n => {
    $('tab-' + n).classList.toggle('active', n === t);
    $('panel-' + n).classList.toggle('active', n === t);
  });
}

// ── Queue ───────────────────────────────────────────────────────────────────

function renderProposals(items) {
  S.proposals = items;
  const n = items.length;
  $('qcnt').textContent = n;
  $('qcnt').classList.toggle('zero', n === 0);
  $('queueEl').innerHTML = n
    ? items.map(proposalHtml).join('')
    : '<div class="empty">No pending proposals.</div>';
}

function proposalHtml(p, i) {
  const slug = p.payload.slug ?? p.payload.feature_slug ?? p.payload.feature_uuid ?? '—';
  const kind = p.kind.toLowerCase().replace(/_/g, '-');
  const hlcShort = p.hlc.length > 14 ? p.hlc.slice(0,14) + '…' : p.hlc;
  const raw = JSON.stringify(p.payload, null, 2);
  const preview = raw.length > 500 ? raw.slice(0, 500) + '\n…' : raw;
  return \`<div class="prop" id="pr\${i}" style="animation-delay:\${i*25}ms;opacity:0">
  <div class="prop-row" onclick="toggleProp(\${i})">
    <span class="kbadge k-\${x(kind)}">\${x(p.kind)}</span>
    <span class="prop-slug">\${x(String(slug))}</span>
    \${p.label ? \`<span class="lbadge">\${x(p.label)}</span>\` : ''}
    <span class="prop-hlc">\${x(hlcShort)}</span>
    <span class="chevron">›</span>
  </div>
  <div class="prop-body">
    <pre class="payload">\${x(preview)}</pre>
    <div class="acts">
      <button class="btn"      onclick="doAccept(\${i})">Accept</button>
      <button class="btn btn-del" onclick="doReject(\${i})">Reject</button>
      <select class="lsel" onchange="doLabel(\${i},this.value)">
        <option value="">Gate label…</option>
        <option value="accept-verbatim">accept-verbatim</option>
        <option value="accept-light-edit">accept-light-edit</option>
        <option value="accept-heavy-edit">accept-heavy-edit</option>
        <option value="reject">reject</option>
      </select>
    </div>
  </div>
</div>\`;
}

function toggleProp(i) { $('pr' + i)?.classList.toggle('open'); }
function doAccept(i) { const p = S.proposals[i]; if (p) vscode.postMessage({ type:'accept', hlc:p.hlc }); }
function doReject(i) { const p = S.proposals[i]; if (p) vscode.postMessage({ type:'reject', hlc:p.hlc }); }
function doLabel(i, lbl) { const p = S.proposals[i]; if (p && lbl) vscode.postMessage({ type:'label', hlc:p.hlc, label:lbl }); }

// ── Feature tree ────────────────────────────────────────────────────────────

function renderTree(items) {
  S.tree = items;
  const el = $('ftreeEl');
  if (!items.length) { el.innerHTML = '<div class="empty">No features yet.</div>'; return; }
  const byP = {};
  items.forEach(f => { const k = f.parent_uuid||'__r'; (byP[k]=byP[k]||[]).push(f); });

  function node(f, pre, last) {
    const sc = f.state.toLowerCase();
    const conn = pre ? (last ? '└─' : '├─') : '';
    const childPre = pre ? (last ? '  ' : '│ ') : '';
    const ch = byP[f.uuid] || [];
    let h = \`<div class="tnode\${S.selected?.uuid===f.uuid?' sel':''}" id="tn-\${f.uuid}" onclick="loadFeat('\${f.uuid}')">
  <span class="tc">\${x(pre+conn)}</span>
  <span class="sq \${sc}"></span>
  <span class="tslug" title="\${x(f.slug)}">\${x(f.slug)}</span>
  \${f.binding_count ? \`<span class="tcnt">\${f.binding_count}</span>\` : ''}
</div>\`;
    ch.forEach((c,ci) => { h += node(c, pre+childPre, ci===ch.length-1); });
    return h;
  }

  const roots = byP['__r'] || [];
  el.innerHTML = roots.map((r,ri) => node(r, '', ri===roots.length-1)).join('');
}

function loadFeat(uuid) {
  document.querySelectorAll('.tnode').forEach(n => n.classList.remove('sel'));
  $('tn-' + uuid)?.classList.add('sel');
  switchTab('features');
  vscode.postMessage({ type:'loadFeature', uuid });
}

// ── Feature detail ──────────────────────────────────────────────────────────

function renderDetail(f, bindings, history) {
  S.selected = f;
  const sc = f.state.toLowerCase();
  $('fdetailEl').innerHTML = \`
<div class="d-head">
  <span class="d-slug">\${x(f.slug)}</span>
  <span class="spill s-\${sc}">\${x(f.state)}</span>
  \${f.retired ? '<span class="spill s-deprecated">retired</span>' : ''}
</div>
<hr class="div">

<div class="sec">
  <div class="sec-lbl">Intent</div>
  <div class="intent-txt" id="itxt">\${x(f.intent||'—')}</div>
  <textarea class="intent-ed" id="ied" rows="3">\${x(f.intent)}</textarea>
  <div class="save-row" id="isave">
    <button class="btn" onclick="saveAmend()">Save</button>
    <button class="btn btn-ghost" onclick="cancelAmend()">Cancel</button>
  </div>
</div>

\${bindings.length ? \`<div class="sec">
  <div class="sec-lbl">Bindings · \${bindings.length}</div>
  <ul class="blist">\${bindings.map(bindHtml).join('')}</ul>
</div>\` : ''}

\${history.length ? \`<div class="sec">
  <div class="sec-lbl">History</div>
  <ul class="hlist">\${history.slice(0,10).map(t => \`<li class="hitem">
    <span class="hkind">\${x(t.kind)}</span>
    <span>\${t.accepted_at ? new Date(t.accepted_at).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'2-digit'}) : 'pending'}</span>
  </li>\`).join('')}</ul>
</div>\` : ''}

<hr class="div">
<div class="abar">
  <button class="btn btn-ghost" onclick="startAmend()">Edit intent</button>
  <button class="btn btn-ghost" onclick="startRename()">Rename</button>
  <input class="rinput" id="rinput" value="\${x(f.slug)}" placeholder="new-slug">
  <button class="btn" id="rsave" style="display:none" onclick="saveRename()">Save</button>
  \${!f.retired ? '<button class="btn btn-del" onclick="doRetire()">Retire</button>' : ''}
</div>\`;
}

function bindHtml(b) {
  const sym = b.anchor.symbol_path || b.anchor.ts_query || '';
  const enc = x(JSON.stringify(b.anchor));
  return \`<li class="bitem">
  <span class="barrow" onclick='navBinding(\${JSON.stringify(b.anchor)})'>→</span>
  <span class="bfile" onclick='navBinding(\${JSON.stringify(b.anchor)})'>\${x(b.anchor.file)}</span>
  \${sym ? \`<span class="bsym">::\${x(sym)}</span>\` : ''}
</li>\`;
}

function navBinding(a) {
  vscode.postMessage({ type:'navigateBinding', file:a.file, symbolPath:a.symbol_path, tsQuery:a.ts_query, occurrenceIndex:a.occurrence_index||0 });
}

function startAmend() {
  $('itxt').style.display = 'none';
  $('ied').style.display = 'block';
  $('isave').style.display = 'flex';
  $('ied').focus();
}
function cancelAmend() {
  $('itxt').style.display = '';
  $('ied').style.display = 'none';
  $('isave').style.display = 'none';
}
function saveAmend() {
  vscode.postMessage({ type:'amend', featureUuid:S.selected.uuid, newIntent:$('ied').value });
  cancelAmend();
}
function startRename() {
  $('rinput').style.display = 'inline';
  $('rsave').style.display = 'inline';
  $('rinput').focus();
}
function saveRename() {
  const v = $('rinput').value.trim();
  if (v) vscode.postMessage({ type:'rename', featureUuid:S.selected.uuid, newSlug:v });
  $('rinput').style.display = 'none';
  $('rsave').style.display = 'none';
}
function doRetire() {
  if (confirm('Retire "' + S.selected.slug + '"?'))
    vscode.postMessage({ type:'retire', featureUuid:S.selected.uuid });
}

// ── Message bus ─────────────────────────────────────────────────────────────

window.addEventListener('message', ({ data: m }) => {
  switch (m.type) {
    case 'status':
      $('pip').className = 'pip' + (m.connected ? ' ok' : '');
      $('statusTxt').textContent = m.connected ? (m.rootDir || 'connected') : 'server not reachable';
      break;
    case 'proposals': renderProposals(m.items); break;
    case 'tree':      renderTree(m.items);      break;
    case 'feature':   renderDetail(m.data, m.bindings, m.history); renderTree(S.tree); break;
    case 'cursorSymbol': {
      const slug = m.symbolName.toLowerCase().replace(/[^a-z0-9_-]/g,'');
      const match = S.tree.find(f => f.slug === m.symbolName || f.slug.includes(slug));
      if (match) loadFeat(match.uuid);
      break;
    }
  }
});

function x(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
</script>
</body>
</html>`;
}
