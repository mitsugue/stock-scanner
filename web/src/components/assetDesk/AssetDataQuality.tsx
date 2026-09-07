import React from 'react';
import type { DeskCardData } from './types';
import { freshnessOf, fmtAgeMin } from './deskFormat';
import { quoteAge, quoteAsOf, quoteFreshnessJa } from '../../domain/liveQuote';

// V12.2.12 — DATA QUALITY(§7-10)。旧WatchlistのData limitations+鮮度の正直
// 表示を統合。測れない鮮度は捏造しない(不変)。

export const AssetDataQuality: React.FC<{ d: DeskCardData; nowMs: number }> = ({ d, nowMs }) => {
  const fresh = freshnessOf(d.strat, d.quote);
  const quote = d.quote?.quoteTruth;
  return (
    <>
      <p className="uac-next" style={{ marginBottom: 2 }}>
        データ状態: <b style={{ color: fresh.color }}>{fresh.text}</b>
        {d.strat.date && <span style={{ marginLeft: 6, color: 'var(--text-faint)' }}>データ日付 {d.strat.date}</span>}
        <span style={{ marginLeft: 6, color: 'var(--text-faint)' }}>updated {fmtAgeMin(d.strat.lastUpdated, nowMs)}</span>
      </p>
      {quote && <p className="uac-next" style={{ marginBottom: 2, fontSize: 10.5, color: 'var(--text-faint)' }}>
        {quote.instrumentType} · {quoteFreshnessJa(quote)} · {quoteAsOf(quote)} · {quoteAge(quote)}
        {' · '}session {quote.session} · entitlement {quote.entitlement}
      </p>}
      <p className="uac-next" style={{ marginBottom: 2, fontSize: 10.5, color: 'var(--text-faint)' }}>
        AI鮮度: {d.aiMeta.freshness === 'fresh' ? '最新' : d.aiMeta.freshness === 'stale' ? '古い(主判断に使わない)'
          : d.aiMeta.freshness === 'unavailable' ? '取得不可' : 'ルールのみ'}
        {d.aiMeta.unavailableReasonJa ? ` — ${d.aiMeta.unavailableReasonJa}` : ''}
      </p>
      {d.strat.dataLimitations.length > 0 && (
        <div className="asset-detail__limits">
          <span className="asset-detail__k">Data limitations</span>
          <ul>{d.strat.dataLimitations.map((x, i) => <li key={i}>{x}</li>)}</ul>
        </div>
      )}
    </>
  );
};
