import * as vscode from 'vscode';
import { parseTreeLine } from 'codenav-semantic-tree/extension-api';
import { readMeta } from '../format/meta-store';

export function registerDefinitionProvider(context: vscode.ExtensionContext): void {
  context.subscriptions.push(
    vscode.languages.registerDefinitionProvider(
      { language: 'codoc' },
      {
        provideDefinition(document, position) {
          const meta = readMeta(document.uri);
          const line = document.lineAt(position.line);
          const parsed = parseTreeLine(line.text);
          if (!parsed?.metadata?.fpath || !parsed.metadata.entity_name) return null;
          const key = `${parsed.metadata.fpath}::${parsed.metadata.entity_name}`;
          const entry = meta?.nodes[key];
          const lineRange = entry?.line_range;
          const folder = vscode.workspace.getWorkspaceFolder(document.uri);
          if (!folder) return null;
          const fileUri = vscode.Uri.joinPath(folder.uri, parsed.metadata.fpath);
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
