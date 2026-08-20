/**
 * settlement-decorations.ts — the ONE layer that draws unsettled text.
 *
 * It replaces the drawing half of three plugins that each owned a slice of the same
 * question (captured-decorations, auto-edit-decorations, and the tracked-change marks
 * agent-proposals materialized), and it draws them from one model — `state/settlement`,
 * which is where the reasoning lives and where the tests are. This file is deliberately
 * thin: build the live text, ask for the claims, put them on screen.
 *
 * ## One visual axis per channel
 *
 * The reason the old surface needed a legend is that all three channels reached for the
 * same axis — an underline, in three colours whose meanings were not guessable and did
 * not survive being stacked. Here each channel owns a different property of the text,
 * so a span carrying two claims shows both at once and neither hides the other:
 *
 *   human → the INK.        blue text. Pulsing while it is still yours to send.
 *   plan  → the OPACITY.    faded, as unbuilt things should look. Solider once accepted.
 *   code  → the GROUND.     green behind what the codebase added, red behind what it cut.
 *
 * ## The human channel has no diff view
 *
 * It draws ink and nothing else — no ghost of what you removed. You are the one who
 * removed it, and showing your own deleted words back to you is the surface narrating
 * your typing; the reader needs to see what they WROTE, which the ink already is. The
 * other two channels do report removals, because there somebody else took the words
 * out and "what did it say before" is exactly the question.
 *
 * The claim still exists either way — a deletion-only edit has no added words to ink,
 * and the margin marker has to know the feature is unsettled. Only the drawing differs.
 *
 * Planned wording that the build then altered therefore reads exactly as it should
 * without anybody being taught a key: the plan's own faded words, with a red ground
 * under the part that did not survive the build.
 *
 * ## Motion says one thing
 *
 * Only `human/open` pulses, and only because it is the one state with an action
 * attached (⌘S). Everything else is a condition, and animating a condition spends the
 * reader's attention on something they cannot act on. The pulse is subtle by
 * construction and CSS drops it under `prefers-reduced-motion`.
 *
 * ## Positions
 *
 * Offsets come back from the model in DISPLAY SPACE (display-text.ts): every inline
 * atom is one object-replacement char, so char `i` inside a textblock at `pos` is doc
 * position `pos + 1 + i` — codeRef chips included. This is the contract that fixed the
 * whole family of mis-anchored underlines; it is the only reason a paragraph citing
 * code can be diffed at all.
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { Node as PMModelNode } from '@tiptap/pm/model';
import { claimsFor, type Claim, type FeatureText } from '../../state/settlement';
import type { FeatureStages } from '../../state/settlement-stages';
import { ATOM_CHAR, paraDisplayText } from './display-text';

export type { FeatureStages };

export const SETTLEMENT_UPDATED = 'codocSettlementUpdated';
const settlementKey = new PluginKey('codocSettlementDecorations');

export interface SettlementDecorationsOptions {
    /** fid (or the proposal id, or the localId — see `liveFeatures`) → its earlier
     *  stages. A feature absent from the map is settled and draws nothing. */
    getStages: () => ReadonlyMap<string, FeatureStages>;
    /** Features whose edits the author has handed off, so their ink stops pulsing.
     *  Held apart from the stages because it is the EDITOR's fact, not the daemon's:
     *  it changes on ⌘S, before any payload comes back. */
    getCommitted?: () => ReadonlySet<string>;
}

/** A feature's blocks in the live document: the heading, its paragraphs, and the doc
 *  position of each so a display-space offset can be resolved. */
interface LiveFeature {
    key: string;
    titlePos: number;
    paraPos: number[];
    text: FeatureText;
}

/**
 * Split the document into features the way the model expects them.
 *
 * Keyed by `fid` when minted, else by the proposal that put it there, else by the
 * client-side `localId` — a heading typed a moment ago has no store id yet, and
 * dropping it here is what used to leave brand-new nodes with no mark at all until the
 * daemon answered.
 */
export function liveFeatures(doc: PMModelNode): LiveFeature[] {
    const out: LiveFeature[] = [];
    let cur: LiveFeature | null = null;
    doc.forEach((node, pos) => {
        if (node.type.name === 'featureHeading') {
            const attrs = node.attrs as { fid?: string | null; localId?: string | null; proposed?: string | null };
            // `proposed` sits between the two identities because a materialized plan
            // node has neither: its proposal id IS its identity for as long as it is on
            // screen, and without this rung its own claims would be filed under a key
            // nothing in the document matches — the plan would render as plain prose.
            const key = attrs.fid ?? attrs.proposed ?? attrs.localId ?? null;
            cur = key ? { key, titlePos: pos, paraPos: [], text: { title: paraDisplayText(node), paras: [] } } : null;
            if (cur) out.push(cur);
        } else if (cur && node.isTextblock) {
            cur.paraPos.push(pos);
            cur.text.paras.push(paraDisplayText(node));
        }
    });
    return out;
}

/** The doc position a claim's offsets are relative to, or null when the block it names
 *  is no longer in the document (a paragraph deleted between payload and paint). */
function contentStart(f: LiveFeature, c: Claim): number | null {
    if (c.block.kind === 'title') return f.titlePos + 1;
    const pos = f.paraPos[c.block.index];
    return pos === undefined ? null : pos + 1;
}

/** The CSS modifier for a claim: its channel, then its stage, then its direction. Three
 *  independent tokens rather than one fused name, so the stylesheet expresses the axes
 *  separately and a claim carrying two channels composes instead of colliding. */
export function claimClass(c: Claim): string {
    return `ce-settle ${c.channel} ${c.stage} ${c.edit}`;
}

/** Removed text, made readable: display space carries an object-replacement char per
 *  inline atom, which would otherwise print as a tofu box in the ghost. */
function readable(text: string): string {
    return text.replace(new RegExp(ATOM_CHAR, 'g'), '⟦ref⟧').trim();
}

/** The hover sentence for a claim — the same wording the node marker uses, so the
 *  margin and the prose never explain the same state two different ways. */
export function claimTitle(c: Claim): string {
    if (c.channel === 'human') {
        return c.stage === 'open'
            ? 'You wrote this. Recorded here only — ⌘S sends it to the agent.'
            : 'You wrote this, and it is with the agent.';
    }
    if (c.channel === 'plan') {
        return c.stage === 'proposed'
            ? 'Proposed wording — nothing is built yet. Accept or reject it on the heading.'
            : 'Planned and accepted. No code behind it yet.';
    }
    return c.edit === 'add'
        ? 'This is what the code now says.'
        : 'The code no longer says this.';
}

/**
 * A deletion has no text left to cover, so it is drawn as a GHOST of the removed
 * words rather than as a bare caret.
 *
 * The caret was the older answer and it was the wrong one for a review surface: a
 * two-pixel mark tells you something went, and withholds the one fact you need to
 * decide whether you mind. For the code channel especially — where the point is
 * "the codebase dropped this claim" — the words themselves are the message.
 *
 * Only the plan and code channels reach here; the human channel is ink only.
 */
function ghost(c: Claim): HTMLElement {
    const el = document.createElement('span');
    el.className = claimClass(c) + ' ce-settle-ghost';
    el.contentEditable = 'false';
    el.textContent = readable(c.removed ?? '');
    el.title = claimTitle(c);
    return el;
}

export function buildSettlementDecorations(
    doc: PMModelNode, stages: ReadonlyMap<string, FeatureStages>,
    committed: ReadonlySet<string> = new Set(),
): DecorationSet {
    if (!stages.size) return DecorationSet.empty;
    const decos: Decoration[] = [];
    for (const f of liveFeatures(doc)) {
        const st = stages.get(f.key);
        if (!st) continue;
        for (const c of claimsFor({ ...st, live: f.text, committed: committed.has(f.key) })) {
            const base = contentStart(f, c);
            if (base === null) continue;
            // The human channel is ink only — see the header. The claim is still in the
            // model (the marker reads it); it just is not drawn.
            if (c.channel === 'human' && c.edit === 'del') continue;
            if (c.edit === 'del') {
                decos.push(Decoration.widget(base + c.start, () => ghost(c), {
                    side: 1,
                    // Keyed by everything that changes what is drawn, so ProseMirror
                    // reuses the node across rebuilds instead of remounting it — a
                    // remount restarts the pulse, which reads as a second edit.
                    key: `st-${c.channel}-${c.stage}-${c.start}-${(c.removed ?? '').length}`,
                }));
            } else if (c.end > c.start) {
                decos.push(Decoration.inline(base + c.start, base + c.end, {
                    class: claimClass(c),
                    title: claimTitle(c),
                }));
            }
        }
    }
    return DecorationSet.create(doc, decos);
}

export const SettlementDecorations = Extension.create<SettlementDecorationsOptions>({
    name: 'settlementDecorations',
    addOptions() {
        return { getStages: () => new Map(), getCommitted: () => new Set() };
    },
    addProseMirrorPlugins() {
        const options = this.options;
        return [new Plugin({
            key: settlementKey,
            state: {
                init: (_c, state) => buildSettlementDecorations(
                    state.doc, options.getStages(), options.getCommitted?.() ?? new Set()),
                // No `nextDecorations` here, deliberately: this layer is derived from the
                // TEXT, so a keystroke really does invalidate it — that is the whole point
                // of it. It is also cheap for the same reason the layers it replaces were:
                // only features the host names in `getStages` are diffed at all, and a
                // settled document names none of them.
                apply: (tr, old) => (tr.docChanged || tr.getMeta(SETTLEMENT_UPDATED))
                    ? buildSettlementDecorations(tr.doc, options.getStages(), options.getCommitted?.() ?? new Set())
                    : old,
            },
            props: {
                decorations(state) { return this.getState(state); },
            },
        })];
    },
});
