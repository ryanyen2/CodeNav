/**
 * schema.ts — assembles the codoc editor's TipTap extension set (U1).
 *
 * The document vocabulary mirrors `state/pm-doc.ts` exactly (featureHeading,
 * paragraph, text, codeRef + marks strong/em/highlight/comment/author) so the
 * pure serializer/deserializer and the live editor agree on the model.
 *
 * We start from StarterKit but disable the block types that don't belong in a
 * feature outliner (its own heading, lists, blockquote, code blocks, rules, …),
 * keeping document/paragraph/text/bold/italic/hardBreak/history/dropcursor/
 * gapcursor, then add the custom nodes/marks. `getSchema(codocExtensions())`
 * builds the ProseMirror schema headlessly (no DOM) — used by the parity tests.
 */
import StarterKit from '@tiptap/starter-kit';
import { getSchema, Extensions } from '@tiptap/core';
import { FeatureHeading } from './feature-heading';
import { CodeRef } from './code-ref';
import { AuthorMark } from './author-mark';
import { HighlightMark } from './highlight-mark';
import { CommentMark } from './comment-mark';

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
            // Kept: document, paragraph, text, bold, italic, hardBreak,
            //       history, dropcursor, gapcursor.
        }),
        FeatureHeading,
        CodeRef,
        AuthorMark,
        HighlightMark,
        CommentMark,
    ];
}

/** Build the ProseMirror schema headlessly (no editor view / DOM needed). */
export function codocSchema() {
    return getSchema(codocExtensions());
}
