/**
 * consult-decorations.ts — showing the author that a link became an instruction.
 *
 * Descriptions carry three markdown-native signals into realize directives. Two
 * of them tell the author they registered; one did not:
 *
 *   • `**bold**` → `Focus:`   — a real `bold` mark, and `inlineRunsToText` writes it
 *     back out as `**…**`. Visible, and it survives the save (it did not before: the
 *     serializer dropped the mark, so the asterisks the daemon needed never existed).
 *   • `[label](https://…)` → `Consult:` — nothing. It stayed raw markdown while
 *     silently becoming an instruction the agent WebFetches before implementing.
 *   • `> …` → STEER — retired (loop_b step 2.7); inline comments carry steers now.
 *     Deliberately NOT decorated: a cue for a dead path is worse than no cue.
 *
 * A DECORATION, not a schema mark. The description round-trips through
 * `inlineRunsToText` to the exact markdown `codoc.codoc_file.parse` reads, and a
 * Link mark would put a serializer between the author's text and that parser —
 * one more place for the two to disagree about what the author wrote. The text
 * stays literally what the daemon parses; only its appearance changes.
 *
 * The pattern MIRRORS `parse._LINK_RE`. That is the whole contract: this must
 * highlight exactly what becomes a `Consult:` line, never a character more. A cue
 * that over-matches is a lie about what the agent was told.
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import type { Node as PMModelNode } from '@tiptap/pm/model';
import { Decoration, DecorationSet } from '@tiptap/pm/view';

/** Mirrors `codoc/codoc_file/parse.py:_LINK_RE`. `codoc:` links are refs, not
 *  consults, and are excluded by requiring an http(s) scheme — exactly as there. */
export const CONSULT_RE = /\[([^\]]*)\]\((https?:\/\/[^)\s]+)\)/g;

const consultKey = new PluginKey('codocConsultDecorations');

export interface ConsultSpan { from: number; to: number; label: string; url: string }

/** Every `Consult:`-bound link in the document, in document positions. */
export function consultSpans(doc: PMModelNode): ConsultSpan[] {
    const out: ConsultSpan[] = [];
    doc.descendants((node, pos) => {
        if (!node.isText || !node.text) return;
        CONSULT_RE.lastIndex = 0;
        for (let m = CONSULT_RE.exec(node.text); m; m = CONSULT_RE.exec(node.text)) {
            out.push({
                from: pos + m.index,
                to: pos + m.index + m[0].length,
                label: m[1],
                url: m[2],
            });
        }
    });
    return out;
}

export function buildConsultDecorations(doc: PMModelNode): DecorationSet {
    const decos = consultSpans(doc).map(s =>
        Decoration.inline(s.from, s.to, {
            class: 'ce-consult',
            title: `The agent reads this before implementing — ${s.url}`,
        }));
    return decos.length ? DecorationSet.create(doc, decos) : DecorationSet.empty;
}

export function consultDecorationsPlugin(): Plugin {
    return new Plugin({
        key: consultKey,
        state: {
            init: (_c, state) => buildConsultDecorations(state.doc),
            // Text-keyed, so this genuinely must rebuild as you type — a link
            // appears character by character and the cue has to keep up. It is
            // cheap: one regex per text node, no DOM built here.
            apply: (tr, old, _o, newState) =>
                tr.docChanged ? buildConsultDecorations(newState.doc) : old,
        },
        props: { decorations(state) { return consultKey.getState(state); } },
    });
}

export const ConsultDecorations = Extension.create({
    name: 'codocConsultDecorations',
    addProseMirrorPlugins() { return [consultDecorationsPlugin()]; },
});
