/**
 * Sidebar tree view for .codoc structure (sigil icons, hierarchy).
 */

import * as vscode from 'vscode';
import { parseTreeBlock } from 'codenav-semantic-tree/extension-api';

export class CodocTreeDataProvider implements vscode.TreeDataProvider<CodocTreeItem> {
  private _onDidChangeTreeData = new vscode.EventEmitter<CodocTreeItem | undefined | void>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;
  private docUri: vscode.Uri | undefined;

  refresh(uri?: vscode.Uri): void {
    this.docUri = uri;
    this._onDidChangeTreeData.fire();
  }

  getTreeItem(element: CodocTreeItem): vscode.TreeItem {
    return element;
  }

  async getChildren(element?: CodocTreeItem): Promise<CodocTreeItem[]> {
    if (!this.docUri) return [];
    let doc: vscode.TextDocument;
    try {
      doc = await vscode.workspace.openTextDocument(this.docUri);
    } catch {
      return [];
    }
    const tree = parseTreeBlock(doc.getText());
    if (element) {
      const childNodes = element.node?.children ?? [];
      return childNodes.map(
        n =>
          new CodocTreeItem(
            `${n.sigil} ${n.feature}`,
            n,
            n.children.length > 0 ? vscode.TreeItemCollapsibleState.Expanded : vscode.TreeItemCollapsibleState.None
          )
      );
    }
    const root = tree.root;
    return [
      new CodocTreeItem(
        `${root.sigil} ${root.feature}`,
        root,
        root.children.length > 0 ? vscode.TreeItemCollapsibleState.Expanded : vscode.TreeItemCollapsibleState.None
      ),
    ];
  }
}

class CodocTreeItem extends vscode.TreeItem {
  constructor(
    label: string,
    public readonly node: import('codenav-semantic-tree/extension-api').SemanticNode,
    collapsibleState: vscode.TreeItemCollapsibleState
  ) {
    super(label, collapsibleState);
  }
}

export function registerTreeView(context: vscode.ExtensionContext): void {
  const provider = new CodocTreeDataProvider();
  context.subscriptions.push(
    vscode.window.registerTreeDataProvider('codenavTreeView', provider)
  );
  context.subscriptions.push(
    vscode.window.onDidChangeActiveTextEditor(editor => {
      if (editor?.document.languageId === 'codoc') provider.refresh(editor.document.uri);
    })
  );
}
