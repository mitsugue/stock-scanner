import React from 'react';
import { useNewsIntelligence, type NewsIntelEvent } from '../../hooks/useNewsIntelligence';
import { useMarketShock } from '../../hooks/useMarketShock';
import './NewsAlertsPanel.css';

// v13.5.60 (owner iPhone review 2026-09-07): the Alerts page opens with the
// ニュース・市場リスク section — every material news-intelligence event
// (HIGH/CRITICAL, not stale) and every corroborated market shock, with the
// full interpretation that Today only summarises. Evidence only: nothing here
// carries action authority, and the SDA is untouched.

export const NEWS_ALERTS_SECTION_ID = 'news-intel';

const SEVERITY_TONE: Record<string, string> = {
  CRITICAL: 'var(--value-negative)', HIGH: 'var(--amber, #fbbf24)',
  MEDIUM: 'var(--accent)', LOW: 'var(--text-muted)',
  WATCH: 'var(--text-muted)', INFO: 'var(--text-faint)',
};
const DIRECTION_JA: Record<string, string> = {
  BULLISH: '強気', BEARISH: '弱気', MIXED: '混在', UNCLEAR: '方向判定不能',
};

export function materialNewsEvents(events: NewsIntelEvent[]): NewsIntelEvent[] {
  return events
    .filter((event) => (event.severity === 'HIGH' || event.severity === 'CRITICAL')
      && String(event.staleness).toUpperCase() !== 'STALE')
    .filter((event, index, rows) => rows.findIndex((candidate) =>
      candidate.eventType === event.eventType && candidate.source === event.source) === index)
    .sort((left, right) => (right.severity === 'CRITICAL' ? 1 : 0)
      - (left.severity === 'CRITICAL' ? 1 : 0)
      || String(right.sourceReceivedAt ?? '').localeCompare(String(left.sourceReceivedAt ?? '')));
}

const receivedJa = (value: string | null | undefined): string => {
  if (!value) return '—';
  const t = Date.parse(value);
  if (!Number.isFinite(t)) return '—';
  return new Date(t).toLocaleString('ja-JP', {
    timeZone: 'Asia/Tokyo', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  });
};

export const NewsAlertsPanel: React.FC = () => {
  const news = useNewsIntelligence();
  const shock = useMarketShock();
  const material = materialNewsEvents(news.view?.events ?? []);
  const shocks = shock.view?.events ?? [];
  const unread = news.status === 'error' && news.view == null;
  return (
    <section id={NEWS_ALERTS_SECTION_ID} className="news-alerts card" aria-label="ニュース・市場リスク">
      <div className="news-alerts__head">
        <b>ニュース・市場リスク</b>
        <span>{material.length + shocks.length}件 · 売買権限なし</span>
      </div>
      {shocks.length > 0 && <div className="news-alerts__group">
        <small>市場リスク（市場横断で確認された衝撃）</small>
        {shocks.map((event) => <article key={event.eventId} className="news-alerts__item"
          id={`news-${event.eventId}`} data-severity={event.severity}>
          <p className="news-alerts__title">
            <mark style={{ color: SEVERITY_TONE[event.severity] ?? 'inherit',
              borderColor: SEVERITY_TONE[event.severity] ?? 'inherit' }}>{event.severity}</mark>
            <b>{event.headlineJa}</b>
          </p>
          <p className="news-alerts__why">{event.whyJa}</p>
          <p className="news-alerts__meta">
            {event.sources.map((source) => source.name).join(' · ')}
            {event.asOf ? ` · ${event.asOf}` : ''}
            {event.crossMarket.confirmed && ` · 市場横断確認: ${event.crossMarket.signals.join('/')}`}
          </p>
        </article>)}
      </div>}
      <div className="news-alerts__group">
        <small>重大ニュース（ARGUSの解釈 · 記事本文ではありません）</small>
        {news.status === 'loading' && material.length === 0
          && <p className="news-alerts__empty">読み込み中…</p>}
        {news.status !== 'loading' && material.length === 0 && <p className="news-alerts__empty">
          {unread ? 'ニュースを取得できていません（重大ニュースが無いという意味ではありません）'
            : '直近の重大ニュースなし（INFO/WATCH級は表示しません）'}
        </p>}
        {material.map((event) => {
          const direction = event.impactDirection?.primaryDirection ?? 'UNCLEAR';
          return <article key={event.eventId} className="news-alerts__item"
            id={`news-${event.eventId}`} data-severity={event.severity}>
            <p className="news-alerts__title">
              <mark style={{ color: SEVERITY_TONE[event.severity] ?? 'inherit',
                borderColor: SEVERITY_TONE[event.severity] ?? 'inherit' }}>{event.severity}</mark>
              <em>{DIRECTION_JA[direction] ?? direction}</em>
              <b>{event.headlineJa}</b>
            </p>
            <p className="news-alerts__why">{event.whyJa}</p>
            {event.japanImpactJa && event.japanImpactJa !== event.whyJa
              && <p className="news-alerts__japan">日本株への波及: {event.japanImpactJa}</p>}
            {event.marketReadings.length > 0 && <p className="news-alerts__readings">
              {event.marketReadings.slice(0, 4).map((reading) =>
                `${reading.labelJa} ${reading.value ?? '—'}${reading.unit}`).join(' · ')}
            </p>}
            <p className="news-alerts__meta">
              {event.source} · 受信 {receivedJa(event.sourceReceivedAt)} JST ·{' '}
              {event.confirmationState === 'MARKET_CONFIRMED' ? '市場確認済み' : '市場確認待ち'}
              {event.backfill ? ' · 再処理(過去分)' : ''}
            </p>
          </article>;
        })}
      </div>
      <p className="news-alerts__note">
        方向判定不能 = このニュースからは上下を決めない、という判定です。ニュースは売買権限を持ちません。
      </p>
    </section>
  );
};

export default NewsAlertsPanel;
