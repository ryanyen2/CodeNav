/**
 * HTTP client for /semantic_tree endpoints.
 */

import type { CodocMetaJson } from 'codenav-semantic-tree/extension-api';


const defaultBase = 'http://localhost:8001';

export interface AnalyzeResult {
  tree_md: string;
  root_dir: string;
  file_count: number;
  entity_count: number;
}

export interface SyncResult {
  tree_md: string;
  root_dir: string;
  file_count: number;
  entity_count: number;
  is_incremental?: boolean;
  error?: string;
}

export interface TreeEditOperation {
  op: string;
  target: string;
  params: Record<string, unknown>;
  targets: Array<{ node_path: string; fpath?: string; entity_name?: string; line_range?: [number, number] }>;
}

export interface TreeEditResult {
  operations: TreeEditOperation[];
  error?: string;
}

export interface ApplyResult {
  operations: TreeEditOperation[];
  applied: boolean;
  tree_version?: number;
  error?: string;
  modified_fpaths?: string[];
  planned_changes?: Array<{ fpath: string; line_start: number; line_end: number; new_content: string }>;
  /** Unified diff of planned/applied changes (for diff view) */
  unified_diff?: string;
}

export class ApiClient {
  constructor(private baseUrl: string = defaultBase) {}

  setBaseUrl(url: string): void {
    this.baseUrl = url.replace(/\/$/, '');
  }

  private async request<T>(
    method: string,
    path: string,
    body?: unknown
  ): Promise<{ ok: boolean; status: number; data?: T; errorDetail?: string }> {
    const url = `${this.baseUrl}/semantic_tree${path}`;
    const res = await fetch(url, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    let data: unknown;
    try {
      data = await res.json();
    } catch {
      data = {};
    }
    const errorDetail =
      !res.ok && data && typeof data === 'object' && 'detail' in data
        ? String((data as { detail?: string }).detail)
        : undefined;
    return {
      ok: res.ok,
      status: res.status,
      data: res.ok ? (data as T) : undefined,
      errorDetail,
    };
  }

  async health(): Promise<boolean> {
    try {
      const base = this.baseUrl.replace(/\/semantic_tree\/?.*$/, '');
      const res = await fetch(`${base}/health`);
      return res.ok;
    } catch {
      return false;
    }
  }

  async analyze(path: string, options?: { excluded_dirs?: string[]; excluded_files?: string[] }): Promise<AnalyzeResult | null> {
    const { ok, data } = await this.request<AnalyzeResult>('POST', '/analyze', {
      path,
      excluded_dirs: options?.excluded_dirs ?? [],
      excluded_files: options?.excluded_files ?? [],
    });
    return ok && data ? data : null;
  }

  async sync(path: string, options?: { force_full?: boolean }): Promise<SyncResult | null> {
    const { ok, data, errorDetail } = await this.request<SyncResult>('POST', '/sync', {
      path,
      force_full: options?.force_full ?? false,
    });
    if (!ok) return { tree_md: '', root_dir: '', file_count: 0, entity_count: 0, error: errorDetail ?? 'Sync failed' };
    return data ?? null;
  }

  async getTree(path: string): Promise<{ tree_md: string; root_dir: string } | null> {
    const url = `${this.baseUrl}/semantic_tree/tree?path=${encodeURIComponent(path)}`;
    const res = await fetch(url);
    if (!res.ok) return null;
    const data = await res.json();
    return data?.tree_md ? data : null;
  }

  async treeEdit(baseTreeMd: string, editedTreeMd: string): Promise<TreeEditResult> {
    const { data } = await this.request<TreeEditResult>('POST', '/tree_edit', {
      base_tree_md: baseTreeMd,
      edited_tree_md: editedTreeMd,
    });
    return data ?? { operations: [] };
  }

  async applyTreeEdit(path: string, editedTreeMd: string): Promise<ApplyResult> {
    const { ok, data } = await this.request<ApplyResult>('POST', '/apply_tree_edit', {
      path,
      edited_tree_md: editedTreeMd,
    });
    return data ?? { operations: [], applied: false };
  }

  async apply(
    path: string,
    editedTreeMd: string,
    dryRun: boolean,
    options?: {
      diffFormat?: 'line_replace' | 'unified_diff' | 'search_replace';
      /** Tree before this edit; when set, diff is base vs edited so only actual edits produce ops */
      baseTreeMd?: string;
    }
  ): Promise<ApplyResult> {
    const body: Record<string, unknown> = {
      path,
      edited_tree_md: editedTreeMd,
      dry_run: dryRun,
      diff_format: options?.diffFormat ?? 'unified_diff',
    };
    if (options?.baseTreeMd !== undefined && options.baseTreeMd !== '') {
      body.base_tree_md = options.baseTreeMd;
    }
    const { ok, data, errorDetail } = await this.request<ApplyResult>('POST', '/apply', body);
    const result = data ?? { operations: [], applied: false };
    if (!ok && errorDetail) result.error = errorDetail;
    return result;
  }
}
