/**
 * command-emitter.ts — the settle→commands translation for the NETWORK home.
 *
 * The editor bundle runs in two homes (host-bridge.ts), and the store-authoritative
 * model (U3/U4) says an authored edit reaches Loop B as an identity-keyed COMMAND —
 * never as a document for someone to diff. In VS Code the extension host does that
 * translation (`tree-editor.settleDoc`), because it is the process that reads the
 * projection and therefore holds the baselines.
 *
 * On the hub there is no such process: the browser is the only party that ever sees a
 * projection, so the browser has to be the one that emits. What was there instead posted
 * the whole doc as `doc-settle`, and the hub wrote it to `tree.doc.json` — a file that
 * since U4 is the DAEMON's own output and since U7 is read by nobody as input. So a
 * remote contributor's prose went nowhere (the next daemon pass overwrote the file from
 * the store), while the write itself made `reconcile.safe_write_tree` see the projection
 * as "ahead of the store" and skip re-rendering both exports until something else moved.
 * An edit that is silently dropped AND stalls the surface it was made on.
 *
 * This module is the missing half, and it is deliberately thin: the rules live in
 * `state/edit-provenance.ts`, the same object the extension host uses, so the two homes
 * cannot drift into different answers about what an edit was or what it replaced. What is
 * local to this file is only the wire form and the emission token.
 *
 * What stays server-side is the capability gate: `serve/dispatch.py` accepts
 * `set_title`/`set_description` from a SUGGEST role and requires HANDOFF for
 * `add`/`move`/`retire` (KTD10). A refused command is dropped by the outbox and
 * surfaced by `viewer-status`, so an outsider's structural edit fails loudly rather
 * than looking saved.
 */
import { featureUnits } from '../state/commands-from-doc';
import { EditProvenance } from '../state/edit-provenance';
import type { CommandEntry } from '../state/edits-channel';
import type { PMNode } from '../state/pm-doc';
import type { WebviewMessage } from './protocol';

export interface CommandEmitter {
    /** Record an arriving projection as a citable baseline (see EditProvenance.observe). */
    observe(payload: { doc?: PMNode; baselineId?: number }): void;
    /** The commands a settled doc implies, against the baseline it CITES. Treated as sent:
     *  the transport's outbox is durable and retries, so an enqueue is as good as an
     *  append and the overlay may advance now. */
    settle(doc: PMNode, baselineId?: number): CommandEntry[];
    /** Fold an explicitly authored command (a tree-pane drag) in, so a later settle does
     *  not re-derive it from a baseline that predates it. */
    record(commands: readonly CommandEntry[]): void;
    /** A fresh emission token — one settle, or one drag (see commands-from-doc). */
    token(): string;
}

/** Wire form of one command, as `serve/dispatch._command` reads it. camelCase keys with
 *  snake_case accepted — this sends the camelCase spelling the module documents. */
export function commandMessage(c: CommandEntry): WebviewMessage {
    return {
        kind: c.kind,
        id: c.id,
        ...(c.feature_id ? { featureId: c.feature_id } : {}),
        ...(c.local_id ? { localId: c.local_id } : {}),
        ...(c.base_text !== undefined ? { baseText: c.base_text } : {}),
        ...(c.session ? { session: c.session } : {}),
        ...(c.payload ? { payload: c.payload } : {}),
    } as WebviewMessage;
}

/**
 * `session` names this browser tab's editing session — distinct per tab, stable while it
 * lives (a caller may supply one for tests). It is what lets the daemon tell a burst of
 * this author's own commands from a genuine disagreement with somebody else.
 */
export function createCommandEmitter(session?: string): CommandEmitter {
    const tag = session ?? `hub${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
    const provenance = new EditProvenance(tag);
    let emissions = 0;
    const token = (): string => `${tag}.${++emissions}`;

    return {
        token,
        observe(payload): void {
            if (payload.doc) provenance.observe(featureUnits(payload.doc), payload.baselineId);
        },
        settle(doc, baselineId): CommandEntry[] {
            const commands = provenance.settle(featureUnits(doc), baselineId, token());
            provenance.record(commands);
            return commands;
        },
        record(commands): void {
            provenance.record(commands);
        },
    };
}
