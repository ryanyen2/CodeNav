import * as vscode from 'vscode';
import { parseTreeLine } from 'codenav-semantic-tree/extension-api';
import { readMeta } from '../format/meta-store';

export function registerHoverProvider(context: vscode.ExtensionContext): void {
  context.subscriptions.push(
    vscode.languages.registerHoverProvider(
      { language: 'codoc' },
      {
        provideHover(document, position) {
          const meta = readMeta(document.uri);
          const line = document.lineAt(position.line);
          const parsed = parseTreeLine(line.text);
          if (!parsed) return null;
          const key =
            parsed.metadata?.fpath && parsed.metadata?.entity_name
              ? `${parsed.metadata.fpath}::${parsed.metadata.entity_name}`
              : null;
          const parts: string[] = [
            `**${parsed.sigil}** ${parsed.feature}`,
            parsed.metadata?.fpath ? `\nFile: \`${parsed.metadata.fpath}\`` : '',
            parsed.metadata?.entity_name ? `\nEntity: \`${parsed.metadata.entity_name}\`` : '',
          ];
          if (key && meta?.nodes[key]) {
            const n = meta.nodes[key];
            if (n.contracts?.sig) parts.push(`\nSignature: \`${n.contracts.sig}\``);
            parts.push(`\nStatus: ${n.status}`);
          }
          return new vscode.Hover(parts.join(''));
        },
      }
    )
  );
}
