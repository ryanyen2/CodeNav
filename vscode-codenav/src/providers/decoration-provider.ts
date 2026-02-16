import * as vscode from 'vscode';
import { parseTreeLine } from 'codenav-semantic-tree/extension-api';
import { readMeta } from '../format/meta-store';

export function registerDecorationProvider(context: vscode.ExtensionContext): void {
  const sigilDecorationType = vscode.window.createTextEditorDecorationType({
    opacity: '0.9',
  });
  const statusDecorationType = vscode.window.createTextEditorDecorationType({
    borderWidth: '0 0 0 3px',
    borderColor: 'var(--vscode-charts-green)',
  });

  function updateDecorations(editor: vscode.TextEditor): void {
    if (editor.document.languageId !== 'codoc') return;
    const doc = editor.document;
    const meta = readMeta(doc.uri);
    const sigilRanges: vscode.DecorationOptions[] = [];
    const statusRanges: vscode.DecorationOptions[] = [];
    for (let i = 0; i < doc.lineCount; i++) {
      const line = doc.lineAt(i);
      const parsed = parseTreeLine(line.text);
      if (!parsed) continue;
      const range = new vscode.Range(i, 0, i, Math.min(2, line.text.length));
      sigilRanges.push({ range });
      const key =
        parsed.metadata?.fpath && parsed.metadata?.entity_name
          ? `${parsed.metadata.fpath}::${parsed.metadata.entity_name}`
          : null;
      if (key && meta?.nodes[key]?.status === 'resolved') {
        statusRanges.push({ range: new vscode.Range(i, 0, i, line.text.length) });
      }
    }
    editor.setDecorations(sigilDecorationType, sigilRanges);
    editor.setDecorations(statusDecorationType, statusRanges);
  }

  context.subscriptions.push(
    vscode.window.onDidChangeActiveTextEditor(editor => {
      if (editor) updateDecorations(editor);
    }),
    vscode.workspace.onDidChangeTextDocument(e => {
      const editor = vscode.window.activeTextEditor;
      if (editor && editor.document === e.document) updateDecorations(editor);
    })
  );
  if (vscode.window.activeTextEditor) {
    updateDecorations(vscode.window.activeTextEditor);
  }
}
