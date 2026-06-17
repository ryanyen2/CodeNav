/**
 * doc-move.ts — reparent a feature (and its subtree) inside the authored doc (U2b).
 *
 * In the single-writer model the host edits `tree.doc.json`, not `tree.codoc`, so a
 * tree-pane drag-to-reparent is a pure doc transform: move the dragged feature's
 * heading + its description paragraphs + its deeper-level descendants under the new
 * parent, shifting their `level` so the subtree stays tree-valid. Loop B then derives
 * the MOVE_NODE op from `parse_doc_file`. Pure + testable; mirrors the text-level
 * `editMove` the host used to do, but on ProseMirror blocks.
 */
import {
    NODE_FEATURE_HEADING, PMNode, FeatureHeadingAttrs,
} from './pm-doc';

interface Unit { fid: string | null; level: number; blocks: PMNode[]; }

/** Split a doc into per-feature units (heading + its following non-heading blocks).
 *  Blocks before the first heading (rare) ride with the first unit's preamble. */
function toUnits(doc: PMNode): { preamble: PMNode[]; units: Unit[] } {
    const preamble: PMNode[] = [];
    const units: Unit[] = [];
    let cur: Unit | null = null;
    for (const b of doc.content ?? []) {
        if (b.type === NODE_FEATURE_HEADING) {
            const a = (b.attrs ?? {}) as Partial<FeatureHeadingAttrs>;
            cur = { fid: a.fid ?? null, level: typeof a.level === 'number' ? a.level : 0, blocks: [b] };
            units.push(cur);
        } else if (cur) {
            cur.blocks.push(b);
        } else {
            preamble.push(b);
        }
    }
    return { preamble, units };
}

/** [start, end) index range of a unit's subtree (itself + deeper-level units). */
function subtreeRange(units: Unit[], i: number): [number, number] {
    let end = i + 1;
    while (end < units.length && units[end].level > units[i].level) end++;
    return [i, end];
}

function setLevel(unit: Unit, level: number): Unit {
    const [h, ...rest] = unit.blocks;
    return { ...unit, level, blocks: [{ ...h, attrs: { ...(h.attrs ?? {}), level } }, ...rest] };
}

/**
 * Move feature `sourceId` (+ subtree) under `newParentId` (null = root). Returns a
 * new doc, or `null` when the move is a no-op or invalid (unknown source/parent,
 * already there, or a cycle — moving under one's own descendant).
 */
export function moveFeatureInDoc(doc: PMNode, sourceId: string, newParentId: string | null): PMNode | null {
    const { preamble, units } = toUnits(doc);
    const srcIdx = units.findIndex(u => u.fid === sourceId);
    if (srcIdx < 0) return null;
    const [srcStart, srcEnd] = subtreeRange(units, srcIdx);

    // Cycle guard: the new parent must not be the source or inside its subtree.
    if (newParentId === sourceId) return null;
    if (newParentId !== null) {
        const subtreeFids = new Set(units.slice(srcStart, srcEnd).map(u => u.fid));
        if (subtreeFids.has(newParentId)) return null;
    }

    const srcLevel = units[srcIdx].level;
    let newLevel = 0;
    if (newParentId !== null) {
        const parent = units.find(u => u.fid === newParentId);
        if (!parent) return null;
        newLevel = parent.level + 1;
    }
    // No-op: already directly under that parent at the right level. (Parent is the
    // nearest shallower unit before the source.)
    const curParent = (() => {
        for (let j = srcIdx - 1; j >= 0; j--) if (units[j].level < srcLevel) return units[j].fid;
        return null;
    })();
    if (curParent === newParentId && srcLevel === newLevel) return null;

    const delta = newLevel - srcLevel;
    const moved = units.slice(srcStart, srcEnd).map(u => setLevel(u, Math.max(0, u.level + delta)));
    const remaining = [...units.slice(0, srcStart), ...units.slice(srcEnd)];

    // Insertion point: end of the new parent's subtree (or end of doc for root).
    let insertAt = remaining.length;
    if (newParentId !== null) {
        const pIdx = remaining.findIndex(u => u.fid === newParentId);
        if (pIdx < 0) return null;
        const [, pEnd] = subtreeRange(remaining, pIdx);
        insertAt = pEnd;
    }
    const reordered = [...remaining.slice(0, insertAt), ...moved, ...remaining.slice(insertAt)];
    const content = [...preamble, ...reordered.flatMap(u => u.blocks)];
    return { ...doc, content };
}
