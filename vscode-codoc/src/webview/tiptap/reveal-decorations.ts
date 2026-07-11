/**
 * reveal-decorations.ts — the signature "ghost → resolved" reveal (P-reveal).
 *
 * While the agent is realizing a feature (`sync.phase[fid]` is editing/reflecting),
 * that feature's DESCRIPTION renders as dim "ghost" prose — the opaque text the model
 * has staked out but not yet resolved. When the phase flips to `done`, the prose
 * RESOLVES word-by-word from mute → ink (a brief staggered fade), the visual codoc
 * equivalent of an agent finishing its work in the doc.
 *
 * This is the STATUS axis (no hue): ghost is a foreground-opacity dim, the reveal is a
 * pure opacity/transform fade. Both respect prefers-reduced-motion (the CSS gate jumps
 * to the resolved state). It reads the SAME `sync.phase` map ActivityDecorations uses
 * (the heading dot) — this is the body-prose counterpart.
 *
 * The geometry (which range is a feature's body; where the word boundaries fall) is a
 * pure function so it is unit-testable; the plugin only wires phase-transition detection
 * + a one-shot timer to clear the transient reveal decorations once the animation lands.
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { Node as PMModelNode } from '@tiptap/pm/model';
import type { FeaturePhase } from '../../state/activity-model';

export const REVEAL_UPDATED = 'codocRevealUpdated';
const REVEAL_CLEAR = 'codocRevealClear';
const revealKey = new PluginKey('codocReveal');

/** Per-word stagger + how long after the last word we drop the transient decorations. */
const WORD_STEP_MS = 22;
const REVEAL_TAIL_MS = 420;
const MAX_REVEAL_WORDS = 140;   // bound the work on a very long description

export interface RevealDecorationsOptions {
    getPhases: () => Record<string, FeaturePhase>;
}

interface FeatureBody { fid: string; from: number; to: number }

/** The description range of every feature: from just after a `featureHeading` to just
 *  before the next heading (or doc end). Empty bodies (heading immediately followed by
 *  another heading) are dropped. Pure — the unit tests drive this directly. */
export function featureBodyRanges(doc: PMModelNode): FeatureBody[] {
    const heads: { fid: string; pos: number; end: number }[] = [];
    doc.forEach((node, pos) => {
        if (node.type.name === 'featureHeading') {
            heads.push({ fid: (node.attrs.fid as string) || '', pos, end: pos + node.nodeSize });
        }
    });
    const out: FeatureBody[] = [];
    for (let i = 0; i < heads.length; i++) {
        const h = heads[i];
        if (!h.fid) continue;
        const from = h.end;
        const to = i + 1 < heads.length ? heads[i + 1].pos : doc.content.size;
        if (to > from) out.push({ fid: h.fid, from, to });
    }
    return out;
}

const PHASE_ACTIVE = (p: FeaturePhase | undefined): boolean => p === 'editing' || p === 'reflecting';

/** Direct-child paragraph ranges within [from, to) — the node-level spans the resolving
 *  sweep wraps (one per paragraph in a feature's body). Pure — unit-tested directly. */
export function paragraphRanges(doc: PMModelNode, from: number, to: number): { from: number; to: number }[] {
    const out: { from: number; to: number }[] = [];
    doc.forEach((node, pos) => {
        if (pos < from || pos >= to || node.type.name !== 'paragraph') return;
        out.push({ from: pos, to: pos + node.nodeSize });
    });
    return out;
}

/** One node decoration per paragraph in [from, to) — the CSS `::after` sweep band sizes to
 *  the whole paragraph box, so this must be a node (not inline) decoration. */
function sweepDecorations(doc: PMModelNode, from: number, to: number): Decoration[] {
    return paragraphRanges(doc, from, to).map(r => Decoration.node(r.from, r.to, { class: 'ce-resolving-sweep' }));
}

/** Inline word decorations across [from,to], each with an incrementing animation-delay so
 *  the reveal sweeps left→right. Caps at MAX_REVEAL_WORDS (the rest simply fade with no
 *  per-word stagger, so a huge body never spawns thousands of decorations). */
function wordDecorations(doc: PMModelNode, from: number, to: number): Decoration[] {
    const decos: Decoration[] = [];
    let idx = 0;
    doc.nodesBetween(from, to, (node, pos) => {
        if (!node.isText || !node.text) return;
        const text = node.text;
        const re = /\S+/g;
        let m: RegExpExecArray | null;
        while ((m = re.exec(text)) !== null) {
            const wFrom = Math.max(from, pos + m.index);
            const wTo = Math.min(to, pos + m.index + m[0].length);
            if (wTo <= wFrom) continue;
            const delay = idx < MAX_REVEAL_WORDS ? idx * WORD_STEP_MS : MAX_REVEAL_WORDS * WORD_STEP_MS;
            decos.push(Decoration.inline(wFrom, wTo, { class: 'ce-reveal-word', style: `animation-delay:${delay}ms` }));
            idx++;
        }
    });
    return decos;
}

function build(doc: PMModelNode, phases: Record<string, FeaturePhase>, resolving: Set<string>): DecorationSet {
    const decos: Decoration[] = [];
    for (const body of featureBodyRanges(doc)) {
        if (PHASE_ACTIVE(phases[body.fid])) {
            // ghost: dim the whole description while the agent works it
            decos.push(Decoration.inline(body.from, body.to, { class: 'ce-ghost' }));
        } else if (resolving.has(body.fid)) {
            decos.push(...wordDecorations(doc, body.from, body.to), ...sweepDecorations(doc, body.from, body.to));
        }
    }
    return DecorationSet.create(doc, decos);
}

/** fids that just left an active phase (→ resolve), comparing a prev snapshot to current. */
export function newlyResolved(prev: Record<string, FeaturePhase>, cur: Record<string, FeaturePhase>): string[] {
    const out: string[] = [];
    for (const fid of Object.keys(prev)) {
        if (PHASE_ACTIVE(prev[fid]) && !PHASE_ACTIVE(cur[fid])) out.push(fid);
    }
    return out;
}

interface RevealState { set: DecorationSet; prev: Record<string, FeaturePhase>; resolving: Set<string> }

export const RevealDecorations = Extension.create<RevealDecorationsOptions>({
    name: 'revealDecorations',

    addOptions() {
        return { getPhases: () => ({}) };
    },

    addProseMirrorPlugins() {
        const getPhases = (): Record<string, FeaturePhase> => this.options.getPhases();
        return [
            new Plugin<RevealState>({
                key: revealKey,
                state: {
                    init: (_c, state): RevealState => {
                        const phases = getPhases();
                        return { set: build(state.doc, phases, new Set()), prev: { ...phases }, resolving: new Set() };
                    },
                    apply: (tr, value, _o, newState): RevealState => {
                        let { prev, resolving } = value;
                        const cleared = tr.getMeta(REVEAL_CLEAR) as string[] | undefined;
                        if (cleared) { resolving = new Set(resolving); cleared.forEach(f => resolving.delete(f)); }
                        if (tr.getMeta(REVEAL_UPDATED)) {
                            const cur = getPhases();
                            const fresh = newlyResolved(prev, cur);
                            if (fresh.length) { resolving = new Set(resolving); fresh.forEach(f => resolving.add(f)); }
                            prev = { ...cur };
                        }
                        if (!tr.getMeta(REVEAL_UPDATED) && !cleared && !tr.docChanged) {
                            return { set: value.set.map(tr.mapping, tr.doc), prev, resolving };
                        }
                        return { set: build(newState.doc, getPhases(), resolving), prev, resolving };
                    },
                },
                props: { decorations(state) { return revealKey.getState(state)?.set; } },
                // Schedule a one-shot clear per resolving feature once its reveal has played,
                // so the transient word decorations don't linger (they'd re-animate on the
                // next unrelated rebuild). Reduced motion → the CSS lands instantly; the timer
                // still tidies up.
                view(view) {
                    const timed = new Set<string>();
                    const tick = () => {
                        const st = revealKey.getState(view.state);
                        if (!st) return;
                        for (const fid of st.resolving) {
                            if (timed.has(fid)) continue;
                            timed.add(fid);
                            const words = (() => {
                                const b = featureBodyRanges(view.state.doc).find(x => x.fid === fid);
                                return b ? Math.min(MAX_REVEAL_WORDS, view.state.doc.textBetween(b.from, b.to).split(/\s+/).filter(Boolean).length) : 0;
                            })();
                            const dur = words * WORD_STEP_MS + REVEAL_TAIL_MS;
                            window.setTimeout(() => {
                                if (view.isDestroyed) return;
                                timed.delete(fid);
                                view.dispatch(view.state.tr.setMeta(REVEAL_CLEAR, [fid]));
                            }, dur);
                        }
                    };
                    tick();
                    return { update: tick };
                },
            }),
        ];
    },
});
