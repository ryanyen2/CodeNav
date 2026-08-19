/**
 * schema.ts — assembles the codoc editor's TipTap extension set (U1).
 *
 * The document vocabulary mirrors `state/pm-doc.ts` exactly (featureHeading,
 * paragraph, text, codeRef + marks bold/comment/author) so the pure
 * serializer/deserializer and the live editor agree on the model.
 *
 * We start from StarterKit but disable the block types that don't belong in a
 * feature outliner (its own heading, lists, blockquote, code blocks, rules, …),
 * keeping document/paragraph/text/bold/hardBreak/history/dropcursor/gapcursor,
 * then add the custom nodes/marks. `getSchema(codocExtensions())` builds the
 * ProseMirror schema headlessly (no DOM) — used by the parity tests.
 *
 * `bold` is the ONLY inline formatting the schema keeps, and it is not formatting:
 * `**…**` is the focus signal the daemon reads into a realize directive. Italic and
 * highlight were also here, and were pure decoration — the serializer dropped them,
 * so an author styled a span, saved, and the next projection erased it. A mark the
 * store cannot carry has no business in the schema.
 */
import StarterKit from '@tiptap/starter-kit';
import { getSchema, Extensions } from '@tiptap/core';
import { FeatureHeading } from './feature-heading';
import { ParagraphOwner } from './paragraph-owner';
import { CodeRef } from './code-ref';
import { AuthorMark } from './author-mark';
import { CommentMark } from './comment-mark';
// Vendored tracked-changes engine (sungkhum/tiptap-track-changes, MIT — see
// track-changes/NOTICE): registers insertion/deletion/format-change marks + the
// suggest-mode plugin. Mode defaults to 'edit' (no interception) until the editing
// model wires it (U3+); registering it here puts the marks in the schema so
// agent-authored tracked changes can be rendered and serialization can strip them.
import { TrackChangesExtension } from './track-changes';
import { MarkHygiene } from './mark-hygiene';
import { DragHandle } from './drag-handle';
import { Placeholder } from './placeholder';
import { ConsultDecorations } from './consult-decorations';

export function codocExtensions(): Extensions {
    return [
        StarterKit.configure({
            // Disable the outliner-irrelevant block + mark types.
            heading: false,
            bulletList: false,
            orderedList: false,
            listItem: false,
            blockquote: false,
            codeBlock: false,
            horizontalRule: false,
            strike: false,
            code: false,
            italic: false,
            // Kept: document, paragraph, text, bold, hardBreak,
            //       history, dropcursor, gapcursor.
        }),
        FeatureHeading,
        // ownerId on paragraphs + the keep-owner sweep (invariant I2): prose is anchored
        // to its feature by identity, so a heading inserted above it never steals it.
        ParagraphOwner,
        CodeRef,
        AuthorMark,
        CommentMark,
        // featureHeading is the outliner's block node; register it for node-level
        // change tracking (the global dataTracked attr) alongside paragraph.
        TrackChangesExtension.configure({ mode: 'edit', additionalBlockTypes: ['featureHeading'] }),
        // …and immediately after it, the rule that keeps those marks off the human's
        // text. An agent's insertion mark means "drop this when projecting to
        // tree.codoc", so a keystroke that inherited one would erase itself on save.
        MarkHygiene,
        // Reordering a feature by hand. Registered in the shared schema because
        // the hub serves this same bundle — the gesture is not a VS Code extra.
        DragHandle,
        // What an empty document and an empty description say, and the cue that a
        // link has become an instruction the agent will read.
        Placeholder,
        ConsultDecorations,
    ];
}

/** Build the ProseMirror schema headlessly (no editor view / DOM needed). */
export function codocSchema() {
    return getSchema(codocExtensions());
}
