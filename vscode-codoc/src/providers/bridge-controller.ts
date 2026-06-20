/**
 * bridge-controller.ts — the host side of the live cross-surface diff bridge (P2 / spec §A).
 *
 * Doc→code (§A.1/§A.2): when the webview reports the user is editing a feature's prose
 * (`bridge-open {fid}`), this opens the feature's primary binding file BESIDE (non-focus-
 * stealing, reusing the `codoc.openRef` path) and lights the implicated declaration lines
 * GREEN (the doc moved, the code will follow) — plus a live CodeLens reading
 * `◇ implicated by "<title>"`. Caret-leave (`bridge-dim`) clears the highlight; the pane
 * STAYS open (opening is eager, closing is the user's call). A feature with no symbol-level
 * binding (an unrealized plan) gets the §A.4 file-level "new code will be added here" marker.
 *
 * It does NOT own the code→doc direction (that is a thin watcher in tree-editor.ts pushing
 * `code-touch`); it owns only the doc→code highlight + lens state and the editor that carries it.
 *
 * Binding anchors are the ground truth — no LLM, no doc-parse: the round-trip is untouched.
 */
import * as vscode from 'vscode';
import * as path from 'path';
import { WorkspaceState } from '../state/workspace-state';
import { CodocDecorations, applyBridgeDecorations, BRIDGE_FLASH_MS } from './decoration';
import { primaryBinding, implicatedLeaves, BridgeBinding } from '../state/bridge';
import { isRefResolved } from '../state/registry-model';

/** The persisted "the user dismissed the code pane this session" flag (§A.6) — once set, the
 *  bridge stops auto-opening Beside (it still updates the highlight if the file is visible). */
const BRIDGE_OPEN_KEY = 'codoc.bridgeOpen';

/** Editor reduced-motion: VS Code relays the workbench setting to the host. `auto` resolves to
 *  the OS preference, which the host can't read directly — treat it as "motion ON" (the flash
 *  is a 200ms decoration swap, the gentlest possible). Only an explicit `on` skips the flash. */
function editorReducedMotion(): boolean {
    return vscode.workspace.getConfiguration('workbench').get<string>('reduceMotion') === 'on';
}

/** The live doc→code bridge target: the feature being edited and the code it implicates. */
interface BridgeState {
    fid: string;
    file: string;          // workspace-relative
    title: string;
    leaves: Set<string>;   // implicated declared names in `file`
    fileLevel: boolean;    // §A.4: no symbol binding → file-level "will add code here"
}

export class BridgeController {
    private state: BridgeState | null = null;
    /** The fid+file the lit lines currently belong to — so a plain repaint (sidecar reload,
     *  same target) does NOT re-flash; only a genuine target change does (P2 fix 1). */
    private litSig: string | null = null;
    private flashTimer: ReturnType<typeof setTimeout> | null = null;
    /** Fires when the bridge target changes so the bridge CodeLens provider re-queries. */
    private readonly _onDidChange = new vscode.EventEmitter<void>();
    readonly onDidChange = this._onDidChange.event;

    constructor(
        private readonly context: vscode.ExtensionContext,
        private readonly ws: WorkspaceState,
        private readonly dec: CodocDecorations,
    ) {}

    /** The current bridge target (read by the bridge CodeLens provider). */
    get current(): { file: string; title: string; leaves: Set<string>; fileLevel: boolean } | null {
        return this.state;
    }

    /** Whether the user dismissed the code pane this session (§A.6) — gates auto-open. */
    private get autoOpen(): boolean {
        return this.context.workspaceState.get<boolean>(BRIDGE_OPEN_KEY, true);
    }

    /** Doc→code (§A.1): the user is editing feature `fid`. Resolve its primary binding file,
     *  open it Beside (non-focus-stealing, unless dismissed this session), and light the
     *  implicated lines. A dead/unresolvable binding silently no-ops (§A.6 — the doc already
     *  shows the dead-ref hovercard). */
    async open(fid: string): Promise<void> {
        const binds = (this.ws.sidecar.by_feature[fid] ?? []) as BridgeBinding[];
        const realized = this.ws.sidecar.features[fid]?.realized !== false;
        const title = this.ws.sidecar.features[fid]?.title ?? this.titleFromFeatures(fid);

        const primary = primaryBinding(binds);
        if (primary) {
            // §A.6 dead/unresolvable binding → no-op (don't open a broken split).
            if (!isRefResolved(this.ws.registry, primary.file, primary.symbol || null)) return;
            this.state = {
                fid, file: primary.file, title,
                leaves: implicatedLeaves(binds, primary.file),
                fileLevel: false,
            };
            await this.reveal(primary.file);
        } else if (!realized) {
            // §A.4 no binding + unrealized plan → file-level "new code will be added here".
            // Target file: the nearest sibling/parent binding, else don't open a split.
            const target = this.likelyTargetFile(fid);
            if (!target) { this.clear(fid); return; }
            this.state = { fid, file: target, title, leaves: new Set(), fileLevel: true };
            await this.reveal(target);
        } else {
            // realized but binding-less → nothing to bridge to.
            this.clear(fid);
            return;
        }
        this.paint();          // new target → flash-then-settle (§A.2 + P2 fix 1)
        this._onDidChange.fire();
    }

    /** Caret-leave (§A.1): clear the code-side highlight for `fid` (or unconditionally when
     *  null). The pane stays open. */
    clear(fid: string | null): void {
        if (fid !== null && this.state && this.state.fid !== fid) return; // a newer feature owns the bridge
        this.state = null;
        this.litSig = null;
        this.cancelFlash();
        this.paint();
        this._onDidChange.fire();
    }

    /** Remember that the user dismissed the code pane this session (§A.6) — once set, the bridge
     *  stops auto-opening Beside this session (the highlight still updates if reopened). */
    rememberDismissed(): void {
        void this.context.workspaceState.update(BRIDGE_OPEN_KEY, false);
    }

    /**
     * §A.6 dismiss-memory (P2 fix 3): the host calls this on every visible-editors change. The
     * bridge tracks which files IT has opened; when one of those leaves the visible set while
     * the bridge would still want it shown, the user closed it on purpose → remember the
     * dismissal so we don't auto-reopen it this session. Then repaint (a closed file's
     * decorations are gone with it; an unrelated change just refreshes).
     */
    noteVisibleEditorsChanged(): void {
        const visible = new Set(vscode.window.visibleTextEditors
            .filter(ed => ed.document.languageId !== 'codoc')
            .map(ed => vscode.workspace.asRelativePath(ed.document.fileName)));
        // any bridge-opened file that is no longer visible = an explicit dismissal.
        let dismissed = false;
        for (const f of this.openedByBridge) {
            if (!visible.has(f)) { this.openedByBridge.delete(f); dismissed = true; }
        }
        if (dismissed) this.rememberDismissed();
        this.repaint();
    }

    /** Files the bridge itself opened Beside this session (the dismissal-detection set). */
    private readonly openedByBridge = new Set<string>();

    /** Re-apply the green bridge decorations on every visible editor (public; called by the
     *  host's repaint loop on a sidecar reload / visible-editor change). A plain repaint NEVER
     *  re-flashes (the lit signature is unchanged) — only a genuine target change does. */
    repaint(): void { this.paint(); }

    /**
     * Paint the bridge decorations across visible editors. When the lit target (fid+file)
     * differs from the last paint, the bound file FLASHES (a brighter variant) then settles to
     * the resting variant ~200ms later (P2 fix 1 — "the code responded"); a repaint of the same
     * target just keeps the resting variant. Reduced motion skips the flash (resting directly).
     */
    private paint(): void {
        const sig = this.state ? `${this.state.fid}|${this.state.file}|${this.state.fileLevel ? 'F' : [...this.state.leaves].sort().join(',')}` : null;
        const isNewTarget = sig !== this.litSig;
        this.litSig = sig;
        const flash = isNewTarget && sig !== null && !editorReducedMotion();
        this.cancelFlash();

        for (const ed of vscode.window.visibleTextEditors) {
            if (ed.document.languageId === 'codoc') continue;
            const rel = vscode.workspace.asRelativePath(ed.document.fileName);
            if (this.state && rel === this.state.file) {
                const leaves = this.state.fileLevel ? null : this.state.leaves;
                const fileTitle = this.state.fileLevel ? this.state.title : undefined;
                applyBridgeDecorations(ed, this.dec, leaves, fileTitle, flash ? 'flash' : 'rest');
            } else {
                applyBridgeDecorations(ed, this.dec, null, undefined, 'clear');
            }
        }

        if (flash) {
            // settle the flash → resting on the bridged file after the beat.
            this.flashTimer = setTimeout(() => {
                this.flashTimer = null;
                for (const ed of vscode.window.visibleTextEditors) {
                    if (ed.document.languageId === 'codoc' || !this.state) continue;
                    if (vscode.workspace.asRelativePath(ed.document.fileName) !== this.state.file) continue;
                    applyBridgeDecorations(ed, this.dec, this.state.fileLevel ? null : this.state.leaves,
                        this.state.fileLevel ? this.state.title : undefined, 'rest');
                }
            }, BRIDGE_FLASH_MS);
        }
    }

    private cancelFlash(): void {
        if (this.flashTimer) { clearTimeout(this.flashTimer); this.flashTimer = null; }
    }

    /** Tear down timers + decorations (host deactivate). */
    dispose(): void {
        this.cancelFlash();
        this.state = null; this.litSig = null;
        for (const ed of vscode.window.visibleTextEditors) {
            if (ed.document.languageId !== 'codoc') applyBridgeDecorations(ed, this.dec, null, undefined, 'clear');
        }
    }

    /** Open `relFile` Beside, non-focus-stealing (§A.1) — reuses the openRef behaviour. Skips
     *  the open (just keeps the highlight) when the user dismissed the pane this session. */
    private async reveal(relFile: string): Promise<void> {
        if (!this.ws.rootDir) return;
        // already visible? don't steal focus or churn — the repaint handles the highlight.
        const already = vscode.window.visibleTextEditors.some(
            ed => vscode.workspace.asRelativePath(ed.document.fileName) === relFile);
        if (already) { this.openedByBridge.add(relFile); return; }   // track for dismiss-detection
        if (!this.autoOpen) return;                                  // §A.6: user dismissed this session
        const uri = vscode.Uri.file(path.join(this.ws.rootDir, relFile));
        try {
            const doc = await vscode.workspace.openTextDocument(uri);
            await vscode.window.showTextDocument(doc, {
                viewColumn: vscode.ViewColumn.Beside,
                preserveFocus: true,   // the webview keeps the caret — a calm companion (§A.1)
                preview: true,
            });
            this.openedByBridge.add(relFile);  // §A.6: remember WE opened it so a close = dismissal
        } catch {
            // §A.6: unresolvable file → silent no-op (no broken split).
            this.state = null;
        }
    }

    private titleFromFeatures(fid: string): string {
        return this.ws.features.find(f => f.id === fid)?.title ?? '';
    }

    /** §A.4: the most-likely target file for a not-yet-coded feature — the file of the nearest
     *  sibling/parent binding. Null when none (then no split is opened). */
    private likelyTargetFile(fid: string): string | null {
        const feat = this.ws.features.find(f => f.id === fid);
        const parentId = feat?.parent_id ?? null;
        // a parent's first binding file, else any sibling's.
        const tryFid = (id: string | null): string | null => {
            if (!id) return null;
            const b = this.ws.sidecar.by_feature[id]?.[0];
            return b ? b.file : null;
        };
        const fromParent = tryFid(parentId);
        if (fromParent) return fromParent;
        if (parentId) {
            for (const sib of this.ws.features) {
                if (sib.parent_id === parentId && sib.id !== fid) {
                    const f = tryFid(sib.id);
                    if (f) return f;
                }
            }
        }
        return null;
    }
}
