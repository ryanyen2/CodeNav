/**
 * projection.ts: the daemon's `projection.json`, typed, the op bodies the
 * editor sends back, and the block kinds (docs/model.md, "Block kinds").
 *
 * Nothing here touches the DOM or ProseMirror. The projection is the truth for
 * every block the person is not holding; the editor adopts it per block.
 */

/** A character identity: the k-th scalar minted by an op, or a provisional
 *  reference to the k-th scalar of an unacked insert this client submitted. */
export type CharRef = [op: string, k: number] | ['$cid', cid: string, k: number];
export type Anchor = CharRef | 'START';

export const START = 'START';

export function isProvisional(r: CharRef): r is ['$cid', string, number] {
    return r.length === 3;
}

export function refEq(a: CharRef, b: CharRef): boolean {
    return a.length === b.length && a[0] === b[0] && a[1] === b[1] && (a.length === 2 || a[2] === (b as ['$cid', string, number])[2]);
}

export interface Run {
    start: number;      // live offsets, half-open, in Unicode scalars
    end: number;
    actor: string;
    at: string;
    plan: string | null;
    op: string;         // the minting op of the run's first character
    k0: number;         // …and that character's index within the op
}

export interface Span {
    kind: 'bold' | 'code' | 'link' | 'cite' | 'comment' | string;
    attrs: Record<string, string>;
    start: number;      // live offsets, half-open
    end: number;
}

export interface SuggestDel { plan: string; start: number; end: number }

export interface TextField {
    text: string;
    runs: Run[];
    spans: Span[];
    suggest_del: SuggestDel[];
}

export interface ProjectedBlock {
    id: string;
    kind: 'heading' | 'paragraph' | 'bullet' | 'numbered' | 'table' | 'diagram' | 'code' | string;
    level: number;
    rank: string;
    head: number;
    fields: Record<string, TextField | unknown>;
    /** For a kind that is not heading, prose or source: which fields are Text,
     *  whether it sits in the reading flow, and whether it is exported. */
    layout?: Layout;
}

export interface Layout { text: string[]; flow: boolean; export: boolean }

export interface Claim {
    bid: string;
    path: string;
    start: number;
    end: number;
    channel: 'human' | 'plan' | 'code';
    stage: 'unsent' | 'sent' | 'open' | 'accepted' | 'added' | 'removed' | 'marked' | 'unmarked';
    actor: string;
    text: string;
}

export interface PlanInfo {
    id: string;
    actor: string;
    kind: string;
    target: string | null;
    note: string;
    status: string;
    /** The features accepting this plan hands off; empty for a plan by the loop,
     *  a translator or a suggester, and for a delete plan. */
    handoff?: string[];
}

export type Phase = 'synced' | 'edited' | 'queued' | 'realizing' | 'proposed' | 'drifted' | 'planned';

export interface Watermarks { handoff: Record<string, number>; realized: Record<string, number> }

export interface Projection {
    /** Changes whenever the daemon rebuilds its store; `head` is comparable only within one. */
    store?: string;
    head: number;
    frontier: Record<string, number>;
    blocks: ProjectedBlock[];
    phases: Record<string, Phase>;
    plans: PlanInfo[];
    bindings: { by_feature: Record<string, { file: string; symbol: string }[]>; by_file: Record<string, string[]> };
    claims: Record<string, Claim[]>;
    /** Per feature, the hand-off and realized frontiers (absent when neither). */
    watermarks?: Record<string, Watermarks>;
    /** The layout each plugin kind registered in this checkout gives a new block. */
    kinds?: Record<string, Layout>;
}

export const NO_WATERMARKS: Watermarks = { handoff: {}, realized: {} };

/** List items: prose with an indent `level`. */
export const ITEMS: ReadonlySet<string> = new Set(['bullet', 'numbered']);
/** Body kinds holding one line of inline text in field `text`. */
export const PROSE: ReadonlySet<string> = new Set(['paragraph', 'bullet', 'numbered']);
/** Kinds holding raw multi-line text in field `source`, with no marks. */
export const SOURCE: ReadonlySet<string> = new Set(['table', 'diagram', 'code']);

/** A heading or a prose kind: one line, and the only kinds that split, merge or change kind. */
export const isOneLine = (kind: string): boolean => kind === 'heading' || PROSE.has(kind);

/** The Text field the editor edits for a block of a heading, prose or source `kind`. */
export const pathOf = (kind: string): string => (SOURCE.has(kind) ? 'source' : 'text');

/** Whether the editor has a node type of its own for this kind. */
export const isBuiltin = (kind: string): boolean => isOneLine(kind) || SOURCE.has(kind);

/** A block's Text fields: fixed for heading, prose and source kinds; for any
 *  other kind, what the block's own layout says. */
export const textPaths = (b: ProjectedBlock): string[] => (isBuiltin(b.kind) ? [pathOf(b.kind)] : b.layout?.text ?? []);

/** The layout of a non-builtin block, as the projection gives it. */
export const layoutOf = (b: ProjectedBlock): Layout => b.layout ?? { text: [], flow: true, export: true };

/** The store kind of an editor node: its type for heading and prose, its
 *  `kind` attribute for a source block or a plugin block. */
export function kindOf(node: { type: { name: string }; attrs: Record<string, unknown> }): string {
    const name = node.type.name;
    return name === 'source' || name === 'kindBlock' ? String(node.attrs.kind) : name;
}

export function textField(b: ProjectedBlock, path = 'text'): TextField | null {
    const f = b.fields[path];
    return f && typeof f === 'object' && 'runs' in (f as object) ? (f as TextField) : null;
}

/** Live character refs of a field, in order, from its runs. */
export function refsOf(field: TextField): CharRef[] {
    // The projection publishes runs unmerged: one run per segment, so
    // (op, k0 + i) is the identity of the run's i-th character.
    const out: CharRef[] = [];
    for (const r of field.runs) {
        for (let k = 0; k < r.end - r.start; k++) out.push([r.op, r.k0 + k]);
    }
    return out;
}

/** An op body as the editor submits it. */
export interface OpBody {
    kind: string;
    target: Record<string, unknown>;
    payload: Record<string, unknown>;
    plan?: string | null;
}

export interface Submission extends OpBody {
    cid: string;
    batch: string;
}

export interface Ack { cid: string; op: string | null; conflict: string | null }

/** Unicode scalars of a JS string (UTF-16 → code points), the unit `k` counts in. */
export function scalars(s: string): string[] {
    return Array.from(s);
}

/** The applier records NFC (docs/model.md, "Definitions"), so the editor counts and sends NFC: a
 *  decomposed "e + ́" typed by an IME or pasted from macOS is one character to
 *  the store, and a shadow that thought it was two would name anchors that do
 *  not exist. */
export function nfc(s: string): string {
    return s.normalize('NFC');
}
