/**
 * tree-center.ts — pure geometry for the tree pane's continuous re-center (U2).
 *
 * Webview-local, DOM-free, so it's unit-testable in vitest's node env (the eased scroll
 * itself lives in doc-view.ts + motion.ts and is verified in the EDH gate, U7).
 */

/** Should an `onActiveFeature` activation re-center the tree? ONLY the scroll-driven spy does;
 *  a caret/selection move just highlights — otherwise typing or arrow-nav animates the tree,
 *  the "tree keeps scrolling jank" the codebase removed (KTD2). */
export function shouldCenter(source: 'scroll' | 'selection'): boolean {
    return source === 'scroll';
}

/**
 * The scrollTop that centers a row in the tree viewport — clamped to the scrollable range,
 * with a center deadband so micro-scrolls don't jitter the pane.
 *
 * Returns `currentScrollTop` unchanged (a no-op) when the row already sits within
 * `deadbandFrac` of viewport-height from the ideal centered position. Otherwise returns the
 * clamped centering target.
 */
export function centerScrollTarget(
    rowOffsetTop: number,
    rowHeight: number,
    viewportHeight: number,
    scrollHeight: number,
    currentScrollTop: number,
    deadbandFrac = 0.15,
): number {
    const ideal = rowOffsetTop - (viewportHeight - rowHeight) / 2;
    const maxScroll = Math.max(0, scrollHeight - viewportHeight);
    const clamped = Math.max(0, Math.min(ideal, maxScroll));
    const deadband = viewportHeight * deadbandFrac;
    if (Math.abs(clamped - currentScrollTop) <= deadband) return currentScrollTop;
    return Math.round(clamped);
}
