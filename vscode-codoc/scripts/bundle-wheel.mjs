// Build the codoc Python wheel and stage it in vscode-codoc/bundled/ so it ships
// inside the VSIX. The extension's provisioning step (src/setup/provision.ts)
// installs this wheel with `uv tool install`, pinning codoc's exact version while
// transitive deps (torch / lancedb / cocoindex) resolve from PyPI.
//
// Run via `npm run bundle-wheel`; wired into `vscode:prepublish` so `vsce package`
// always embeds a fresh wheel. Requires `uv` on the build machine (maintainer-side
// only — end users never run this).
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
