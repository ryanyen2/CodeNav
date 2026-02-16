import * as vscode from 'vscode';
import { parseTreeLine, type NodeStatus } from 'codenav-semantic-tree/extension-api';
import { readMeta, writeMeta } from '../format/meta-store';

const STATUS_CYCLE: NodeStatus[] = ['resolved', 'draft', 'planned', 'resolved'];

export function registerToggleStatusCommand(context: vscode.ExtensionContext): void {
  context.subscriptions.push(
    vscode.commands.registerCommand('codenav.toggleStatus', async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor || editor.document.languageId !== 'codoc') return;
      const line = editor.document.lineAt(editor.selection.active.line);
      const parsed = parseTreeLine(line.text);
      if (!parsed || !parsed.metadata?.fpath || !parsed.metadata?.entity_name) {
        vscode.window.showInformationMessage('Place cursor on a node line with (entity) to toggle status.');
        return;
      }
      const key = `${parsed.metadata.fpath}::${parsed.metadata.entity_name}`;
      const meta = readMeta(editor.document.uri);
      if (!meta) return;
      const entry = meta.nodes[key];
      const current: NodeStatus = entry?.status ?? 'resolved';
      const idx = STATUS_CYCLE.indexOf(current);
      const next = STATUS_CYCLE[idx + 1] ?? STATUS_CYCLE[0]!;
      meta.nodes[key] = { ...entry, status: next, contracts: entry?.contracts ?? {} };
      writeMeta(editor.document.uri, meta);
      vscode.window.showInformationMessage(`Status: ${current} → ${next}`);
    })
  );
}
