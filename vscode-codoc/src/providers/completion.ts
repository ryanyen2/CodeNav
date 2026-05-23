import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { ServerState } from '../state/server';

const AT_TRIGGER_RE = /@[\w.:]*$/;

interface SidecarEntry { file: string; symbol: string; uuid: string }
type Sidecar = Record<string, SidecarEntry[]>;

export class CodocCompletionProvider implements vscode.CompletionItemProvider {
    private symbolItems: vscode.CompletionItem[] = [];
    private featureItems: vscode.CompletionItem[] = [];
    private lastCacheMs = 0;
    private static readonly TTL_MS = 30_000;

    constructor(private server: ServerState) {}

    private get codocDir(): string | null {
        return this.server.rootDir ? path.join(this.server.rootDir, '.codoc') : null;
    }

    private loadSymbolsFromSidecar(): vscode.CompletionItem[] {
        const dir = this.codocDir;
        if (!dir) return [];
        const sidecarPath = path.join(dir, 'tree', '_index.bindings.json');
        try {
            const data = JSON.parse(fs.readFileSync(sidecarPath, 'utf-8')) as Sidecar;
            const seen = new Set<string>();
            const items: vscode.CompletionItem[] = [];
            for (const entries of Object.values(data)) {
                for (const b of entries) {
                    if (!b.symbol) continue;
                    const sepIdx = b.symbol.lastIndexOf('::');
                    const short = sepIdx >= 0 ? b.symbol.slice(sepIdx + 2) : b.symbol;
                    if (!seen.has(short)) {
                        seen.add(short);
                        const item = new vscode.CompletionItem(short, vscode.CompletionItemKind.Function);
                        item.detail = b.file;
                        item.insertText = short;
                        item.documentation = new vscode.MarkdownString(`\`${b.symbol}\`  \n${b.file}`);
                        items.push(item);
                    }
                    // Full qualified form when short name is ambiguous
                    if (sepIdx >= 0 && !seen.has(b.symbol)) {
                        seen.add(b.symbol);
                        const full = new vscode.CompletionItem(b.symbol, vscode.CompletionItemKind.Reference);
                        full.detail = `${b.file} (qualified)`;
                        full.insertText = b.symbol;
                        items.push(full);
                    }
                }
            }
            return items;
        } catch {
            return [];
        }
    }

    private async loadFeatures(): Promise<vscode.CompletionItem[]> {
        if (!this.server.connected || !this.server.client) return [];
        try {
            const features = await this.server.client.getTree();
            return features.map(f => {
                const item = new vscode.CompletionItem(`feature:${f.slug}`, vscode.CompletionItemKind.Module);
                item.detail = f.intent || '';
                item.insertText = `feature:${f.slug}`;
                return item;
            });
        } catch {
            return [];
        }
    }

    private async refreshCache(): Promise<void> {
        const now = Date.now();
        if (now - this.lastCacheMs < CodocCompletionProvider.TTL_MS) return;
        this.lastCacheMs = now;
        this.symbolItems = this.loadSymbolsFromSidecar();
        this.featureItems = await this.loadFeatures();
    }

    async provideCompletionItems(
        document: vscode.TextDocument,
        position: vscode.Position,
    ): Promise<vscode.CompletionItem[]> {
        if (document.languageId !== 'codoc') return [];
        const prefix = document.lineAt(position.line).text.slice(0, position.character);
        if (!AT_TRIGGER_RE.test(prefix)) return [];
        await this.refreshCache();
        return [...this.symbolItems, ...this.featureItems];
    }
}
