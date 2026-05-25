import * as vscode from 'vscode';
import { WorkspaceState, ParsedFeature } from '../state/workspace-state';

class FeatureTreeItem extends vscode.TreeItem {
    constructor(public readonly feature: ParsedFeature) {
        const label = feature.title || '(untitled)';
        // Determine whether this feature has children by inspecting via the provider.
        super(label, vscode.TreeItemCollapsibleState.Collapsed);

        this.description = feature.retired ? '(retired)' : undefined;
        this.tooltip = feature.description
            ? `${label}\n\n${feature.description}`
            : label;
        this.contextValue = 'codocFeature';
        this.iconPath = feature.retired
            ? new vscode.ThemeIcon('circle-slash', new vscode.ThemeColor('disabledForeground'))
            : new vscode.ThemeIcon('symbol-module');

        this.command = {
            command: 'codoc.open',
            title: 'Open tree.codoc',
            arguments: [],
        };
    }
}

class PlaceholderItem extends vscode.TreeItem {
    constructor(label: string) {
        super(label, vscode.TreeItemCollapsibleState.None);
        this.iconPath = new vscode.ThemeIcon('info');
        this.contextValue = 'codocPlaceholder';
    }
}

type TreeNode = FeatureTreeItem | PlaceholderItem;

export class FeatureTreeProvider implements vscode.TreeDataProvider<TreeNode> {
    private _onDidChangeTreeData = new vscode.EventEmitter<TreeNode | undefined | null | void>();
    readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

    // parent_id → children (null key = root)
    private childrenMap = new Map<string | null, ParsedFeature[]>();

    constructor(private state: WorkspaceState) {
        // Refresh whenever the files change.
        state.onDidChange(() => this.refresh());
        this._buildMap();
    }

    private _buildMap(): void {
        this.childrenMap.clear();
        for (const f of this.state.features) {
            const key = f.parent_id ?? null;
            if (!this.childrenMap.has(key)) this.childrenMap.set(key, []);
            this.childrenMap.get(key)!.push(f);
        }
    }

    refresh(): void {
        this._buildMap();
        this._onDidChangeTreeData.fire();
    }

    getTreeItem(element: TreeNode): vscode.TreeItem {
        return element;
    }

    getChildren(element?: TreeNode): vscode.ProviderResult<TreeNode[]> {
        if (!this.state.rootDir) {
            return [new PlaceholderItem('codoc not initialized — run `codoc init`')];
        }

        const parentKey = (element instanceof FeatureTreeItem) ? element.feature.id : null;
        const children = this.childrenMap.get(parentKey) ?? [];

        if (parentKey === null && children.length === 0) {
            return [new PlaceholderItem('No features — run `codoc init` to bootstrap')];
        }

        return children.map(f => {
            const item = new FeatureTreeItem(f);
            item.collapsibleState = this.childrenMap.has(f.id)
                ? vscode.TreeItemCollapsibleState.Collapsed
                : vscode.TreeItemCollapsibleState.None;
            return item;
        });
    }
}
