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
