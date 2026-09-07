// v13.5.38 — MARKET SIGNALS (SIG-01..07) owner-facing contract.
//
// Proves: seven fixed IDs, the numerator is computed from per-signal state
// (never hard-coded), every signal renders an independent truthful state,
// DATA_GATED/UNAVAILABLE never count, glossary coverage, and the panel
// renders the surface from the projection only.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

require.extensions['.ts'] = (mod, filename) => {
  const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    fileName: filename,
  }).outputText;
  mod._compile(output, filename);
};

const src = path.join(__dirname, '..', 'src');
const ms = require(path.join(src, 'domain', 'marketSignals.ts'));
const glossary = require(path.join(src, 'domain', 'glossary.ts'));

const fam = (status, conditionMet) => ({ status, conditionMet });

// 1) seven identities, fixed order, owner vocabulary (no legacy engine name).
assert.deepEqual(ms.MARKET_SIGNAL_DEFINITIONS.map((d) => d.id),
  ['SIG-01', 'SIG-02', 'SIG-03', 'SIG-04', 'SIG-05', 'SIG-06', 'SIG-07']);
for (const d of ms.MARKET_SIGNAL_DEFINITIONS) {
  assert.ok(!/SHO/.test(d.nameEn) && !/SHO/.test(d.nameJa), `${d.id} must not carry the legacy name`);
}

// 2) numerator computed from state — 0, 3 and 7 all derive from data.
const derivedZero = ms.marketSignalsView({ families: {
  D01: fam('AVAILABLE', false), D02: fam('AVAILABLE', false), D03: fam('AVAILABLE', false),
  D04: fam('AVAILABLE', false), D05: fam('AVAILABLE', false), D06: fam('AVAILABLE', false),
  D07: fam('AVAILABLE', false) } });
assert.equal(derivedZero.countLabel, '0 / 7');
const derivedThree = ms.marketSignalsView({ families: {
  D01: fam('AVAILABLE', true), D02: fam('AVAILABLE', true), D03: fam('AVAILABLE', true),
  D04: fam('LICENSE_BLOCKED', null), D05: fam('AVAILABLE', false), D06: fam('AVAILABLE', null),
  D07: fam('MISSING', null) } });
assert.equal(derivedThree.countLabel, '3 / 7');
assert.equal(derivedThree.source, 'derived');
const derivedSeven = ms.marketSignalsView({ families: Object.fromEntries(
  ['D01', 'D02', 'D03', 'D04', 'D05', 'D06', 'D07'].map((k) => [k, fam('AVAILABLE', true)])) });
assert.equal(derivedSeven.countLabel, '7 / 7');

// 3) independent, truthful per-signal states (missing provider => UNAVAILABLE, never CLEAR/zero-silent).
const states = Object.fromEntries(derivedThree.signals.map((s) => [s.id, s.state]));
assert.deepEqual(states, {
  'SIG-01': 'ACTIVE', 'SIG-02': 'ACTIVE', 'SIG-03': 'ACTIVE', 'SIG-04': 'LICENSE_BLOCKED',
  'SIG-05': 'CLEAR', 'SIG-06': 'DATA_GATED', 'SIG-07': 'UNAVAILABLE',
});
assert.equal(ms.marketSignalsView({ families: {} }).signals.every((s) => s.state === 'UNAVAILABLE'), true);

// 4) server projection is preferred but the count is recounted from the rows it shows.
const server = ms.marketSignalsView({
  marketSignals: { total: 7, activeCount: 99, countLabel: '99 / 7', signals: [
    { id: 'SIG-01', state: 'ACTIVE' }, { id: 'SIG-02', state: 'CLEAR' },
    { id: 'SIG-03', state: 'DATA_GATED' }, { id: 'SIG-04', state: 'LICENSE_BLOCKED' },
    { id: 'SIG-05', state: 'STALE' }, { id: 'SIG-06', state: 'UNAVAILABLE' },
    { id: 'SIG-07', state: 'ACTIVE' } ] },
  families: {},
});
assert.equal(server.source, 'server');
assert.equal(server.countLabel, '2 / 7');
assert.equal(server.signals.find((s) => s.id === 'SIG-05').stateJa, '古い');

// 5) every rendered state has a Japanese label and a glossary entry.
for (const state of ['ACTIVE', 'CLEAR', 'DATA_GATED', 'STALE', 'LICENSE_BLOCKED', 'UNAVAILABLE']) {
  assert.ok(ms.MARKET_SIGNAL_STATE_JA[state], `label for ${state}`);
  const key = glossary.MARKET_SIGNAL_STATE_GLOSSARY[state];
  assert.ok(key && glossary.glossaryEntry(key), `glossary entry for ${state}`);
}

// 6) the panel renders the surface from the projection and never hard-codes a count.
const panel = fs.readFileSync(path.join(src, 'components', 'today', 'ArgusTodayPanel.tsx'), 'utf8');
assert.ok(panel.includes('marketSignalsView(decisionEvidence.marketView?.projection ?? null)'), 'panel must derive MARKET SIGNALS from the projection');
// v13.5.59 (owner iPhone): MARKET SIGNALS is rendered ONCE — at the top of the
// Primary Action (tap to expand). The second copy in the market view is gone.
assert.ok(!panel.includes('data-argus-contract="market-signals-v1"'), 'no duplicate MARKET SIGNALS block');
assert.ok(!panel.includes('className="mv-fams"'), 'the seven family chips no longer repeat the signals');
assert.ok(panel.includes('{topSignals ? topSignals.countLabel'), 'count must come from the view');
assert.ok(!/["'`]\s*1 \/ 7\s*["'`]/.test(panel), 'no hard-coded 1 / 7 literal');
assert.ok(panel.includes('data-signal-state={row.state}'), 'per-signal state rendered independently');

// 7) v13.5.39: the TOP command area (the block the owner reads first) renders
//    MARKET SIGNALS x / 7 from the real projection, never a hard-coded count.
assert.ok(panel.includes('data-argus-contract="market-signals-top-v1"'), 'top block contract marker');
assert.ok(panel.includes('const topSignals = marketSignalsView(decisionEvidence.marketView?.projection ?? null)'),
  'top block derives from the projection');
assert.ok(panel.includes("{topSignals ? topSignals.countLabel : '— / 7'}"), 'top count from the view or truthful placeholder');
assert.ok(panel.includes('<small>MARKET SIGNALS</small>'), 'owner-facing name at the top');
assert.ok(panel.includes('data-argus-contract="market-signals-top-detail-v1"'), 'seven per-signal states expand at the top');
assert.ok(!/<b>1 \/ 7<\/b>/.test(panel), 'no hard-coded 1 / 7');

console.log('market-signals.test: SIG-01..07, computed x/7, independent states, top block, glossary ok');

// ── v13.5.44: NOT_APPLICABLE is a distinct, glossaried, non-counting state ──
const naState = ms.signalStateFromFamily({ status: 'NOT_APPLICABLE', conditionMet: null });
assert.equal(naState, 'NOT_APPLICABLE');
assert.equal(ms.MARKET_SIGNAL_STATE_JA.NOT_APPLICABLE, '該当なし');
assert.ok(glossary.GLOSSARY[glossary.MARKET_SIGNAL_STATE_GLOSSARY.NOT_APPLICABLE], 'glossary entry for NOT_APPLICABLE');
console.log('market-signals.test: NOT_APPLICABLE state ok');
