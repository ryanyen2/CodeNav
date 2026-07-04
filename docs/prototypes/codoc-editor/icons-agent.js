/* codoc agent marks — "working" loop indicators + agent avatar glyphs.
   Aesthetic: paper-white, restrained, GPU-cheap (transform/opacity only).
   Classic script (no ES modules) — safe under file://. */
window.CODOC_AGENT = {
  workers: [
    /* (a) cdw1 — segmented orbital arc that rotates calmly */
    `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
  <style>
    @keyframes cdw1-spin { to { transform: rotate(360deg); } }
    .cdw1-rot { transform-origin: 12px 12px; animation: cdw1-spin 1.6s cubic-bezier(.45,.05,.55,.95) infinite; }
    @media (prefers-reduced-motion: reduce) { .cdw1-rot { animation: none; } }
  </style>
  <g class="cdw1-rot">
    <path d="M12 4.5 A7.5 7.5 0 0 1 19.5 12" opacity="0.95"/>
    <path d="M12 19.5 A7.5 7.5 0 0 1 5.4 15.6" opacity="0.55"/>
    <path d="M5.1 8 A7.5 7.5 0 0 1 8 5.1" opacity="0.3"/>
  </g>
</svg>`,
    /* (b) cdw2 — 3 dots arranged on a circle, pulsing in sequence */
    `<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" stroke="none">
  <style>
    @keyframes cdw2-pulse { 0%,70%,100% { opacity:.25; transform:scale(.78);} 35% { opacity:1; transform:scale(1);} }
    .cdw2-d { transform-box: fill-box; transform-origin: center; animation: cdw2-pulse 1.5s cubic-bezier(.4,0,.2,1) infinite; }
    .cdw2-d2 { animation-delay: .18s; }
    .cdw2-d3 { animation-delay: .36s; }
    @media (prefers-reduced-motion: reduce) { .cdw2-d { animation: none; opacity:.7; } }
  </style>
  <circle class="cdw2-d cdw2-d1" cx="12" cy="5.4" r="1.9"/>
  <circle class="cdw2-d cdw2-d2" cx="17.7" cy="15" r="1.9"/>
  <circle class="cdw2-d cdw2-d3" cx="6.3" cy="15" r="1.9"/>
</svg>`,
    /* (c) cdw3 — thin ring with a traveling dash ("progress comet") */
    `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
  <style>
    @keyframes cdw3-travel { to { stroke-dashoffset: -56.5; } }
    .cdw3-track { opacity: 0.18; }
    .cdw3-comet { stroke-dasharray: 14 42.5; stroke-dashoffset: 0; animation: cdw3-travel 1.4s linear infinite; }
    @media (prefers-reduced-motion: reduce) { .cdw3-comet { animation: none; } }
  </style>
  <circle class="cdw3-track" cx="12" cy="12" r="9"/>
  <circle class="cdw3-comet" cx="12" cy="12" r="9"/>
</svg>`,
  ],
  avatars: [
    /* faceted cube — isometric, one low-opacity filled top facet */
    `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round">
  <path d="M12 3 20 7.5 20 16.5 12 21 4 16.5 4 7.5 Z"/>
  <path d="M12 3 20 7.5 12 12 4 7.5 Z" fill="currentColor" fill-opacity="0.1"/>
  <path d="M12 12 12 21 M12 12 20 7.5 M12 12 4 7.5"/>
</svg>`,
    /* concentric aperture — nested ring + iris, one filled core */
    `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
  <circle cx="12" cy="12" r="8.5"/>
  <path d="M12 3.5 L16 8.2 M20.1 13.5 L14.4 14.7 M8 21 L9.3 15.3 M4.4 9.5 L9.9 11"/>
  <circle cx="12" cy="12" r="3.2" fill="currentColor" fill-opacity="0.1"/>
</svg>`,
    /* stacked-chevron glyph — three nested chevrons, top one filled */
    `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round">
  <path d="M5 8.5 L12 4 L19 8.5 L12 13 Z" fill="currentColor" fill-opacity="0.1"/>
  <path d="M5 13 L12 17.5 L19 13"/>
  <path d="M5 17.5 L12 22 L19 17.5"/>
</svg>`,
  ],
};
