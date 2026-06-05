/**
 * doc-layout.ts — linearize the feature tree into a one-page documentation
 * article, with citations woven inline. Pure (no `vscode`, no I/O) so it can be
 * unit-tested and shared between the extension host and the webview bundle.
 *
 * The article mirrors the tree's parent→child structure exactly (so the tree
 * pane and doc pane stay 1:1 for scroll-spy). Only *siblings* may be re-ordered:
 * with `siblingOrder: 'dependency'` a feature that depends on an earlier sibling
 * is placed after it ("prerequisites first"), via a stable topological sort that
 * falls back to file order on cycles/ties.
 *
 * Three citation classes coexist (see `weaveBlocks` / `bindingRail` / `crossRefs`):
 *   1. authored inline refs `[label](codoc:file#symbol)` — kept at their position
 *   2. derived bindings (sidecar by_feature) — a rail at the section end
 *   3. feature→feature edges — superscript "see also" cross-refs (top-K by weight)
 */

import { ParsedFeature } from './tree-model';
import {
    SidecarData,
    SymbolEntry,
    directedEdges,
    bindingsForFeature,
} from './bindings-model';

export type ProposalOp = 'add' | 'move' | 'retire' | 'amend';
export type FeaturePhase = 'editing' | 'reflecting' | 'done';

/** A run of section prose: plain text or an authored citation chip. */
export type InlineRun =
    | { t: 'text'; s: string }
    | { t: 'cite'; label: string; file: string; symbol: string | null };

export interface CrossRef {
    toId: string;
    toTitle: string;
    rel: 'depends' | 'usedby';
    weight: number;
    kinds: string[];
}

export interface SectionProposal {
    op: ProposalOp;
    eventId: string;
    tag: string;
    title?: string | null;
    description?: string | null;
}

export interface SectionFlags {
    retired: boolean;
    realized: boolean;
    proposalOp: ProposalOp | null;
    eventId: string | null;
    isGhost: boolean;            // ADD/MOVE ghost section (not a live feature)
    activeMode: 'write' | 'read' | null;
    phase: FeaturePhase | null;
}

export interface DocSection {
    id: string;                  // feature id, or event id for ghosts
    title: string;
    level: number;               // tree depth (0 = root)
    parentId: string | null;
    raw: string;                 // unprocessed description text (for the editor)
    blocks: InlineRun[][];       // paragraphs → inline runs
    bindings: SymbolEntry[];     // binding rail (deduped against authored refs)
    crossRefs: CrossRef[];       // sorted by weight desc; renderer slices top-K
    flags: SectionFlags;
    proposal: SectionProposal | null;
    contentHash: string;         // stable over active/phase state; drives crossfade
}

export interface LayoutOptions {
    siblingOrder?: 'dependency' | 'tree';
    /** active write/read per feature id (from activity.json) — overlaid on flags */
    activeModes?: Map<string, 'write' | 'read'>;
    /** per-feature reflection phase (from activity.json) — overlaid on flags */
    phases?: Map<string, FeaturePhase>;
}

const REF_SPLIT_RE = /\[([^\]]*)\]\(codoc:([^)#]+)(?:#([^)]+))?\)/g;

/** FNV-1a (32-bit) hex — small, dependency-free content fingerprint. */
function fnv1a(str: string): string {
    let h = 0x811c9dc5;
    for (let i = 0; i < str.length; i++) {
        h ^= str.charCodeAt(i);
        h = Math.imul(h, 0x01000193);
    }
    return (h >>> 0).toString(16);
}

/** Leaf identifier of a `file::A::b` symbol path (for ref de-dup). */
function leaf(symbol: string): string {
    const parts = symbol.split('::');
    return parts[parts.length - 1] ?? symbol;
}

export interface RailItem {
    symbol: string;       // full symbol_path, e.g. "execution.py::WriteOnlyStringIO::read"
    file: string;
    label: string;        // file stripped, "::" → ".", e.g. "WriteOnlyStringIO.read"
    depth: number;        // members nest under their container (0 = top-level)
}
export interface RailGroup {
    file: string;
    items: RailItem[];     // sorted by symbol_path (clusters a class with its methods)
}

/**
 * Group the derived binding rail by file so the filename isn't repeated on every
 * chip, and order each file's symbols by their symbol_path — which clusters a
 * class with its methods and reads top-to-bottom like a structural minimap.
 * (True file-line ordering would need positions the sidecar doesn't carry yet;
 * symbol_path order is the structural proxy.)
 */
export function groupBindings(bindings: SymbolEntry[]): RailGroup[] {
    const byFile = new Map<string, SymbolEntry[]>();
    for (const b of bindings) {
        const list = byFile.get(b.file);
        if (list) list.push(b); else byFile.set(b.file, [b]);
    }
    const groups: RailGroup[] = [];
    for (const [file, entries] of byFile) {
        // Code-point order (not locale): keeps a class's prefix immediately
        // before its `Class::method` entries, and is deterministic.
        entries.sort((a, b) => (a.symbol < b.symbol ? -1 : a.symbol > b.symbol ? 1 : 0));
        const items: RailItem[] = entries.map(e => {
            const rel = e.symbol.startsWith(file + '::') ? e.symbol.slice(file.length + 2) : e.symbol;
            const segs = rel.split('::');
            const isModule = segs.length === 1 && (segs[0] === '__module__' || segs[0] === '<module>');
            return {
                symbol: e.symbol,
                file,
                label: isModule ? '‹module›' : segs.join('.'),
                depth: Math.max(0, segs.length - 1),
            };
        });
        groups.push({ file, items });
    }
    // Largest group first (the file this feature most lives in), then by name.
    groups.sort((a, b) => b.items.length - a.items.length || a.file.localeCompare(b.file));
    return groups;
}

/** Split a single paragraph into text + authored-citation runs at ref positions. */
function weaveParagraph(text: string): InlineRun[] {
    const runs: InlineRun[] = [];
    REF_SPLIT_RE.lastIndex = 0;
    let last = 0;
    let m: RegExpExecArray | null;
    while ((m = REF_SPLIT_RE.exec(text)) !== null) {
        if (m.index > last) runs.push({ t: 'text', s: text.slice(last, m.index) });
        runs.push({ t: 'cite', label: m[1] || leaf(m[3] ?? m[2]), file: m[2], symbol: m[3] ?? null });
        last = m.index + m[0].length;
    }
    if (last < text.length) runs.push({ t: 'text', s: text.slice(last) });
    return runs.length ? runs : [{ t: 'text', s: text }];
}

/** Description → paragraphs (split on blank lines) → inline runs. */
function weaveBlocks(description: string): InlineRun[][] {
    if (!description.trim()) return [];
    return description
        .split(/\n{2,}/)
        .map(p => p.trim())
        .filter(p => p.length > 0)
        .map(weaveParagraph);
}

/** Derived bindings minus any already cited inline (same file + symbol/leaf). */
function bindingRail(bindings: SymbolEntry[], feature: ParsedFeature): SymbolEntry[] {
    const cited = feature.refs;
    return bindings.filter(b => !cited.some(r =>
        r.file === b.file &&
        (r.symbol === null || r.symbol === b.symbol || r.symbol === leaf(b.symbol)),
    ));
}

/**
 * Cross-feature "see also" refs for a feature, sorted by weight desc and deduped
 * by target (keeping the strongest direction). Targets that aren't live,
 * non-retired features (or are self) are dropped.
 */
function crossRefsFor(
    fid: string,
    dir: ReturnType<typeof directedEdges>,
    titleOf: Map<string, string>,
): CrossRef[] {
    const raw: CrossRef[] = [];
    for (const e of dir.out.get(fid) ?? []) {
        raw.push({ toId: e.to, toTitle: titleOf.get(e.to) ?? '', rel: 'depends', weight: e.weight, kinds: e.kinds });
    }
    for (const e of dir.in.get(fid) ?? []) {
        raw.push({ toId: e.to, toTitle: titleOf.get(e.to) ?? '', rel: 'usedby', weight: e.weight, kinds: e.kinds });
    }
    raw.sort((a, b) => b.weight - a.weight);
    const seen = new Set<string>();
    const out: CrossRef[] = [];
    for (const r of raw) {
        if (r.toId === fid) continue;
        if (!titleOf.has(r.toId)) continue; // not a live, non-retired feature
        if (seen.has(r.toId)) continue;     // keep strongest direction only
        seen.add(r.toId);
        out.push(r);
    }
    return out;
}

/**
 * Stable topological sort of a sibling group: a feature that depends on an
 * earlier sibling is placed *after* it. `index` gives the original (file) order
 * used for tie-breaking and as the cycle fallback.
 */
function orderSiblings(
    ids: string[],
    dir: ReturnType<typeof directedEdges>,
): string[] {
    const set = new Set(ids);
    const index = new Map(ids.map((id, i) => [id, i]));
    // precedence: prereq → dependant (dependant must come after its prereqs)
    const prereqCount = new Map<string, number>(ids.map(id => [id, 0]));
    const dependants = new Map<string, string[]>(ids.map(id => [id, []]));
    for (const id of ids) {
        for (const e of dir.out.get(id) ?? []) {
            if (!set.has(e.to) || e.to === id) continue; // only intra-sibling edges
            prereqCount.set(id, (prereqCount.get(id) ?? 0) + 1);
            dependants.get(e.to)!.push(id);
        }
    }
    const ready = ids.filter(id => (prereqCount.get(id) ?? 0) === 0)
        .sort((a, b) => index.get(a)! - index.get(b)!);
    const ordered: string[] = [];
    const placed = new Set<string>();
    while (ready.length) {
        const id = ready.shift()!;
        if (placed.has(id)) continue;
        ordered.push(id);
        placed.add(id);
        const newly: string[] = [];
        for (const dep of dependants.get(id) ?? []) {
            const n = (prereqCount.get(dep) ?? 0) - 1;
            prereqCount.set(dep, n);
            if (n === 0 && !placed.has(dep)) newly.push(dep);
        }
        if (newly.length) {
            ready.push(...newly);
            ready.sort((a, b) => index.get(a)! - index.get(b)!);
        }
    }
    // Cycle fallback: append any unplaced in original file order.
    for (const id of ids) if (!placed.has(id)) ordered.push(id);
    return ordered;
}

/**
 * Build the ordered list of document sections from the parsed tree + sidecar.
 * Live features become sections; ADD/MOVE proposals become ghost sections at
 * their destination parent; RETIRE/AMEND decorate the live section in place.
 */
export function layoutDoc(
    features: ParsedFeature[],
    sidecar: SidecarData,
    opts: LayoutOptions = {},
): DocSection[] {
    const siblingOrder = opts.siblingOrder ?? 'dependency';
    const dir = directedEdges(sidecar);

    // Title map of live, non-retired features (cross-ref targets).
    const titleOf = new Map<string, string>();
    for (const f of features) {
        if (f.id && !f.retired) titleOf.set(f.id, f.title);
    }

    const byFeatureProp = sidecar.proposals?.by_feature ?? {};
    const byEventProp = sidecar.proposals?.by_event ?? {};

    // children map (parent id, "" = root) → ordered child ids (live + ghosts)
    const childIds = new Map<string, string[]>();
    const sectionOf = new Map<string, DocSection>();

    const addChild = (parentId: string | null, id: string): void => {
        const key = parentId ?? '';
        const list = childIds.get(key);
        if (list) list.push(id);
        else childIds.set(key, [id]);
    };

    // 1. Live feature sections.
    for (const f of features) {
        if (!f.id) continue;
        const prop = byFeatureProp[f.id];
        const bindings = bindingsForFeature(sidecar, f.id);
        const proposal: SectionProposal | null = prop
            ? { op: prop.op, eventId: prop.event_id, tag: prop.tag, title: prop.title ?? null, description: prop.description ?? null }
            : null;
        sectionOf.set(f.id, {
            id: f.id,
            title: f.title,
            level: 0,
            parentId: f.parent_id,
            raw: f.description,
            blocks: weaveBlocks(f.description),
            bindings: bindingRail(bindings, f),
            crossRefs: crossRefsFor(f.id, dir, titleOf),
            flags: {
                retired: f.retired,
                realized: sidecar.features[f.id]?.realized !== false,
                proposalOp: proposal ? proposal.op : null,
                eventId: proposal ? proposal.eventId : null,
                isGhost: false,
                activeMode: opts.activeModes?.get(f.id) ?? null,
                phase: opts.phases?.get(f.id) ?? null,
            },
            proposal,
            contentHash: fnv1a(`${f.title} ${f.description} ${JSON.stringify(proposal)}`),
        });
        addChild(f.parent_id, f.id);
    }

    // 2. ADD/MOVE ghost sections at their destination parent.
    for (const [eventId, p] of Object.entries(byEventProp)) {
        const parentId = p.parent_id ?? null;
        const movedTitle = p.op === 'move' && p.feature_id
            ? (sectionOf.get(p.feature_id)?.title ?? p.title ?? '')
            : '';
        const title = p.op === 'move' ? (movedTitle || p.title || '(moved)') : (p.title || '(new feature)');
        const proposal: SectionProposal = { op: p.op, eventId, tag: p.tag, title: p.title ?? null, description: p.description ?? null };
        sectionOf.set(eventId, {
            id: eventId,
            title,
            level: 0,
            parentId,
            raw: p.description ?? '',
            blocks: weaveBlocks(p.description ?? ''),
            bindings: [],
            crossRefs: [],
            flags: {
                retired: false,
                realized: true,
                proposalOp: p.op,
                eventId,
                isGhost: true,
                activeMode: null,
                phase: null,
            },
            proposal,
            contentHash: fnv1a(`ghost ${eventId} ${title} ${p.description ?? ''}`),
        });
        addChild(parentId, eventId);
    }

    // 3. DFS in (optionally dependency-ordered) sibling order, assigning levels.
    const order = (ids: string[]): string[] => {
        if (siblingOrder !== 'dependency' || ids.length < 2) return ids;
        const ordered = orderSiblings(ids, dir);
        // ghosts (no file index / no edges) trail the live siblings, stable.
        const ghosts = ids.filter(id => sectionOf.get(id)?.flags.isGhost);
        const live = ordered.filter(id => !sectionOf.get(id)?.flags.isGhost);
        return [...live, ...ghosts];
    };

    const out: DocSection[] = [];
    const walk = (parentKey: string, level: number): void => {
        for (const id of order(childIds.get(parentKey) ?? [])) {
            const sec = sectionOf.get(id);
            if (!sec) continue;
            sec.level = level;
            out.push(sec);
            walk(id, level + 1);
        }
    };
    walk('', 0);
    return out;
}
