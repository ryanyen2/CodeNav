import * as vscode from 'vscode';

import { registerAnalyzeCommand } from './commands/analyze-command';
import { registerSyncCommand } from './commands/sync-command';
import { registerApplyCommand } from './commands/apply-command';
import { registerPreviewCommand } from './commands/preview-command';
import { registerToggleStatusCommand } from './commands/toggle-status-command';
import { BackendManager } from './backend/backend-manager';
import { registerDecorationProvider } from './providers/decoration-provider';
import { registerCrossEditorHighlight } from './providers/cross-editor-highlight';
import { registerHoverProvider } from './providers/hover-provider';
import { registerDefinitionProvider } from './providers/definition-provider';
import { registerDocumentSymbolProvider } from './providers/document-symbol-provider';
import { registerFoldingProvider } from './providers/folding-provider';
import { registerCodeLensProvider } from './providers/code-lens-provider';
import { registerTreeView } from './tree-view/tree-data-provider';

export function activate(context: vscode.ExtensionContext): void {
  try {
    const backend = new BackendManager(context);
    context.subscriptions.push(backend);

    registerAnalyzeCommand(context, backend);
    registerSyncCommand(context, backend);
    registerApplyCommand(context, backend);
    registerPreviewCommand(context, backend);
    registerToggleStatusCommand(context, backend);

    registerDecorationProvider(context);
    registerCrossEditorHighlight(context);
    registerHoverProvider(context);
    registerDefinitionProvider(context);
    registerDocumentSymbolProvider(context);
    registerFoldingProvider(context);
    registerCodeLensProvider(context, backend);
    registerTreeView(context);
  } catch (err) {
    console.error('[CodeNav] activate failed:', err);
    throw err;
  }
}

export function deactivate(): void {}
