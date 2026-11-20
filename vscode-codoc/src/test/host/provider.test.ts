import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it, vi } from 'vitest';

import { CodocEditorProvider } from '../../host/provider';

vi.mock('vscode', () => ({
    Uri: { file: (p: string) => ({ fsPath: p }), joinPath: (...p: unknown[]) => p },
    RelativePattern: class {},
    workspace: {
        createFileSystemWatcher: () => ({ onDidChange: () => ({ dispose() {} }), onDidCreate: () => ({ dispose() {} }), dispose() {} }),
    },
}));

describe('the status the host posts', () => {
    it('names the language status.json names, not config.json', async () => {
        const codocDir = join(mkdtempSync(join(tmpdir(), 'codoc-')), '.codoc');
        mkdirSync(codocDir);
        // The daemon resolved `zh` to `zh-Hans` (doclang.resolve); config.json keeps what was typed.
        writeFileSync(join(codocDir, 'status.json'), JSON.stringify({ state: 'synced', doc_language: 'zh-Hans' }));
        writeFileSync(join(codocDir, 'config.json'), JSON.stringify({ doc_language: 'zh' }));
        const posted: any[] = [];
        let onMessage: (m: unknown) => Promise<void> = async () => {};
        const panel = {
            webview: {
                options: {}, html: '', cspSource: '',
                asWebviewUri: () => '',
                postMessage: (m: unknown) => { posted.push(m); return Promise.resolve(true); },
                onDidReceiveMessage: (f: typeof onMessage) => { onMessage = f; },
            },
            onDidDispose: () => {},
        };
        const provider = new CodocEditorProvider({ extensionUri: {} } as any, () => {}, async () => {});
        await provider.resolveCustomTextEditor({ uri: { fsPath: join(codocDir, 'tree.codoc'), toString: () => 'tree' } } as any, panel as any);
        await onMessage({ kind: 'ready' });
        expect(posted.find(m => m.kind === 'status')?.status).toEqual({ state: 'synced', doc_language: 'zh-Hans', terminal: true });
    });
});
