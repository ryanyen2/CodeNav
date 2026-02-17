import * as vscode from 'vscode';
import { parseCodocDocument } from '../codoc-document/codoc-document';
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
 * Code location from server-only data (.codoc.meta.json). No regex or inferred ranges.
 * Node key must match server: fpath::entity_name for leaves, fpath for file nodes.
 */
export interface CodeLocationFromMeta {
  fpath: string;
  startLine: number;
  endLine: number;
}

/**
 * Resolve code location from server-provided meta when available; otherwise fallback so
 * we still open the file (tab open/switch works). Key matches server: fpath::entity_name or fpath.
 */
function codeLocationFromMeta(
  info: { entityId: string | null; fpath: string | null },
  meta: { nodes: Record<string, { line_range?: [number, number] }> } | null
): CodeLocationFromMeta | null {
  if (!info.fpath) return null;
  const nodeKey = info.entityId ?? info.fpath;
  const entry = meta?.nodes?.[nodeKey];
  const lineRange = entry?.line_range;
  const startLine = lineRange && lineRange.length >= 2 ? lineRange[0] : 1;
  const endLine = lineRange && lineRange.length >= 2 ? lineRange[1] : 1;
  return {
    fpath: info.fpath,
    startLine,
    endLine,
  };
}

/**
 * When the user's caret is on a .codoc node:
 * - Use only server-provided line_range from .codoc.meta.json (no regex).
 * - Reuse existing tab if file is open; else show in split with preserveFocus.
 * - Highlight only the starting line of the corresponding section in the code file.
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

  function applyHighlightToEditor(
    codeEditor: vscode.TextEditor,
    loc: CodeLocationFromMeta
  ): void {
    const doc = codeEditor.document;
    const line0 = Math.max(0, loc.startLine - 1);
    if (line0 >= doc.lineCount) return;
    const lineObj = doc.lineAt(line0);
    const range = new vscode.Range(line0, 0, line0, Math.max(0, lineObj.text.length));
    codeEditor.setDecorations(CODE_HIGHLIGHT_DECORATION, [{ range }]);
  }

  function updateHighlight(codocEditor: vscode.TextEditor, lineIndex: number): void {
    const doc = codocEditor.document;
    if (!isCodocDoc(doc)) return;

    const enabled = vscode.workspace
      .getConfiguration('codenav')
      .get<boolean>('crossEditorHighlight', true);
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
    const loc = codeLocationFromMeta(info, meta);
    if (!loc) {
      clearCodeHighlight();
      return;
    }

    const folder = vscode.workspace.getWorkspaceFolder(doc.uri);
    if (!folder) return;

    const codeUri = vscode.Uri.joinPath(folder.uri, loc.fpath);
    const uriStr = codeUri.toString();

    const existing = vscode.window.visibleTextEditors.find(
      (e) => e.document.uri.toString() === uriStr
    );

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
      applyHighlightToEditor(existing, loc);
      return;
    }

    vscode.workspace.openTextDocument(codeUri).then((codeDoc) => {
      vscode.window
        .showTextDocument(codeDoc, {
          viewColumn: vscode.ViewColumn.Beside,
          preserveFocus: true,
          preview: false,
        })
        .then((codeEditor) => {
          lastCodeEditor = codeEditor;
          lastHighlightUri = uriStr;
          applyHighlightToEditor(codeEditor, loc);
        });
    });
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
