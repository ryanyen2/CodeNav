/**
 * Apply a structured tree patch (insertions, updates, deletions) to .codoc markdown.
 * Patch format matches backend tree_patch: insertions[], updates[], deletions[].
 */

export interface TreePatch {
  insertions?: Array<{ after_entity_id: string; new_lines: string[] }>;
  updates?: Array<{ entity_id: string; feature: string }>;
  deletions?: string[];
}

const FPATH_RE = /\[([^\]]+)\]/;
const ENTITY_RE = /\(([^)]+)\)/;

interface ParsedLine {
  depth: number;
  raw: string;
  fpath: string | null;
  entityName: string | null;
}

function parseTreeLines(md: string): { lines: ParsedLine[]; depsStart: number } {
  const lines = md.split('\n');
  const parsed: ParsedLine[] = [];
  let depsStart = -1;
  let currentFpath: string | null = null;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]!;
    if (line.trim() === 'deps:') {
      depsStart = i;
      break;
    }
    if (!line.trim()) continue;
    const depth = (line.length - line.trimStart().length) / 2;
    const fpathM = line.match(FPATH_RE);
    const entityM = line.match(ENTITY_RE);
    const fpath = fpathM ? fpathM[1]! : null;
    const entityName = entityM ? entityM[1]! : null;
    if (fpath) currentFpath = fpath;
    parsed.push({
      depth,
      raw: line,
      fpath: currentFpath,
      entityName,
    });
  }
  return { lines: parsed, depsStart };
}

function nodeRanges(parsed: ParsedLine[]): Map<string, { start: number; end: number }> {
  const out = new Map<string, { start: number; end: number }>();
  for (let i = 0; i < parsed.length; i++) {
    const { fpath, entityName } = parsed[i]!;
    if (!fpath) continue;
    const eid = entityName ? `${fpath}::${entityName}` : fpath;
    let end = i;
    for (let j = i + 1; j < parsed.length; j++) {
      if (parsed[j]!.depth <= parsed[i]!.depth) break;
      end = j;
    }
    out.set(eid, { start: i, end });
  }
  return out;
}

/**
 * Apply patch to prior markdown. Returns merged markdown or null on error.
 */
export function applyPatchToMarkdown(priorMd: string, patch: TreePatch): string | null {
  try {
    const { lines: parsed, depsStart } = parseTreeLines(priorMd);
    let treeLines = parsed.map(p => p.raw);
    const rest = priorMd.split('\n').slice(depsStart >= 0 ? depsStart : treeLines.length);
    let ranges = nodeRanges(parsed);

    const deletions = patch.deletions ?? [];
    const toDrop = new Set<number>();
    for (const eid of deletions) {
      const r = ranges.get(eid);
      if (r) for (let k = r.start; k <= r.end; k++) toDrop.add(k);
    }
    treeLines = treeLines.filter((_, i) => !toDrop.has(i));
    const reparsed = parseTreeLines(treeLines.join('\n')).lines;
    ranges = nodeRanges(reparsed);

    const updates = patch.updates ?? [];
    for (const u of updates) {
      const eid = u.entity_id;
      const feature = u.feature;
      const r = ranges.get(eid);
      if (r == null || feature == null) continue;
      const line = treeLines[r.start]!;
      const match = line.match(/^(\s*-\s*[%~$^]\s+)(.+?)(\s+\[|\s+\(|\s+#)(.*)$/);
      if (match) {
        treeLines[r.start] = match[1]! + feature + match[3]! + match[4]!;
      }
    }
    ranges = nodeRanges(parseTreeLines(treeLines.join('\n')).lines);

    const insertions = patch.insertions ?? [];
    for (const ins of insertions) {
      const afterId = ins.after_entity_id;
      const newLines = ins.new_lines ?? [];
      const r = ranges.get(afterId);
      if (r == null || newLines.length === 0) continue;
      const insertAt = r.end + 1;
      treeLines.splice(insertAt, 0, ...newLines);
      const reparse = parseTreeLines(treeLines.join('\n')).lines;
      ranges = nodeRanges(reparse);
    }

    const result = treeLines.join('\n') + (rest.length ? '\n' + rest.join('\n') : '');
    return result;
  } catch {
    return null;
  }
}
