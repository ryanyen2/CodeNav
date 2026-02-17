import * as vscode from 'vscode';
import { parseTreeLine } from 'codenav-semantic-tree/extension-api';
import type { Sigil } from 'codenav-semantic-tree/extension-api';
import { readMeta } from '../format/meta-store';
import {
  parseCodocDocument,
  getRelatedLineIndices,
  type CodocDocumentSnapshot,
} from '../codoc-document/codoc-document';

const SIGIL_BORDER_COLORS: Record<Sigil, string> = {
  '/': 'var(--vscode-charts-blue)',
  '%': 'var(--vscode-charts-green)',
  $: 'var(--vscode-charts-orange)',
  '^': 'var(--vscode-charts-purple)',
  '~': 'var(--vscode-editorLineNumber-foreground)',
};

/** Icons shown before the line (contentText) so node type is visible without relying on sigil. */
const SIGIL_ICONS: Record<Sigil, string> = {
  '/': '📁',
  '%': '📄',
  $: 'ƒ',
  '^': 'C',
  '~': '◇',
};

const STATUS_BORDER_COLORS: Record<string, string> = {
  resolved: 'var(--vscode-charts-green)',
  draft: 'var(--vscode-charts-yellow)',
  planned: 'var(--vscode-charts-blue)',
  unresolved: 'var(--vscode-charts-red)',
  surfaced: 'var(--vscode-charts-purple)',
};

function isCodocDoc(doc: vscode.TextDocument): boolean {
  return doc.languageId === 'codoc' || doc.uri.fsPath.endsWith('.codoc');
}

export function registerDecorationProvider(context: vscode.ExtensionContext): void {
  const sigilDecorationTypes: Record<Sigil, vscode.TextEditorDecorationType> = {} as Record<
    Sigil,
    vscode.TextEditorDecorationType
  >;
  for (const [sigil, color] of Object.entries(SIGIL_BORDER_COLORS)) {
    sigilDecorationTypes[sigil as Sigil] = vscode.window.createTextEditorDecorationType({
      borderWidth: '0 0 0 2px',
      borderColor: color,
      isWholeLine: false,
      rangeBehavior: vscode.DecorationRangeBehavior.ClosedClosed,
    });
  }

  const iconDecorationTypes: Record<Sigil, vscode.TextEditorDecorationType> = {} as Record<
    Sigil,
    vscode.TextEditorDecorationType
  >;
  for (const [sigil, color] of Object.entries(SIGIL_BORDER_COLORS)) {
    const icon = SIGIL_ICONS[sigil as Sigil];
    iconDecorationTypes[sigil as Sigil] = vscode.window.createTextEditorDecorationType({
      before: {
        contentText: icon + ' ',
        color: color,
        fontWeight: 'normal',
        margin: '0 2px 0 0',
      },
      rangeBehavior: vscode.DecorationRangeBehavior.ClosedClosed,
    });
  }

  const hideSigilDecorationType = vscode.window.createTextEditorDecorationType({
    opacity: '0',
    rangeBehavior: vscode.DecorationRangeBehavior.ClosedClosed,
  });
  context.subscriptions.push(hideSigilDecorationType);

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

  const focusRelatedDecorationType = vscode.window.createTextEditorDecorationType({
    backgroundColor: new vscode.ThemeColor('editor.findMatchHighlightBackground'),
    opacity: '1',
    isWholeLine: true,
    rangeBehavior: vscode.DecorationRangeBehavior.ClosedClosed,
  });

  const dimUnrelatedDecorationType = vscode.window.createTextEditorDecorationType({
    opacity: '0.45',
    isWholeLine: true,
    rangeBehavior: vscode.DecorationRangeBehavior.ClosedClosed,
  });

  for (const t of Object.values(sigilDecorationTypes)) context.subscriptions.push(t);
  for (const t of Object.values(iconDecorationTypes)) context.subscriptions.push(t);
  for (const t of Object.values(statusDecorationTypes)) context.subscriptions.push(t);
  context.subscriptions.push(focusRelatedDecorationType, dimUnrelatedDecorationType);

  function updateDecorations(editor: vscode.TextEditor, cursorLine?: number): void {
    if (!isCodocDoc(editor.document)) return;
    const doc = editor.document;
    const meta = readMeta(doc.uri);
    const text = doc.getText();
    const snapshot = parseCodocDocument(text);

    const sigilRanges: Partial<Record<Sigil, vscode.DecorationOptions[]>> = {};
    const iconRanges: Partial<Record<Sigil, vscode.DecorationOptions[]>> = {};
    const hideSigilRanges: vscode.DecorationOptions[] = [];
    const statusRanges: Record<string, vscode.DecorationOptions[]> = {};
    for (const s of Object.keys(STATUS_BORDER_COLORS)) statusRanges[s] = [];

    let currentFpath: string | undefined;
    const treeLineIndices = new Set<number>();

    for (let i = 0; i < doc.lineCount; i++) {
      const line = doc.lineAt(i);
      const parsed = parseTreeLine(line.text);
      if (!parsed) continue;

      treeLineIndices.add(i);
      const lineRange = new vscode.Range(i, 0, i, line.text.length);
      const indentLen = line.text.length - line.text.trimStart().length;
      const sigilStart = indentLen + 2;
      const sigilEnd = sigilStart + 1;
      const sigilRange = new vscode.Range(i, sigilStart, i, sigilEnd);

      if (!sigilRanges[parsed.sigil]) sigilRanges[parsed.sigil] = [];
      sigilRanges[parsed.sigil]!.push({ range: sigilRange });

      if (!iconRanges[parsed.sigil]) iconRanges[parsed.sigil] = [];
      iconRanges[parsed.sigil]!.push({ range: sigilRange });

      hideSigilRanges.push({ range: sigilRange });

      const fpath =
        parsed.metadata?.fpath ?? (parsed.sigil === '%' ? parsed.feature?.trim() : currentFpath);
      if (parsed.sigil === '%' && parsed.feature?.trim()) currentFpath = parsed.feature.trim();
      const entityName = parsed.metadata?.entity_name;
      const nodeKey =
        fpath && entityName ? `${fpath}::${entityName}` : fpath || null;
      const status = (nodeKey && meta?.nodes[nodeKey]?.status) || parsed.status || 'resolved';
      if (statusRanges[status]) {
        statusRanges[status].push({ range: lineRange });
      }
    }

    for (const [sigil, type] of Object.entries(sigilDecorationTypes)) {
      editor.setDecorations(type, sigilRanges[sigil as Sigil] ?? []);
    }
    for (const [sigil, type] of Object.entries(iconDecorationTypes)) {
      editor.setDecorations(type, iconRanges[sigil as Sigil] ?? []);
    }
    editor.setDecorations(hideSigilDecorationType, hideSigilRanges);
    for (const [status, type] of Object.entries(statusDecorationTypes)) {
      editor.setDecorations(type, statusRanges[status] ?? []);
    }

    const focusMode = vscode.workspace.getConfiguration('codenav').get<boolean>('focusMode', true);
    if (focusMode && cursorLine !== undefined && treeLineIndices.size > 0) {
      const related = getRelatedLineIndices(snapshot, cursorLine);
      const focusRanges: vscode.DecorationOptions[] = [];
      const dimRanges: vscode.DecorationOptions[] = [];
      for (let i = 0; i < doc.lineCount; i++) {
        const line = doc.lineAt(i);
        const range = new vscode.Range(i, 0, i, line.text.length);
        const isTreeLine = treeLineIndices.has(i);
        const isDepsLine =
          snapshot.depsStartLine >= 0 &&
          i >= snapshot.depsStartLine &&
          line.text.trim().length > 0 &&
          line.text.trim() !== 'deps:';
        if (!isTreeLine && !isDepsLine) continue;
        if (related.has(i)) focusRanges.push({ range });
        else dimRanges.push({ range });
      }
      editor.setDecorations(focusRelatedDecorationType, focusRanges);
      editor.setDecorations(dimUnrelatedDecorationType, dimRanges);
    } else {
      editor.setDecorations(focusRelatedDecorationType, []);
      editor.setDecorations(dimUnrelatedDecorationType, []);
    }
  }

  function getCursorLine(editor: vscode.TextEditor | undefined): number | undefined {
    if (!editor || editor.document.uri.scheme !== 'file') return undefined;
    return editor.selection.active.line;
  }

  function refreshActive(): void {
    const editor = vscode.window.activeTextEditor;
    if (editor) updateDecorations(editor, getCursorLine(editor));
  }

  context.subscriptions.push(
    vscode.window.onDidChangeActiveTextEditor(() => refreshActive()),
    vscode.window.onDidChangeTextEditorSelection((e) => {
      if (e.textEditor.document === e.textEditor.document && isCodocDoc(e.textEditor.document)) {
        updateDecorations(e.textEditor, e.selections[0]?.active.line);
      }
    }),
    vscode.workspace.onDidChangeTextDocument((e) => {
      const editor = vscode.window.activeTextEditor;
      if (editor && editor.document === e.document) {
        updateDecorations(editor, getCursorLine(editor));
      }
    }),
    vscode.workspace.onDidOpenTextDocument((doc) => {
      if (!doc.uri.fsPath.endsWith('.codoc')) return;
      setTimeout(refreshActive, 50);
    })
  );

  refreshActive();
}
