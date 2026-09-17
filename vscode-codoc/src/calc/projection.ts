/**
 * projection.ts — the daemon's `projection.json`, typed, and the op bodies the
 * editor sends back (docs/substrate/09-editor.md §5–6).
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
    kind: 'bold' | 'link' | 'cite' | 'comment' | string;
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
    kind: 'heading' | 'paragraph' | string;
    level: number;
    rank: string;
    head: number;
    fields: Record<string, TextField | unknown>;
}

export interface Claim {
    bid: string;
    path: string;
    start: number;
    end: number;
    channel: 'human' | 'plan' | 'code';
    stage: 'unsent' | 'sent' | 'open' | 'accepted' | 'added' | 'removed';
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
}

export type Phase = 'synced' | 'edited' | 'queued' | 'realizing' | 'proposed' | 'drifted' | 'planned';

export interface Watermarks { handoff: Record<string, number>; realized: Record<string, number> }

export interface Projection {
    head: number;
    frontier: Record<string, number>;
    blocks: ProjectedBlock[];
    phases: Record<string, Phase>;
    plans: PlanInfo[];
    bindings: { by_feature: Record<string, { file: string; symbol: string }[]>; by_file: Record<string, string[]> };
    claims: Record<string, Claim[]>;
    /** Per feature, the hand-off and realized frontiers (absent when neither). */
    watermarks?: Record<string, Watermarks>;
}

export const NO_WATERMARKS: Watermarks = { handoff: {}, realized: {} };

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
