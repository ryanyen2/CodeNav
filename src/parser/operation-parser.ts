/**
 * Parser for operation block in test cases (test_cases §2).
 * Format: op: AddNode, target: path, params: { ... }
 */

import type { Operation, OperationType, Contract } from '../types.js';

export function extractOperationBlock(content: string): string {
  const marker = '--- OPERATION ---';
  const idx = content.indexOf(marker);
  if (idx === -1) return '';
  const start = idx + marker.length;
  const rest = content.slice(start);
  const nextH = rest.match(/\n---/);
  const end = nextH ? nextH.index! + 1 : rest.length;
  return rest.slice(0, end).trim();
}

/** Parse YAML-like params: key: value, and contract block with sig:/inv: etc. */
function parseParamsBlock(lines: string[]): Record<string, unknown> {
  const params: Record<string, unknown> = {};
  const baseIndent = lines.find(l => l.trim()) ? lines.find(l => l.trim())!.length - lines.find(l => l.trim())!.trimStart().length : 0;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('---')) continue;
    const indent = line.length - line.trimStart().length;

    if (trimmed.startsWith('- ') && trimmed.includes('feature:')) {
      const item = trimmed.slice(2).trim();
      const featMatch = item.match(/feature:\s*(.+)/);
      const feat = featMatch ? featMatch[1].replace(/^["']|["']$/g, '').trim() : '';
      let cont: Contract = {};
      if (i + 1 < lines.length && lines[i + 1].trim().startsWith('contract:')) {
        const next = lines[i + 1].trim().replace(/^contract:\s*/, '');
        cont = parseContractInline(next);
        i++;
      } else if (i + 2 < lines.length && lines[i + 1].trim().startsWith('contract:')) {
        const subLines: string[] = [];
        let j = i + 2;
        while (j < lines.length && (lines[j].length - lines[j].trimStart().length) > indent) {
          subLines.push(lines[j].trim());
          j++;
        }
        cont = parseContractBlock(subLines);
        i = j - 1;
      }
      if (!params.specs) params.specs = [];
      (params.specs as unknown[]).push({ feature: feat, contract: cont });
      continue;
    }

    const colon = trimmed.indexOf(':');
    if (colon === -1) continue;
    const key = trimmed.slice(0, colon).trim();
    let value: unknown = trimmed.slice(colon + 1).trim();
    if (value === '' && i + 1 < lines.length) {
      const nextIndent = lines[i + 1].length - lines[i + 1].trimStart().length;
      if (nextIndent > indent) {
        if (key === 'contract') {
          const subLines: string[] = [];
          let j = i + 1;
          while (j < lines.length && (lines[j].length - lines[j].trimStart().length) > indent) {
            subLines.push(lines[j].trim());
            j++;
          }
          value = parseContractBlock(subLines);
          i = j - 1;
        } else if (key === 'target') {
          const list: string[] = [];
          let j = i + 1;
          while (j < lines.length && lines[j].trimStart().startsWith('- ')) {
            list.push(lines[j].trim().slice(2).trim());
            j++;
          }
          value = list;
          i = j - 1;
        }
      }
    }
    if (typeof value === 'string' && (value.startsWith('{') || value.includes('sig:'))) {
      value = parseContractInline(value);
    }
    params[key] = value;
  }
  return params;
}

function parseContractBlock(lines: string[]): Contract {
  const c: Contract = {};
  for (const l of lines) {
    if (l.startsWith('sig:')) c.sig = l.slice(4).trim();
    else if (l.startsWith('inv:')) c.inv = l.slice(4).trim();
    else if (l.startsWith('cls:')) c.cls = l.slice(4).trim();
    else if (l.startsWith('exp:')) c.exp = l.slice(4).trim();
  }
  return c;
}

function parseContractInline(s: string): Contract {
  const c: Contract = {};
  const sigMatch = s.match(/\bsig:\s*([^{}]+(?:\([^)]*\)[^{}]*)?)/);
  if (sigMatch) c.sig = sigMatch[1].trim();
  const invMatch = s.match(/\binv:\s*([^{}\n]+)/);
  if (invMatch) c.inv = invMatch[1].trim();
  const clsMatch = s.match(/\bcls:\s*([^{}\n]+)/);
  if (clsMatch) c.cls = clsMatch[1].trim();
  const expMatch = s.match(/\bexp:\s*([^{}\n]+)/);
  if (expMatch) c.exp = expMatch[1].trim();
  return c;
}

/**
 * Parse operation block text into an Operation.
 */
export function parseOperationBlock(block: string): Operation | null {
  const lines = block.split(/\r?\n/);
  let op: OperationType | null = null;
  let target: string | string[] = '';
  const params: Record<string, unknown> = {};

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();
    if (trimmed.startsWith('op:')) {
      op = trimmed.slice(3).trim() as OperationType;
      continue;
    }
    if (trimmed.startsWith('target:')) {
      const val = trimmed.slice(7).trim();
      if (val) target = val;
      continue;
    }
    if (trimmed.startsWith('params:')) {
      const rest = lines.slice(i + 1);
      const paramLines: string[] = [];
      for (const r of rest) {
        if (r.trim().startsWith('---')) break;
        paramLines.push(r);
      }
      Object.assign(params, parseParamsBlock(paramLines));
      break;
    }
  }

  if (!op) return null;

  const targetList = Array.isArray(params.target) ? params.target as string[] : target ? [target] : [];
  const resolvedTarget = targetList.length > 1 ? targetList : targetList[0] ?? target;

  switch (op) {
    case 'AddNode':
      return {
        op: 'AddNode',
        target: resolvedTarget as string,
        params: {
          feature: (params.feature as string) ?? '',
          contract: (params.contract as Contract) ?? undefined,
          group_name: params.group_name as string | undefined,
        },
      };
    case 'DeleteNode':
      return {
        op: 'DeleteNode',
        target: resolvedTarget as string,
        params: {
          strategy: params.strategy as 'cascade' | 'sever' | 'redirect' | 'abort' | undefined,
          redirect_to: params.redirect_to as string | undefined,
          redirect_import: params.redirect_import as string | undefined,
        },
      };
    case 'MoveNode':
      return {
        op: 'MoveNode',
        target: resolvedTarget as string,
        params: { new_parent: (params.new_parent as string) ?? '' },
      };
    case 'EditFeature':
      return {
        op: 'EditFeature',
        target: resolvedTarget as string,
        params: { new_feature: (params.new_feature as string) ?? '' },
      };
    case 'EditContract':
      return {
        op: 'EditContract',
        target: resolvedTarget as string,
        params: { new_contract: (params.new_contract as Contract) ?? {} },
      };
    case 'ReorderChildren':
      return {
        op: 'ReorderChildren',
        target: resolvedTarget as string,
        params: { permutation: (params.permutation as number[]) ?? [] },
      };
    case 'ExtractAndGroup':
      return {
        op: 'ExtractAndGroup',
        target: (params.target as string[]) ?? [],
        params: {
          group_feature: (params.group_feature as string) ?? '',
          group_name: (params.group_name as string) ?? '',
        },
      };
    case 'SplitFunction':
      return {
        op: 'SplitFunction',
        target: resolvedTarget as string,
        params: { specs: (params.specs as Array<{ feature: string; contract: Contract }>) ?? [] },
      };
    case 'MergeNodes':
      return {
        op: 'MergeNodes',
        target: (params.target as string[]) ?? [],
        params: { merged_feature: (params.merged_feature as string) ?? '' },
      };
    default:
      return null;
  }
}
