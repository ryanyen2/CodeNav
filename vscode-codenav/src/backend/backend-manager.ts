/**
 * Backend health check, optional auto-start, status bar indicator.
 */

import * as vscode from 'vscode';
import { ApiClient } from './api-client';

const STATUS_BAR_PRIORITY = 100;

export class BackendManager implements vscode.Disposable {
  private readonly client: ApiClient;
  private statusBarItem: vscode.StatusBarItem | undefined;
  private _status: 'synced' | 'code_changed' | 'tree_edited' | 'offline' = 'offline';

  constructor(private context: vscode.ExtensionContext) {
    const base = vscode.workspace.getConfiguration('codenav').get<string>('serverUrl') ?? 'http://localhost:8001';
    this.client = new ApiClient(base);
    this.statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, STATUS_BAR_PRIORITY);
    context.subscriptions.push(this.statusBarItem);
    context.subscriptions.push(
      vscode.workspace.onDidChangeConfiguration(e => {
        if (e.affectsConfiguration('codenav.serverUrl')) {
          this.client.setBaseUrl(vscode.workspace.getConfiguration('codenav').get<string>('serverUrl') ?? 'http://localhost:8001');
        }
      })
    );
  }

  get api(): ApiClient {
    return this.client;
  }

  setStatus(s: 'synced' | 'code_changed' | 'tree_edited' | 'offline'): void {
    this._status = s;
    if (!this.statusBarItem) return;
    switch (s) {
      case 'synced':
        this.statusBarItem.text = '$(check) CodeNav: Synced';
        break;
      case 'code_changed':
        this.statusBarItem.text = '$(sync) CodeNav: Code Changed';
        break;
      case 'tree_edited':
        this.statusBarItem.text = '$(warning) CodeNav: Tree Edited';
        break;
      case 'offline':
        this.statusBarItem.text = '$(error) CodeNav: Backend Offline';
        break;
    }
    this.statusBarItem.show();
  }

  async checkHealth(): Promise<boolean> {
    const ok = await this.client.health();
    if (!ok) this.setStatus('offline');
    return ok;
  }

  dispose(): void {
    this.statusBarItem?.dispose();
    this.statusBarItem = undefined;
  }
}
