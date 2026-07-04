/** Pure predicate for the platform "save the file" keyboard chord — ⌘S on mac, Ctrl-S
 *  elsewhere. Factored out of the webview's window-level interceptor (doc-view.ts) so the
 *  chord-recognition logic is unit-testable without a DOM/host (U6 / R11, R12).
 *
 *  The webview swallows this chord from ANY focus context and repurposes it as a commit
 *  (stage & send) — `tree.codoc` is a derived, read-only export and the daemon is the sole
 *  writer, so a native save would only flash the "content is newer" dialog. */
export interface KeyChord {
    readonly metaKey: boolean;
    readonly ctrlKey: boolean;
    readonly key: string;
}

/** True when `ev` is the platform save chord (⌘S on mac, Ctrl-S otherwise). */
export function isSaveChord(ev: KeyChord, isMac: boolean): boolean {
    const mod = isMac ? ev.metaKey : ev.ctrlKey;
    return mod && (ev.key === 's' || ev.key === 'S');
}
