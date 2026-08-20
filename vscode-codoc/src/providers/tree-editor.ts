/**
 * Codoc Tree Editor — a CustomTextEditorProvider that replaces the plain-text
 * view of tree.codoc with a webview: a feature-tree nav pane (left) beside one
 * continuous documentation article (right), every feature a section with its
 * citations woven inline. Single-writer (U2b): the host persists the webview's
 * authored intent to tree.doc.json and never writes tree.codoc; the daemon is the
 * sole tree.codoc writer, and Loop B learns webview edits from tree.doc.json.
 *
 * This host module builds the DocPayload (tree nodes + ordered doc sections via
 * `layoutDoc` + live sync state from activity.json) and serves the bundled
 * webview client (dist/webview/doc-view.{js,css}). All rendering, scroll-sync,
 * keyboard nav and inline editing live in src/webview/doc-view.ts.
 */

import * as vscode from 'vscode';
import * as cp from 'node:child_process';
import * as fs from 'fs/promises';
import * as fsSync from 'fs';
import * as path from 'path';
import { cachedExecutables } from '../setup/provision';
import { WorkspaceState } from '../state/workspace-state';
import { parseTreeCodoc, extractLinks } from '../state/tree-model';
import { activeFeatureModes, featurePhases, featureSteps } from '../state/activity-model';
import { editKey, pruneSeen } from '../state/auto-edits';
import { PMNode } from '../state/pm-doc';
import { DocFile, parseDocFile, buildSuggestions, insertAtAnchor, Suggestion } from '../state/suggestion-model';
import { applyAgentProposals, agentAmendsFrom } from '../state/agent-proposals';
import { materializePlan } from '../state/plan-materialize';
import { buildStages, planNodesFrom, stagedProposals } from '../state/settlement-stages';
import { moveCommand, featureUnits } from '../state/commands-from-doc';
import { EditProvenance } from '../state/edit-provenance';
import {
    CommentThread, commentNoteText, mergeThreads, reconcileComments, storedThreads,
} from '../state/comment-model';
import { directedEdges, heldFeatures, heldDetail, divergentFeatures, blocksForFeature, mintedByLocalId } from '../state/bindings-model';
import { DOC_LANGUAGE_CHOICES, writeDocLanguage } from '../state/doc-language';
import { readDocLanguage } from '../state/codoc-config';
import { openPastDiff } from './past-content';
import {
    EditsFile, parseEditsFile, emptyEditsFile, CommandEntry,
} from '../state/edits-channel';
import { assembleThreads } from '../state/threads';
import { buildHoverCards } from '../state/registry-model';
import { BridgeController } from './bridge-controller';
import { declLines, featureIdsForChangedLines, changedLineNumbers, userTouchedFids } from '../state/bridge';
import { isAgentActive, agentRole } from '../state/activity-model';
import type { AutoEdit, SidecarData } from '../state/bindings-model';
import type { DocPayload, UINode, SyncState, RefSymbol, ThreadsData, WebviewPrefs } from '../webview/protocol';

const DOC_FILENAME = 'tree.doc.json';

/** workspaceState key for the per-workspace webview prefs (B-U2: the glance toggle).
 *  One blob per document uri so two open trees keep separate prefs. */
const PREFS_KEY = 'codoc.webviewPrefs';
/** workspaceState key for acknowledged loop rewrites (`fid@at`) — see unseenAutoEdits. */
const AUTO_SEEN_KEY = 'codoc.seenAutoEdits';

/** A "codoc"-named OutputChannel for the translate run's streamed output — the same
 *  lazy-singleton-per-module idiom extension.ts and credentials.ts use. */
let _channel: vscode.OutputChannel | undefined;
function treeEditorChannel(): vscode.OutputChannel {
    if (!_channel) _channel = vscode.window.createOutputChannel('codoc');
    return _channel;
}

export class CodocTreeEditorProvider implements vscode.CustomTextEditorProvider {
    public static readonly viewType = 'codoc.tree-editor';

    /** Open panels by tree.codoc uri — lets code→doc navigation reveal a feature
     *  in the LIVE webview instead of dumping the user into the raw text editor.
     *  Latest resolve wins (a re-opened editor replaces its stale entry). */
    private static panelByUri = new Map<string, vscode.WebviewPanel>();

    /** Reveal `fid` in the open webview for `treeUri`. Returns false when no
     *  panel is live (caller falls back / retries after opening one). The
     *  webview buffers the message until its first projection paints, so racing
     *  a freshly-opened editor is safe. */
    public static revealFeature(treeUri: vscode.Uri, fid: string): boolean {
        const panel = CodocTreeEditorProvider.panelByUri.get(treeUri.toString());
        if (!panel) return false;
        panel.reveal(panel.viewColumn, false);
        void panel.webview.postMessage({ kind: 'reveal-feature', fid });
        return true;
    }

    /** Open the in-document find widget in whichever tree webview is active.
     *
     *  Needed alongside the webview's own ⌘F listener because that listener only
     *  sees the keystroke when focus is INSIDE the iframe: with the cursor in the
     *  tree pane or the toolbar, VS Code routes the chord to the keybinding
     *  instead. Returns false when no panel is live. */
    public static openFind(replace: boolean): boolean {
        let sent = false;
        for (const panel of CodocTreeEditorProvider.panelByUri.values()) {
            if (!panel.active) continue;
            void panel.webview.postMessage({ kind: 'find', replace });
            sent = true;
        }
        return sent;
    }

    constructor(
        private readonly context: vscode.ExtensionContext,
        private readonly state: WorkspaceState,
        private readonly bridge: BridgeController,
    ) {}

    private rev = 0;
    /** The store projection per open tree (the daemon-written tree.doc.json, U4/KTD9).
     *  The webview is a pure projection consumer — this is the LAST projection the host
     *  rendered the editor from, NOT an authoritative copy the host writes back. It is
     *  the identity-keyed baseline a settle diffs against to emit commands (the host
     *  never persists tree.doc.json). Re-read on every buildPayload (the daemon is its
     *  sole writer); the in-memory copy is just the diff baseline + the live comment
     *  thread store. */
    private docFileByUri = new Map<string, DocFile>();
    /** Per open tree: what this host has told the editor, and what it has written itself —
     *  the citable projection baselines plus the optimistic `base_text` overlay
     *  (state/edit-provenance.ts, shared with the hub's browser-side emitter so one rule
     *  serves both homes). Every projection read is `observe`d; every settle diffs against
     *  the baseline the editor CITES rather than against whatever landed last. */
    private provenanceByUri = new Map<string, EditProvenance>();
    private baselineSeq = 0;
    /** Whether the current append outage has already been reported (re-armed on success),
     *  so a persistent failure doesn't fire a dialog per keystroke. */
    private hostOpFailureNotified = false;
    /** Names one emission of commands — a settle, or a drag. See `settleToken`. */
    private emissionSeq = 0;
    private readonly sessionTag = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;

    /**
     * A fresh token for one emission of commands, unique for the life of the machine.
     *
     * The counter alone would restart at 0 with the extension host and could then
     * reuse an id that an earlier, never-applied command already claimed — the
     * daemon would fold the new edit as a replay of an edit that never happened.
     * The session tag makes each host process's ids disjoint from every other's,
     * including its own past lives and a second window open on the same repo.
     */
    private settleToken(): string {
        return `${this.sessionTag}.${++this.emissionSeq}`;
    }
    /** Suggesting-mode DRAFTS (U4), per doc uri: feature ids whose edit the human is
     *  holding as a draft (the daemon keeps their code-implying directive out of the
     *  agent queue until hand-off). The host is the SOLE writer of edits.json `drafts`
     *  (marks on settle, clears on hand-off; the daemon only reads + preserves them), so
     *  this in-memory mirror is authoritative for the synchronous buildPayload. Seeded
     *  from edits.json on editor open so held drafts survive a reload. */
    private draftFidsByUri = new Map<string, Set<string>>();
    /** W3 saved-flash: fid → settle timestamp, per doc uri. A settled title/
     *  description edit lands here; when a later projection shows the fid NOT
     *  held (prose-only → committed live), the webview gets a quiet "saved"
     *  flash — the edit's only acknowledgment. Held fids drop silently (the
     *  pending badge covers them); stale entries expire. */
    private savedPendingByUri = new Map<string, Map<string, number>>();

    async resolveCustomTextEditor(
        document: vscode.TextDocument,
        panel: vscode.WebviewPanel,
        _token: vscode.CancellationToken,
    ): Promise<void> {
        panel.webview.options = {
            enableScripts: true,
            // `dist` serves the editor bundle; `.codoc/media` lets an `image` block's
            // local attachment (screenshot/upload) load as a real `<img>` via
            // `asWebviewUri` (see `mediaSrc`) instead of rendering as inert text.
            localResourceRoots: [
                vscode.Uri.joinPath(this.context.extensionUri, 'dist'),
                vscode.Uri.joinPath(document.uri, '..', '..', '.codoc', 'media'),
            ],
        };
        panel.webview.html = this.html(panel.webview);

        // Seed the live comment-thread store from the last-persisted tree.doc.json (if any).
        // U4: the host no longer WRITES tree.doc.json — the daemon is its sole writer — but a
        // pre-U4 workspace may carry comment threads in it; we read them once so open threads
        // survive the migration. The projection (build_doc_from_store) carries the comment
        // MARKS; the thread bodies live here until U8 migrates them into the store.
        const saved = await this.loadDocFile(document);
        if (saved) this.docFileByUri.set(document.uri.toString(), saved);

        // Seed the held-draft mirror (U4) from edits.json so drafts the daemon is still
        // holding survive a reload and re-raise the hand-off affordance.
        const seedEdits = await this.readEditsFile(document);
        const seedSet = this.draftSet(document);
        for (const d of seedEdits.drafts ?? []) seedSet.add(d.feature_id);

        const post = (): void => {
            const payload = this.buildPayload(document, panel.webview);
            panel.webview.postMessage({ kind: 'doc', payload });
            // W3 saved-flash drain: a settled prose edit is acknowledged on the
            // first projection where its feature is NOT held. The age floor
            // skips the settle's own immediate repost (holds are stale there —
            // the daemon hasn't classified yet); the ceiling expires entries a
            // dead daemon will never acknowledge.
            const savedPending = this.savedPending(document);
            if (savedPending.size) {
                const held = new Set(payload.awaitingAI ?? []);
                const now = Date.now();
                const flash: string[] = [];
                for (const [fid, ts] of savedPending) {
                    const age = now - ts;
                    if (held.has(fid) || age > 15_000) { savedPending.delete(fid); continue; }
                    if (age < 600) continue;  // too soon — wait for the daemon's echo
                    flash.push(fid);
                    savedPending.delete(fid);
                }
                if (flash.length) panel.webview.postMessage({ kind: 'saved-flash', fids: flash });
            }
        };

        const subs: vscode.Disposable[] = [
            // P2 code→doc (§A.3): a bound SOURCE file was edited → map the changed line ranges
            // through this file's bindings to feature ids and spark their doc headings.
            vscode.workspace.onDidChangeTextDocument(e => {
                if (e.document.languageId === 'codoc' || e.contentChanges.length === 0) return;
                const touched = this.featuresTouchedBy(e);
                // P2 fix 4: suppress the spark for features the AGENT owns right now (its own
                // realize writes must not read as "external code drift to review"). The spark is
                // for the user hand-editing code; with no open epoch nothing is filtered.
                const fids = userTouchedFids(touched, {
                    epochOpen: isAgentActive(this.state.activity, this.state.activityMtimeMs),
                    phase: Object.fromEntries(featurePhases(this.state.activity)),
                    held: new Set(heldFeatures(this.state.sidecar)),
                });
                if (!fids.length) return;
                // §A.3: a large change (multi-line or a big replacement) will likely re-question
                // the prose → mark those fids `big` (the doc tick gets divergent-grade weight).
                const big = this.isLargeChange(e) ? fids : undefined;
                panel.webview.postMessage({ kind: 'code-touch', fids, big });
            }),
            this.state.onDidChange(() => post()),
        ];
        CodocTreeEditorProvider.panelByUri.set(document.uri.toString(), panel);
        panel.onDidDispose(() => {
            for (const s of subs) s.dispose();
            // Only clear our own registration — a newer panel may have taken the slot.
            if (CodocTreeEditorProvider.panelByUri.get(document.uri.toString()) === panel) {
                CodocTreeEditorProvider.panelByUri.delete(document.uri.toString());
            }
        });

        panel.webview.onDidReceiveMessage(async msg => {
            switch (msg.kind) {
                case 'ready':
                    post();
                    return;
                case 'doc-settle':
                    await this.settleDoc(document, msg.doc, msg.baselineId);
                    post();  // U2b: no tree.codoc write → repost so the tree pane/badges
                    return;  // reflect the settle now (sourced from the saved doc)
                case 'commit':
                    // Save = stage & send (U4): persist the latest doc (marks drafts), then
                    // hand the staged code-implying edits to the agent in the same turn.
                    await this.settleDoc(document, msg.doc, msg.baselineId);
                    await this.handOff(document);
                    post();
                    return;
                case 'withdraw-realization':
                    await this.withdrawRealization(document, msg.featureId);
                    return;
                case 'hand-off':
                    await this.handOff(document);
                    post();  // drafts cleared → the hand-off button drops on the next paint
                    return;
                case 'tree-move':
                    await this.editMove(document, msg.sourceId, msg.newParentId);
                    post();  // U2b: doc-level move → repost (saved doc leads tree.codoc)
                    return;
                case 'open-binding': {
                    // <module>-level bindings have no symbol to jump to — just open the file.
                    const leafName = (msg.symbol || '').split('::').pop() ?? '';
                    const sym = (leafName === '__module__' || leafName === '<module>' || leafName === '‹module›')
                        ? '' : msg.symbol;
                    await vscode.commands.executeCommand('codoc.openRef', msg.file, sym);
                    return;
                }
                case 'open-code-diff':
                    await this.openCodeDiff(document, msg.files, msg.baseSha, msg.title);
                    return;
                case 'start-daemon':
                    // The recovery offered by the "nothing picked that up" notice. The
                    // command owns the whole decision (trust, provisioning, the replay
                    // lock, the crash-loop budget) so the webview does not have to know
                    // any of it — it only knows the click happened.
                    await vscode.commands.executeCommand('codoc.startDaemon');
                    return;
                case 'open-link':
                    // Consult strand: open the external page in the browser. The
                    // Consult signal is specified as `https://` links only, so a
                    // non-https (e.g. plain http://) url is simply not opened.
                    if (/^https:\/\//.test(msg.url)) await vscode.env.openExternal(vscode.Uri.parse(msg.url));
                    return;
                case 'verdict': {
                    const ids: string[] = Array.isArray(msg.eventIds)
                        ? msg.eventIds
                        : (msg.eventId ? [msg.eventId] : []);
                    // `edits` — the author amended an editable ghost before accepting;
                    // the daemon applies the proposal with the edited text.
                    if (ids.length) this.state.writeVerdict(ids, !!msg.accept, msg.edits);
                    return;
                }
                case 'auto-edit-verdict':
                    // The reader's verdict on an unasked loop rewrite. Keep = the ack
                    // the dwell used to give. Revert = restore the previous wording as
                    // an ordinary authored edit (the daemon classifies it; since the
                    // code already moved, that can queue reconciliation work).
                    await this.resolveAutoEdit(msg.fid, msg.at);
                    post();
                    return;
                case 'translate-tree':
                    // Stage 2 of the language switch: the workspace setting is already
                    // switched (stage 1); this runs the explicit conversion. Progress
                    // arrives via .codoc/translate.json (watched → payload.translation).
                    await this.startTranslate(document, String(msg.code ?? ''));
                    return;
                case 'comment-create':
                    await this.createComment(document, msg.doc, msg.thread, msg.mediaData, msg.mediaMime);
                    post();  // U2b: no tree.codoc write → repost so the marker/threads refresh
                    return;
                case 'comment-edit':
                    await this.editComment(document, msg.id, msg.body);
                    post();
                    return;
                case 'open-session':
                    await this.openSession(msg.sessionId);
                    return;
                case 'launch-agent':
                    // The steer is already on its way (comment-create ran first). This
                    // only decides to run the queue NOW instead of leaving it for the
                    // next sync — see extension.ts's codoc.realize.
                    await vscode.commands.executeCommand('codoc.realize');
                    return;
                case 'comment-resolve':
                    await this.resolveComment(document, msg.doc, msg.id);
                    post();
                    return;
                case 'block-edit':
                    // v6: the webview edited a typed-media block (diagram/latex/…). Hand
                    // it to Loop B's `lower` dispatch through edits.json. A pure move
                    // (ord-only) never sends this — only content edits / adds / removes.
                    await this.handleBlockEdit(document, msg.block);
                    post();  // reflect the queued directive / dropped projection
                    return;
                case 'auto-edit-seen':
                    // The reader dwelled on a feature the loop had rewritten. Persist the
                    // acknowledgement HOST-side so it survives a window reload, and repost
                    // so the mark clears now rather than on the next daemon write.
                    await this.markAutoEditSeen(msg.fid, msg.at);
                    post();
                    return;
                case 'ask-dismiss':
                    // Deleting the file IS the teardown — the overlay owns no other
                    // state, so this cannot half-clear. The watcher reposts with no
                    // `ask`, which is also how a `codoc_walkthrough_clear` from the
                    // agent's side reaches the editor.
                    await this.dismissAsk(document);
                    return;
                case 'set-pref':
                    await this.setPref(document, msg.pref, msg.value);
                    // Normally no repost: the webview already applied the pref
                    // optimistically and persistence is all the host owes. History is the
                    // exception — turning it on is a request for DATA the payload
                    // withholds while it is off (W8 `revisions`), so this one flip has to
                    // go back to the host and return with it.
                    if (msg.pref === 'blame') post();
                    return;
                case 'set-doc-language':
                    await this.setDocLanguage(document, msg.code);
                    post();
                    return;
                case 'bridge-open':
                    // P2 doc→code (§A.1): light the edited feature's bound code — and open
                    // it Beside only when the gesture was explicit (reveal), never from
                    // typing. Absent means an older webview: treat as explicit, the old
                    // behaviour.
                    await this.bridge.open(msg.fid, { reveal: msg.reveal !== false });
                    return;
                case 'bridge-dim':
                    // Caret left the feature (§A.1): clear the code-side highlight (pane stays open).
                    this.bridge.clear(msg.fid);
                    return;
            }
        });
    }

    // ── per-workspace webview prefs (B-U2) ────────────────────────────────────
    //    The glance toggle lives in workspaceState, keyed by document uri so two open
    //    trees don't share state. Decoration-only — it never enters tree.doc.json /
    //    tree.codoc, so the round-trip stays a no-op.

    private prefsFor(document: vscode.TextDocument): WebviewPrefs {
        const all = this.context.workspaceState.get<Record<string, WebviewPrefs>>(PREFS_KEY) ?? {};
        const p = all[document.uri.toString()];
        return { glance: !!p?.glance, blame: !!p?.blame };
    }

    private async setPref(
        document: vscode.TextDocument,
        pref: 'glance' | 'blame',
        value: boolean,
    ): Promise<void> {
        const all = this.context.workspaceState.get<Record<string, WebviewPrefs>>(PREFS_KEY) ?? {};
        const key = document.uri.toString();
        const cur = all[key] ?? { glance: false };
        all[key] = { ...cur, [pref]: value };
        await this.context.workspaceState.update(PREFS_KEY, all);
    }

    /**
     * Change the language the tree is AUTHORED in (`.codoc/config.json`).
     *
     * The daemon re-reads that file on its next pass — there is no cache and no
     * restart — so the switch takes effect on the next node codoc writes. It does
     * NOT retranslate the tree: existing prose is the author's, and an amend to it
     * follows the node's own language, so switching mid-project leaves a bilingual
     * tree bilingual rather than rewriting half of it.
     */
    private async setDocLanguage(
        document: vscode.TextDocument,
        code: string,
    ): Promise<void> {
        // The editor's own document, NOT `window.activeTextEditor` — this runs while a
        // CUSTOM editor holds focus, and a custom editor is not a text editor, so
        // `activeTextEditor` is undefined exactly when this handler fires.
        try {
            await writeDocLanguage(vscode.Uri.joinPath(document.uri, '..'), code);
        } catch (err) {
            // Reported, never swallowed: a silent failure here means the author
            // believes they switched language and every later node disagrees.
            void vscode.window.showErrorMessage(
                `codoc: could not write .codoc/config.json — ${err instanceof Error ? err.message : String(err)}`);
        }
    }

    // ── stage 2 of the language switch: `codoc translate` (two-stage UX) ────────
    //    Stage 1 (set-doc-language) only changes what NEW prose comes out in; the
    //    tree itself is converted by this explicit second gesture. The CLI writes
    //    per-batch progress to .codoc/translate.json (watched → payload.translation),
    //    which is what drives the per-node skeletons and their incremental reveal.

    /** The live `codoc translate` child, if any — one run at a time (the CLI's loop
     *  lock would serialize a second one anyway, in the worst possible way: silently
     *  queued behind the first). */
    private translateChild: cp.ChildProcess | null = null;

    private async startTranslate(document: vscode.TextDocument, code: string): Promise<void> {
        if (!code) return;
        if (this.translateChild && this.translateChild.exitCode === null) return; // already running
        if (!vscode.workspace.isTrusted) return; // same gate as every other spawn (KTD6/R5)
        const rootDir = this.state.rootDir
            ?? path.join(document.uri.fsPath, '..', '..');
        const execs = cachedExecutables(this.context);
        if (!execs) {
            void vscode.window.showErrorMessage(
                'codoc: the CLI is not provisioned yet — run "codoc: Set Up" first, then retry the translation.');
            return;
        }
        const channel = treeEditorChannel();
        const args = ['translate', '--root', rootDir, '--to', code, '--yes'];
        channel.appendLine(`$ ${execs.codoc} ${args.join(' ')}`);
        // Argv-only (shell:false); `code` is a BCP-47 tag from the host-supplied menu.
        const child = cp.spawn(execs.codoc, args, { cwd: rootDir, env: { ...process.env }, shell: false });
        this.translateChild = child;
        child.stdout?.on('data', (b: Buffer) => channel.append(b.toString()));
        child.stderr?.on('data', (b: Buffer) => channel.append(b.toString()));
        child.on('error', err => {
            this.translateChild = null;
            void vscode.window.showErrorMessage(`codoc translate could not start: ${err.message}`);
        });
        child.on('close', exit => {
            this.translateChild = null;
            // The progress file's `finally` write already cleared the skeletons; this
            // is only the human-facing outcome line.
            if (exit !== 0) {
                void vscode.window.showErrorMessage(
                    `codoc translate exited with ${exit} — see the codoc output channel. ` +
                    'Already-translated nodes are saved; running it again resumes.');
            }
        });
    }

    /**
     * The reader's verdict on an unasked loop rewrite (the in-situ auto-edit diff).
     *
     * Either verdict IS the acknowledgement (the mark clears and never returns for
     * this `fid@at`). A revert additionally restores the previous wording through the
     * ordinary authored-command channel — the SAME path a human retyping it would
     * take — so the daemon's classifier decides honestly whether restoring the old
     * claim implies code work now that the code has moved on.
     */
    private async resolveAutoEdit(fid: string, at: string): Promise<void> {
        // Keep is the whole verdict on this side: the rewrite is acknowledged and the
        // strip clears. RESTORE is not emitted here at all.
        //
        // It used to be. The host built a `set_description` carrying the previous
        // wording and left the document alone, so the store kept the loop's sentence,
        // every projection re-rendered it, and a button labelled "Restore mine" changed
        // nothing a reader could see. The restore is now an edit of the DOCUMENT
        // (`structure-commands.restoreFeatureDescription`), which reaches the store by
        // the ordinary settle — with a base_text the editor can vouch for instead of one
        // re-derived from possibly-stale text here, the author's own ink on the words,
        // and the same held-draft gate, since a settle-authored description edit already
        // goes through it. Reverting words still never silently dispatches an agent.
        await this.markAutoEditSeen(fid, at);
    }

    // ── unasked loop rewrites: which ones the reader has caught up on (v6) ──────
    //    Kept HOST-side (workspaceState) rather than in the webview's own state so an
    //    acknowledgement survives a window reload — being told twice about the same
    //    rewrite is exactly the noise this feature exists to avoid. Keyed `fid@at`, so
    //    a LATER rewrite of the same feature is news again rather than pre-acknowledged.
    //    The host also does the filtering: the webview only ever receives rewrites that
    //    are still owed attention, which keeps the catch-up count honest by construction.

    /** Append one answered rewrite to `.codoc/reviewed.host.jsonl` — the same
     *  append-log shape as the verdict and host-op channels, for the same reason: the
     *  webview holds no cross-process lock, and an append cannot erase anything. */
    private async appendReviewed(fid: string, at: string): Promise<void> {
        const root = this.state.rootDir;
        if (!root) return;
        const target = path.join(root, '.codoc', 'reviewed.host.jsonl');
        const line = JSON.stringify({ feature_id: fid, at }) + '\n';
        try {
            await fs.appendFile(target, line, 'utf-8');
        } catch {
            try {
                await fs.mkdir(path.dirname(target), { recursive: true });
                await fs.appendFile(target, line, 'utf-8');
            } catch { /* best effort: the in-memory set is still authoritative here */ }
        }
    }

    private seenAutoEdits(): Set<string> {
        return new Set(this.context.workspaceState.get<string[]>(AUTO_SEEN_KEY) ?? []);
    }

    private async markAutoEditSeen(fid: string, at: string): Promise<void> {
        const seen = this.seenAutoEdits();
        seen.add(fid + '@' + at);
        // And on DISK, because this window's memory is not the only reader.
        //
        // A rewrite is answered by Keep as often as by Restore, and Keep changes nothing
        // else: the document already says what the reader agreed to. Recorded only in
        // workspaceState, that answer was invisible to everything outside this extension
        // host — including the study player, which holds a checkpoint until the things
        // outstanding at it have been answered. A participant clicked Keep, the sidecar
        // still listed the rewrite, and the recording waited out its full fifteen-minute
        // cap before handing over — with the live assistant unreachable until it did,
        // because the handover record is written when playback returns.
        //
        // The daemon ignores this file (watch._classify falls through to `_is_code`, and
        // it is not code), so it costs nothing but the line.
        void this.appendReviewed(fid, at);
        // Bounded: the set is pruned to what is still on offer on every payload, but a
        // hard cap keeps a pathological session from growing the memento without end.
        await this.context.workspaceState.update(AUTO_SEEN_KEY, [...seen].slice(-400));
    }

    /** The rewrites still owed attention, and a pruned seen-set (dropping keys whose
     *  rewrite is no longer offered, so acknowledgements can't accumulate forever). */
    private unseenAutoEdits(sidecar: SidecarData): Record<string, AutoEdit> {
        const all = sidecar.auto_edits ?? {};
        const seen = this.seenAutoEdits();
        const pruned = pruneSeen(seen, all);
        if (pruned.size !== seen.size) void this.context.workspaceState.update(AUTO_SEEN_KEY, [...pruned]);
        const out: Record<string, AutoEdit> = {};
        for (const [fid, e] of Object.entries(all)) {
            if (!pruned.has(editKey(fid, e))) out[fid] = e;
        }
        return out;
    }

    // ── inline comments — span-anchored steering notes (see comment-model.ts) ────
    //    U2b: the host no longer writes tree.codoc, so a comment can't ride the
    //    `> …` text round-trip. Instead it is handed to Loop B as a one-shot STEER
    //    on edits.json (the same channel as authorship annotations); the thread is
    //    marked `sent` (handed off) and lingers in the UI until the realize cycle
    //    settles. The webview owns the anchor mark; the host owns the thread store.

    /** Hand a typed-media block edit to Loop B (v6). Keyed by the STABLE block id
     *  (KTD8), so a move is never a block-edit and a delete+undo nets to nothing.
     *  Loop B dispatches `lower` by the block's declared capability. `mediaData`/
     *  `mediaMime` (an `add`'s image/pdf file, from block-suggestion.ts's file
     *  picker) are written under `.codoc/media/` first — content becomes the
     *  resulting ref, exactly like an `image` block authored any other way. */
    private async handleBlockEdit(
        document: vscode.TextDocument,
        block: { block_id: string; feature_id: string; kind: string;
                 action: 'edit' | 'add' | 'remove'; content?: string; prev_content?: string;
                 mediaData?: string; mediaMime?: string },
    ): Promise<void> {
        if (!block?.block_id || !block.feature_id || !block.kind) return;
        let content = block.content ?? '';
        if (block.mediaData) {
            const ref = await this.writeMediaAttachment(document, block.block_id, block.mediaData, block.mediaMime);
            if (ref) content = ref;
        }
        await this.appendHostOp(document, 'appendBlockEdit', {
            block_id: block.block_id, feature_id: block.feature_id, kind: block.kind,
            action: block.action, content,
            prev_content: block.prev_content ?? '', ts: Date.now(),
        });
    }

    /** Hand a thread's note to Loop B as a one-shot steer, and mark it sent. A
     *  thread carrying a screenshot attachment (U6) rides its stored ref + kind on
     *  the steer; Loop B folds it into the directive as a transient `Consult:` line. */
    private async steerComment(document: vscode.TextDocument, thread: CommentThread): Promise<void> {
        if (!thread.featureId) return;  // a null-fid comment waits for the mint
        await this.appendHostOp(document, 'appendSteer', {
            feature_id: thread.featureId, text: commentNoteText(thread),
            comment_id: thread.id,
            ...(thread.media ? { media: thread.media.ref, media_kind: thread.media.kind } : {}),
            // W8: the steer carries the whole thread, so the daemon can persist it (a
            // comment used to live only in this process's memory) and scope the directive
            // to the code the note actually named.
            body: thread.body,
            anchor_text: thread.anchorText,
            ...(thread.codeRefs?.length ? { code_refs: thread.codeRefs } : {}),
            ...(thread.scope && thread.scope !== 'code' ? { scope: thread.scope } : {}),
            ts: Date.now(),
        });
    }

    /** Persist a comment-screenshot OR block (image/pdf) attachment (U6/Phase 0)
     *  under `.codoc/media/` and return a repo-relative ref the realizing agent
     *  (or a rendered `<img>`, see mediaSrc in buildPayload) can open. Keyed by
     *  `key` (a thread id or block id) so two attachments never collide. Returns
     *  null on any write failure (a missing attachment must not block the edit). */
    private async writeMediaAttachment(document: vscode.TextDocument, key: string, dataB64: string, mime?: string): Promise<string | null> {
        try {
            const ext = (mime?.split('/')[1] || 'png').replace(/[^a-z0-9]/gi, '') || 'png';
            const safe = key.replace(/[^a-zA-Z0-9_-]/g, '') || 'shot';
            const dir = vscode.Uri.joinPath(document.uri, '..', 'media');
            await vscode.workspace.fs.createDirectory(dir);
            await vscode.workspace.fs.writeFile(vscode.Uri.joinPath(dir, `${safe}.${ext}`), Buffer.from(dataB64, 'base64'));
            return path.posix.join('.codoc', 'media', `${safe}.${ext}`);
        } catch { return null; }
    }

    /** Create a comment (U4): store any screenshot attachment, keep the thread in the
     *  live in-memory store (the projection carries the anchor MARK; the body lives
     *  here until U8 promotes it into the store `comments` table), and hand the note
     *  to Loop B as a steer. The host NO LONGER persists tree.doc.json (KTD9). */
    private async createComment(document: vscode.TextDocument, _doc: PMNode, thread: CommentThread, mediaData?: string, mediaMime?: string): Promise<void> {
        const df = this.docFileFor(document);
        let norm: CommentThread = { ...thread, status: 'sent', serialized: true };
        if (mediaData) {
            const ref = await this.writeMediaAttachment(document, thread.id, mediaData, mediaMime);
            if (ref) norm = { ...norm, media: { kind: 'screenshot', ref } };
        }
        df.comments = [...df.comments.filter(c => c.id !== norm.id), norm];
        await this.steerComment(document, norm);
    }

    /** Edit a comment's body (U4) — update the live thread + re-hand the note as a
     *  steer. No tree.doc.json write. */
    private async editComment(document: vscode.TextDocument, id: string, body: string): Promise<void> {
        const df = this.docFileFor(document);
        const t = df.comments.find(c => c.id === id);
        if (!t) return;
        t.body = body;
        await this.steerComment(document, t);
    }

    /** Resolve / delete a comment (U4): drop the thread from the live store. The
     *  projection's comment mark clears when the daemon next renders; no tree.doc.json
     *  write (the daemon is its sole writer). */
    /** Close a thread — durably (W8).
     *
     *  This used to filter an in-memory array and nothing else, so a resolve survived
     *  exactly as long as the tab. It now rides the ordinary host-op log to the daemon,
     *  which marks the stored thread `resolved` rather than deleting it: a resolved
     *  comment is the durable answer to "why does this code look like this" — the note,
     *  the code it named, and the directive it produced — and discarding it at the moment
     *  it becomes history is the one time that record is most worth keeping. The local
     *  filter stays, so the card leaves the margin immediately instead of after a pass. */
    private async resolveComment(document: vscode.TextDocument, _doc: PMNode, id: string): Promise<void> {
        const df = this.docFileFor(document);
        df.comments = df.comments.filter(c => c.id !== id);
        await this.appendHostOp(document, 'resolveComment', { comment_id: id, ts: Date.now() });
    }

    /** Resolve an `image` block's repo-relative `.codoc/media/...` ref (or an
     *  already-absolute `http(s)://` reference) to a URL the webview can load
     *  directly into an `<img src>` — VS Code webviews can't load an arbitrary
     *  local file path, they need `asWebviewUri` translation into the panel's
     *  `vscode-webview://` scheme, scoped to a `localResourceRoots` entry (see
     *  `resolveCustomTextEditor`, which adds `.codoc/media` alongside `dist`).
     *  Returns `undefined` for anything else (e.g. a bare filename with no
     *  resolvable location) so the webview falls back to a placeholder rather
     *  than a broken `<img>`. */
    private mediaSrc(webview: vscode.Webview, document: vscode.TextDocument, ref: string): string | undefined {
        const trimmed = (ref || '').trim();
        if (!trimmed) return undefined;
        if (/^https?:\/\//.test(trimmed)) return trimmed;
        if (!trimmed.startsWith('.codoc/media/')) return undefined;
        // document.uri is .../.codoc/tree.codoc; '..' → .codoc, '..' → repo root.
        const abs = vscode.Uri.joinPath(document.uri, '..', '..', trimmed);
        return webview.asWebviewUri(abs).toString();
    }

    private buildPayload(document: vscode.TextDocument, webview: vscode.Webview): DocPayload {
        const uri = document.uri.toString();
        // U4 (store-authoritative): the webview is a pure PROJECTION CONSUMER. The
        // authoritative rich doc is the daemon-written tree.doc.json (KTD9 — the daemon
        // is its sole writer, rendered from the store projection per U2). The host reads
        // it; it does NOT parse tree.codoc text into a doc, hold a docAhead gate, or
        // re-persist (all removed, R18). The left-pane node tree still derives from the
        // daemon-rendered tree.codoc (read-only export, U6) which is identity-stable.
        const projectionDoc = this.readProjectionDoc(document);
        const { features } = parseTreeCodoc(document.getText());
        const sidecar = this.state.sidecar;
        const status = this.state.status;
        const activity = this.state.activity;
        const activeModes = activeFeatureModes(activity, sidecar, this.state.activityMtimeMs);
        // Effective phase: an explicit signal (editing/reflecting/done) wins;
        // otherwise a feature whose bound file is being written reads as 'editing'
        // so it shimmers immediately, before the hook's explicit mark lands.
        const phases = featurePhases(activity);
        for (const [fid, mode] of activeModes) {
            if (mode === 'write' && !phases.has(fid)) phases.set(fid, 'editing');
        }

        const nodes: Record<string, UINode> = {};
        const roots: string[] = [];
        const childrenOf: Record<string, string[]> = {};
        const depthOf: Record<string, number> = {};

        for (const f of features) {
            if (!f.id) continue;
            const depth = f.parent_id && depthOf[f.parent_id] !== undefined ? depthOf[f.parent_id] + 1 : 0;
            depthOf[f.id] = depth;

            const binds = sidecar.by_feature[f.id] ?? [];
            const meta = sidecar.features[f.id];
            const prop = sidecar.proposals?.by_feature?.[f.id];

            nodes[f.id] = {
                id: f.id,
                title: f.title,
                parent_id: f.parent_id,
                retired: f.retired,
                realized: meta?.realized !== false,
                refCount: binds.length,
                bindings: binds,
                proposal: prop ? {
                    op: prop.op,
                    eventId: prop.event_id,
                    tag: prop.tag,
                    title: prop.title ?? null,
                    description: prop.description ?? null,
                    writesCode: prop.writes_code ?? null,
                    verdictPending: !!prop.verdict_pending,
                } : null,
                depth,
                children: [],
                activeMode: activeModes.get(f.id) ?? null,
                // Present only when this node is the exception to the tree's language
                // (the sidecar omits it otherwise), so a monolingual tree carries no
                // per-row tags and a bilingual one tags exactly the rows that differ.
                ...(meta?.lang ? { lang: meta.lang } : {}),
            };

            if (f.parent_id) {
                (childrenOf[f.parent_id] ??= []).push(f.id);
            } else {
                roots.push(f.id);
            }
        }
        // ── Inject ADD/MOVE proposals as inline ghost rows at their destination
        //    parent (RETIRE/AMEND already decorate their live node via `proposal`). ─
        const byEvent = sidecar.proposals?.by_event ?? {};
        for (const [eventId, p] of Object.entries(byEvent)) {
            const parentId = p.parent_id ?? null;
            const depth = parentId && depthOf[parentId] !== undefined ? depthOf[parentId] + 1 : 0;
            depthOf[eventId] = depth;
            const movedTitle = p.op === 'move' && p.feature_id ? (nodes[p.feature_id]?.title ?? p.title ?? '') : '';
            nodes[eventId] = {
                id: eventId,
                title: p.op === 'move' ? (movedTitle || p.title || '(moved)') : (p.title || '(new feature)'),
                parent_id: parentId,
                retired: false,
                realized: true,
                refCount: 0,
                bindings: [],
                proposal: {
                    op: p.op, eventId, tag: p.tag,
                    title: p.title ?? null, description: p.description ?? null,
                    writesCode: p.writes_code ?? null, verdictPending: !!p.verdict_pending,
                },
                isProposal: true,
                proposalOp: p.op,
                depth,
                children: [],
                activeMode: null,
            };
            if (parentId && nodes[parentId]) {
                insertAtAnchor(childrenOf[parentId] ??= [], eventId, p.after_id, p.before_id);
            } else {
                insertAtAnchor(roots, eventId, p.after_id, p.before_id);
            }
        }

        // All pending proposal event ids → toolbar Accept-all / Reject-all.
        const pendingEventIds = [
            ...Object.values(sidecar.proposals?.by_feature ?? {}).map(p => p.event_id),
            ...Object.keys(byEvent),
        ];

        const rootName = (this.state.rootDir ?? '').split('/').filter(Boolean).pop() ?? 'workspace';

        // Straight from .codoc/config.json (see readDocLanguage for why not the
        // sidecar), so a switch shows up on the very next repaint instead of waiting
        // for a daemon render pass.
        const docLanguage = readDocLanguage(
            path.join(document.uri.fsPath, '..'), sidecar.doc_language?.code);

        // The tree pane mirrors the EDITOR's order exactly — both are the parsed
        // tree.codoc (store) order — so the two line up 1:1 and scroll-spy selects
        // the right row. (Dependency re-ordering the editable doc would fight editing
        // and isn't persisted, so parse order is the single source of truth.)
        for (const id of Object.keys(nodes)) nodes[id].children = childrenOf[id] ?? [];

        const sync: SyncState = {
            state: status.state,
            pending: status.pending,
            activeWrite: [...activeModes].filter(([, m]) => m === 'write').map(([id]) => id),
            activeRead: [...activeModes].filter(([, m]) => m === 'read').map(([id]) => id),
            phase: Object.fromEntries(phases),
            realize: this.parseRealizeProgress(status.detail),
            steps: Object.fromEntries(featureSteps(activity, sidecar, this.state.activityMtimeMs)),   // P2b agent ribbon
            sessionLive: isAgentActive(activity, this.state.activityMtimeMs),  // W3: gates nudge wording
            agent: agentRole(activity),  // W1: which agent owns the epoch
        };

        // Authoritative rich doc (U4): the daemon-written store projection, read above.
        // The host borrows the in-memory comment threads (lifecycle below) but never
        // re-sources structure from text and never re-persists tree.doc.json.
        const prevFile = this.docFileByUri.get(uri) ?? null;
        const doc = projectionDoc;
        // Record the projection's feature units as the identity-keyed baseline the next
        // settle diffs against (commandsForSettle) — replaces the docAhead text compare.
        // Stamp a monotonic baselineId and keep a short history so a settle can cite the
        // EXACT baseline it was computed from (#4), immune to an in-flight projection.
        const baselineId = ++this.baselineSeq;
        this.provenance(document).observe(featureUnits(doc), baselineId);

        // Comment lifecycle: the projection carries the anchor MARKS; the bodies come
        // from the STORE (sidecar `comments`, W8) with this host's not-yet-drained
        // threads layered on top.
        //
        // Before W8 the bodies lived only here, in process memory seeded once from a
        // legacy tree.doc.json: closing the tab lost every note, and the anchor
        // underline outlived the thread it pointed at. The store copy is now the
        // authority; the local copy exists only to cover the window between authoring a
        // note and the daemon's next pass — without it a fresh comment would blink out
        // of the margin and back.
        const stored = storedThreads(this.state.sidecar);
        const rc = reconcileComments(features, prevFile?.comments ?? [], {
            inSync: status.state === 'in_sync',
        });
        const comments = mergeThreads(stored, rc.threads);

        const docFile: DocFile = { version: 1, doc, suggestions: prevFile?.suggestions ?? [], comments: rc.threads };
        this.docFileByUri.set(uri, docFile);

        // Unified pending diffs: code-ahead (from sidecar proposals) + doc-ahead
        // (Old text for amend diffs comes from the parsed features.) Since U3/U2b the
        // human commits directly — there are no doc-ahead suggestions — so this is the
        // agent's code-ahead proposals derived from the sidecar.
        const titleOf = new Map(features.filter(f => f.id).map(f => [f.id as string, f.title]));
        const descOf = new Map(features.filter(f => f.id).map(f => [f.id as string, f.description]));
        const suggestions = buildSuggestions(
            sidecar,
            fid => titleOf.get(fid) ?? '',
            fid => descOf.get(fid) ?? '',
        );

        // Per-feature unified Connections (U4 → U5): Depends-on / Used-by (feature_edges,
        // ranked by coupling weight) + Bound code (by_feature bindings) + Consult (the
        // description's external https:// links). Full ranked lists — the inline line caps
        // each strand at THREADS_COLLAPSE_AT and reports `collapsed`, the peek shows all.
        // reads/usedBy dedup within their own strand (a mutual dependency may appear in both).
        const dir = directedEdges(sidecar);
        const threads: Record<string, ThreadsData> = {};
        for (const f of features) {
            if (!f.id) continue;
            const t = assembleThreads({
                out: dir.out.get(f.id) ?? [],   // {to, weight, kinds} — weight ranks rows
                in: dir.in.get(f.id) ?? [],
                bindings: sidecar.by_feature[f.id] ?? [],
                links: extractLinks(descOf.get(f.id) ?? ''),  // Consult strand (parse-free assembler)
                titleOf: fid => sidecar.features[fid]?.title ?? '',
                selfId: f.id,
            });
            if (t) threads[f.id] = t;
        }

        // Tier-1 hover-preview cards (U4): precompute every ref + feature card from
        // the registry + sidecar host-side (the webview can't read files / call
        // Python). The owning feature's description threads in the gist (the sidecar
        // has none) — keyed by the registry ref's feature_id / the feature id.
        const hoverCards = buildHoverCards(
            this.state.registry,
            sidecar,
            fid => descOf.get(fid) ?? null,
        );

        // Per-feature pitch (B-U1 slice) for glance mode — fall back to the title so a
        // feature with no derived pitch still collapses to a meaningful one-liner.
        const pitches: Record<string, string> = {};
        for (const [fid, meta] of Object.entries(sidecar.features)) {
            pitches[fid] = (meta.pitch && meta.pitch.trim()) ? meta.pitch : meta.title;
        }

        // Agent → human: materialize every pending proposal into the PAYLOAD doc, so a
        // plan is read where it will land rather than in a widget beside it. An AMEND
        // becomes tracked-change marks on the prose it changes; an ADD becomes a real
        // node at the rank it will take, flagged `proposed`; a RETIRE strikes the words
        // it proposes to remove.
        //
        // tree.doc.json (docFile.doc, persisted above) stays the clean human baseline,
        // and three paths keep it that way: the baseline-aware serializer excludes
        // insertion-marked runs, and `featureUnits` / `renderTreeFromDoc` both skip
        // `proposed` nodes. Without those a settle would author the machine's proposal
        // as the reader's own edit (see state/plan-materialize.ts).
        const staged = stagedProposals(suggestions);
        const docForPayload = materializePlan(
            applyAgentProposals(doc, agentAmendsFrom(suggestions)),
            planNodesFrom(staged, key => {
                // Matched on the same key `stagedProposals` files under — the event id for
                // an add, the feature id for anything else. Deriving it a second time by a
                // different rule is how the anchors and the stages drift apart.
                const s = suggestions.find(x =>
                    (x.kind === 'add' ? (x.eventId ?? x.id) : x.featureId) === key);
                return {
                    parentId: s?.parentId ?? null, afterId: s?.afterId ?? null,
                    beforeId: s?.beforeId ?? null, featureId: s?.featureId ?? null,
                    authorId: s?.originRole || 'claude-code',
                };
            }),
        );
        // The settlement model's host half, built from the CLEAN doc — `projected` means
        // "what the daemon last wrote", which is exactly the doc before any of the above.
        const stages = buildStages(doc, staged, this.unseenAutoEdits(sidecar), sidecar.hold_detail ?? {});
        const held = heldFeatures(sidecar);  // hold set — reused for awaitingAI + the draft gate

        // v6: per-feature typed-media blocks for the webview to render below each
        // feature. Persistent only (the sidecar slice already excludes transient);
        // a feature with no typed media is omitted so the map stays small.
        const blocks: Record<string, ReturnType<typeof blocksForFeature>> = {};
        for (const fid of Object.keys(sidecar.features)) {
            const fb = blocksForFeature(sidecar, fid);
            if (!fb.length) continue;
            blocks[fid] = fb.map(b => b.kind === 'image'
                ? { ...b, mediaSrc: this.mediaSrc(webview, document, b.content) }
                : b);
        }

        return {
            nodes,
            roots,
            status: { state: status.state, pending: status.pending },
            sync,
            rootName,
            docLanguage: docLanguage,
            docLanguageChoices: DOC_LANGUAGE_CHOICES,
            pendingEventIds,
            baselineId,
            doc: docForPayload,
            stages,
            symbols: this.buildSymbols(sidecar),
            // v6: what the loop rewrote on its own authority (auto AMENDs only —
            // the sidecar already excludes refresh/attach/detach as machinery).
            autoEdits: this.unseenAutoEdits(sidecar),
            suggestions,
            threads,
            comments,
            hoverCards,
            pitches,
            awaitingAI: held,
            holdDetail: heldDetail(sidecar),
            divergent: divergentFeatures(sidecar),
            // U4: only drafts the daemon is actually HOLDING surface the hand-off action —
            // a prose-only edit produces no directive (never enters `held`), so it commits
            // live and raises no affordance, exactly the "prose commits live; only
            // code-implying drafts" decision.
            drafts: [...(this.draftFidsByUri.get(uri) ?? [])].filter(fid => held.includes(fid)),
            blocks,
            mintedByLocalId: mintedByLocalId(sidecar),
            prefs: this.prefsFor(document),
            history: sidecar.feature_history ?? {},
            // `codoc translate` progress (lease-guarded) — per-node skeletons + the
            // toolbar line. Works whether the run was started from the menu or a
            // terminal: the file is the channel, not the child process.
            ...(this.state.translation ? { translation: this.state.translation } : {}),
            // The /codoc:ask overlay. Always present as a field (null when there is
            // none) rather than omitted, because the webview must be able to tell
            // "dismissed" from "this payload didn't carry one" — omitting it would
            // leave a cleared walkthrough on screen until the next reload.
            ask: this.state.ask,
            // W8: the timeline window rides the payload only while the stance that reads
            // it is on. It is the one slice that carries PROSE — the tree's whole recent
            // edit history, with both sides of every change — and posting that across the
            // webview boundary on every pass would tax every reader for a view most of
            // them are not looking at. `set-pref` reposts when History flips on, which is
            // the one moment the gate can be wrong.
            revisions: this.prefsFor(document).blame ? this.state.revisions : null,
            rev: ++this.rev,
        };
    }

    /**
     * Show what an agent wrote for one change: each touched file, diffed against the
     * commit its directive was handed off at (W8).
     *
     * The tree has always been able to say WHICH code a change touched — bindings are
     * the whole point — and never what the change DID to it. Joining the recorded base
     * commit to those files closes that: "codoc rewrote this description" becomes
     * "…and here are the eleven lines the agent wrote because of it".
     *
     * Several files open through a picker rather than as a fan of tabs. Eleven diff tabs
     * is not a review surface, it is a mess someone has to close.
     */
    private async openCodeDiff(
        document: vscode.TextDocument, files: string[], baseSha: string, title: string,
    ): Promise<void> {
        const root = this.state.rootDir;
        if (!root) return;
        if (!baseSha) {
            // Deliberately explicit rather than silent. The affordance was offered
            // because files were touched; if the anchor is missing the reader is owed
            // the reason, not a click that does nothing.
            void vscode.window.showInformationMessage(
                'codoc did not record which commit this change started from, so it cannot '
                + 'show a diff. Changes made from now on carry one.');
            return;
        }
        const list = (files ?? []).filter(f => !!f);
        if (!list.length) return;
        const pick = list.length === 1
            ? list[0]
            : await vscode.window.showQuickPick(list, {
                title: `Code changed by: ${title}`,
                placeHolder: 'Pick a file to compare against the commit this change started from',
            });
        if (!pick) return;
        const ok = await openPastDiff(root, baseSha, pick, title);
        if (!ok) {
            void vscode.window.showWarningMessage(
                `codoc will not open "${pick}" — it resolves outside the workspace.`);
        }
    }

    /**
     * Open the coding session a change was asked for in (W8).
     *
     * Claude Code stores a session's transcript at
     * `~/.claude/projects/<cwd with separators flattened to '-'>/<session id>.jsonl`.
     * That layout is a convention, not an API, so this is written to degrade rather than
     * assume: no file, no message, or a different agent entirely (`CODOC_AGENT` names
     * codex/gemini/cursor too) all end in the same honest "codoc recorded which session
     * asked for this, but cannot find its transcript" — never a broken editor tab.
     *
     * Opened read-only in a side column, and never `preview: false`: this is evidence a
     * reader glances at on the way back to their work, not a document they are switching
     * to.
     */
    private async openSession(sessionId: string): Promise<void> {
        const root = this.state.rootDir;
        if (!root || !sessionId) return;
        const home = process.env.HOME || process.env.USERPROFILE || '';
        // The id comes from a control file. Anything that is not a plain session id could
        // walk out of the transcript directory, so it is refused rather than sanitized —
        // a mangled id has no correct interpretation.
        if (!home || !/^[A-Za-z0-9._-]+$/.test(sessionId) || sessionId.startsWith('.')) return;
        const slug = path.resolve(root).replace(/[/\\.]/g, '-');
        const file = path.join(home, '.claude', 'projects', slug, `${sessionId}.jsonl`);
        try {
            await fs.access(file);
        } catch {
            void vscode.window.showInformationMessage(
                `codoc recorded that session ${sessionId} asked for this change, but its `
                + 'transcript is not on this machine.');
            return;
        }
        const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(file));
        await vscode.window.showTextDocument(doc, {
            viewColumn: vscode.ViewColumn.Beside, preview: true, preserveFocus: true,
        });
    }

    /** Dismiss the walkthrough overlay by deleting `.codoc/ask.json`.
     *
     *  Safe to do from the host despite the "the host holds no cross-process lock"
     *  rule that keeps it off `edits.json`: there is nothing to read-modify-write
     *  here. The worst a race with a fresh `codoc_walkthrough` can do is leave the
     *  new overlay on screen, which is the harmless direction. */
    private async dismissAsk(document: vscode.TextDocument): Promise<void> {
        const dir = path.dirname(document.uri.fsPath);
        try {
            await vscode.workspace.fs.delete(vscode.Uri.file(path.join(dir, 'ask.json')));
        } catch { /* already gone — dismissing nothing is not a failure */ }
    }

    /** Bound-symbol autocomplete candidates from the sidecar `by_file` (deduped by
     *  file + leaf name) — the same source as the plain-text completion provider. */
    private buildSymbols(sidecar: SidecarData): RefSymbol[] {
        // Deliberately NOT the canonical `symbolLeaf`: strips only the `file::`
        // qualifier and KEEPS `Class.method` (this leaf becomes the `#symbol` link
        // target). Kept in sync with completion.ts:leaf — see the note there.
        const leaf = (s: string): string => { const i = s.indexOf('::'); return i >= 0 ? s.slice(i + 2) : s; };
        const seen = new Set<string>();
        const out: RefSymbol[] = [];
        for (const [file, entries] of Object.entries(sidecar.by_file)) {
            for (const e of entries) {
                const name = leaf(e.symbol);
                const key = `${file}#${name}`;
                if (seen.has(key)) continue;
                seen.add(key);
                out.push({ file, label: name, symbol: name, detail: `${file} · ${e.feature_title}` });
            }
        }
        return out;
    }

    private docUri(document: vscode.TextDocument): vscode.Uri {
        return vscode.Uri.joinPath(document.uri, '..', DOC_FILENAME);
    }

    // ── .codoc/edits.json — the provenance/intent channel to the loops ────────
    //    (schema mirrored by codoc/loop/edits.py; see state/edits-channel.ts)
    //
    // U9 — the host is a SEPARATE process and does not hold the daemon/hub's
    // edits.json cross-process lock, so it MUST NOT read-modify-write edits.json: a
    // lock-less RMW can clobber the daemon's locked RMW (a lost command / hand-off /
    // steer) and its fixed-tmp rename can ENOENT-crash against a concurrent writer.
    // Instead every host write APPENDS one op per line to edits.host.jsonl (O_APPEND is
    // atomic per small write — two windows can even append concurrently), and the daemon
    // MERGES the log into edits.json under the lock at the start of every Loop B pass
    // (codoc/loop/edits.py:merge_host_ops), replaying each op through the same writers.
    // The host still READS edits.json (read is race-free with atomic writes) to seed the
    // draft mirror; it just never writes it.

    private editsUri(document: vscode.TextDocument): vscode.Uri {
        return vscode.Uri.joinPath(document.uri, '..', 'edits.json');
    }

    private hostOpsUri(document: vscode.TextDocument): vscode.Uri {
        return vscode.Uri.joinPath(document.uri, '..', 'edits.host.jsonl');
    }

    private async readEditsFile(document: vscode.TextDocument): Promise<EditsFile> {
        try {
            const bytes = await vscode.workspace.fs.readFile(this.editsUri(document));
            return parseEditsFile(JSON.parse(Buffer.from(bytes).toString('utf-8')));
        } catch {
            return emptyEditsFile();
        }
    }

    /**
     * Append ops to edits.host.jsonl (the IDE→daemon log). Pure append — no read, no
     * lock — so it never races the daemon's locked merge. `fn` names the daemon-side
     * writer (appendCommand / appendSteer / appendBlockEdit / appendCancellation /
     * appendHandoffs / setDrafts); `arg` is its payload (see edits.py:_dispatch_host_op).
     *
     * A batch is written as ONE append. The message handler is async and VS Code does
     * not serialize it, so a per-op append lets a concurrent settle, drag or comment
     * interleave its lines between ours — and the daemon applies in file order, which
     * would then be the wrong order. One write keeps a batch contiguous.
     *
     * Failure is REPORTED, never swallowed. This used to throw into a floating promise:
     * the append vanished, the repost after it never ran, and the editor sat showing
     * "saved" over an edit that had reached nothing.
     */
    private async appendHostOps(
        document: vscode.TextDocument,
        ops: ReadonlyArray<{ fn: string; arg: unknown }>,
    ): Promise<boolean> {
        if (!ops.length) return true;
        const payload = ops.map(op => JSON.stringify(op) + '\n').join('');
        const target = this.hostOpsUri(document).fsPath;
        try {
            await fs.appendFile(target, payload, 'utf-8');
            this.hostOpFailureNotified = false;   // re-arm: a later outage is worth saying again
            return true;
        } catch (first) {
            try {
                // The usual cause is a missing .codoc (a fresh or re-initialized workspace).
                await fs.mkdir(path.dirname(target), { recursive: true });
                await fs.appendFile(target, payload, 'utf-8');
                this.hostOpFailureNotified = false;
                return true;
            } catch {
                this.reportHostOpFailure(first);
                return false;
            }
        }
    }

    private async appendHostOp(document: vscode.TextDocument, fn: string, arg: unknown): Promise<void> {
        await this.appendHostOps(document, [{ fn, arg }]);
    }

    /** Tell the author their edit did not land. Silence here is the worst outcome:
     *  they keep typing into a surface that is no longer recording anything. */
    private reportHostOpFailure(err: unknown): void {
        console.error('[codoc] could not append to edits.host.jsonl', err);
        if (this.hostOpFailureNotified) return;
        this.hostOpFailureNotified = true;
        const detail = err instanceof Error ? err.message : String(err);
        void vscode.window.showErrorMessage(
            `codoc could not record your edit (${detail}). Recent changes to the tree are NOT saved.`,
        );
    }

    /** Mark the held-draft set for a batch of edited feature ids (U4 suggesting mode).
     *  The daemon HOLDS only the code-implying ones (a prose-only edit produces no
     *  directive → nothing held → commits live), so over-marking is harmless; the
     *  hand-off affordance is gated host-side by `drafts ∩ held` (buildPayload). The
     *  in-memory `draftSet` is authoritative for the synchronous buildPayload; we append a
     *  `setDrafts` snapshot of it (the daemon preserves drafts, so a reload re-seeds).
     *  A fresh `add` (no fid yet) is skipped — it has no feature id to hold. */
    private async markDrafts(document: vscode.TextDocument, featureIds: readonly string[]): Promise<void> {
        const fids = featureIds.filter(Boolean);
        if (!fids.length) return;
        const set = this.draftSet(document);
        for (const fid of fids) set.add(fid);
        await this.appendHostOp(document, 'setDrafts', [...set]);
    }

    /** Append identity-keyed commands (U3) as host ops. This is the ONLY channel a
     *  structural/description edit reaches Loop B now — the host never persists
     *  tree.doc.json (KTD9). Idempotent on the store ledger (KTD8): a re-emitted id folds. */
    private async emitCommands(document: vscode.TextDocument, commands: readonly CommandEntry[]): Promise<void> {
        const ok = await this.appendHostOps(document, commands.map(c => ({ fn: 'appendCommand', arg: c })));
        // Only on a successful append: if the edit never reached the log the store never
        // moved, and claiming it did would make the next edit cite text that exists nowhere.
        if (ok) this.provenance(document).record(commands);
    }

    /** Read the daemon-written store projection (tree.doc.json) for this tree (U4/KTD9).
     *  Synchronous so buildPayload stays sync; tolerant — a missing/corrupt projection
     *  degrades to an empty doc (the daemon writes it on the first Loop B pass). */
    private readProjectionDoc(document: vscode.TextDocument): PMNode {
        try {
            const raw = fsSync.readFileSync(this.docUri(document).fsPath, 'utf-8');
            // tree.doc.json is the bare PM doc the daemon renders (build_doc_from_store);
            // parseDocFile tolerates both a bare {type:'doc'} doc and a {doc,…} envelope.
            const parsed = parseDocFile(JSON.parse(raw));
            if (parsed?.doc?.type === 'doc') return parsed.doc;
        } catch { /* not written yet / corrupt → empty doc */ }
        return { type: 'doc', content: [] };
    }

    private async loadDocFile(document: vscode.TextDocument): Promise<DocFile | null> {
        try {
            const bytes = await vscode.workspace.fs.readFile(this.docUri(document));
            return parseDocFile(JSON.parse(Buffer.from(bytes).toString('utf-8')));
        } catch {
            return null; // not created yet
        }
    }

    private docFileFor(document: vscode.TextDocument): DocFile {
        const uri = document.uri.toString();
        let df = this.docFileByUri.get(uri);
        if (!df) {
            df = { version: 1, doc: this.readProjectionDoc(document), suggestions: [], comments: [] };
            this.docFileByUri.set(uri, df);
        }
        return df;
    }

    /**
     * Whole-doc settle (U4 — store-authoritative): the webview is a projection consumer
     * + COMMAND EMITTER. The host diffs the settled doc against the last projection it
     * rendered — KEYED BY IDENTITY (fid, else localId) — and emits the minimal command
     * set (add / set_title / set_description / move / retire) to edits.json (U3). It does
     * NOT persist tree.doc.json (the daemon is its sole writer, KTD9) and never writes
     * tree.codoc. Loop B applies the commands via apply_op (no doc-diff inference) and
     * re-renders both files; the file-watch repaint closes the loop.
     */
    private async settleDoc(document: vscode.TextDocument, doc: PMNode, baselineId?: number): Promise<void> {
        // #4 — diff against the EXACT projection the settle was computed from. The editor
        // stamps that baseline when it ADOPTS a projection and echoes the id back here.
        // Diffing against a newer one instead is the phantom-retire / silent-revert bug: a
        // feature the daemon changed after the editor's baseline reads as a user edit.
        const commands = this.provenance(document).settle(
            featureUnits(doc), baselineId, this.settleToken(),
            () => featureUnits(this.readProjectionDoc(document)));
        if (!commands.length) return;  // no identity-keyed change
        await this.emitCommands(document, commands);
        // W3: remember which existing features this settle edited, so the next
        // projection can acknowledge a prose-only commit with a saved-flash.
        const savedPending = this.savedPending(document);
        for (const c of commands) {
            if ((c.kind === 'set_title' || c.kind === 'set_description') && c.feature_id) {
                savedPending.set(c.feature_id, Date.now());
            }
        }
        // Held-draft gate (U4): mark every touched EXISTING feature a draft so its
        // code-implying directive stays held until hand-off. A retire/add carries its
        // own hand-off semantics (retire is destructive; add mints), so only the
        // amend-style kinds seed the draft set.
        await this.markDrafts(document, commands
            .filter(c => c.kind === 'set_title' || c.kind === 'set_description' || c.kind === 'move')
            .map(c => c.feature_id ?? ''));
    }

    /** Withdraw a queued realization (U6): append a cancellation to edits.json. The
     *  daemon (watching edits.json) wakes Loop B, which prunes the feature's directive
     *  from the queue and releases the hold; the committed prose is kept. No payload
     *  repost — the daemon's resulting sidecar/status write drives the UI refresh. */
    private async withdrawRealization(document: vscode.TextDocument, featureId: string): Promise<void> {
        await this.appendHostOp(document, 'appendCancellation', { feature_id: featureId, ts: Date.now() });
    }

    /** The per-uri edit-provenance book; created on first use. */
    private provenance(document: vscode.TextDocument): EditProvenance {
        const uri = document.uri.toString();
        let p = this.provenanceByUri.get(uri);
        if (!p) { p = new EditProvenance(this.sessionTag); this.provenanceByUri.set(uri, p); }
        return p;
    }

    /** W3: the per-uri saved-flash pending map; created empty on first use. */
    private savedPending(document: vscode.TextDocument): Map<string, number> {
        const uri = document.uri.toString();
        let m = this.savedPendingByUri.get(uri);
        if (!m) { m = new Map(); this.savedPendingByUri.set(uri, m); }
        return m;
    }

    /** The in-memory held-draft set for a doc uri (U4); created empty on first use. */
    private draftSet(document: vscode.TextDocument): Set<string> {
        const uri = document.uri.toString();
        let s = this.draftFidsByUri.get(uri);
        if (!s) { s = new Set<string>(); this.draftFidsByUri.set(uri, s); }
        return s;
    }

    /** Hand ALL held drafts to the agent (U4 — the one batch-commit action): clear the
     *  edits.json `drafts` set. The daemon's next Loop B pass derives every held
     *  directive's `handed_off` as true and writes realize.md (the agent trigger). The
     *  committed prose is untouched. Reconciles with the on-disk drafts first so a draft
     *  marked in another panel is also released. */
    private async handOff(document: vscode.TextDocument): Promise<void> {
        // Held-draft model: a doc AMEND is born HELD; hand-off is the POSITIVE realize
        // signal. Write the currently-held draft fids to `handoffs` (the daemon flips
        // their held directives to handed_off → realize.md) AND clear the drafts set
        // (the "captured" UI drops). Writing handoffs is what actually realizes — under
        // the held-draft model, merely clearing drafts no longer hands anything off.
        const set = this.draftSet(document);
        const fids = [...set];
        set.clear();
        // Write the hand-off requests (the daemon flips their held directives to
        // handed_off → realize.md), then clear the drafts snapshot. Two ops, replayed in
        // order by the daemon's merge.
        await this.appendHostOp(document, 'appendHandoffs', fids);
        await this.appendHostOp(document, 'setDrafts', []);
    }

    /** Loop B / realize stamps progress into status.detail in ONE shape —
     *  "implementing <done>/<total>: <title>" (codoc/loop/sdk_realize.py
     *  format_realize_detail, shared by the MCP realize_progress tool). The parse
     *  is ANCHORED to that `implementing N/M` head so an unrelated `status.detail`
     *  carrying a stray "d/d" (a path, a date, "N change(s)") is never misread as
     *  realize progress. */
    private parseRealizeProgress(detail: string): SyncState['realize'] {
        const m = /^\s*implementing\s+(\d+)\s*\/\s*(\d+)(?:\s*[:\-]\s*(.*))?/i.exec(detail || '');
        if (!m) return undefined;
        return { done: Number(m[1]), total: Number(m[2]), current: (m[3] ?? '').trim() };
    }

    /** P2 code→doc (§A.3): the feature ids a source-file text change touched. Maps each
     *  changed line range → the enclosing declaration → the feature(s) bound to it via this
     *  file's `by_file` bindings. Empty when the file has no bindings or no change hit a
     *  bound decl. Pure logic in state/bridge.ts; this only reads the document + sidecar. */
    private featuresTouchedBy(e: vscode.TextDocumentChangeEvent): string[] {
        const rel = vscode.workspace.asRelativePath(e.document.fileName);
        const fileEntries = this.state.sidecar.by_file[rel];
        if (!fileEntries || fileEntries.length === 0) return [];
        const lineTexts: string[] = [];
        for (let i = 0; i < e.document.lineCount; i++) lineTexts.push(e.document.lineAt(i).text);
        const decls = declLines(lineTexts);
        const changed = new Set<number>();
        for (const c of e.contentChanges) {
            for (const ln of changedLineNumbers(c.range.start.line, c.range.end.line, e.document.lineCount)) {
                changed.add(ln);
            }
        }
        return featureIdsForChangedLines(fileEntries, decls, [...changed]);
    }

    /** §A.3 heuristic: "large enough that Loop A will likely re-question the prose" — a change
     *  that spans multiple lines or replaces/inserts a sizeable chunk. Cheap + deterministic;
     *  the real verdict still comes from Loop A, this only picks the doc tick's weight. */
    private isLargeChange(e: vscode.TextDocumentChangeEvent): boolean {
        const LARGE_CHARS = 80;
        return e.contentChanges.some(c =>
            c.range.end.line > c.range.start.line          // multi-line edit
            || c.text.length >= LARGE_CHARS                // big insertion
            || c.rangeLength >= LARGE_CHARS);              // big deletion/replacement
    }

    /** Move a feature (and its subtree) under a new parent (or to root if null).
     *  U4 (store-authoritative): emit an identity-keyed `move` command (U3) keyed by the
     *  source fid — NOT a doc/text rewrite and no tree.doc.json persist (the daemon is
     *  its sole writer, KTD9). Loop B applies MOVE_NODE via apply_op. A move targeting a
     *  not-yet-minted node (no fid) is dropped: it has no stable store identity to move. */
    private async editMove(document: vscode.TextDocument, sourceId: string, newParentId: string | null): Promise<void> {
        if (!sourceId) return;
        await this.emitCommands(document, [moveCommand(sourceId, newParentId, this.settleToken())]);
        await this.markDrafts(document, [sourceId]);
    }

    private html(webview: vscode.Webview): string {
        const nonce = String(Date.now()) + Math.random().toString(36).slice(2);
        const csp = webview.cspSource;
        const asset = (...p: string[]): vscode.Uri =>
            webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, 'dist', 'webview', ...p));
        const scriptUri = asset('doc-view.js');
        const styleUri = asset('doc-view.css');
        return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${csp} 'unsafe-inline'; script-src 'nonce-${nonce}'; font-src ${csp}; img-src ${csp};" />
<link rel="stylesheet" href="${styleUri}" />
<title>codoc</title>
</head>
<body>
<div id="app"></div>
<script nonce="${nonce}" src="${scriptUri}"></script>
</body>
</html>`;
    }
}
