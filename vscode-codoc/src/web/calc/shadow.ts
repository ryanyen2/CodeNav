/**
 * shadow.ts: a block's live characters with their identities.
 *
 * The store's truth for a field, as the editor last saw it, advanced by the
 * editor's own unacked ops. It answers the one question recording needs: which
 * character identity sits at live offset i. Offsets are Unicode scalars, the
 * unit `k` counts in; the caller converts from UTF-16 positions.
 *
 * Provisional refs (`['$cid', cid, k]`) name characters this client inserted
 * before the daemon acked; `resolve(cid, opId)` rewrites them once it has.
 */
import { type Anchor, type CharRef, START, isProvisional, nfc, refEq, scalars } from './projection';

/** A run of consecutive characters one op minted: `[op, k0, len]`, or
 *  `['$cid', cid, k0, len]` for this client's unacked insert. */
export type Segment = [op: string, k0: number, len: number] | ['$cid', cid: string, k0: number, len: number];

/** Character identities as the segment list a `text.delete` names. A delete
 *  by segments kills exactly these characters, never a word another actor put
 *  between them that the page has not seen. */
export function segmentsOf(refs: readonly CharRef[]): Segment[] {
    const out: Segment[] = [];
    for (const r of refs) {
        const last = out[out.length - 1];
        if (isProvisional(r)) {
            if (last?.length === 4 && last[1] === r[1] && last[2] + last[3] === r[2]) last[3]++;
            else out.push(['$cid', r[1], r[2], 1]);
        } else if (last?.length === 3 && last[0] === r[0] && last[1] + last[2] === r[1]) {
            last[2]++;
        } else {
            out.push([r[0], r[1], 1]);
        }
    }
    return out;
}

export class Shadow {
    /** Parallel arrays: character i has identity refs[i]. */
    chars: string[];
    refs: CharRef[];

    constructor(chars: string[] = [], refs: CharRef[] = []) {
        if (chars.length !== refs.length) throw new Error('shadow: chars/refs length mismatch');
        this.chars = chars;
        this.refs = refs;
    }

    static fromField(text: string, refs: CharRef[]): Shadow {
        const cs = scalars(text);
        if (cs.length !== refs.length) throw new Error(`shadow: text has ${cs.length} scalars, ${refs.length} refs`);
        return new Shadow(cs, refs.slice());
    }

    get length(): number { return this.chars.length; }

    text(): string { return this.chars.join(''); }

    /** The insertion anchor for "insert at offset i": the character before it, or START. */
    anchorBefore(i: number): Anchor {
        if (i <= 0) return START;
        if (i > this.length) throw new RangeError(`shadow: offset ${i} > ${this.length}`);
        return this.refs[i - 1];
    }

    ref(i: number): CharRef {
        if (i < 0 || i >= this.length) throw new RangeError(`shadow: no character at ${i}`);
        return this.refs[i];
    }

    /** Offset of a character identity, or -1. */
    indexOf(r: CharRef): number {
        return this.refs.findIndex(x => refEq(x, r));
    }

    /** Record a local insert: `text` after offset `at`, minted by client op
     *  `cid`. The text is NFC, as the applier will record it. */
    insert(at: number, text: string, cid: string): void {
        const cs = scalars(nfc(text));
        const refs: CharRef[] = cs.map((_c, k) => ['$cid', cid, k]);
        this.chars.splice(at, 0, ...cs);
        this.refs.splice(at, 0, ...refs);
    }

    /** Record a local delete of [from, to); returns the identities removed. */
    delete(from: number, to: number): CharRef[] {
        const gone = this.refs.slice(from, to);
        this.chars.splice(from, to - from);
        this.refs.splice(from, to - from);
        return gone;
    }

    /** Split after offset `at` (exclusive end of the head). Returns the tail. */
    split(at: number): Shadow {
        const tail = new Shadow(this.chars.slice(at), this.refs.slice(at));
        this.chars = this.chars.slice(0, at);
        this.refs = this.refs.slice(0, at);
        return tail;
    }

    /** Append another shadow's characters (a merge). */
    append(tail: Shadow): void {
        this.chars.push(...tail.chars);
        this.refs.push(...tail.refs);
    }

    /** The daemon acked client op `cid` as `opId`: provisional refs become real. */
    resolve(cid: string, opId: string): number {
        let n = 0;
        for (let i = 0; i < this.refs.length; i++) {
            const r = this.refs[i];
            if (isProvisional(r) && r[1] === cid) {
                this.refs[i] = [opId, r[2]];
                n++;
            }
        }
        return n;
    }
}
