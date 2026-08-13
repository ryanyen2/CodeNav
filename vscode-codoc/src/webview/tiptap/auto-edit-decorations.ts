/**
 * auto-edit-decorations.ts — "codoc rewrote this while you weren't looking",
 * as an IN-SITU reviewable diff with an explicit verdict.
 *
 * Loop A applies safe ops without asking, and exactly one of them changes what the
 * document SAYS: a small AMEND rewrites a description in place. Everything else the
 * loop does automatically is index machinery and is deliberately never shown (the
 * triage is in `render._auto_edits`).
 *
 * This used to clear itself when the reader dwelled on the section — and that was
 * the complaint: the one record of "the AI changed your document" evaporated the
 * moment you looked at it, with no way to disagree. It is now the same review
 * surface every other agent change gets: the change drawn as a tracked-change diff
 * IN the prose (old words struck where they stood, new words underlined), plus a
 * quiet Keep / Restore pair on the heading. Nothing clears on its own.
 *
 * The CONSEQUENCE asymmetry with a planned proposal is stated, not implied: this
 * rewrite happened because the CODE already changed. Keep costs nothing (the doc
 * already says this). Restore is a real edit — it re-asserts a claim the code may
 * no longer honor, so it goes back through the authored-command channel where the
 * daemon classifies it and, if it implies code work, holds it for hand-off like any
 * other code-implying edit. The strip's hover text says exactly that.
 *
 * Decorations only — the diff is never materialized as engine marks here, because
 * an auto-edit is ALREADY APPLIED: the doc's canonical text is the NEW text, and
 * the baseline-aware serializer (insertions excluded, deletions kept) would render
 * marked prose back to the PREVIOUS text — a phantom revert on the next settle.
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { Node as PMModelNode } from '@tiptap/pm/model';
import { nextDecorations } from './decoration-policy';
import { alignParas, mdDisplayText, paraDisplayText, ATOM_CHAR } from './display-text';
import { wordDiff } from '../../state/doc-diff';
import { displacedHuman } from '../../state/auto-edits';
import type { AutoEdit } from '../../state/bindings-model';

export const AUTO_EDITS_UPDATED = 'codocAutoEditsUpdated';
const autoKey = new PluginKey('codocAutoEditDecorations');

export interface AutoEditHandlers {
    /** Keep codoc's wording — acknowledges the rewrite; the mark clears for good. */
    keep: (fid: string, at: string) => void;
    /** Restore the previous wording — an ordinary authored edit; the daemon
     *  classifies it and may queue reconcile work (held until hand-off). */
    revert: (fid: string, at: string, prev: string) => void;
}

export interface AutoEditDecorationsOptions {
    /** fid → the rewrite the reader has NOT resolved yet (already filtered by the
     *  seen-set; this layer draws whatever it is handed). */
    getUnseen: () => Record<string, AutoEdit>;
    handlers?: AutoEditHandlers;
}

/** The loop's prose is stored as one string with blank-line paragraph breaks — the
 *  same split `_description_lines` round-trips through. */
function prevParas(prev: string): string[] {
    return prev.split(/\n{2,}/).map(s => s.trim());
}

/** A review-diff span: an `add` range to underline in the live text, or a `del`
 *  point carrying the removed text to draw struck-through where it stood. Unlike
 *  captured-decorations' blockDiffSpans, deletions adjacent to insertions are NOT
 *  suppressed — this is a review surface, and "what did it say before" is exactly
 *  the question a replacement raises. */
export type ReviewSpan =
    | { kind: 'add'; from: number; to: number }
    | { kind: 'del'; at: number; text: string };

export function reviewDiffSpans(base: string, current: string, contentStart: number): ReviewSpan[] {
    const spans: ReviewSpan[] = [];
    if (base === current) return spans;
    let curOff = 0;
    for (const run of wordDiff(base, current)) {
        if (run.t === 'same') {
            curOff += run.s.length;
        } else if (run.t === 'ins') {
            spans.push({ kind: 'add', from: contentStart + curOff, to: contentStart + curOff + run.s.length });
            curOff += run.s.length;
        } else {
            spans.push({ kind: 'del', at: contentStart + curOff, text: run.s });
        }
    }
    return spans;
}

function delWidget(at: number, text: string, mine: boolean, key: string): Decoration {
    const shown = text.replace(new RegExp(ATOM_CHAR, 'g'), '⟦ref⟧');
    return Decoration.widget(at, () => {
        const el = document.createElement('del');
        el.className = 'ce-autoedit-del' + (mine ? ' mine' : '');
        el.contentEditable = 'false';
        el.textContent = shown;
        el.title = 'What this said before codoc rewrote it.';
        return el;
    }, { side: -1, key });
}

/** The verdict strip: origin in plain words, the loop's own recorded WHY, and the
 *  Keep / Restore pair with their consequences spelled out. Mirrors the shape of
 *  suggestion-decorations' verdictStrip so the two review surfaces read as one. */
function verdictStrip(
    fid: string, edit: AutoEdit, handlers: AutoEditHandlers | undefined,
): HTMLElement {
    const mine = displacedHuman(edit);
    const row = document.createElement('span');
    row.className = 'ce-verdict autoedit' + (mine ? ' mine' : '');
    row.contentEditable = 'false';

    const dir = document.createElement('span');
    dir.className = 'ce-tc-dir';
    dir.textContent = mine ? 'rewrote your wording · from code' : 'rewritten · from code';
    dir.title = (mine
        ? 'codoc edited words YOU wrote, to match code that already changed. '
        : 'codoc rewrote this description to match code that already changed. ')
        + (edit.rationale ? `Why: ${edit.rationale}` : '');
    row.append(dir);

    if (!handlers) return row;
    const actions = document.createElement('span');
    actions.className = 'ce-tc-btns';
    const once = (fn: () => void) => (ev: Event): void => {
        ev.preventDefault();
        ev.stopPropagation();
        actions.querySelectorAll('button').forEach(b => { (b as HTMLButtonElement).disabled = true; });
        actions.classList.add('applying');
        fn();
    };
    const restore = document.createElement('button');
    restore.type = 'button';
    restore.className = 'ce-diff-btn reject';
    restore.textContent = 'Restore mine';
    restore.title = 'Put the previous wording back. The code keeps its changes — '
        + 'restoring a claim the code no longer matches is a real edit, so it goes '
        + 'to the agent for reconciliation (held until you send it).';
    restore.addEventListener('mousedown', ev => ev.preventDefault());
    restore.addEventListener('click', once(() => handlers.revert(fid, edit.at, edit.prev)));
    const keep = document.createElement('button');
    keep.type = 'button';
    keep.className = 'ce-diff-btn accept';
    keep.textContent = 'Keep';
    keep.title = 'Keep codoc\'s wording. The doc already says this — nothing else changes.';
    keep.addEventListener('mousedown', ev => ev.preventDefault());
    keep.addEventListener('click', once(() => handlers.keep(fid, edit.at)));
    actions.append(restore, keep);
    row.append(actions);
    return row;
}

/** Build the marks for every unresolved rewrite present in this doc. Exported
 *  headless so the anchoring can be tested without a view (the widget factories
 *  only run on render). */
export function buildAutoEditDecorations(
    doc: PMModelNode, unseen: Record<string, AutoEdit>,
    handlers?: AutoEditHandlers,
): DecorationSet {
    if (!Object.keys(unseen).length) return DecorationSet.empty;
    const decos: Decoration[] = [];
    interface Para { node: PMModelNode; pos: number }
    interface Group { fid: string; headNode: PMModelNode; headPos: number; paras: Para[] }
    const groups: Group[] = [];
    let g: Group | null = null;
    doc.forEach((node, pos) => {
        if (node.type.name === 'featureHeading') {
            const fid = node.attrs.fid as string | null;
            g = fid && unseen[fid] ? { fid, headNode: node, headPos: pos, paras: [] } : null;
            if (g) groups.push(g);
            return;
        }
        if (g && node.type.name === 'paragraph') g.paras.push({ node, pos });
    });

    for (const grp of groups) {
        const edit = unseen[grp.fid];
        const mine = displacedHuman(edit);
        const cls = 'ce-autoedit' + (mine ? ' mine' : '');
        decos.push(Decoration.node(grp.headPos, grp.headPos + grp.headNode.nodeSize,
                                   { class: 'ce-autoedit-head' }));
        // ONE resolution surface per feature, on the feature — same rule as every
        // other proposal (suggestion-decorations rule 2).
        decos.push(Decoration.widget(grp.headPos + 1 + grp.headNode.content.size,
            () => verdictStrip(grp.fid, edit, handlers),
            { side: 1, key: 'auto-v-' + grp.fid + '@' + edit.at }));

        const baseDisplay = prevParas(edit.prev).map(mdDisplayText);
        const curDisplay = grp.paras.map(p => paraDisplayText(p.node));
        const pairing = alignParas(baseDisplay, curDisplay);
        grp.paras.forEach((p, k) => {
            if (p.node.content.size === 0) return;
            decos.push(Decoration.node(p.pos, p.pos + p.node.nodeSize, { class: cls }));
            const bi = pairing[k];
            const spans = reviewDiffSpans(bi == null ? '' : baseDisplay[bi], curDisplay[k], p.pos + 1);
            spans.forEach((sp, j) => {
                if (sp.kind === 'add') {
                    decos.push(Decoration.inline(sp.from, sp.to, { class: 'ce-autoedit-add' + (mine ? ' mine' : '') }));
                } else if (sp.text.trim()) {
                    // The old words, struck through where they stood — the other half
                    // of the diff the underline alone couldn't say.
                    decos.push(delWidget(sp.at, sp.text, mine, `auto-d-${grp.fid}-${k}-${j}@${edit.at}`));
                }
            });
        });
    }
    return DecorationSet.create(doc, decos);
}

export const AutoEditDecorations = Extension.create<AutoEditDecorationsOptions>({
    name: 'autoEditDecorations',

    addOptions() {
        return { getUnseen: () => ({}), handlers: undefined };
    },

    addProseMirrorPlugins() {
        const getUnseen = (): Record<string, AutoEdit> => this.options.getUnseen();
        const handlers = (): AutoEditHandlers | undefined => this.options.handlers;
        return [
            new Plugin({
                key: autoKey,
                state: {
                    init: (_c, state) => buildAutoEditDecorations(state.doc, getUnseen(), handlers()),
                    // Structure-keyed (decoration-policy): the rewrite is a fact from the
                    // payload, not something the reader's typing changes. Typing inside a
                    // marked paragraph only MOVES the marks — and the reader editing the
                    // prose themselves is exactly when re-diffing it against the loop's
                    // old text would start underlining their own words back at them.
                    apply: (tr, old, _o, newState) => nextDecorations(
                        tr, old, !!tr.getMeta(AUTO_EDITS_UPDATED),
                        () => buildAutoEditDecorations(newState.doc, getUnseen(), handlers()),
                    ),
                },
                props: { decorations(state) { return autoKey.getState(state); } },
            }),
        ];
    },
});
