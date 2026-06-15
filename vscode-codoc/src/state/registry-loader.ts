/**
 * registry-loader.ts — the Node `fs`/`path` reader for the cross-reference
 * registry (`.codoc/tree.index.json`). Split out of `registry-model.ts` so that
 * pure model + resolver stays free of node builtins and can bundle into the
 * browser webview (U4's hover cards run client-side). Only the extension HOST
 * imports this.
 */
import * as fs from 'fs';
import * as path from 'path';
import type { RegistryData } from './registry-model';

/**
 * Load .codoc/tree.index.json. Tolerant — a missing or corrupt file returns null
 * (never throws), mirroring how the bindings sidecar is read in workspace-state.
 */
export function loadRegistry(rootDir: string): RegistryData | null {
    try {
        const raw = fs.readFileSync(path.join(rootDir, '.codoc', 'tree.index.json'), 'utf-8');
        const data = JSON.parse(raw) as RegistryData;
        // Minimal shape guard — a JSON blob missing `refs` would crash callers.
        if (!Array.isArray(data.refs)) return null;
        return data;
    } catch {
        return null;
    }
}
