import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repo = path.resolve(root, '..');
const source = fs.readFileSync(path.join(root, 'src/domain/runtimeVersionTruth.ts'), 'utf8');
const viteSource = fs.readFileSync(path.join(root, 'vite.config.ts'), 'utf8');
const hookSource = fs.readFileSync(
  path.join(root, 'src/hooks/useProductionBackendIdentity.ts'), 'utf8');
const shellSource = fs.readFileSync(path.join(root, 'src/components/AppShell.tsx'), 'utf8');
const output = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  fileName: 'runtimeVersionTruth.ts',
}).outputText;
const truth = await import(`data:text/javascript;base64,${Buffer.from(output).toString('base64')}`);

const manifest = {
  schema: 'argus-production-release-manifest-v1',
  service: 'argus-backend',
  environment: 'production',
  verifiedHealth: true,
  verifiedReady: true,
  version: '13.4.5',
  buildSha: '183b940c08505f1373a3b34b0c7fc2bc37bbae90',
  deploymentId: 'dep-d9rrkmgae00c73a9acl0',
  deployedAt: '2026-08-08T23:52:52Z',
};
const identity = truth.parseProductionBackendIdentity(manifest);
assert.deepEqual(identity, {
  backendVersion: '13.4.5',
  backendSha: '183b940c08505f1373a3b34b0c7fc2bc37bbae90',
  deploymentId: 'dep-d9rrkmgae00c73a9acl0',
  deployedAt: '2026-08-08T23:52:52Z',
});
const productManifest = JSON.parse(fs.readFileSync(path.join(repo, 'product-version.json'), 'utf8'));
assert.deepEqual(productManifest, {
  schemaVersion: 'argus-product-version-v1', productVersion: 'v13.5.57',
});

const baseTruth = {
  productVersion: productManifest.productVersion,
  frontendVersion: '13.3.6',
  frontendBuildSha: '183b940c08505f1373a3b34b0c7fc2bc37bbae90',
  backendVersion: identity.backendVersion,
  backendBuildSha: identity.backendSha,
};
assert.equal(truth.runtimeVersionLabel(baseTruth.productVersion), 'v13.5.57');
assert.deepEqual(truth.runtimeVersionTruth(baseTruth), {
  productVersion: 'v13.5.57',
  frontendVersion: '13.3.6',
  frontendBuildSha: '183b940c08505f1373a3b34b0c7fc2bc37bbae90',
  backendVersion: '13.4.5',
  backendBuildSha: '183b940c08505f1373a3b34b0c7fc2bc37bbae90',
});
assert.equal(truth.runtimeVersionTruth({
  ...baseTruth, frontendVersion: '14.8.1',
}).productVersion, 'v13.5.57');
assert.equal(truth.runtimeVersionTruth({
  ...baseTruth, backendVersion: '15.0.0',
}).productVersion, 'v13.5.57');
assert.deepEqual(
  [truth.runtimeVersionTruth(baseTruth).frontendVersion,
    truth.runtimeVersionTruth(baseTruth).backendVersion],
  ['13.3.6', '13.4.5'],
);

for (const malformed of [
  null,
  {},
  { ...manifest, schema: 'wrong' },
  { ...manifest, version: '13.4' },
  { ...manifest, buildSha: 'short' },
  { ...manifest, deploymentId: 'github-main-123' },
  { ...manifest, deployedAt: 'yesterday' },
  { ...manifest, verifiedReady: false },
]) {
  assert.equal(truth.parseProductionBackendIdentity(malformed), null);
}
assert.equal(truth.runtimeVersionLabel('malformed'), 'product version unavailable');
assert.equal(truth.runtimeVersionTruth({
  ...baseTruth, productVersion: '13.3.6',
}).productVersion, null);
assert.match(viteSource, /new URL\('\.\.\/product-version\.json'/);
assert.match(viteSource, /throw new Error\('invalid canonical product-version\.json'\)/);
assert.doesNotMatch(viteSource, /productVersion[^\n]*readVersion\(\)/);
assert.equal((shellSource.match(/runtimeVersionLabel\(__PRODUCT_VERSION__\)/g) ?? []).length, 1);
assert.match(shellSource,
  /A\.R\.G\.U\.S\. <span className="shell__brand-pro">Pro<\/span>[\s\S]*\{versionLabel\}/);
assert.doesNotMatch(shellSource, /shell__brand[\s\S]{0,500}__APP_VERSION__/);

assert.match(hookSource, /if \(!navigator\.onLine\)[\s\S]*return;/);
assert.ok(hookSource.indexOf('if (!navigator.onLine)') < hookSource.indexOf('await fetch('));
assert.match(hookSource, /addEventListener\('online', handleOnline\)/);
assert.match(hookSource, /addEventListener\('offline', handleOffline\)/);
assert.match(hookSource, /removeEventListener\('online', handleOnline\)/);
assert.match(hookSource, /removeEventListener\('offline', handleOffline\)/);

console.log('runtime version truth tests passed');
