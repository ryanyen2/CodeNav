/**
 * Codoc Tree Editor — a CustomTextEditorProvider that replaces the plain-text
 * view of tree.codoc with a webview: a feature-tree nav pane (left) beside one
 * continuous documentation article (right), every feature a section with its
 * citations woven inline. The .codoc text file remains the source of truth;
 * edits flow back through WorkspaceEdit + document.save() so the daemon's
 * watch / Loop B path is unchanged.
 *
 * This host module builds the DocPayload (tree nodes + ordered doc sections via
 * `layoutDoc` + live sync state from activity.json) and serves the bundled
 * webview client (dist/webview/doc-view.{js,css}). All rendering, scroll-sync,
 * keyboard nav and inline editing live in src/webview/doc-view.ts.
 */

import * as vscode from 'vscode';
import * as fs from 'fs/promises';
import * as path from 'path';
import { WorkspaceState } from '../state/workspace-state';
import { parseTreeCodoc, extractLinks } from '../state/tree-model';
import { activeFeatureModes, featurePhases } from '../state/activity-model';
import { reconcileDoc } from '../state/doc-reconcile';
import { renderTreeFromDoc } from '../state/doc-serialize';
import { moveFeatureInDoc } from '../state/doc-move';
import { PMNode } from '../state/pm-doc';
import { DocFile, parseDocFile, emptyDocFile, buildSuggestions, Suggestion } from '../state/suggestion-model';
import { applyAgentProposals, agentAmendsFrom } from '../state/agent-proposals';
import {
    CommentThread, commentNoteText, reconcileComments, reanchorComments,
    stripOrphanComments,
} from '../state/comment-model';
import { directedEdges, agentAmendsByFeature, heldFeatures, divergentFeatures } from '../state/bindings-model';
import {
    EditsFile, parseEditsFile, emptyEditsFile,
    annotationsForSettle, intentsFromSuggestions, appendCancellation, appendSteer,
} from '../state/edits-channel';
import { assembleThreads } from '../state/threads';
import { buildHoverCards } from '../state/registry-model';
import type { SidecarData } from '../state/bindings-model';
import type { DocPayload, UINode, SyncState, RefSymbol, ThreadsData, WebviewPrefs } from '../webview/protocol';

const DOC_FILENAME = 'tree.doc.json';

/** workspaceState key for the per-workspace webview prefs (B-U2: overview dismiss +
 *  glance toggle). One blob per document uri so two open trees keep separate prefs. */
const PREFS_KEY = 'codoc.webviewPrefs';

export class CodocTreeEditorProvider implements vscode.CustomTextEditorProvider {
    public static readonly viewType = 'codoc.tree-editor';

    constructor(
        private readonly context: vscode.ExtensionContext,
        private readonly state: WorkspaceState,
    ) {}

    private rev = 0;
    /** The authoritative rich doc + persisted doc-ahead suggestions per open
     *  tree.codoc (carries authorship marks). Loaded from tree.doc.json, reconciled
     *  with the text on each payload, and persisted on a user doc-commit. */
    private docFileByUri = new Map<string, DocFile>();
    /** Single-writer (U2b): uris whose saved doc is AHEAD of the on-disk tree.codoc
     *  — the webview committed an edit the daemon hasn't rendered back yet. While set,
     *  buildPayload sources the tree from the saved doc (not the stale text) so a
     *  payload in that window never reverts the user's just-settled edit. Cleared
     *  when tree.codoc catches up. */
    private docAhead = new Set<string>();

    async resolveCustomTextEditor(
        document: vscode.TextDocument,
        panel: vscode.WebviewPanel,
        _token: vscode.CancellationToken,
    ): Promise<void> {
        panel.webview.options = {
            enableScripts: true,
            localResourceRoots: [vscode.Uri.joinPath(this.context.extensionUri, 'dist')],
        };
        panel.webview.html = this.html(panel.webview);

        // Seed the in-memory authoritative doc + suggestions from tree.doc.json
        // (if any) so the first payload already carries authorship marks + diffs.
        const saved = await this.loadDocFile(document);
        if (saved) this.docFileByUri.set(document.uri.toString(), saved);

        const post = (): void => {
            panel.webview.postMessage({ kind: 'doc', payload: this.buildPayload(document) });
        };

        const subs: vscode.Disposable[] = [
            vscode.workspace.onDidChangeTextDocument(e => {
                if (e.document.uri.toString() !== document.uri.toString()) return;
                // U2b: the host never writes tree.codoc now, so this fires only when the
                // DAEMON rendered it — and the daemon yields until Loop B has applied any
                // pending doc edit (safe_write_tree), so the new text is authoritative.
                // Clear docAhead → buildPayload sources from tree.codoc (with minted ids).
                this.docAhead.delete(document.uri.toString());
                post();
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
                    await this.settleDoc(document, msg.doc);
                    post();  // U2b: no tree.codoc write → repost so the tree pane/badges
                    return;  // reflect the settle now (sourced from the saved doc)
                case 'suggest-create':
                    await this.createSuggestions(document, msg.suggestions);
                    post();
                    return;
                case 'suggest-withdraw':
                    await this.withdrawSuggestion(document, msg.id);
                    post();
                    return;
                case 'withdraw-realization':
                    await this.withdrawRealization(document, msg.featureId);
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
                    await this.createComment(document, msg.doc, msg.thread);
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
                case 'set-pref':
                    await this.setPref(document, msg.pref, msg.value);
                    // No payload repost needed — the webview already applied it
                    // optimistically; persistence is all the host owes here.
                    return;
            }
        });
    }

    // ── per-workspace webview prefs (B-U2) ────────────────────────────────────
    //    Overview dismiss + glance toggle live in workspaceState, keyed by document
    //    uri so two open trees don't share state. Decoration-only — they never enter
    //    tree.doc.json / tree.codoc, so the round-trip stays a no-op.

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

    /** Hand a thread's note to Loop B as a one-shot steer, and mark it sent. */
    private async steerComment(document: vscode.TextDocument, thread: CommentThread): Promise<void> {
        if (!thread.featureId) return;  // a null-fid comment waits for the mint
        const file = appendSteer(await this.readEditsFile(document), {
            feature_id: thread.featureId, text: commentNoteText(thread),
            comment_id: thread.id, ts: Date.now(),
        });
        await this.writeEditsFile(document, file);
    }

    /** Create a comment: persist the doc (with its anchor mark) + the thread, and
     *  hand the note to Loop B as a steer. */
    private async createComment(document: vscode.TextDocument, doc: PMNode, thread: CommentThread): Promise<void> {
        const df = this.docFileFor(document);
        df.doc = doc;
        const norm: CommentThread = { ...thread, status: 'sent', serialized: true };
        df.comments = [...df.comments.filter(c => c.id !== norm.id), norm];
        await this.persistDocFile(document, df);
        await this.steerComment(document, norm);
    }

    /** Edit a comment's body — re-hands the replacing note as a steer. */
    private async editComment(document: vscode.TextDocument, id: string, body: string): Promise<void> {
        const df = this.docFileFor(document);
        const t = df.comments.find(c => c.id === id);
        if (!t) return;
        t.body = body;
        await this.persistDocFile(document, df);
        await this.steerComment(document, t);
    }

    /** Resolve / delete a comment: drop the thread; the doc carries the anchor-mark
     *  removal. No tree.codoc write (single-writer). */
    private async resolveComment(document: vscode.TextDocument, doc: PMNode, id: string): Promise<void> {
        const df = this.docFileFor(document);
        df.doc = doc;
        df.comments = df.comments.filter(c => c.id !== id);
        await this.persistDocFile(document, df);
        this.docFileByUri.set(document.uri.toString(), df);  // refresh the panel
    }

    private buildPayload(document: vscode.TextDocument): DocPayload {
        const uri = document.uri.toString();
        // U2b single-writer: when the saved doc LEADS the on-disk tree.codoc (a
        // webview edit the daemon hasn't rendered back yet), source the tree from the
        // saved doc — otherwise a payload triggered in that window (a sidecar/status
        // change) would reconcile from the stale text and revert the user's settle.
        // Clear the flag once tree.codoc catches up.
        const savedDoc = this.docFileByUri.get(uri)?.doc;
        let sourceText = document.getText();
        if (this.docAhead.has(uri) && savedDoc) {
            const savedText = renderTreeFromDoc(savedDoc);
            if (savedText === document.getText()) this.docAhead.delete(uri);
            else sourceText = savedText;
        }
        const { features } = parseTreeCodoc(sourceText);
        const sidecar = this.state.sidecar;
        const status = this.state.status;
        const activity = this.state.activity;
        const activeModes = activeFeatureModes(activity, sidecar);
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
        };

        // Authoritative rich doc: structure from `sourceText` (the saved doc while it
        // leads, else the on-disk text), authorship marks borrowed from the in-memory
        // saved doc by fid (re-anchored where text is unchanged).
        const realized = (fid: string): boolean => sidecar.features[fid]?.realized !== false;
        const prevFile = this.docFileByUri.get(uri) ?? null;
        // v4 changes feed → descriptions an agent amended get pencil ink (instead
        // of a mark reset) when their text drifted under the saved doc.
        const reconciled = reconcileDoc(sourceText, prevFile?.doc ?? null, realized,
            agentAmendsByFeature(sidecar));

        // Comment lifecycle: first re-anchor any null-fid thread whose feature has
        // since been minted (its anchor mark now sits under a fid'd heading), then
        // harvest raw-editor `> …` notes into threads, flip a serialized-then-
        // vanished thread to `sent` (Loop B drained it), drop feature-gone /
        // settled ones. Then GC anchor marks for any dropped thread.
        const anchored = reanchorComments(reconciled, prevFile?.comments ?? []);
        const rc = reconcileComments(features, anchored.threads, {
            inSync: status.state === 'in_sync',
        });
        rc.changed = rc.changed || anchored.changed;
        const liveIds = new Set(rc.threads.map(t => t.id));
        const doc = stripOrphanComments(reconciled, liveIds);

        const docFile: DocFile = { version: 1, doc, suggestions: prevFile?.suggestions ?? [], comments: rc.threads };
        this.docFileByUri.set(uri, docFile);
        if (rc.changed) void this.persistDocFile(document, docFile); // harvest / drain survive a reload

        // Unified pending diffs: code-ahead (from sidecar proposals) + doc-ahead
        // (persisted). Old text for amend diffs comes from the parsed features.
        const titleOf = new Map(features.filter(f => f.id).map(f => [f.id as string, f.title]));
        const descOf = new Map(features.filter(f => f.id).map(f => [f.id as string, f.description]));

        // Re-base each persisted doc-ahead suggestion against the CURRENT text so the
        // card shows `current → proposed` (not a stale baseline), Apply can't clobber
        // a change the loop made meanwhile, and a suggestion auto-clears once the
        // text/code caught up to it (nothing left to change).
        const liveDocAhead = this.rebaseDocAhead(docFile.suggestions, titleOf, descOf);
        if (liveDocAhead.length !== docFile.suggestions.length) {
            docFile.suggestions = liveDocAhead;
            void this.persistDocFile(document, docFile); // auto-clear satisfied ones
            void this.syncIntents(document, docFile);    // …and release their holds
        }
        const suggestions = buildSuggestions(
            sidecar,
            liveDocAhead,
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

        return {
            nodes,
            roots,
            status: { state: status.state, pending: status.pending },
            sync,
            rootName,
            pendingEventIds,
            doc: docForPayload,
            symbols: this.buildSymbols(sidecar),
            suggestions,
            threads,
            comments: docFile.comments,
            hoverCards,
            pitches,
            awaitingAI: heldFeatures(sidecar),
            divergent: divergentFeatures(sidecar),
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

    private editsUri(document: vscode.TextDocument): vscode.Uri {
        return vscode.Uri.joinPath(document.uri, '..', 'edits.json');
    }

    private async readEditsFile(document: vscode.TextDocument): Promise<EditsFile> {
        try {
            const bytes = await vscode.workspace.fs.readFile(this.editsUri(document));
            return parseEditsFile(JSON.parse(Buffer.from(bytes).toString('utf-8')));
        } catch {
            return emptyEditsFile();
        }
    }

    private async writeEditsFile(document: vscode.TextDocument, file: EditsFile): Promise<void> {
        const target = this.editsUri(document).fsPath;
        const tmp = path.join(path.dirname(target), '.edits.json.tmp');
        await fs.writeFile(tmp, JSON.stringify(file, null, 2), 'utf-8');
        await fs.rename(tmp, target);
    }

    /** Append per-feature authorship annotations for a settle. Written BEFORE the
     *  tree.codoc save so the daemon's Loop B pass (woken by that save) already
     *  sees them. Loop B drains `edits`; `intents` stay host-owned. */
    private async annotateSettle(
        document: vscode.TextDocument,
        prevText: string,
        nextText: string,
        opts: { actor?: string; mode?: string; suggestionId?: string } = {},
    ): Promise<void> {
        const anns = annotationsForSettle(
            parseTreeCodoc(prevText).features,
            parseTreeCodoc(nextText).features,
            { actor: opts.actor ?? 'human', mode: opts.mode ?? 'pen',
              suggestionId: opts.suggestionId, ts: Date.now() },
        );
        if (!anns.length) return;
        const file = await this.readEditsFile(document);
        file.edits.push(...anns);
        await this.writeEditsFile(document, file);
    }

    /** Rewrite the intents list (the doc-wins hold set) from the current persisted
     *  doc-ahead suggestions — create/withdraw/apply/auto-clear all converge here. */
    private async syncIntents(document: vscode.TextDocument, df: DocFile): Promise<void> {
        const file = await this.readEditsFile(document);
        const next = intentsFromSuggestions(df.suggestions, Date.now());
        const same = JSON.stringify(file.intents.map(i => [i.id, i.feature_id])) ===
                     JSON.stringify(next.map(i => [i.id, i.feature_id]));
        if (same) return;
        file.intents = next;
        await this.writeEditsFile(document, file);
    }

    private async loadDocFile(document: vscode.TextDocument): Promise<DocFile | null> {
        try {
            const bytes = await vscode.workspace.fs.readFile(this.docUri(document));
            return parseDocFile(JSON.parse(Buffer.from(bytes).toString('utf-8')));
        } catch {
            return null; // not created yet
        }
    }

    private docFileWriteSeq = 0;

    /** Persist tree.doc.json (doc + doc-ahead suggestions + comments) atomically
     *  (tmp → rename). Watched by neither the VS Code WorkspaceState nor the
     *  Python daemon, so this write never loops back. The tmp name carries a
     *  per-write counter: buildPayload can fire two fire-and-forget persists in one
     *  pass (comment reconcile + suggestion rebase), and a shared tmp path would
     *  let the second writeFile clobber the first mid-rename. */
    private async persistDocFile(document: vscode.TextDocument, docFile: DocFile): Promise<void> {
        const target = this.docUri(document).fsPath;
        const tmp = path.join(path.dirname(target), `.${DOC_FILENAME}.${++this.docFileWriteSeq}.tmp`);
        await fs.writeFile(tmp, JSON.stringify(docFile), 'utf-8');
        await fs.rename(tmp, target);
    }

    private docFileFor(document: vscode.TextDocument): DocFile {
        const uri = document.uri.toString();
        let df = this.docFileByUri.get(uri);
        if (!df) {
            df = emptyDocFile(reconcileDoc(document.getText(), null));
            this.docFileByUri.set(uri, df);
        }
        return df;
    }

    /**
     * Whole-doc settle (R3 / U2b single-writer): persist the entire edited doc (with
     * marks) to tree.doc.json — and that is ALL the host writes. It does NOT touch
     * tree.codoc; the daemon is the sole writer of that file. Loop B learns this edit
     * from tree.doc.json (parse_doc_file) — woken by the doc.json write (the daemon
     * watches it) and the authorship annotation below — applies the AMEND/MOVE/ADD/
     * RETIRE op, and re-renders tree.codoc itself. Removing the host's
     * applyEdit+document.save() is what fully closes the "content is newer" conflict.
     */
    private async settleDoc(document: vscode.TextDocument, doc: PMNode): Promise<void> {
        const df = this.docFileFor(document);
        df.doc = doc;
        await this.persistDocFile(document, df);

        const next = renderTreeFromDoc(doc);
        const uri = document.uri.toString();
        if (next === document.getText()) { this.docAhead.delete(uri); return; } // no change

        // Tell the loops WHO authored this settle (per changed feature). The diff is
        // prev on-disk text (== store state) vs the new doc render.
        await this.annotateSettle(document, document.getText(), next);
        // The saved doc now leads the on-disk tree.codoc until the daemon renders it
        // back — buildPayload sources from the saved doc meanwhile (no reversion).
        this.docAhead.add(uri);
    }

    /** Re-base persisted doc-ahead suggestions onto the current text + drop the ones
     *  whose change is already present (satisfied). A field with no intended change
     *  tracks the current value so a drifting title can't manufacture a phantom diff. */
    private rebaseDocAhead(
        suggestions: Suggestion[],
        titleOf: Map<string, string>,
        descOf: Map<string, string>,
    ): Suggestion[] {
        const out: Suggestion[] = [];
        for (const s of suggestions) {
            if (s.direction !== 'doc-ahead') { out.push(s); continue; }
            if (!s.featureId || !titleOf.has(s.featureId)) continue; // feature gone → drop
            const curTitle = titleOf.get(s.featureId) ?? '';
            const curDesc = descOf.get(s.featureId) ?? '';
            const titleIntended = (s.titleNew ?? '') !== (s.titleOld ?? '');
            const descIntended = (s.descNew ?? '') !== (s.descOld ?? '');
            const r: Suggestion = {
                ...s,
                titleOld: curTitle,
                titleNew: titleIntended ? s.titleNew : curTitle,
                descOld: curDesc,
                descNew: descIntended ? s.descNew : curDesc,
            };
            if ((r.titleNew ?? '') !== r.titleOld || (r.descNew ?? '') !== r.descOld) out.push(r); // still has a change
        }
        return out;
    }

    /** Persist captured doc-ahead suggestions (Suggesting mode). They render as
     *  persistent diffs awaiting the agent; tree.codoc is NOT touched (intent only).
     *  Title and description changes for the same feature are MERGED into one card. */
    private async createSuggestions(document: vscode.TextDocument, suggestions: Suggestion[]): Promise<void> {
        if (!suggestions.length) return;
        const df = this.docFileFor(document);
        for (const s of suggestions) {
            const existing = df.suggestions.find(x => x.direction === 'doc-ahead' && x.featureId === s.featureId);
            if (!existing) { df.suggestions.push(s); continue; }
            // Compose: a desc-only capture must not erase a prior title change.
            if ((s.titleNew ?? '') !== (s.titleOld ?? '')) { existing.titleOld = s.titleOld; existing.titleNew = s.titleNew; }
            if ((s.descNew ?? '') !== (s.descOld ?? '')) { existing.descOld = s.descOld; existing.descNew = s.descNew; }
            existing.id = s.id;
        }
        await this.persistDocFile(document, df);
        await this.syncIntents(document, df); // register the doc-wins holds
    }

    private async withdrawSuggestion(document: vscode.TextDocument, id: string): Promise<void> {
        const df = this.docFileFor(document);
        df.suggestions = df.suggestions.filter(s => s.id !== id);
        await this.persistDocFile(document, df);
        await this.syncIntents(document, df); // release the hold
    }

    /** Withdraw a queued realization (U6): append a cancellation to edits.json. The
     *  daemon (watching edits.json) wakes Loop B, which prunes the feature's directive
     *  from the queue and releases the hold; the committed prose is kept. No payload
     *  repost — the daemon's resulting sidecar/status write drives the UI refresh. */
    private async withdrawRealization(document: vscode.TextDocument, featureId: string): Promise<void> {
        const file = appendCancellation(await this.readEditsFile(document), featureId, Date.now());
        await this.writeEditsFile(document, file);
    }

    /** Loop B / realize may stamp "done/total" progress into status.detail
     *  (e.g. "implementing 2/5: <title>"). Best-effort parse for the doc header. */
    private parseRealizeProgress(detail: string): SyncState['realize'] {
        const m = /(\d+)\s*\/\s*(\d+)(?:\s*[:\-]\s*(.*))?/.exec(detail || '');
        if (!m) return undefined;
        return { done: Number(m[1]), total: Number(m[2]), current: (m[3] ?? '').trim() };
    }

    /** Move a feature (and its subtree) under a new parent (or to root if null).
     *  U2b: a pure transform on the authored doc (moveFeatureInDoc) persisted to
     *  tree.doc.json — NOT a tree.codoc text rewrite. Loop B derives the MOVE_NODE
     *  from parse_doc_file; the daemon renders tree.codoc. */
    private async editMove(document: vscode.TextDocument, sourceId: string, newParentId: string | null): Promise<void> {
        const df = this.docFileFor(document);
        const moved = moveFeatureInDoc(df.doc, sourceId, newParentId);
        if (!moved) return;  // no-op / invalid / cycle
        df.doc = moved;
        await this.persistDocFile(document, df);
        await this.annotateSettle(document, document.getText(), renderTreeFromDoc(moved));
        this.docAhead.add(document.uri.toString());
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
