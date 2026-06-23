/**
 * local-id.ts — stable client-side identity for a feature node before the daemon
 * mints its server `fid`.
 *
 * A `featureHeading` authored in the webview has `fid: null` until Loop B mints it.
 * Across that window — and across moves, indent/outdent, type-changes, and undo —
 * we still need a content-independent handle so decorations and gestures never
 * confuse one node for another (the structural half of KTD8: identity is
 * deterministic, never inferred from content). `localId` is that handle. It is
 * minted once at node creation, carried in `tree.doc.json` (the host-owned doc),
 * and preserved by ProseMirror across transforms; `fid` remains the durable server
 * identity once assigned, and the two coexist on the node.
 */

/** Mint a fresh local node id (`lid-…`). Content-independent and collision-safe. */
export function newLocalId(): string {
    const c = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto;
    if (c?.randomUUID) return 'lid-' + c.randomUUID().replace(/-/g, '').slice(0, 12);
    // Fallback (no Web Crypto): time + random is ample for a single-doc session.
    return 'lid-' + Math.abs(Date.now() ^ Math.floor(Math.random() * 1e9)).toString(36);
}
