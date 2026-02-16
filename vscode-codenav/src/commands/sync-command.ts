import * as vscode from 'vscode';
import { parseTreeBlock, treeToCleanMarkdown } from 'codenav-semantic-tree/extension-api';
import type { BackendManager } from '../backend/backend-manager';
import { writeMeta, metaFromTree } from '../format/meta-store';

export function registerSyncCommand(
  context: vscode.ExtensionContext,
  backend: BackendManager
): void {
  context.subscriptions.push(
    vscode.commands.registerCommand('codenav.sync', async () => {
      const folder = vscode.workspace.workspaceFolders?.[0];
      if (!folder) {
        vscode.window.showErrorMessage('Open a workspace folder first.');
        return;
      }
      const ok = await backend.checkHealth();
      if (!ok) {
        vscode.window.showErrorMessage('CodeNav backend is offline.');
        return;
      }
      await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: 'CodeNav: Syncing…' },
        async () => {
          const result = await backend.api.sync(folder.uri.fsPath, { force_full: false });
          if (!result || result.error) {
            vscode.window.showErrorMessage(result?.error ?? 'Sync failed.');
            return;
          }
          if (!result.tree_md) {
            vscode.window.showErrorMessage('Sync returned no tree.');
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
          vscode.window.showInformationMessage(
            result.is_incremental ? 'CodeNav: Synced (incremental).' : 'CodeNav: Synced.'
          );
        }
      );
    })
  );
}
