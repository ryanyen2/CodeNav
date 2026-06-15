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
import { PMNode } from '../state/pm-doc';
import { DocFile, parseDocFile, emptyDocFile, buildSuggestions, Suggestion } from '../state/suggestion-model';
import {
    CommentThread, commentsByFid, injectComments, reconcileComments, reanchorComments,
    stripOrphanComments,
} from '../state/comment-model';
import { directedEdges, agentAmendsByFeature } from '../state/bindings-model';
import { buildOverview } from '../state/overview';
import {
    EditsFile, parseEditsFile, emptyEditsFile,
    annotationsForSettle, intentsFromSuggestions,
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
                if (e.document.uri.toString() === document.uri.toString()) post();
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
                    return;
                case 'suggest-create':
                    await this.createSuggestions(document, msg.suggestions);
                    post();
                    return;
                case 'suggest-withdraw':
                    await this.withdrawSuggestion(document, msg.id);
                    post();
                    return;
                case 'move':
                    await this.editMove(document, msg.sourceId, msg.newParentId);
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
                    // Consult strand: open the external page in the browser.
                    if (/^https?:\/\//.test(msg.url)) await vscode.env.openExternal(vscode.Uri.parse(msg.url));
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
                    return;
                case 'comment-edit':
                    await this.editComment(document, msg.id, msg.body);
                    return;
                case 'comment-resolve':
                    await this.resolveComment(document, msg.doc, msg.id);
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
        return { overviewDismissed: !!p?.overviewDismissed, glance: !!p?.glance };
    }

    private async setPref(
        document: vscode.TextDocument,
        pref: 'overviewDismissed' | 'glance',
        value: boolean,
    ): Promise<void> {
        const all = this.context.workspaceState.get<Record<string, WebviewPrefs>>(PREFS_KEY) ?? {};
        const key = document.uri.toString();
        const cur = all[key] ?? { overviewDismissed: false, glance: false };
        all[key] = { ...cur, [pref]: value };
        await this.context.workspaceState.update(PREFS_KEY, all);
    }

    // ── inline comments — span-anchored steering notes (see comment-model.ts) ────
    //    A comment serializes to a `> …` line under its feature; Loop B drains it
    //    into a STEER directive. The host owns the thread store + the `> …` write;
    //    the webview owns the anchor mark + the composer/popover UI.

    /** Project the doc to canonical tree.codoc, splice in every OPEN comment's
     *  `> …` line, and write it (waking the daemon's Loop B) — but only when the
     *  text actually changed, so a no-op stays a no-op. */
    private async writeTreeWithComments(document: vscode.TextDocument, df: DocFile): Promise<boolean> {
        const next = injectComments(renderTreeFromDoc(df.doc), commentsByFid(df.comments));
        if (next === document.getText()) return false;
        const edit = new vscode.WorkspaceEdit();
        const last = document.lineAt(document.lineCount - 1);
        edit.replace(document.uri, new vscode.Range(0, 0, last.lineNumber, last.text.length), next);
        await vscode.workspace.applyEdit(edit);
        await document.save();
        return true;
    }

    /** Create a comment: persist the doc (with its new anchor mark) + the thread,
     *  then queue the note as a `> …` steering line for the agent. */
    private async createComment(document: vscode.TextDocument, doc: PMNode, thread: CommentThread): Promise<void> {
        const df = this.docFileFor(document);
        df.doc = doc;
        const norm: CommentThread = { ...thread, status: 'open', serialized: false };
        df.comments = [...df.comments.filter(c => c.id !== norm.id), norm];
        await this.persistDocFile(document, df);
        await this.writeTreeWithComments(document, df);
    }

    /** Edit an open comment's body — re-queues the replacing `> …` line. */
    private async editComment(document: vscode.TextDocument, id: string, body: string): Promise<void> {
        const df = this.docFileFor(document);
        const t = df.comments.find(c => c.id === id);
        if (!t || t.status !== 'open') return;
        t.body = body;
        // Keep serialized:true — writeTreeWithComments rewrites the line NOW, so the
        // new note is in the text before any reconcile. Resetting it to false would
        // let a concurrent daemon drain take the "never written" branch and resurrect
        // the note (double-queue); staying true means a drain correctly flips to sent.
        await this.persistDocFile(document, df);
        await this.writeTreeWithComments(document, df);
    }

    /** Resolve / delete a comment: drop the thread + its `> …` line; the doc carries
     *  the anchor-mark removal. */
    private async resolveComment(document: vscode.TextDocument, doc: PMNode, id: string): Promise<void> {
        const df = this.docFileFor(document);
        df.doc = doc;
        df.comments = df.comments.filter(c => c.id !== id);
        await this.persistDocFile(document, df);
        if (!(await this.writeTreeWithComments(document, df))) {
            // no text change (the note was already drained) — still refresh the panel
            this.docFileByUri.set(document.uri.toString(), df);
        }
    }

    private buildPayload(document: vscode.TextDocument): DocPayload {
        const { features } = parseTreeCodoc(document.getText());
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

        // Authoritative rich doc: structure from the text, authorship marks borrowed
        // from the in-memory saved doc by fid (re-anchored where text is unchanged).
        const uri = document.uri.toString();
        const realized = (fid: string): boolean => sidecar.features[fid]?.realized !== false;
        const prevFile = this.docFileByUri.get(uri) ?? null;
        // v4 changes feed → descriptions an agent amended get pencil ink (instead
        // of a mark reset) when their text drifted under the saved doc.
        const reconciled = reconcileDoc(document.getText(), prevFile?.doc ?? null, realized,
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

        // Concept-first overview landing (B-U2): top themes + grounded diagram edges,
        // derived purely from the loaded sidecar (parentless features + feature_edges +
        // the B-U1 pitch). Empty when nothing is parentless.
        const overview = buildOverview(sidecar);

        // Per-feature pitch (B-U1 slice) for glance mode — fall back to the title so a
        // feature with no derived pitch still collapses to a meaningful one-liner.
        const pitches: Record<string, string> = {};
        for (const [fid, meta] of Object.entries(sidecar.features)) {
            pitches[fid] = (meta.pitch && meta.pitch.trim()) ? meta.pitch : meta.title;
        }

        return {
            nodes,
            roots,
            status: { state: status.state, pending: status.pending },
            sync,
            rootName,
            pendingEventIds,
            doc,
            symbols: this.buildSymbols(sidecar),
            suggestions,
            threads,
            comments: docFile.comments,
            hoverCards,
            overview,
            pitches,
            prefs: this.prefsFor(document),
            rev: ++this.rev,
        };
    }

    /** Bound-symbol autocomplete candidates from the sidecar `by_file` (deduped by
     *  file + leaf name) — the same source as the plain-text completion provider. */
    private buildSymbols(sidecar: SidecarData): RefSymbol[] {
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
     * Whole-doc settle (R3): persist the entire edited doc (with marks) to
     * tree.doc.json, then project it to canonical tree.codoc and write the whole
     * file. The existing parse→diff→apply pipeline derives the AMEND / MOVE / ADD /
     * RETIRE ops. Pending code-ahead ghosts are store-side; the daemon re-emits
     * them on its next render (they're not authored doc content).
     */
    private async settleDoc(document: vscode.TextDocument, doc: PMNode): Promise<void> {
        const df = this.docFileFor(document);
        df.doc = doc;
        await this.persistDocFile(document, df);

        // Splice open comments back in so a prose/structure settle never drops a
        // pending `> …` steering note (the residual-#3 fix): renderTreeFromDoc drops
        // comment marks, injectComments re-adds each open thread's line.
        const next = injectComments(renderTreeFromDoc(doc), commentsByFid(df.comments));
        if (next === document.getText()) return; // no structural/text change → no write

        // Tell the loops WHO authored this settle (per changed feature) before the
        // save wakes the daemon. Webview settles are direct human edits (a
        // Suggesting-mode capture goes through suggest-create, never here).
        await this.annotateSettle(document, document.getText(), next);

        const edit = new vscode.WorkspaceEdit();
        const last = document.lineAt(document.lineCount - 1);
        edit.replace(document.uri, new vscode.Range(0, 0, last.lineNumber, last.text.length), next);
        await vscode.workspace.applyEdit(edit);
        await document.save();
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

    /** Loop B / realize may stamp "done/total" progress into status.detail
     *  (e.g. "implementing 2/5: <title>"). Best-effort parse for the doc header. */
    private parseRealizeProgress(detail: string): SyncState['realize'] {
        const m = /(\d+)\s*\/\s*(\d+)(?:\s*[:\-]\s*(.*))?/.exec(detail || '');
        if (!m) return undefined;
        return { done: Number(m[1]), total: Number(m[2]), current: (m[3] ?? '').trim() };
    }

    /** Move a feature (and its subtree) under a new parent (or to root if null). */
    private async editMove(document: vscode.TextDocument, sourceId: string, newParentId: string | null): Promise<void> {
        const text = document.getText();
        const { features } = parseTreeCodoc(text);
        const src = features.find(f => f.id === sourceId);
        if (!src) return;
        if (src.parent_id === newParentId) return;
        // Cycle guard.
        if (newParentId) {
            const blocked = new Set<string>([sourceId]);
            let grew = true;
            while (grew) {
                grew = false;
                for (const f of features) {
                    if (f.id && f.parent_id && blocked.has(f.parent_id) && !blocked.has(f.id)) {
                        blocked.add(f.id); grew = true;
                    }
                }
            }
            if (blocked.has(newParentId)) return;
        }

        const lines = text.split('\n');
        const isMarker = (l: string): boolean => /^\s*[-~]\s+/.test(l);
        const lead = (l: string): number => (/^(\s*)/.exec(l) ?? ['', ''])[1].length;
        const srcIndent = lead(lines[src.line]);

        // Subtree extent: continues while we see lines at deeper indent, until
        // a marker at indent ≤ src or the pending sentinel.
        let subtreeEnd = lines.length - 1;
        for (let i = src.line + 1; i < lines.length; i++) {
            if (lines[i].startsWith('# ── pending changes')) { subtreeEnd = i - 1; break; }
            if (isMarker(lines[i]) && lead(lines[i]) <= srcIndent) { subtreeEnd = i - 1; break; }
        }
        while (subtreeEnd > src.line && lines[subtreeEnd].trim() === '') subtreeEnd--;

        // Compute new indent.
        let newIndent = 0;
        if (newParentId) {
            const np = features.find(f => f.id === newParentId);
            if (!np) return;
            newIndent = lead(lines[np.line]) + 4;
        }
        const delta = newIndent - srcIndent;

        const moved = lines.slice(src.line, subtreeEnd + 1).map(l => {
            if (l.trim() === '') return l;
            if (delta > 0) return ' '.repeat(delta) + l;
            if (delta < 0) {
                const li = lead(l);
                const strip = Math.min(-delta, li);
                return l.slice(strip);
            }
            return l;
        });

        // Cut the subtree.
        const remaining = lines.slice(0, src.line).concat(lines.slice(subtreeEnd + 1));

        // Find insertion point: end of the new parent's subtree (or end of file
        // for root), in the post-cut buffer.
        let insertAt = remaining.length;
        if (newParentId) {
            const remText = remaining.join('\n');
            const remFeatures = parseTreeCodoc(remText).features;
            const np = remFeatures.find(f => f.id === newParentId);
            if (!np) return;
            const npIndent = lead(remaining[np.line]);
            insertAt = remaining.length;
            for (let i = np.line + 1; i < remaining.length; i++) {
                if (remaining[i].startsWith('# ── pending changes')) { insertAt = i; break; }
                if (isMarker(remaining[i]) && lead(remaining[i]) <= npIndent) { insertAt = i; break; }
            }
        }

        const out = remaining.slice(0, insertAt).concat(moved).concat(remaining.slice(insertAt));
        const edit = new vscode.WorkspaceEdit();
        const lastLine = document.lineAt(document.lineCount - 1);
        edit.replace(document.uri,
            new vscode.Range(0, 0, lastLine.lineNumber, lastLine.text.length),
            out.join('\n'));
        await vscode.workspace.applyEdit(edit);
        await document.save();
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
