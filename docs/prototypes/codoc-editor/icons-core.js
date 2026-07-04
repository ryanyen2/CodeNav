// Core line-icon set. Each value is the INNER svg markup.
// Spec: viewBox 0 0 24 24, fill none, stroke currentColor, stroke-width 1.5,
// round caps + joins. Minimalist line style. Inner markup only (no <svg> wrapper).
window.CODOC_ICONS = {
  // Agent working — segmented orbital arc; spins cleanly about (12,12).
  working: `<path d="M12 4a8 8 0 0 1 8 8"/><path d="M20 12a8 8 0 0 1-3.5 6.6"/><path d="M9.5 19.6A8 8 0 0 1 4 12"/>`,

  // Spark — single clean 4-point sparkle (concave star).
  spark: `<path d="M12 3.5c.4 3.9 1.6 5.1 5.5 5.5 -3.9.4-5.1 1.6-5.5 5.5 -.4-3.9-1.6-5.1-5.5-5.5 3.9-.4 5.1-1.6 5.5-5.5Z"/><path d="M18.5 15.5c.2 1.6.7 2.1 2.3 2.3 -1.6.2-2.1.7-2.3 2.3 -.2-1.6-.7-2.1-2.3-2.3 1.6-.2 2.1-.7 2.3-2.3Z"/>`,

  // Paper plane — sent / handed off.
  plane: `<path d="M20.5 3.5 3.7 10.3c-.6.2-.6 1 0 1.2l6.6 2.2 2.2 6.6c.2.6 1 .6 1.2 0L20.5 3.5Z"/><path d="M20.5 3.5 10.3 13.7"/>`,

  // Check in circle — accept / done.
  check: `<circle cx="12" cy="12" r="8.5"/><path d="m8.5 12 2.5 2.5 4.5-5"/>`,

  // X in circle — reject / dismiss.
  x: `<circle cx="12" cy="12" r="8.5"/><path d="m9 9 6 6m0-6-6 6"/>`,

  // Peek — code brackets </>.
  peek: `<path d="m9 8-4 4 4 4"/><path d="m15 8 4 4-4 4"/><path d="m13 6-2 12"/>`,

  // Graph node — ringed dot with two connector stubs.
  node: `<circle cx="12" cy="12" r="3.25"/><path d="M3.5 12h5.25"/><path d="M15.25 12h5.25"/>`,

  // Fountain-pen nib — authoring intent.
  pen: `<path d="M14.5 3.5 5 13l-1.5 7.5L11 19l9.5-9.5a3 3 0 0 0-4.2-4.2Z"/><path d="m5 13 6 6"/><path d="M11 12.5 12.5 14"/>`,

  // Suggest — pencil with dashed underline.
  suggest: `<path d="M16.5 3.5 7 13l-1.2 4.7 4.7-1.2 9.5-9.5a2.5 2.5 0 0 0-3.5-3.5Z"/><path d="m14.5 5.5 3.5 3.5"/><path d="M4 20.5h2.5m2.5 0h2.5m2.5 0H17"/>`,

  // Presence caret — text caret with a small flag/tag at top.
  caret: `<path d="M8 5h8a1 1 0 0 1 .8 1.6L14 10v0a1 1 0 0 0-.2.6"/><path d="M8 5l0 0"/><path d="M11 7v13"/>`,

  // Chevron — single right-pointing; CSS-rotated for expand/collapse.
  chevron: `<path d="m9.5 6 6 6-6 6"/>`,

  // Chain link — external Consult: link.
  link: `<path d="M10 13.5a3.5 3.5 0 0 0 5 0l2.5-2.5a3.5 3.5 0 0 0-5-5L11 7.5"/><path d="M14 10.5a3.5 3.5 0 0 0-5 0L6.5 13a3.5 3.5 0 0 0 5 5L13 16.5"/>`,

  // File — document outline with folded corner.
  file: `<path d="M6 3.5h7l5 5V20a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1Z"/><path d="M13 3.5V8a1 1 0 0 0 1 1h4"/>`,

  // Status dot — filled (allowed exception).
  dot: `<circle cx="12" cy="12" r="3" fill="currentColor" stroke="none"/>`,
};
