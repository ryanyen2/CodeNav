import * as vscode from 'vscode';
import { parseCodocDocument } from '../codoc-document/codoc-document';
import { readMeta } from '../format/meta-store';

export function registerDefinitionProvider(context: vscode.ExtensionContext): void {
  context.subscriptions.push(
    vscode.languages.registerDefinitionProvider(
      { language: 'codoc' },
      {
        provideDefinition(document, position) {
          const snapshot = parseCodocDocument(document.getText());
          const info = snapshot.lineInfos.find((l) => l.lineIndex === position.line);
          if (!info?.fpath) return null;
          const meta = readMeta(document.uri);
          const nodeKey = info.entityId ?? info.fpath;
          const entry = meta?.nodes[nodeKey];
          const lineRange = entry?.line_range;
          const folder = vscode.workspace.getWorkspaceFolder(document.uri);
          if (!folder) return null;
          const fileUri = vscode.Uri.joinPath(folder.uri, info.fpath);
          const start = lineRange ? lineRange[0] - 1 : 0;
          const end = lineRange ? lineRange[1] - 1 : start;
          return new vscode.Location(
            fileUri,
            new vscode.Range(start, 0, end, 0)
          );
        },
      }
    )
  );
}
