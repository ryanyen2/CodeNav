import * as vscode from 'vscode';
import { WorkspaceState, ParsedFeature } from '../state/workspace-state';
import { bindingsForFeature } from '../state/bindings-model';

class FeatureTreeItem extends vscode.TreeItem {
    constructor(
        public readonly feature: ParsedFeature,
        state: WorkspaceState,
    ) {
        const label = feature.title || '(untitled)';
        super(label, vscode.TreeItemCollapsibleState.Collapsed);

        const refCount = feature.id ? bindingsForFeature(state.sidecar, feature.id).length : 0;
        const parts: string[] = [];
        if (feature.retired) parts.push('(retired)');
        if (refCount > 0) parts.push(`${refCount} ref${refCount === 1 ? '' : 's'}`);
        this.description = parts.length ? parts.join('  ') : undefined;

        this.tooltip = feature.description
            ? `${label}\n\n${feature.description}`
            : label;
        this.contextValue = 'codocFeature';

        // State-aware icons.
        const isActive = feature.line >= 0 && state.activeFeatureLines.includes(feature.line);
        if (feature.retired) {
            this.iconPath = new vscode.ThemeIcon('circle-slash', new vscode.ThemeColor('disabledForeground'));
        } else if (isActive) {
            this.iconPath = new vscode.ThemeIcon('zap', new vscode.ThemeColor('charts.yellow'));
        } else {
            this.iconPath = new vscode.ThemeIcon('symbol-module');
        }

        this.command = {
            command: 'codoc.navigateToFeature',
            title: 'Navigate to feature',
            arguments: [feature.id],
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
    // feature id → tree item (for treeView.reveal)
    private _itemMap = new Map<string, FeatureTreeItem>();

    constructor(private state: WorkspaceState) {
        // Refresh whenever the files change.
        state.onDidChange(() => this.refresh());
        this._buildMap();
    }

    private _buildMap(): void {
        this.childrenMap.clear();
        this._itemMap.clear();
        for (const f of this.state.features) {
            const key = f.parent_id ?? null;
            if (!this.childrenMap.has(key)) this.childrenMap.set(key, []);
            this.childrenMap.get(key)!.push(f);
            if (f.id) this._itemMap.set(f.id, new FeatureTreeItem(f, this.state));
        }
        // Fix collapsible state based on whether each feature has children.
        for (const [id, item] of this._itemMap) {
            item.collapsibleState = this.childrenMap.has(id)
                ? vscode.TreeItemCollapsibleState.Collapsed
                : vscode.TreeItemCollapsibleState.None;
        }
    }

    refresh(): void {
        this._buildMap();
        this._onDidChangeTreeData.fire();
    }

    /** The tree item for a feature id, if one exists (for treeView.reveal). */
    itemForId(id: string): FeatureTreeItem | undefined {
        return this._itemMap.get(id);
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
            // Reuse the cached item so reveal()'s identity matching works.
            const cached = f.id ? this._itemMap.get(f.id) : undefined;
            if (cached) return cached;
            const item = new FeatureTreeItem(f, this.state);
            item.collapsibleState = this.childrenMap.has(f.id)
                ? vscode.TreeItemCollapsibleState.Collapsed
                : vscode.TreeItemCollapsibleState.None;
            return item;
        });
    }

    getParent(element: TreeNode): TreeNode | null {
        if (!(element instanceof FeatureTreeItem)) return null;
        const parentId = element.feature.parent_id;
        if (!parentId) return null;
        return this._itemMap.get(parentId) ?? null;
    }
}
