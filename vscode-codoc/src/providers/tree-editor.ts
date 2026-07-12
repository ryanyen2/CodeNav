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
import * as fs from 'fs/promises';
import * as fsSync from 'fs';
import * as path from 'path';
import { WorkspaceState } from '../state/workspace-state';
import { parseTreeCodoc, extractLinks } from '../state/tree-model';
import { activeFeatureModes, featurePhases, featureSteps } from '../state/activity-model';
import { PMNode } from '../state/pm-doc';
import { DocFile, parseDocFile, buildSuggestions, Suggestion } from '../state/suggestion-model';
import { applyAgentProposals, agentAmendsFrom } from '../state/agent-proposals';
import { settleCommands, moveCommand, featureUnits, FeatureUnit } from '../state/commands-from-doc';
import {
    CommentThread, commentNoteText, reconcileComments,
} from '../state/comment-model';
import { directedEdges, heldFeatures, heldDetail, divergentFeatures, blocksForFeature, mintedByLocalId } from '../state/bindings-model';
import {
    EditsFile, parseEditsFile, emptyEditsFile, CommandEntry,
} from '../state/edits-channel';
import { assembleThreads } from '../state/threads';
import { buildHoverCards } from '../state/registry-model';
import { BridgeController } from './bridge-controller';
import { declLines, featureIdsForChangedLines, changedLineNumbers, userTouchedFids } from '../state/bridge';
import { isAgentActive } from '../state/activity-model';
import type { SidecarData } from '../state/bindings-model';
import type { DocPayload, UINode, SyncState, RefSymbol, ThreadsData, WebviewPrefs } from '../webview/protocol';

const DOC_FILENAME = 'tree.doc.json';

/** workspaceState key for the per-workspace webview prefs (B-U2: the glance toggle).
 *  One blob per document uri so two open trees keep separate prefs. */
const PREFS_KEY = 'codoc.webviewPrefs';

export class CodocTreeEditorProvider implements vscode.CustomTextEditorProvider {
    public static readonly viewType = 'codoc.tree-editor';

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
    /** The feature units of the last projection rendered to the editor, per uri — the
     *  fallback baseline `settleDoc` diffs against when a settle cites no / an unknown
     *  baseline. Refreshed whenever a new projection is read in buildPayload. */
    private projectedUnitsByUri = new Map<string, FeatureUnit[]>();
    /** Short per-uri history of projection baselines a settle may cite (#4): each payload
     *  is stamped with a monotonic `baselineId` and its feature units are recorded here.
     *  settleDoc diffs against the EXACT baseline the settle cites (the editor's view when
     *  the user typed) — not a newer projection — so a feature the daemon added in flight
     *  is never misread as a user deletion (a phantom retire). Bounded so it can't grow
     *  unbounded across agent realize epochs while the user types. */
    private baselinesByUri = new Map<string, Array<{ id: number; units: FeatureUnit[] }>>();
    private baselineSeq = 0;
    private static readonly BASELINE_HISTORY = 16;
    /** Suggesting-mode DRAFTS (U4), per doc uri: feature ids whose edit the human is
     *  holding as a draft (the daemon keeps their code-implying directive out of the
     *  agent queue until hand-off). The host is the SOLE writer of edits.json `drafts`
     *  (marks on settle, clears on hand-off; the daemon only reads + preserves them), so
     *  this in-memory mirror is authoritative for the synchronous buildPayload. Seeded
     *  from edits.json on editor open so held drafts survive a reload. */
    private draftFidsByUri = new Map<string, Set<string>>();

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
            panel.webview.postMessage({ kind: 'doc', payload: this.buildPayload(document, panel.webview) });
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
        panel.onDidDispose(() => { for (const s of subs) s.dispose(); });

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
                case 'move':
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
                    if (ids.length) this.state.writeVerdict(ids, !!msg.accept);
                    return;
                }
                case 'comment-create':
                    await this.createComment(document, msg.doc, msg.thread, msg.mediaData, msg.mediaMime);
                    post();  // U2b: no tree.codoc write → repost so the marker/threads refresh
                    return;
                case 'comment-edit':
                    await this.editComment(document, msg.id, msg.body);
                    post();
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
                case 'set-pref':
                    await this.setPref(document, msg.pref, msg.value);
                    // No payload repost needed — the webview already applied it
                    // optimistically; persistence is all the host owes here.
                    return;
                case 'bridge-open':
                    // P2 doc→code (§A.1): open the edited feature's bound code Beside + light it.
                    await this.bridge.open(msg.fid);
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
        return { glance: !!p?.glance };
    }

    private async setPref(
        document: vscode.TextDocument,
        pref: 'glance',
        value: boolean,
    ): Promise<void> {
        const all = this.context.workspaceState.get<Record<string, WebviewPrefs>>(PREFS_KEY) ?? {};
        const key = document.uri.toString();
        const cur = all[key] ?? { glance: false };
        all[key] = { ...cur, [pref]: value };
        await this.context.workspaceState.update(PREFS_KEY, all);
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
    private async resolveComment(document: vscode.TextDocument, _doc: PMNode, id: string): Promise<void> {
        const df = this.docFileFor(document);
        df.comments = df.comments.filter(c => c.id !== id);
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
                } : null,
                depth,
                children: [],
                activeMode: activeModes.get(f.id) ?? null,
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
                proposal: { op: p.op, eventId, tag: p.tag, title: p.title ?? null, description: p.description ?? null },
                isProposal: true,
                proposalOp: p.op,
                depth,
                children: [],
                activeMode: null,
            };
            if (parentId && nodes[parentId]) {
                (childrenOf[parentId] ??= []).push(eventId);
            } else {
                roots.push(eventId);
            }
        }

        // All pending proposal event ids → toolbar Accept-all / Reject-all.
        const pendingEventIds = [
            ...Object.values(sidecar.proposals?.by_feature ?? {}).map(p => p.event_id),
            ...Object.keys(byEvent),
        ];

        const rootName = (this.state.rootDir ?? '').split('/').filter(Boolean).pop() ?? 'workspace';

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
        const baselineUnits = featureUnits(doc);
        this.projectedUnitsByUri.set(uri, baselineUnits);
        const baselineId = ++this.baselineSeq;
        const hist = this.baselinesByUri.get(uri) ?? [];
        hist.push({ id: baselineId, units: baselineUnits });
        while (hist.length > CodocTreeEditorProvider.BASELINE_HISTORY) hist.shift();
        this.baselinesByUri.set(uri, hist);

        // Comment lifecycle (U4): the projection carries the anchor MARKS; the thread
        // bodies live in the in-memory store (seeded from the last tree.doc.json, kept
        // live by the comment handlers via the steer channel). Drop feature-gone /
        // settled threads — but no doc mutation / persist (the daemon owns the doc).
        const rc = reconcileComments(features, prevFile?.comments ?? [], {
            inSync: status.state === 'in_sync',
        });

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

        // Agent → human (U4): materialize each code-ahead AMEND as the engine's
        // tracked-change marks in the PAYLOAD doc only. tree.doc.json (docFile.doc,
        // persisted above) stays the clean human baseline; the baseline-aware
        // serializer renders the marked doc back to the same tree.codoc. add/move/
        // retire stay compact widgets (suggestion-decorations.ts).
        const docForPayload = applyAgentProposals(doc, agentAmendsFrom(suggestions));
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
            pendingEventIds,
            baselineId,
            doc: docForPayload,
            symbols: this.buildSymbols(sidecar),
            suggestions,
            threads,
            comments: docFile.comments,
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
            rev: ++this.rev,
        };
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

    /** Append ONE op to edits.host.jsonl (the IDE→daemon log). Pure append — no read, no
     *  lock — so it never races the daemon's locked merge. `fn` names the daemon-side
     *  writer (appendCommand / appendSteer / appendBlockEdit / appendCancellation /
     *  appendHandoffs / setDrafts); `arg` is its payload (see edits.py:_dispatch_host_op). */
    private async appendHostOp(document: vscode.TextDocument, fn: string, arg: unknown): Promise<void> {
        const line = JSON.stringify({ fn, arg }) + '\n';
        await fs.appendFile(this.hostOpsUri(document).fsPath, line, 'utf-8');
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
        for (const c of commands) await this.appendHostOp(document, 'appendCommand', c);
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
        const uri = document.uri.toString();
        // #4 — diff against the EXACT projection the settle was computed from. The webview
        // echoes the payload's baselineId; we look it up in the short history. Diffing
        // against a NEWER projection (whatever last landed in projectedUnitsByUri) is the
        // phantom-retire bug: a feature the daemon added after the editor's baseline would
        // appear in `prev` but not `next` and be misread as a user deletion → a retire.
        const hist = this.baselinesByUri.get(uri) ?? [];
        const fallback = this.projectedUnitsByUri.get(uri) ?? featureUnits(this.readProjectionDoc(document));
        const commands = settleCommands(hist, baselineId, fallback, featureUnits(doc), Date.now());
        if (!commands.length) return;  // no identity-keyed change
        await this.emitCommands(document, commands);
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
        await this.emitCommands(document, [moveCommand(sourceId, newParentId, Date.now())]);
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
