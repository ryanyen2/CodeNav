import * as vscode from 'vscode';
import { ServerState } from '../state/server';
import { FeatureResponse } from '../api/client';

export class FeatureTreeItem extends vscode.TreeItem {
    constructor(
        public readonly feature: FeatureResponse,
        collapsibleState: vscode.TreeItemCollapsibleState,
    ) {
        super(feature.title || feature.slug, collapsibleState);

        const stateLabel = feature.retired ? 'retired'
            : feature.state === 'severed' ? 'severed'
            : feature.state === 'strained' ? 'strained'
            : null;

        this.description = stateLabel ? `(${stateLabel})` : undefined;

        const tooltipLines = [feature.title || feature.slug];
        if (feature.intent) tooltipLines.push('', feature.intent);
        this.tooltip = tooltipLines.join('\n');

        this.contextValue = 'codocFeature';

        this.command = {
            command: 'codoc.navigateToFeature',
            title: 'Navigate to feature',
            arguments: [feature.title || feature.slug],
        };

        if (feature.retired) {
            this.iconPath = new vscode.ThemeIcon('circle-slash',
                new vscode.ThemeColor('disabledForeground'));
        } else if (feature.state === 'severed') {
            this.iconPath = new vscode.ThemeIcon('error',
                new vscode.ThemeColor('errorForeground'));
        } else if (feature.state === 'strained') {
            this.iconPath = new vscode.ThemeIcon('warning',
                new vscode.ThemeColor('editorWarning.foreground'));
        } else {
            this.iconPath = new vscode.ThemeIcon('symbol-module');
        }
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

    // null key = root features
    private childrenMap = new Map<string | null, FeatureResponse[]>();
    private loaded = false;
    private loading = false;

    constructor(private server: ServerState) {}

    async loadFeatures(): Promise<void> {
        if (this.loading) return;
        this.loading = true;
        try {
            if (!this.server.client) {
                this.childrenMap.clear();
                this.loaded = false;
                return;
            }
            const features = await this.server.client.getTree();
            this.childrenMap.clear();
            for (const f of features) {
                const key = f.parent_uuid ?? null;
                if (!this.childrenMap.has(key)) this.childrenMap.set(key, []);
                this.childrenMap.get(key)!.push(f);
            }
            this.loaded = true;
        } catch {
            this.childrenMap.clear();
            this.loaded = false;
        } finally {
            this.loading = false;
            this._onDidChangeTreeData.fire();
        }
    }

    refresh(): void {
        void this.loadFeatures();
    }

    getTreeItem(element: TreeNode): vscode.TreeItem {
        return element;
    }

    getChildren(element?: TreeNode): vscode.ProviderResult<TreeNode[]> {
        if (!this.server.client) {
            return [new PlaceholderItem('codoc server offline — run `codoc server`')];
        }
        if (!this.loaded) {
            void this.loadFeatures();
            return [new PlaceholderItem('Loading features…')];
        }

        const parentKey = (element instanceof FeatureTreeItem) ? element.feature.uuid : null;
        const children = this.childrenMap.get(parentKey) ?? [];

        if (parentKey === null && children.length === 0) {
            return [new PlaceholderItem('No features — run `codoc sync` to bootstrap')];
        }

        return children.map(f => {
            const hasChildren = this.childrenMap.has(f.uuid);
            return new FeatureTreeItem(
                f,
                hasChildren
                    ? vscode.TreeItemCollapsibleState.Collapsed
                    : vscode.TreeItemCollapsibleState.None,
            );
        });
    }
}
