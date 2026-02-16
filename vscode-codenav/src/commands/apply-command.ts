import * as vscode from 'vscode';
import { parseTreeBlock, treeToMarkdown } from 'codenav-semantic-tree/extension-api';
import type { BackendManager } from '../backend/backend-manager';
import { readMeta } from '../format/meta-store';
import { enrichTreeFromMeta } from '../format/clean-parser';

export function registerApplyCommand(
  context: vscode.ExtensionContext,
  backend: BackendManager
): void {
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

      await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: 'CodeNav: Applying…' },
        async () => {
          const result = await backend.api.apply(folder.uri.fsPath, fullMd, false);
          if (result.error) {
            vscode.window.showErrorMessage(`Apply failed: ${result.error}`);
            return;
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
