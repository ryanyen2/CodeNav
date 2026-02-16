import * as vscode from 'vscode';
import { parseTreeBlock, treeToMarkdown } from 'codenav-semantic-tree/extension-api';
import type { BackendManager } from '../backend/backend-manager';
import { readMeta } from '../format/meta-store';
import { enrichTreeFromMeta } from '../format/clean-parser';

const BASE_TREE_KEY = 'codenav:baseTree';

function baseTreeKey(folderFsPath: string): string {
  return `${BASE_TREE_KEY}:${folderFsPath}`;
}

export function registerApplyCommand(
  context: vscode.ExtensionContext,
  backend: BackendManager
): void {
  // Store .codoc content as "base" on open and save so Apply diffs base vs current (only actual edits)
  function storeBaseIfCodoc(doc: vscode.TextDocument): void {
    if (!doc.uri.fsPath.endsWith('.codoc')) return;
    const folder = vscode.workspace.getWorkspaceFolder(doc.uri);
    if (!folder) return;
    context.globalState.update(baseTreeKey(folder.uri.fsPath), doc.getText());
  }
  context.subscriptions.push(
    vscode.workspace.onDidOpenTextDocument(storeBaseIfCodoc),
    vscode.workspace.onDidSaveTextDocument(storeBaseIfCodoc)
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('codenav.apply', async () => {
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
      let doc: vscode.TextDocument;
      try {
        doc = await vscode.workspace.openTextDocument(codocUri);
      } catch {
        vscode.window.showErrorMessage('No .codoc file found. Run Analyze first.');
        return;
      }
      const cleanText = doc.getText();
      const meta = readMeta(codocUri);
      const tree = parseTreeBlock(cleanText);
      if (meta) enrichTreeFromMeta(tree, meta);
      const fullMd = treeToMarkdown(tree);

      const storedBase = context.globalState.get<string>(baseTreeKey(folder.uri.fsPath));
      if (!storedBase) {
        vscode.window.showWarningMessage(
          'CodeNav: No base tree stored. Open the .codoc file and run Apply again so only your edits are applied (not the whole tree vs backend).'
        );
      }

      await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: 'CodeNav: Applying…' },
        async () => {
          const result = await backend.api.apply(folder.uri.fsPath, fullMd, false, {
            diffFormat: 'unified_diff',
            baseTreeMd: storedBase ?? undefined,
          });
          if (result.error) {
            vscode.window.showErrorMessage(`Apply failed: ${result.error}`);
            return;
          }
          context.globalState.update(baseTreeKey(folder.uri.fsPath), fullMd);
          if (result.unified_diff) {
            const diffDoc = await vscode.workspace.openTextDocument({
              content: result.unified_diff,
              language: 'diff',
            });
            await vscode.window.showTextDocument(diffDoc, { viewColumn: vscode.ViewColumn.Beside });
          }
          if (result.applied && result.modified_fpaths?.length) {
            vscode.window.showInformationMessage(`CodeNav: Applied to ${result.modified_fpaths.length} file(s).`);
          } else if ((result.operations?.length ?? 0) > 0 && !result.applied) {
            vscode.window.showWarningMessage(
              `CodeNav: ${result.operations!.length} operation(s) identified but no files modified (dry run or best-effort).`
            );
          } else {
            vscode.window.showInformationMessage('CodeNav: No changes applied.');
          }
        }
      );
    })
  );
}
