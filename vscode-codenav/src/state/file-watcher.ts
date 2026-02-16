/**
 * Watch *.py (and optionally other) files; set "Code Changed" when codebase changes.
 */

import * as vscode from 'vscode';

export function createFileWatcher(
  folder: vscode.WorkspaceFolder,
  onCodeChanged: () => void
): vscode.FileSystemWatcher | undefined {
  const pattern = new vscode.RelativePattern(folder, '**/*.py');
  const watcher = vscode.workspace.createFileSystemWatcher(pattern);
  const notify = () => onCodeChanged();
  watcher.onDidChange(notify);
  watcher.onDidCreate(notify);
  watcher.onDidDelete(notify);
  return watcher;
}
