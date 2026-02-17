import * as vscode from 'vscode';
import { parseCodocDocument, getCodeLocationRef } from '../codoc-document/codoc-document';
import { readMeta } from '../format/meta-store';

const CODE_HIGHLIGHT_DECORATION = vscode.window.createTextEditorDecorationType({
  backgroundColor: new vscode.ThemeColor('editor.findMatchHighlightBackground'),
  isWholeLine: false,
  rangeBehavior: vscode.DecorationRangeBehavior.ClosedClosed,
});

function isCodocDoc(doc: vscode.TextDocument): boolean {
  return doc.languageId === 'codoc' || doc.uri.fsPath.endsWith('.codoc');
}

/**
 * When the user's caret is on a .codoc node that has a code location (fpath + optional line_range),
 * open that file in the split editor and show a lightweight highlight on the corresponding range.
 */
export function registerCrossEditorHighlight(context: vscode.ExtensionContext): void {
  let lastCodeEditor: vscode.TextEditor | undefined;
  let lastHighlightUri: string | null = null;

  function clearCodeHighlight(): void {
    if (lastCodeEditor) {
      try {
        lastCodeEditor.setDecorations(CODE_HIGHLIGHT_DECORATION, []);
      } catch {
        // editor may be closed
      }
      lastCodeEditor = undefined;
    }
    lastHighlightUri = null;
  }

  function updateHighlight(codocEditor: vscode.TextEditor, lineIndex: number): void {
    const doc = codocEditor.document;
    if (!isCodocDoc(doc)) return;

    const enabled = vscode.workspace.getConfiguration('codenav').get<boolean>('crossEditorHighlight', true);
    if (!enabled) {
      clearCodeHighlight();
      return;
    }

    const snapshot = parseCodocDocument(doc.getText());
    const info = snapshot.lineInfos.find((l) => l.lineIndex === lineIndex);
    if (!info) {
      clearCodeHighlight();
      return;
    }

    const meta = readMeta(doc.uri);
    const nodeKey =
      info.entityId ?? (info.fpath ? info.fpath : null);
    const metaEntry = nodeKey ? meta?.nodes[nodeKey] : undefined;
    const lineRange = metaEntry?.line_range;

    const ref = getCodeLocationRef(info, lineRange);
    if (!ref) {
      clearCodeHighlight();
      return;
    }

    const folder = vscode.workspace.getWorkspaceFolder(doc.uri);
    if (!folder) return;

    const codeUri = vscode.Uri.joinPath(folder.uri, ref.fpath);
    const uriStr = codeUri.toString();
    const startLine = Math.max(0, ref.startLine - 1);
    const endLine = Math.max(0, ref.endLine - 1);

    const showInSplit = () => {
      vscode.commands
        .executeCommand('vscode.open', codeUri, vscode.ViewColumn.Beside)
        .then(() => {
          const codeEditor = vscode.window.visibleTextEditors.find((e) => e.document.uri.toString() === uriStr);
          if (!codeEditor) return;

          lastCodeEditor = codeEditor;
          lastHighlightUri = uriStr;
          const range = new vscode.Range(startLine, 0, endLine, 0);
          codeEditor.setDecorations(CODE_HIGHLIGHT_DECORATION, [{ range }]);
        });
    };

    const existing = vscode.window.visibleTextEditors.find((e) => e.document.uri.toString() === uriStr);
    if (lastCodeEditor && lastCodeEditor.document.uri.toString() !== uriStr) {
      try {
        lastCodeEditor.setDecorations(CODE_HIGHLIGHT_DECORATION, []);
      } catch {
        /* editor may be closed */
      }
    }
    if (existing) {
      lastCodeEditor = existing;
      lastHighlightUri = uriStr;
      const range = new vscode.Range(startLine, 0, endLine, 0);
      existing.setDecorations(CODE_HIGHLIGHT_DECORATION, [{ range }]);
    } else {
      showInSplit();
    }
  }

  function onCodocCursorChange(editor: vscode.TextEditor | undefined): void {
    if (!editor || !isCodocDoc(editor.document)) {
      clearCodeHighlight();
      return;
    }
    const line = editor.selection.active.line;
    updateHighlight(editor, line);
  }

  context.subscriptions.push(
    CODE_HIGHLIGHT_DECORATION,
    vscode.window.onDidChangeActiveTextEditor((editor) => {
      if (editor && isCodocDoc(editor.document)) onCodocCursorChange(editor);
      else clearCodeHighlight();
    }),
    vscode.window.onDidChangeTextEditorSelection((e) => {
      if (e.textEditor === e.textEditor && isCodocDoc(e.textEditor.document)) {
        onCodocCursorChange(e.textEditor);
      }
    }),
    vscode.workspace.onDidCloseTextDocument((doc) => {
      if (lastHighlightUri && doc.uri.toString() === lastHighlightUri) {
        clearCodeHighlight();
      }
    })
  );
}
