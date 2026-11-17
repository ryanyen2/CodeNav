// @vitest-environment jsdom
/**
 * Round trips: the real editor's ops, replayed by the real applier.
 *
 * These are the TypeScript half of `tests/loops/test_roundtrip.py`, which runs
 * this file by its path, selects a phase by the `R1.` / `R2.` prefix, and
 * names a working directory in `CODOC_ROUNDTRIP`. The Python half writes the
 * projections, replays through the daemon what these write out, and compares
 * the store's page with the editor's. It is the one check that the page and
 * the store agree about the same edits. Run on their own, vitest skips them.
 *
 * The file's name and place are fixed by that script.
 */
import type { Node as PMNode } from '@tiptap/pm/model';
import * as fs from 'node:fs';
import { describe, expect, it } from 'vitest';

import { kindOf, type Projection, type Submission } from '../web/calc/projection';
import { newThreadOps } from '../web/editor/comments';
import { key, mountProj, paste, pasteText, posIn, select, shadowFaults, typeRule, typeText, type Mounted } from './web/editor/harness';

const TRIP = process.env.CODOC_ROUNDTRIP ?? '';
const PHASE = process.env.CODOC_ROUNDTRIP_PHASE ?? 'R1';

/** A deterministic pseudo-random source, so a failing seed is reproducible. */
function rng(seed: number): () => number {
    let x = seed * 1103515245 + 12345;
    return () => { x = (x * 1103515245 + 12345) & 0x7fffffff; return x / 0x7fffffff; };
}

/** Deterministic `Math.random` for a seed, so the block and batch ids a replay
 *  mints are the ones the recording minted. Returns the undo. */
function seedRandom(seed: number): () => void {
    const orig = Math.random;
    Math.random = rng(seed * 7919 + 1);
    return () => { Math.random = orig; };
}

/** The page as the store would describe it: id, kind, level and text per block. */
const pageOf = (doc: PMNode): Array<[string, string, number, string]> => {
    const out: Array<[string, string, number, string]> = [];
    doc.forEach(n => out.push([n.attrs.bid, kindOf(n), n.type.name === 'heading' ? n.attrs.level : 0,
                               n.textBetween(0, n.content.size, '\n')]));
    return out;
};

/** Replace every match of `q` with `r` through the Find widget's own DOM: one
 *  transaction over many blocks. */
function replaceAll(m: Mounted, q: string, r: string): void {
    m.handle.find(true);
    const inputs = m.container.querySelectorAll('.codoc-find .codoc-find-input') as NodeListOf<HTMLInputElement>;
    inputs[0].value = q;
    inputs[0].dispatchEvent(new window.Event('input', { bubbles: true }));
    inputs[1].value = r;
    const btn = (label: string) => Array.from(m.container.querySelectorAll('.codoc-find .codoc-find-btn'))
        .find(b => b.textContent === label) as HTMLButtonElement;
    btn('All').click();
    btn('×').click();
}

/** One random gesture, and a name for it. */
function gesture(m: Mounted, r: () => number, step: number, seed: number): string {
    const doc = m.view.state.doc;
    const blocks: Array<{ bid: string; kind: string; start: number; len: number }> = [];
    doc.forEach((n, pos) => blocks.push({ bid: n.attrs.bid, kind: n.type.name, start: pos + 1, len: n.content.size }));
    const a = blocks[Math.floor(r() * blocks.length)], b = blocks[Math.floor(r() * blocks.length)];
    const pa = a.start + Math.floor(r() * (a.len + 1)), pb = b.start + Math.floor(r() * (b.len + 1));
    const from = Math.min(pa, pb), to = Math.max(pa, pb);
    const heads = blocks.filter(x => x.kind === 'heading');
    const paras = blocks.filter(x => x.kind === 'paragraph');
    const items = blocks.filter(x => x.kind === 'bullet' || x.kind === 'numbered');
    const g = Math.floor(r() * 21);
    if (g === 15) {
        if (!paras.length) return 'noop';
        const pp = paras[Math.floor(r() * paras.length)];
        const marker = r() < 0.5 ? '-' : '1.';
        typeRule(m.view, pp.bid, marker);
        return `"${marker} " ${pp.bid}`;
    }
    if (g === 16) {
        if (!items.length) return 'noop';
        const it = items[Math.floor(r() * items.length)];
        select(m.view, it.start + it.len);
        key(m.view, 'Tab', 9, { shiftKey: r() < 0.4 });
        return `tab item ${it.bid}`;
    }
    if (g === 17) {
        if (!items.length) return 'noop';
        const it = items[Math.floor(r() * items.length)];
        if (r() < 0.5) { select(m.view, it.start + it.len); key(m.view, 'Enter', 13); return `enter item ${it.bid}`; }
        select(m.view, it.start);
        key(m.view, 'Backspace', 8);
        return `bs item ${it.bid}`;
    }
    if (g === 18) {
        select(m.view, from, to);
        if (r() < 0.5) key(m.view, 'b', 66, { ctrlKey: true }); else key(m.view, 'e', 69, { ctrlKey: true });
        return `mark ${from}-${to}`;
    }
    if (g === 19) {
        select(m.view, from, r() < 0.7 ? from : to);
        pasteText(m, `## M${step}\n\n- a${step}\n    - **b${step}** [l](https://x.io)\n1. c${step}`);
        return `paste md ${from}-${to}`;
    }
    if (g === 20) {
        if (!paras.length) return 'noop';
        const pp = paras[Math.floor(r() * paras.length)];
        select(m.view, pp.start + pp.len);
        key(m.view, 'Enter', 13);
        const back = r() < 0.5;
        key(m.view, back ? 'Backspace' : 'Delete', back ? 8 : 46);
        return `enter+${back ? 'bs' : 'del'} after ${pp.bid}`;
    }
    if (g === 0) { select(m.view, from); typeText(m.view, `x${step}`); return `type@${from}`; }
    if (g === 1) { select(m.view, from, to); typeText(m.view, 'y'); return `type over ${from}-${to}`; }
    if (g === 2) { select(m.view, from, to); key(m.view, 'Backspace', 8); return `bs ${from}-${to}`; }
    if (g === 3) { select(m.view, from, to); key(m.view, 'Delete', 46); return `del ${from}-${to}`; }
    if (g === 4) { select(m.view, r() < 0.5 ? from : to, to); key(m.view, 'Enter', 13); return `enter ${from}-${to}`; }
    if (g === 5) { select(m.view, from, r() < 0.5 ? from : to); paste(m, [`P${step}`, `Q${step}`]); return `paste ${from}-${to}`; }
    if (g === 6 && heads.length > 1) { const h = heads[Math.floor(r() * heads.length)]; m.handle.deleteFeature(h.bid); return `deleteFeature ${h.bid}`; }
    if (g === 7 && heads.length > 1) { const h = heads[Math.floor(r() * heads.length)]; const after = r() < 0.3 ? 'START' : blocks[Math.floor(r() * blocks.length)].bid; m.handle.moveBlock(h.bid, after as 'START'); return `move ${h.bid} after ${after}`; }
    if (g === 8 && heads.length) { const h = heads[Math.floor(r() * heads.length)]; select(m.view, h.start); key(m.view, 'Tab', 9, { shiftKey: r() < 0.5 }); return `relevel ${h.bid}`; }
    if (g === 9) { if (!paras.length) return 'noop'; const pp = paras[Math.floor(r() * paras.length)]; select(m.view, pp.start); typeText(m.view, '##'); const at = posIn(m.view, pp.bid, 2); m.view.someProp('handleTextInput', f => f(m.view, at, at, ' ', () => m.view.state.tr)); return `## ${pp.bid}`; }
    if (g === 10) {
        if (a.len < 2) return 'noop';
        const s0 = a.start + Math.floor(r() * (a.len - 1));
        const e0 = s0 + 1 + Math.floor(r() * (a.len - (s0 - a.start) - 1));
        select(m.view, s0, e0);
        const anchor = m.handle.selectionAnchor();
        if (!anchor) return 'noop';
        m.handle.flush();                        // the anchor refs must not be `$cid` refs
        const ops = newThreadOps(`c-${seed}-${step}`, anchor.bid, anchor.text, `note ${step}`,
                                 r() < 0.5 ? 'code' : 'both', anchor.start, anchor.end);
        m.submitted.push(...ops.map((op, i) => ({ cid: `cm-${seed}-${step}-${i}`, batch: `t-c${seed}-${step}`, ...op })));
        return `comment on ${anchor.bid}[${anchor.text}]`;
    }
    if (g === 11) { const q = ['e', 'a', 'o', 't'][Math.floor(r() * 4)]; replaceAll(m, q, q.toUpperCase()); return `replaceAll ${q}`; }
    if (g === 12) { select(m.view, from); paste(m, [`H${step}`, `body ${step}`], true); return `pasteHeading@${from}`; }
    if (g === 13) { if (!paras.length) return 'noop'; const pp = paras[Math.floor(r() * paras.length)]; select(m.view, pp.start); key(m.view, 'Tab', 9); return `tab on ${pp.bid}`; }
    select(m.view, from); paste(m, [`S${step}`]); return `paste1@${from}`;
}

/** Run `steps` random gestures for one seed, stopping at the first shadow fault. */
function fuzz(p0: Projection, seed: number, steps: number, failures: string[]): { m: Mounted; log: string[] } {
    const restore = seedRandom(seed);
    const r = rng(seed);
    const m = mountProj(JSON.parse(JSON.stringify(p0)) as Projection);
    const log: string[] = [];
    let ok = true;
    for (let step = 0; step < steps && ok; step++) {
        let name = '';
        try { name = gesture(m, r, step, seed); } catch (e) { name = `${name} THREW ${(e as Error).message}`; ok = false; }
        m.handle.flush();
        log.push(name);
        const faults = shadowFaults(m);
        if (faults.length) { failures.push(`seed ${seed} step ${step}: ${log.join(' → ')}\n    ${faults.join('\n    ')}`); ok = false; }
        m.conflicts.length = 0;
    }
    restore();
    return { m, log };
}

describe.runIf(TRIP && PHASE === 'R1')('R1. one actor, every gesture', () => {
    const SEEDS = 24, STEPS = 18;
    it(`${SEEDS} seeds × ${STEPS} gestures keep every shadow honest, and their ops are written out for the applier`, () => {
        const p0 = JSON.parse(fs.readFileSync(`${TRIP}/projection.json`, 'utf8')) as Projection;
        const failures: string[] = [];
        for (let seed = 1; seed <= SEEDS; seed++) {
            const { m, log } = fuzz(p0, seed, STEPS, failures);
            fs.writeFileSync(`${TRIP}/seed${String(seed).padStart(2, '0')}.json`,
                             JSON.stringify({ seed, log, subs: m.submitted, expect: pageOf(m.view.state.doc) }));
        }
        expect(failures).toEqual([]);
    });
});

// Two actors and a plan arriving mid-edit, which one actor cannot show: with
// one actor the shadow and the store never disagree about anything the
// projection carries. Phase R2A records the gestures and their submissions.
// The Python half drains them into a store another person and an agent have
// already moved, and publishes. Phase R2B replays the same gestures (seeded
// `Math.random`, so the ids match), adopts the other person's projection while
// every block it touched is still held, acks with the daemon's real acks, then
// adopts the final projection. The page must then be the store's page.
describe.runIf(TRIP && (PHASE === 'R2A' || PHASE === 'R2B'))('R2. two actors and a plan', () => {
    const SEEDS = 16, STEPS = 12;
    it(`${SEEDS} seeds × ${STEPS} gestures under another actor's edits and an agent's plan`, () => {
        const p1 = JSON.parse(fs.readFileSync(`${TRIP}/p1.json`, 'utf8')) as Projection;
        const p2 = PHASE === 'R2B' ? JSON.parse(fs.readFileSync(`${TRIP}/p2.json`, 'utf8')) as Projection : null;
        const failures: string[] = [];
        for (let seed = 1; seed <= SEEDS; seed++) {
            const pad = String(seed).padStart(2, '0');
            const { m, log } = fuzz(p1, seed, STEPS, failures);
            if (PHASE === 'R2A') {
                fs.writeFileSync(`${TRIP}/r2a-seed${pad}.json`, JSON.stringify({ seed, log, subs: m.submitted }));
                continue;
            }
            const rec = JSON.parse(fs.readFileSync(`${TRIP}/r2b-seed${pad}.json`, 'utf8')) as
                { acks: Array<{ cid: string; op: string; conflict: string | null }>; p3: Projection; subs: Submission[] };
            const a = rec.subs.map(x => x.kind).join(','), b = m.submitted.map(x => x.kind).join(',');
            if (a !== b) { failures.push(`seed ${seed}: the replay diverged\n  A: ${a}\n  B: ${b}`); continue; }
            m.handle.adopt(p2!);                          // the other person's page, while every touched block is held
            const faultsP2 = shadowFaults(m);
            m.handle.ack(rec.acks.map((x, i) => ({ cid: m.submitted[i].cid, op: x.op, conflict: x.conflict })));
            const faultsAck = shadowFaults(m);
            m.handle.adopt(rec.p3);
            fs.writeFileSync(`${TRIP}/r2c-seed${pad}.json`, JSON.stringify(
                { seed, log, afterP3: pageOf(m.view.state.doc), faultsP2, faultsAck, faultsP3: shadowFaults(m) }));
        }
        expect(failures).toEqual([]);
    });
});
