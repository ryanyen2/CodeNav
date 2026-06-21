import esbuild from 'esbuild';
import { cp, mkdir } from 'fs/promises';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const watch = process.argv.includes('--watch');

/** Copy the bundled webfonts (P1 / spec §E.1) into dist so the webview can reference them
 *  via asWebviewUri. They ship as source under assets/fonts/ and land beside the stylesheet
 *  in dist/webview/fonts/; @font-face in doc-view.css points at ./fonts/*.woff2. esbuild has
 *  no native asset copy, so this small step does it (idempotent; runs each build). */
async function copyFonts() {
  const src = join(__dirname, 'assets', 'fonts');
  const dest = join(__dirname, 'dist', 'webview', 'fonts');
  await mkdir(dest, { recursive: true });
  await cp(src, dest, { recursive: true });
}

/** Assemble the STANDALONE SPA the `codoc serve` hub serves (the "deployed page").
 *  `web/index.html` is the strict-CSP shell; it loads `/assets/doc-view.{js,css}`
 *  + `/assets/fonts/*`. The hub mounts its `--static-dir` at `/assets` and serves
 *  `index.html` as the catch-all, so dropping index.html beside the already-built
 *  doc-view.{js,css}/fonts makes `dist/webview/` a complete servable SPA — the
 *  extension's "Start hub" command points `--static-dir` here. Without this the hub
 *  only serves a placeholder (codoc/serve/app.py:_PLACEHOLDER). Idempotent. */
async function copyWebShell() {
  const dest = join(__dirname, 'dist', 'webview');
  await mkdir(dest, { recursive: true });
  await cp(join(__dirname, 'web', 'index.html'), join(dest, 'index.html'));
}

async function build() {
  // 1. Extension host (Node/CommonJS).
  const ext = await esbuild.context({
    entryPoints: [join(__dirname, 'src', 'extension.ts')],
    bundle: true,
    format: 'cjs',
    platform: 'node',
    outfile: join(__dirname, 'dist', 'extension.js'),
    external: ['vscode'],
    sourcemap: true,
  });

  // 2. Webview client (browser/IIFE). `import './doc-view.css'` emits a sibling
  //    dist/webview/doc-view.css that the custom editor links via asWebviewUri.
  //    The @font-face `url(./fonts/*.woff2)` paths are marked EXTERNAL so esbuild leaves
  //    them verbatim instead of trying to resolve them at bundle time (the .woff2 binaries
  //    live in assets/fonts/, copied to dist/webview/fonts/ by copyFonts() — they resolve
  //    at runtime relative to the served stylesheet's webview URI).
  const web = await esbuild.context({
    entryPoints: [join(__dirname, 'src', 'webview', 'doc-view.ts')],
    bundle: true,
    format: 'iife',
    platform: 'browser',
    outfile: join(__dirname, 'dist', 'webview', 'doc-view.js'),
    external: ['*.woff2'],
    sourcemap: true,
  });

  if (watch) {
    await Promise.all([ext.watch(), web.watch()]);
    await copyFonts();        // watch doesn't re-copy assets, but they rarely change
    await copyWebShell();
    console.log('Watching...');
  } else {
    await Promise.all([ext.rebuild(), web.rebuild()]);
    await copyFonts();
    await copyWebShell();
    await Promise.all([ext.dispose(), web.dispose()]);
  }
}

build().catch(() => process.exit(1));
