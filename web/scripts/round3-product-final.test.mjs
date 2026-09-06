import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repo = path.resolve(root, '..');
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');

async function importTypeScriptModule(relativePath) {
  const output = ts.transpileModule(read(relativePath), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
    fileName: relativePath,
  }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(output).toString('base64')}`);
}

const navigation = await importTypeScriptModule('src/navigation.ts');
const today = read('src/components/today/ArgusTodayPanel.tsx');
const command = read('src/routes/CommandCenter.tsx');
const assetHook = read('src/hooks/useAssetIntel.ts');
const assetDecision = read('src/domain/assetDecision.ts');
const assetDesk = read('src/domain/assetDesk.ts');
const notificationEngine = read('src/lib/notifications.ts');
const settings = read('src/routes/Settings.tsx');
const shell = read('src/components/AppShell.tsx');
const versionTruth = read('src/domain/runtimeVersionTruth.ts');
const diagnostics = read('src/routes/DataQualityPage.tsx');
const productVersion = JSON.parse(fs.readFileSync(path.join(repo, 'product-version.json'), 'utf8'));
const scanner = fs.readFileSync(path.join(repo, 'scanner.py'), 'utf8');
const routeCatalog = fs.readFileSync(path.join(repo, 'argus_route_catalog.py'), 'utf8');

assert.deepEqual(navigation.PRIMARY_NAVIGATION.map((item) => item.route),
  ['command', 'watchlist', 'notifications', 'settings']);
assert.deepEqual(navigation.PRIMARY_NAVIGATION.map((item) => item.hash),
  ['#today', '#holdings', '#notifications', '#settings']);
assert.deepEqual(navigation.parseLocationHash('#asset/1321/decision'), {
  route: 'watchlist', asset: { symbol: '1321', section: 'decision' },
});

const retired = [
  'src/domain/argusEngine.ts',
  'src/domain/commandSummary.ts',
  'src/domain/primaryStance.ts',
  'src/lib/holderPosture.ts',
  'src/hooks/useActionAlerts.ts',
  'src/components/dashboard/AlertCard.tsx',
  'src/components/assetDesk/AssetEntryScout.tsx',
];
for (const file of retired) assert.equal(fs.existsSync(path.join(root, file)), false, file);

assert.ok(today.indexOf('at-primary-hero') >= 0);
assert.ok(today.indexOf('at-primary-hero')
  < today.indexOf('<details className="at-evidence card">'));
assert.match(today, /view\.canonicalDecision/);
assert.match(today, /Calibration pending/);
assert.doesNotMatch(today, /candidateLevel \?\? 4|view\.decisions/);
assert.match(command, /sdaBySymbol/);
assert.match(command, /canonicalDecisions/);
assert.doesNotMatch(command, /resolveCommandSummary|buildTodayReview|synthesizeArgusDecision/);

assert.match(assetHook, /evaluateSingleDecisionAuthority/);
assert.match(assetHook, /sdaBySymbol/);
assert.doesNotMatch(assetHook, /mergeAiPrimary|resolveAssetDecision|stanceBySymbol|resolvePrimaryStance/);
assert.match(assetDecision, /judgmentSource: 'sda'/);
assert.match(assetDecision, /authorityRole: 'EVIDENCE_ONLY'/);
assert.doesNotMatch(assetDecision, /mergeAiPrimary|resolveAssetDecision|primary: boolean/);
assert.match(assetDesk, /canonicalPrimaryAction/);
assert.doesNotMatch(assetDesk, /actionOverride.*\?/);

assert.doesNotMatch(scanner, /@app\.route\("\/api\/argus\/(?:action-alerts|entry-scout)"/);
assert.doesNotMatch(routeCatalog, /\/api\/argus\/(?:action-alerts|entry-scout)/);

assert.match(notificationEngine, /MATERIAL_NOTIFICATION_TYPES/);
for (const required of ['primary_action_changed', 'authority_lost', 'target_reached',
  'invalidation_reached', 'event_before', 'strategy_risk', 'sync_backup_warning']) {
  assert.match(notificationEngine, new RegExp(`['"]${required}['"]`));
}
assert.match(notificationEngine, /notificationPreferences/);
assert.match(settings, /saveNotificationPreferences/);
assert.match(settings, /MATERIAL NOTIFICATIONS/);

assert.match(shell, /A\.R\.G\.U\.S\./);
assert.match(shell, /shell__brand-pro">Pro/);
assert.match(shell, /\{versionLabel\}/);
assert.equal((shell.match(/shell__brand-version/g) ?? []).length, 1);
assert.doesNotMatch(shell, /Frontend v|Backend v|backendSha|deploymentId/);
assert.deepEqual(productVersion, {
  schemaVersion: 'argus-product-version-v1', productVersion: 'v13.5.58',
});
assert.match(versionTruth, /runtimeVersionLabel\(productVersion: string\)/);
assert.match(versionTruth, /product version unavailable/);
assert.doesNotMatch(versionTruth, /UI v|API v/);
assert.match(diagnostics, /Product <b>\{versions\?\.productVersion/);
assert.match(diagnostics, /Frontend \{versions\?\.frontendVersion/);
assert.match(diagnostics, /Backend \/ API \{versions\?\.backendVersion/);
assert.match(diagnostics, /Build frontend \{versions\?\.frontendBuildSha/);
assert.doesNotMatch(shell, /__APP_VERSION__|__FRONTEND_BUILD_SHA__/);

console.log('round3-product-final.test: ok (one SDA, four surfaces, sparse notifications)');
