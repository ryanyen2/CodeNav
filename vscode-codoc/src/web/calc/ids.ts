/**
 * ids.ts: the identifiers a page mints.
 *
 * A block id (`b-` + 12 hex) names a block for good. A batch id groups the
 * submissions of one gesture or one settle of typing, and is the undo unit. A
 * client id names one submission so its ack can be matched; the daemon drops
 * a client id it has already seen from this client as a replay, so two pages
 * of one client must never mint the same one. The `page` prefix makes that so.
 */
const page = Math.random().toString(36).slice(2, 8);
let seq = 0;

export const newBid = (): string => `b-${Math.random().toString(16).slice(2, 14).padEnd(12, '0')}`;
export const newBatch = (): string => `t-${Math.random().toString(16).slice(2, 14)}`;
export const newCid = (): string => `ide-${page}-${Date.now().toString(36)}-${(seq++).toString(36)}`;
