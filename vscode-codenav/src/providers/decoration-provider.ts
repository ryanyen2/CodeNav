import * as vscode from 'vscode';
import { parseTreeLine } from 'codenav-semantic-tree/extension-api';
import type { Sigil } from 'codenav-semantic-tree/extension-api';
import { readMeta } from '../format/meta-store';

const SIGIL_BORDER_COLORS: Record<Sigil, string> = {
  '/': 'var(--vscode-charts-blue)',
  '%': 'var(--vscode-charts-green)',
  $: 'var(--vscode-charts-orange)',
  '^': 'var(--vscode-charts-purple)',
  '~': 'var(--vscode-editorLineNumber-foreground)',
};

const STATUS_BORDER_COLORS: Record<string, string> = {
  resolved: 'var(--vscode-charts-green)',
  draft: 'var(--vscode-charts-yellow)',
  planned: 'var(--vscode-charts-blue)',
  unresolved: 'var(--vscode-charts-red)',
  surfaced: 'var(--vscode-charts-purple)',
};

export function registerDecorationProvider(context: vscode.ExtensionContext): void {
  const sigilDecorationTypes: Record<Sigil, vscode.TextEditorDecorationType> = {} as Record<
    Sigil,
    vscode.TextEditorDecorationType
  >;
  for (const [sigil, color] of Object.entries(SIGIL_BORDER_COLORS)) {
    sigilDecorationTypes[sigil as Sigil] = vscode.window.createTextEditorDecorationType({
      borderWidth: '0 0 0 4px',
      borderColor: color,
      isWholeLine: false,
      rangeBehavior: vscode.DecorationRangeBehavior.ClosedClosed,
    });
  }
  const statusDecorationTypes: Record<string, vscode.TextEditorDecorationType> = {};
  for (const [status, color] of Object.entries(STATUS_BORDER_COLORS)) {
    statusDecorationTypes[status] = vscode.window.createTextEditorDecorationType({
      borderWidth: '0 0 0 2px',
      borderColor: color,
      borderStyle: 'solid',
      margin: '0 0 0 4px',
      isWholeLine: false,
      rangeBehavior: vscode.DecorationRangeBehavior.ClosedClosed,
    });
  }
  for (const t of Object.values(sigilDecorationTypes)) context.subscriptions.push(t);
  for (const t of Object.values(statusDecorationTypes)) context.subscriptions.push(t);

  function isCodocDoc(doc: vscode.TextDocument): boolean {
    return doc.languageId === 'codoc' || doc.uri.fsPath.endsWith('.codoc');
  }

  function updateDecorations(editor: vscode.TextEditor): void {
    if (!isCodocDoc(editor.document)) return;
    const doc = editor.document;
    const meta = readMeta(doc.uri);
    const sigilRanges: Partial<Record<Sigil, vscode.DecorationOptions[]>> = {};
    const statusRanges: Record<string, vscode.DecorationOptions[]> = {};
    for (const s of Object.keys(STATUS_BORDER_COLORS)) statusRanges[s] = [];

    let currentFpath: string | undefined;
    for (let i = 0; i < doc.lineCount; i++) {
      const line = doc.lineAt(i);
      const parsed = parseTreeLine(line.text);
      if (!parsed) continue;
      const lineRange = new vscode.Range(i, 0, i, line.text.length);
      const indentLen = line.text.length - line.text.trimStart().length;
      const contentStart = new vscode.Range(i, 0, i, Math.min(indentLen + 4, line.text.length));

      if (!sigilRanges[parsed.sigil]) sigilRanges[parsed.sigil] = [];
      sigilRanges[parsed.sigil]!.push({ range: contentStart });

      const fpath = parsed.metadata?.fpath ?? (parsed.sigil === '%' ? parsed.feature?.trim() : currentFpath);
      if (parsed.sigil === '%' && parsed.feature?.trim()) currentFpath = parsed.feature.trim();
      const entityName = parsed.metadata?.entity_name;
      const nodeKey =
        fpath && entityName
          ? `${fpath}::${entityName}`
          : fpath || null;
      const status = (nodeKey && meta?.nodes[nodeKey]?.status) || parsed.status || 'resolved';
      if (statusRanges[status]) {
        statusRanges[status].push({ range: lineRange });
      }
    }

    for (const [sigil, type] of Object.entries(sigilDecorationTypes)) {
      editor.setDecorations(type, sigilRanges[sigil as Sigil] ?? []);
    }
    for (const [status, type] of Object.entries(statusDecorationTypes)) {
      editor.setDecorations(type, statusRanges[status] ?? []);
    }
  }

  context.subscriptions.push(
    vscode.window.onDidChangeActiveTextEditor(editor => {
      if (editor) updateDecorations(editor);
    }),
    vscode.workspace.onDidChangeTextDocument(e => {
      const editor = vscode.window.activeTextEditor;
      if (editor && editor.document === e.document) updateDecorations(editor);
    }),
    vscode.workspace.onDidOpenTextDocument(doc => {
      if (!doc.uri.fsPath.endsWith('.codoc')) return;
      const editor = vscode.window.activeTextEditor;
      if (editor?.document === doc) {
        updateDecorations(editor);
        return;
      }
      setTimeout(() => {
        const ed = vscode.window.activeTextEditor;
        if (ed?.document === doc) updateDecorations(ed);
      }, 100);
    })
  );
  const active = vscode.window.activeTextEditor;
  if (active) updateDecorations(active);
}
