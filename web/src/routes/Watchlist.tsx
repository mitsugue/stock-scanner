import React, { useEffect, useState } from 'react';
import { PageShell } from './PageShell';
import { ProHandoffButton } from '../components/dashboard/ProHandoffButton';
import { AssetDeskList, type AssetFocusIntent } from '../components/assetDesk/AssetDeskList';
import { EntityProfileEditor } from '../components/dashboard/EntityProfileEditor';
import { AddAssetModal } from '../components/dashboard/AddAssetModal';
import { TradeJournalCard } from '../components/dashboard/TradeJournalCard';
import { Layer2BSyncCard } from '../components/guide/Layer2BSyncCard';
import { useAssets } from '../hooks/useAssets';
import { useAssetIntel } from '../hooks/useAssetIntel';
import { useDecisionEvidence, requestedDecisionEvidenceSymbols } from '../hooks/useDecisionEvidence';
import { deskCoverage, deskCoverageJa, deskCoverageDetailJa } from '../domain/deskCoverage';
import { useLocale, t } from '../i18n';
import { CorePortfolio } from './CorePortfolio';
import '../components/dashboard/Dashboard.css';

// V12.2.12 — ASSET DESK(route key `watchlist` 不変): 個別銘柄情報の唯一の正本。
// 判断はHoldings所有の共有Asset Intel(publish:true)経由でTodayと同一。
// 追加・削除、急落証拠、owner profile、売買記録、FIRE/portfolio evidenceは
// Asset DetailまたはHoldings内のcontextual disclosureとして残す。

function ageLabel(ts: number, nowMs: number): string {
  const m = Math.max(0, Math.round((nowMs - ts) / 60000));
  return m < 1 ? 'just now' : `${m}m ago`;
}

interface Props {
  /** Today等からのdeep-link(展開+スクロール)。App.tsxのpendingAssetFocus。 */
  assetFocus?: AssetFocusIntent | null;
  assetDetail?: boolean;
  initialPortfolioOpen?: boolean;
  onNavigateToAsset?: (symbol: string, section?: string) => void;
  onBackToHoldings?: () => void;
}

export const Watchlist: React.FC<Props> = ({
  assetFocus, assetDetail = false, initialPortfolioOpen = false,
  onNavigateToAsset, onBackToHoldings,
}) => {
  useLocale();   // re-render on locale switch
  const assetsApi = useAssets();
  const { assets, add, remove, reorderGenre, updateHolding } = assetsApi;
  // Holdings owns one canonical acquisition/intelligence lifecycle. Every
  // contextual child below receives this exact snapshot.
  const intel = useAssetIntel({ publish: true, assets });
  // v13.5.63 (GPT review item 3): registered vs priced vs evidenced vs shown.
  const evidence = useDecisionEvidence();
  const coverage = deskCoverage({
    assets,
    pricedSymbols: intel.priceBySymbol,
    evidenceSubjects: evidence.subjects,
    displayedSymbols: new Set((assetDetail && assetFocus?.symbol ? [assetFocus.symbol] : assets.map((a) => a.symbol))
      .map((s) => s.toUpperCase())),
    requestedEvidence: evidence.requested ?? requestedDecisionEvidenceSymbols(),
  });
  const [addOpen, setAddOpen] = useState(false);
  const [nonce, setNonce] = useState(0);            // rescan → remounts the data section
  const [updatedAt, setUpdatedAt] = useState(() => Date.now());
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [supportOpen, setSupportOpen] = useState(false);
  const [portfolioOpen, setPortfolioOpen] = useState(initialPortfolioOpen);

  useEffect(() => {
    setPortfolioOpen(initialPortfolioOpen);
  }, [initialPortfolioOpen]);

  useEffect(() => {
    const t = window.setInterval(() => setNowMs(Date.now()), 30_000);
    return () => window.clearInterval(t);
  }, []);

  function rescan() {
    setNonce((n) => n + 1);
    setUpdatedAt(Date.now());
    setNowMs(Date.now());
  }

  return (
    <PageShell
      title={assetDetail ? 'ASSET DETAIL' : 'HOLDINGS / WATCHLIST'}
      subtitle={assetDetail
        ? `${assetFocus?.symbol ?? '銘柄'} · 判断 / 見通し / 根拠 / 保有`
        : '日本株・米国株・投資信託・仮想通貨ごとに整理します。区分内は長押しで並べ替えられます。'}
    >
      {assetDetail && <button type="button" className="asset-btn" onClick={onBackToHoldings}>
        ← Holdings / Watchlist
      </button>}
      <AssetDeskList
        key={nonce}
        assets={assets}
        intel={intel}
        onReorder={reorderGenre}
        onRemove={remove}
        onUpdateHolding={updateHolding}
        focus={assetFocus}
        detailSymbol={assetDetail ? assetFocus?.symbol : undefined}
        onOpenAsset={onNavigateToAsset}
        toolbar={(
          <div className="asset-toolbar asset-toolbar--end">
            <details className="asset-coverage" data-argus-contract="desk-coverage-v1"
              data-coverage-complete={coverage.complete ? 'true' : 'false'}>
              <summary>{deskCoverageJa(coverage, { loading: evidence.loading, generatedAt: evidence.generatedAt })}{coverage.complete ? '' : ' · 不足あり'}</summary>
              <ul>{deskCoverageDetailJa(coverage).map((row) => <li key={row}>{row}</li>)}</ul>
            </details>
            <span className="asset-toolbar__age">{t('wl.updated')} {ageLabel(updatedAt, nowMs)}</span>
            <button className="asset-btn" onClick={rescan}
              aria-label="Rescan (rule-based refresh, no AI run)">{t('wl.rescan')}</button>
            <button className="asset-btn asset-btn--primary" onClick={() => setAddOpen(true)}
              aria-label="Add asset">{t('wl.addAsset')}</button>
          </div>
        )}
      />

      {!assetDetail && <details className="card cp-workspace" open={portfolioOpen}
        onToggle={(event) => setPortfolioOpen(event.currentTarget.open)}>
        <summary>Advanced portfolio / allocation / risk</summary>
        {portfolioOpen && <CorePortfolio assetsApi={assetsApi}
          portfolioIntel={intel} />}
      </details>}

      {!assetDetail && <details className="card ad-support" open={supportOpen}
        onToggle={(event) => setSupportOpen(event.currentTarget.open)}>
        <summary>Supporting tools</summary>
        {supportOpen && <div className="ad-support__body">
          <Layer2BSyncCard assets={assets} />
          <EntityProfileEditor assets={assets} />
          <TradeJournalCard assets={assets} priceBySymbol={intel.priceBySymbol} />
          <div className="watch-toolbar">
            <ProHandoffButton />
          </div>
        </div>}
      </details>}

      {!assetDetail && addOpen && <AddAssetModal onClose={() => setAddOpen(false)} onAdd={add} />}
    </PageShell>
  );
};
