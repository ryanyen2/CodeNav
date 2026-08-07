/**
 * author-plugin.ts — stamps every span the user types with the active `author`
 * mark (U6). `mode` (pen|pencil) is the active "instrument"; `role` is who is
 * holding it (human at the keyboard here; reflected agent text is stamped by the
 * host path with role=<agent>, mode=pencil). The mark persists in `tree.doc.json`
 * and becomes the Phase-3 merge rule.
 *
 * Mechanism: a ProseMirror plugin whose `appendTransaction` finds the ranges a
 * user transaction inserted and adds the author mark across them. Our own stamp
 * transaction (and host-applied "reflect" transactions) are tagged so they are
 * never re-stamped.
 */
import { Extension } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import type { AuthorMode, AuthorRole } from '../../state/pm-doc';
import { AUTHOR_META, isUserInput } from './edit-origin';
import { clampRanges, insertedRanges } from './tx-ranges';

export { AUTHOR_META, REFLECT_META } from './edit-origin';

export interface AuthorIdentity {
    authorId: string;
    role: AuthorRole;
    mode: AuthorMode;
}

/** A small mutable identity the toolbar toggles (pen ⇄ pencil). One per editor. */
export class AuthorController {
    constructor(public identity: AuthorIdentity = { authorId: 'local-human', role: 'human', mode: 'pen' }) {}
    setMode(mode: AuthorMode): void { this.identity = { ...this.identity, mode }; }
    setRole(role: AuthorRole): void { this.identity = { ...this.identity, role }; }
    get(): AuthorIdentity { return this.identity; }
}

interface AuthorStampOptions {
    controller: AuthorController;
    /** Wall-clock provider — injected so it stays out of the render path / testable. */
    now: () => number;
}

const authorStampKey = new PluginKey('codocAuthorStamp');

export const AuthorStamp = Extension.create<AuthorStampOptions>({
    name: 'authorStamp',

    addOptions() {
        return {
            controller: new AuthorController(),
            now: () => Date.now(),
        };
    },

    addProseMirrorPlugins() {
        const { controller, now } = this.options;
        return [
            new Plugin({
                key: authorStampKey,
                appendTransaction: (transactions, _oldState, newState) => {
                    if (!transactions.some(tr => tr.docChanged && isUserInput(tr))) return null;

                    const markType = newState.schema.marks.author;
                    if (!markType) return null;

                    // Inserted spans in final-doc coordinates. Shared with mark hygiene so
                    // both plugins agree on "what the user just typed" (see tx-ranges.ts).
                    const ranges = clampRanges(
                        insertedRanges(transactions, isUserInput),
                        newState.doc.content.size,
                    );
                    if (ranges.length === 0) return null;

                    const { authorId, role, mode } = controller.get();
                    const mark = markType.create({ authorId, role, mode, ts: now() });
                    const tr = newState.tr.setMeta(AUTHOR_META, true);
                    for (const [from, to] of ranges) tr.addMark(from, to, mark);
                    return tr.steps.length ? tr : null;
                },
            }),
        ];
    },
});
