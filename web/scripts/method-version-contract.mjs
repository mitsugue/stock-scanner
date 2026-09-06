// Single reader for the two frontend/backend method-version CONTRACTS.
//
// The product verifier compares `methodVersion` with `!==`. Two consumer-side
// pins exist in the frontend (verified market snapshot, asset chart cache), and
// each has a producer-side identity in the backend. When they drift, nothing
// errors visibly: verified snapshots are rejected as `method_incompatible`,
// writeAssetChart returns null, and the release's seed-warm-profile job times
// out. From v13.5.14 to v13.5.53 that is exactly what production did, because
// the old drift test composed the backend value from a hardcoded list of three
// modules while scanner.py had grown a fourth.
//
// So: nothing in the test lane may spell a method version out. Read the
// frontend pins from the TS source and the backend identity from the Python
// composition itself, and compare.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const repoRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)), '..', '..');

const stringLiterals = (expression) =>
  [...expression.matchAll(/'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)"/g)]
    .map((match) => match[1] ?? match[2]).join('');

export function readExportedStringConstant(relativePath, name) {
  const source = fs.readFileSync(path.join(repoRoot, relativePath), 'utf8');
  const match = source.match(
    new RegExp(`export const ${name}\\s*=\\s*([\\s\\S]*?);`, 'm'));
  if (!match) throw new Error(`missing_export:${relativePath}:${name}`);
  // Strip comments before joining literals so a commented-out fragment can
  // never become part of the value.
  const expression = match[1].replace(/\/\/.*$/gm, '').replace(/\/\*[\s\S]*?\*\//g, '');
  const value = stringLiterals(expression);
  if (!value) throw new Error(`empty_constant:${relativePath}:${name}`);
  return value;
}

export function readPythonMethodVersion(moduleName) {
  const source = fs.readFileSync(path.join(repoRoot, `${moduleName}.py`), 'utf8');
  const match = source.match(/^METHOD_VERSION\s*=\s*["']([^"']+)["']/m);
  if (!match) throw new Error(`missing_backend_method_version:${moduleName}`);
  return match[1];
}

/** The modules scanner.py joins into `_VERIFIED_VIEW_METHOD_VERSION`, in order. */
export function backendVerifiedViewModules() {
  const source = fs.readFileSync(path.join(repoRoot, 'scanner.py'), 'utf8');
  const block = source.match(/^_VERIFIED_VIEW_METHOD_VERSION\s*=\s*\(([\s\S]*?)^\)/m);
  if (!block) throw new Error('missing_scanner_verified_view_method_version');
  const modules = [...block[1].matchAll(/\{(\w+)\.METHOD_VERSION\}/g)].map((m) => m[1]);
  if (modules.length < 3) throw new Error('scanner_verified_view_composition_unreadable');
  return modules;
}

export function backendVerifiedViewMethodVersion() {
  return backendVerifiedViewModules().map(readPythonMethodVersion).join(':');
}

/** Every chart-intelligence payload carries argus_chart_intelligence.METHOD_VERSION. */
export function backendChartMethodVersion() {
  return readPythonMethodVersion('argus_chart_intelligence');
}

export function frontendVerifiedViewMethodVersion() {
  return readExportedStringConstant('web/src/lib/verifiedSnapshot.ts', 'VERIFIED_VIEW_METHOD_VERSION');
}

export function frontendChartMethodVersion() {
  return readExportedStringConstant('web/src/lib/assetChartCache.ts', 'ASSET_CHART_METHOD_VERSION');
}

export function methodVersionContract() {
  return {
    verifiedView: {
      frontend: frontendVerifiedViewMethodVersion(),
      backend: backendVerifiedViewMethodVersion(),
      modules: backendVerifiedViewModules(),
    },
    chart: {
      frontend: frontendChartMethodVersion(),
      backend: backendChartMethodVersion(),
    },
  };
}
