/**
 * viewer-status.ts — one surface answering one question: what happens to what I type?
 *
 * In VS Code the answer is always the same (you are the maintainer, the daemon is
 * a file away), so this renders nothing there. On the hub it is genuinely
 * uncertain, and the client used to have no way to say so:
 *
 *   • it drew the maintainer's affordances for every viewer, so a read
 *     collaborator's settle came back 403 and the outbox dropped it — correctly,
 *     since a capability you lack never succeeds on retry — with nobody told;
 *   • an offline or restarting hub looked exactly like a working one, so edits
 *     piled into localStorage behind a UI that showed no difference.
 *
 * Both are the same defect: enforcement without legibility, which is silent loss
 * with extra steps. So this owns the whole answer in one place rather than
 * scattering role checks through the shell — a chip that states your role and
 * whether your last edits have landed, and a notice when something was refused.
 *
 * Pure DOM + pure formatting: `describe`/`roleLabel` are unit-tested directly and
 * the mount is a thin shell over them.
 */
import type { Delivery } from './host-bridge';

export interface ViewerInfo {
    capability: 'none' | 'suggest' | 'handoff' | string;
    login: string;
    canSuggest: boolean;
    canHandOff: boolean;
}

/** What this viewer's edits become — said in terms of consequence, not privilege.
 *  "Suggesting" tells you what happens to your words; "read collaborator" tells
 *  you your GitHub permission, which is not the thing you need to know. */
export function roleLabel(v: ViewerInfo | undefined): string {
    if (!v) return '';
    if (v.canHandOff) return 'Editing';
    if (v.canSuggest) return 'Suggesting';
    return 'Read only';
}

/** The one-line explanation behind the chip. */
export function roleHint(v: ViewerInfo | undefined): string {
    if (!v) return '';
    if (v.canHandOff) return 'Your edits apply, and you can hand them to the agent.';
    if (v.canSuggest) return 'Your edits are saved as suggestions. A maintainer hands them to the agent.';
    return 'You can read this tree. Sign in with write access to suggest changes.';
}

/** Delivery, in the user's terms. Returns '' when there is nothing worth saying —
 *  a quiet surface is the resting state, not a permanent "connected" badge nobody
 *  reads. */
export function describe(d: Delivery | undefined): string {
    if (!d || d.state === 'live') return '';
    const n = d.queued;
    const items = `${n} ${n === 1 ? 'change' : 'changes'}`;
    return d.state === 'offline'
        ? `Offline — ${items} waiting`
        : `Saving ${items}…`;
}

/** Why a refusal happened, in terms of what to do about it. `status` is the HTTP
 *  code the hub answered with; the mapping is deliberately coarse, because the
 *  only thing the reader can act on is whether to sign in, ask for access, or
 *  retry later. */
export function rejectionMessage(status: number, v: ViewerInfo | undefined): string {
    if (status === 401) return 'Your session expired — sign in again to keep that edit.';
    if (status === 403) {
        return v && !v.canSuggest
            ? 'That edit needs write access to this repository, so it was not saved.'
            : 'The hub refused that edit — you may not have permission for it.';
    }
    return 'The hub could not accept that edit, so it was not saved.';
}

/**
 * The refusal to announce, if any, given what has already been announced.
 *
 * The bridge reports the last rejection as STATE, not as an event — it stays on
 * every subsequent delivery change so a late subscriber can still see it. Re-
 * announcing on each one would turn a single refusal into a notice on every
 * character typed afterwards, so this returns the notice only when the rejection
 * is one the caller has not seen. Pure, because this is the whole of the logic
 * worth testing and it has no business living inside a DOM closure.
 */
export function noticeFor(
    d: Delivery,
    seen: string | undefined,
    v: ViewerInfo | undefined,
): { key: string; text: string } | null {
    if (!d.rejected) return null;
    const key = `${d.rejected.status}:${JSON.stringify(d.rejected.msg)}`;
    if (key === seen) return null;
    return { key, text: rejectionMessage(d.rejected.status, v) };
}

export interface ViewerStatusHandle {
    setViewer(v: ViewerInfo | undefined): void;
    setDelivery(d: Delivery): void;
}

/**
 * Mount the status chip. `onNotice` receives refusal text so it can reuse the
 * shell's existing transient-notice channel instead of inventing a second one —
 * one place where "your action did not take effect" is said, however it failed.
 */
export function mountViewerStatus(
    parent: HTMLElement,
    onNotice: (text: string) => void,
): ViewerStatusHandle {
    const root = document.createElement('div');
    root.className = 'ce-viewer-status';
    const role = document.createElement('span');
    role.className = 'ce-viewer-role';
    const state = document.createElement('span');
    state.className = 'ce-viewer-delivery';
    root.append(role, state);
    parent.append(root);

    let viewer: ViewerInfo | undefined;
    let seenRejection: string | undefined;

    const paint = (d?: Delivery) => {
        const label = roleLabel(viewer);
        role.textContent = label;
        role.title = roleHint(viewer);
        role.dataset.cap = viewer?.capability ?? '';
        role.hidden = !label;
        const text = describe(d);
        state.textContent = text;
        state.hidden = !text;
        root.dataset.state = d?.state ?? 'live';
        root.hidden = role.hidden && state.hidden;
    };

    paint();
    return {
        setViewer(v) { viewer = v; paint(); },
        setDelivery(d) {
            paint(d);
            const notice = noticeFor(d, seenRejection, viewer);
            if (notice) {
                seenRejection = notice.key;
                onNotice(notice.text);
            }
        },
    };
}
