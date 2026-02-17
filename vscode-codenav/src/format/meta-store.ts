/**
 * Read/write .codoc.meta.json alongside .codoc in workspace.
 * Auto-managed by extension; user does not edit this file.
 */

import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import type { CodocMetaJson } from 'codenav-semantic-tree/extension-api';
import { extractMetadata } from 'codenav-semantic-tree/extension-api';

const META_FILENAME = '.codoc.meta.json';

function metaPathForCodoc(codocUri: vscode.Uri): vscode.Uri {
  const dir = path.dirname(codocUri.fsPath);
  return vscode.Uri.file(path.join(dir, META_FILENAME));
}

export function getMetaPath(codocUri: vscode.Uri): vscode.Uri {
  return metaPathForCodoc(codocUri);
}

export function readMeta(codocUri: vscode.Uri): CodocMetaJson | null {
  const metaUri = metaPathForCodoc(codocUri);
  try {
    const raw = fs.readFileSync(metaUri.fsPath, 'utf-8');
    const data = JSON.parse(raw) as CodocMetaJson;
    if (data.version === 1 && data.nodes) return data;
  } catch {
    // file missing or invalid
  }
  return null;
}

export function writeMeta(codocUri: vscode.Uri, meta: CodocMetaJson): void {
  const metaUri = metaPathForCodoc(codocUri);
  const dir = path.dirname(metaUri.fsPath);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(metaUri.fsPath, JSON.stringify(meta, null, 2), 'utf-8');
}

/**
 * Build .codoc.meta.json from a full SemanticTree (e.g. from backend response).
 */
export function metaFromTree(tree: import('codenav-semantic-tree/extension-api').SemanticTree): CodocMetaJson {
  return extractMetadata(tree);
}

const CONTRACT_KEYS = ['sig', 'inv', 'cls', 'exp'] as const;

interface ServerNodeJson {
  id?: string;
  sigil?: string;
  feature?: string;
  metadata?: { fpath?: string; entity_name?: string; line_range?: [number, number] };
  contract?: Record<string, string>;
  status?: string;
  children?: ServerNodeJson[];
}

/**
 * Build .codoc.meta.json from server tree_json (includes line_range from server algo; no regex).
 * Use this when sync/analyze returns tree_json so code locations match the server mapping.
 */
export function metaFromTreeJson(treeJson: { root: ServerNodeJson }): CodocMetaJson {
  const nodes: Record<string, { status: string; contracts: Record<string, string>; line_range?: [number, number] }> = {};
  const fileExports: Record<string, string[]> = {};

  function walk(node: ServerNodeJson): void {
    const md = node.metadata;
    const fpath = md?.fpath;
    const entity = md?.entity_name;
    const key = fpath && entity ? `${fpath}::${entity}` : fpath || null;
    if (key) {
      const contract = node.contract ?? {};
      const contracts: Record<string, string> = {};
      for (const k of CONTRACT_KEYS) {
        const v = contract[k];
        if (v != null && String(v).trim()) contracts[k] = String(v).trim();
      }
      nodes[key] = {
        status: node.status ?? 'resolved',
        contracts,
        ...(md?.line_range && { line_range: md.line_range }),
      };
    }
    if (node.sigil === '%' && fpath && node.children?.length) {
      const entities = node.children
        .map((c) => c.metadata?.entity_name)
        .filter((e): e is string => Boolean(e));
      if (entities.length) fileExports[fpath] = entities;
    }
    for (const child of node.children ?? []) walk(child);
  }

  if (treeJson.root) walk(treeJson.root);
  return { version: 1, nodes, file_exports: fileExports };
}
