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
    await copyFonts();   // watch doesn't re-copy assets, but they rarely change
    console.log('Watching...');
  } else {
    await Promise.all([ext.rebuild(), web.rebuild()]);
    await copyFonts();
    await Promise.all([ext.dispose(), web.dispose()]);
  }
}

build().catch(() => process.exit(1));
