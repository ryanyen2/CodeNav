import * as vscode from 'vscode';
import { parseTreeBlock, treeToCleanMarkdown } from 'codenav-semantic-tree/extension-api';
import type { BackendManager } from '../backend/backend-manager';
import { readMeta, writeMeta, metaFromTree } from '../format/meta-store';

export function registerAnalyzeCommand(
  context: vscode.ExtensionContext,
  backend: BackendManager
): void {
  context.subscriptions.push(
    vscode.commands.registerCommand('codenav.analyze', async () => {
      const folder = vscode.workspace.workspaceFolders?.[0];
      if (!folder) {
        vscode.window.showErrorMessage('Open a workspace folder first.');
        return;
      }
      const ok = await backend.checkHealth();
      if (!ok) {
        vscode.window.showErrorMessage('CodeNav backend is offline. Start the server and try again.');
        return;
      }
      await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: 'CodeNav: Analyzing codebase…' },
        async () => {
          const result = await backend.api.analyze(folder.uri.fsPath);
          if (!result) {
            vscode.window.showErrorMessage('Analyze failed. Check backend logs.');
            return;
          }
          const tree = parseTreeBlock(result.tree_md);
          const meta = metaFromTree(tree);
          const cleanMd = treeToCleanMarkdown(tree);
          const name = folder.name || 'Project';
          const codocUri = vscode.Uri.joinPath(folder.uri, `${name}.codoc`);
          await vscode.workspace.fs.writeFile(
            codocUri,
            Buffer.from(cleanMd, 'utf-8')
          );
          writeMeta(codocUri, meta);
          backend.setStatus('synced');
          const doc = await vscode.workspace.openTextDocument(codocUri);
          await vscode.window.showTextDocument(doc);
          vscode.window.showInformationMessage(`CodeNav: Created ${name}.codoc (${result.entity_count} entities).`);
        }
      );
    })
  );
}
