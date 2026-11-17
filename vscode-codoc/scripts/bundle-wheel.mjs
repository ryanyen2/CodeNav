// Builds the codoc Python wheel into vscode-codoc/bundled/ so it ships inside
// the VSIX. Setup (src/host/setup/provision.ts) installs that wheel with
// `uv tool install`, which pins codoc itself to the version built here; its
// dependencies come from PyPI.
//
// `npm run bundle-wheel` runs it, and `vscode:prepublish` runs it before every
// package. It needs `uv` on the machine that builds the extension.
import { execFileSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readdirSync, rmSync, mkdirSync, existsSync } from 'node:fs';

const here = dirname(fileURLToPath(import.meta.url));
const extDir = join(here, '..'); // vscode-codoc/
const repoRoot = join(extDir, '..'); // repo root (pyproject.toml lives here)
const bundled = join(extDir, 'bundled');

mkdirSync(bundled, { recursive: true });

// Drop stale wheels so exactly one (current) version ships.
for (const f of readdirSync(bundled)) {
  if (f.endsWith('.whl')) rmSync(join(bundled, f));
}

console.log('Building codoc wheel with uv …');
execFileSync('uv', ['build', '--wheel', '--out-dir', bundled], {
  cwd: repoRoot,
  stdio: 'inherit',
});

// `uv build --out-dir` drops a `.gitignore` containing `*` in the target dir,
// which would shadow the tracked .gitkeep. Remove it — the repo .gitignore
// already ignores bundled/*.whl, and .gitkeep keeps the dir present.
const uvIgnore = join(bundled, '.gitignore');
if (existsSync(uvIgnore)) rmSync(uvIgnore);

const wheels = readdirSync(bundled).filter((f) => f.endsWith('.whl'));
if (wheels.length !== 1) {
  throw new Error(
    `expected exactly one wheel in bundled/, found: ${wheels.join(', ') || '(none)'}`,
  );
}
console.log(`Bundled ${wheels[0]}`);
