import * as vscode from 'vscode';
import { parseTreeBlock, treeToCleanMarkdown } from 'codenav-semantic-tree/extension-api';
import type { BackendManager } from '../backend/backend-manager';
import { writeMeta, metaFromTree, metaFromTreeJson } from '../format/meta-store';
import { applyPatchToMarkdown, type TreePatch } from '../sync/patch-applier';

const CODOC_BACKUP_KEY = (folderPath: string) => `codenav.codocBackup.${folderPath}`;

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
      const name = folder.name || 'Project';
      const codocUri = vscode.Uri.joinPath(folder.uri, `${name}.codoc`);

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

          try {
            parseTreeBlock(result.tree_md);
          } catch {
            vscode.window.showErrorMessage(
              'CodeNav: Sync returned invalid tree markdown. File was not overwritten.'
            );
            return;
          }

          let currentContent: string | undefined;
          try {
            const doc = await vscode.workspace.openTextDocument(codocUri);
            currentContent = doc.getText();
          } catch {
            currentContent = undefined;
          }

          if (currentContent != null) {
            context.globalState.update(CODOC_BACKUP_KEY(folder.uri.fsPath), currentContent);
          }

          let finalMd: string;
          if (
            result.tree_patch &&
            currentContent != null &&
            typeof result.tree_patch === 'object'
          ) {
            const patched = applyPatchToMarkdown(currentContent, result.tree_patch as TreePatch);
            finalMd = patched ?? result.tree_md;
          } else {
            finalMd = result.tree_md;
          }

          const tree = parseTreeBlock(finalMd);
          const meta =
            result.tree_json != null
              ? metaFromTreeJson(result.tree_json)
              : metaFromTree(tree);
          const cleanMd = treeToCleanMarkdown(tree);

          await vscode.workspace.fs.writeFile(
            codocUri,
            Buffer.from(cleanMd, 'utf-8')
          );
          writeMeta(codocUri, meta);
          backend.setStatus('synced');

          const msg = result.is_incremental ? 'CodeNav: Synced (incremental).' : 'CodeNav: Synced.';
          const merge = result.merge_summary;
          if (merge) {
            const parts: string[] = [];
            if (merge.preserved_user_nodes != null) parts.push(`Preserved ${merge.preserved_user_nodes} user nodes`);
            if (merge.surfaced_added != null) parts.push(`surfaced ${merge.surfaced_added} added`);
            if (parts.length) {
              vscode.window.showInformationMessage(`${msg} ${parts.join(', ')}.`);
            } else {
              vscode.window.showInformationMessage(msg);
            }
          } else {
            vscode.window.showInformationMessage(msg);
          }
        }
      );
    })
  );
}
