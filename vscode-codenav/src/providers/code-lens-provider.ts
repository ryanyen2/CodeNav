import * as vscode from 'vscode';
import type { BackendManager } from '../backend/backend-manager';

export function registerCodeLensProvider(
  context: vscode.ExtensionContext,
  _backend: BackendManager
): void {
  context.subscriptions.push(
    vscode.languages.registerCodeLensProvider(
      { language: 'codoc' },
      {
        provideCodeLenses(document): vscode.CodeLens[] {
          if (document.lineCount === 0) return [];
          const range = new vscode.Range(0, 0, 0, 0);
          return [
            new vscode.CodeLens(range, {
              title: 'Sync',
              command: 'codenav.sync',
            }),
            new vscode.CodeLens(range, {
              title: 'Apply',
              command: 'codenav.apply',
            }),
            new vscode.CodeLens(range, {
              title: 'Preview',
              command: 'codenav.preview',
            }),
          ];
        },
      }
    )
  );
}
