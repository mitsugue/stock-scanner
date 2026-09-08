// v13.5.63 (GPT review item 3: 「登録銘柄数と、価格取得数・判断根拠取得数・
// 画面表示数を照合してください」). Pure reconciliation of the device's registered
// assets against what the app actually holds for them right now. Counts only —
// the owner's symbols never leave the device through this module.

export interface DeskCoverageAsset {
  symbol: string;
  market: 'JP' | 'US' | 'CRYPTO' | 'FUND' | 'CORE' | 'MANUAL' | string;
}

export interface DeskCoverageInput {
  assets: readonly DeskCoverageAsset[];
  /** upper-cased symbols with a price on screen */
  pricedSymbols: ReadonlySet<string> | ReadonlyMap<string, unknown>;
  /** decision-evidence subjects keyed by upper-cased symbol (JP/US only) */
  evidenceSubjects: Record<string, unknown> | null | undefined;
  /** upper-cased symbols rendered in the desk list */
  displayedSymbols: ReadonlySet<string>;
  /** symbols the evidence poller was asked for (upper-cased) */
  requestedEvidence?: readonly string[] | null;
}

export interface DeskCoverage {
  registered: number;
  priced: number;
  evidence: number;
  evidenceApplicable: number;
  displayed: number;
  missingPrice: string[];
  missingEvidence: string[];
  notRequested: string[];
  notDisplayed: string[];
  complete: boolean;
}

function has(set: ReadonlySet<string> | ReadonlyMap<string, unknown>, key: string): boolean {
  return set.has(key);
}

export function deskCoverage(input: DeskCoverageInput): DeskCoverage {
  const symbols = [...new Set(input.assets.map((asset) => String(asset.symbol || '').toUpperCase()).filter(Boolean))];
  const marketOf = new Map<string, string>();
  for (const asset of input.assets) marketOf.set(String(asset.symbol || '').toUpperCase(), String(asset.market || ''));
  const subjects = input.evidenceSubjects ?? {};
  const requested = new Set((input.requestedEvidence ?? []).map((symbol) => symbol.toUpperCase()));
  const missingPrice = symbols.filter((symbol) => !has(input.pricedSymbols, symbol));
  const applicable = symbols.filter((symbol) => marketOf.get(symbol) === 'JP' || marketOf.get(symbol) === 'US');
  const missingEvidence = applicable.filter((symbol) => !(symbol in subjects));
  const notRequested = input.requestedEvidence ? applicable.filter((symbol) => !requested.has(symbol)) : [];
  const notDisplayed = symbols.filter((symbol) => !input.displayedSymbols.has(symbol));
  return {
    registered: symbols.length,
    priced: symbols.length - missingPrice.length,
    evidence: applicable.length - missingEvidence.length,
    evidenceApplicable: applicable.length,
    displayed: symbols.length - notDisplayed.length,
    missingPrice, missingEvidence, notRequested, notDisplayed,
    complete: missingPrice.length === 0 && missingEvidence.length === 0 && notDisplayed.length === 0,
  };
}

/** One line for the desk header; the gaps open on tap via deskCoverageDetailJa. */
export function deskCoverageJa(coverage: DeskCoverage, stored?: { loading: boolean; generatedAt: string | null } | null): string {
  const base = `登録 ${coverage.registered} · 価格 ${coverage.priced}/${coverage.registered}`
    + ` · 判断根拠 ${coverage.evidence}/${coverage.evidenceApplicable}`
    + ` · 表示 ${coverage.displayed}/${coverage.registered}`;
  // v13.5.65 (stabilization item 5): while the first fetch of a session runs,
  // the stored document is what the screen shows — say so, with its time.
  if (stored && stored.loading && stored.generatedAt) {
    const t = new Date(stored.generatedAt);
    const clock = Number.isFinite(t.getTime())
      ? t.toLocaleString('ja-JP', { timeZone: 'Asia/Tokyo', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '時刻不明';
    return `${base} · 保存分 ${clock} を表示中（更新取得中）`;
  }
  if (stored && stored.loading) return `${base} · 初回取得中`;
  return base;
}

export function deskCoverageDetailJa(coverage: DeskCoverage): string[] {
  const rows: string[] = [];
  if (coverage.missingPrice.length) rows.push(`価格未取得: ${coverage.missingPrice.join('・')}`);
  if (coverage.notRequested.length) rows.push(`判断根拠 未要求（取得キューの外）: ${coverage.notRequested.join('・')}`);
  const pending = coverage.missingEvidence.filter((symbol) => !coverage.notRequested.includes(symbol));
  if (pending.length) rows.push(`判断根拠 取得中/未着: ${pending.join('・')}`);
  if (coverage.notDisplayed.length) rows.push(`未表示: ${coverage.notDisplayed.join('・')}`);
  if (coverage.registered - coverage.evidenceApplicable > 0) {
    rows.push(`判断根拠の対象外（仮想通貨・投信など）: ${coverage.registered - coverage.evidenceApplicable}件`);
  }
  if (!rows.length) rows.push('登録銘柄すべてに価格と判断根拠が届いています');
  return rows;
}
