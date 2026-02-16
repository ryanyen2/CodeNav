import * as vscode from 'vscode';
import { parseTreeBlock, treeToMarkdown } from 'codenav-semantic-tree/extension-api';
import type { BackendManager } from '../backend/backend-manager';
import { readMeta } from '../format/meta-store';
import { enrichTreeFromMeta } from '../format/clean-parser';

export function registerPreviewCommand(
  context: vscode.ExtensionContext,
  backend: BackendManager
): void {
  context.subscriptions.push(
    vscode.commands.registerCommand('codenav.preview', async () => {
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
        vscode.window.showErrorMessage('No .codoc file found.');
        return;
      }
      const cleanText = doc.getText();
      const meta = readMeta(codocUri);
      const tree = parseTreeBlock(cleanText);
      if (meta) enrichTreeFromMeta(tree, meta);
      const fullMd = treeToMarkdown(tree);

      const result = await backend.api.apply(folder.uri.fsPath, fullMd, true);
      if (result.error) {
        vscode.window.showErrorMessage(`Preview failed: ${result.error}`);
        return;
      }
      const changes = result.planned_changes ?? [];
      if (changes.length === 0) {
        vscode.window.showInformationMessage('CodeNav: No planned changes.');
        return;
      }
      const content = changes
        .map(
          c =>
            `**${c.fpath}** (${c.line_start}-${c.line_end}):\n\`\`\`\n${c.new_content}\n\`\`\``
        )
        .join('\n\n');
      const md = await vscode.workspace.openTextDocument({
        content: '# CodeNav: Preview Apply\n\n' + content,
        language: 'markdown',
      });
      await vscode.window.showTextDocument(md);
    })
  );
}
