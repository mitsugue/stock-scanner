import React from 'react';
import type { ArgusTodayView, MarketSelectionMode, TodayProjection } from '../../domain/argusTodayView';
import { formatEventTime, quoteDisplayLabel, subjectDisplayName } from '../../domain/argusTodayView';
import { displayNewsHeadline, isDigestHeadline } from '../../lib/newsHeadline';
import type { RouteKey } from '../NavRail';
import type { SettingsSection } from '../../navigation';
import { TriangleStepLoader } from '../common/TriangleStepLoader';
import { useDecisionEvidence } from '../../hooks/useDecisionEvidence';
import { useMarketBrief } from '../../hooks/useMarketBrief';
import { GlossaryTip } from '../common/GlossaryTip';
import { REVERSAL_STATE_GLOSSARY, FAMILY_STATE_GLOSSARY } from '../../domain/glossary';
import { marketSignalsView } from '../../domain/marketSignals';
import { tachibanaLiveView, formatJpy, formatPct } from '../../domain/tachibanaLive';
import type { TachibanaLiveDocument } from '../../domain/tachibanaLive';
import { useNewsIntelligence } from '../../hooks/useNewsIntelligence';
import type {
  MarketHorizon, MarketInstrumentMarket, MarketInstrumentSymbol,
} from '../../domain/marketInstruments';
import './ArgusToday.css';

export interface TodayInstrumentState {
  symbol: MarketInstrumentSymbol;
  market: MarketInstrumentMarket;
  shortLabel: string;
  fullLabel: string;
  instrumentType: 'ETF';
  underlying: string;
}

export interface TodayChartLoadState {
  loading: boolean;
  loaderVisible: boolean;
  slowInitial: boolean;
  statusText: string;
  error: string | null;
  snapshotState: string;
  snapshotId: string | null;
  responseSnapshotId: string | null;
  retry: () => void;
}

interface Props {
  view: ArgusTodayView;
  instruments: readonly TodayInstrumentState[];
  selectedSymbol: MarketInstrumentSymbol;
  horizon: MarketHorizon;
  chartLoad: TodayChartLoadState;
  /** Which canonical source currently feeds the visible projection. */
  projectionSource: 'verified-snapshot' | 'headline' | null;
  /** Truthful session/data-freshness note (e.g. EOD prices while JP OPEN). */
  freshnessNoteJa: string | null;
  /** v13.5.59: JP code → company name for the Tachibana rows (code+name rule). */
  jpNameBySymbol?: Record<string, string>;
  /** Market-shock materiality view for the Major News surface. */
  shock: {
    status: 'loading' | 'data' | 'error';
    events: Array<{
      eventId: string; eventClass: string;
      severity: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
      headlineJa: string; whyJa: string;
      crossMarket: { confirmed: boolean; signals: string[] };
      impactDirection?: { primaryDirection: 'BULLISH' | 'BEARISH' | 'MIXED' | 'UNCLEAR';
        directionByTarget: Record<string, string>; transmissionChain: string[] };
      sources: Array<{ name: string; kind: string }>;
      asOf: string | null;
    }>;
  };
  newsIntel: {
    status: 'loading' | 'data' | 'error';
    events: Array<{
      eventId: string; eventType: string;
      severity: 'INFO' | 'WATCH' | 'HIGH' | 'CRITICAL';
      headlineJa: string; whyJa: string; japanImpactJa: string | null;
      confirmationState: 'MARKET_CONFIRMED' | 'MARKET_CONFIRMATION_PENDING';
      marketReadings: Array<{ key: string; labelJa: string;
        value: number | null; change: number | null; unit: string }>;
      source: string; sourceReceivedAt: string | null; backfill: boolean;
      eventMemory: {
        status: string; firstSeenAt: string; openedDaysAgo: number | null;
        episodeId: string; flagRecovery: boolean;
        hypothesisStates: Record<string, string>;
        analogEvidence: { sampleSize: number; independentEpisodeCount: number;
          confidence: string; insufficientEvidence: boolean } | null;
        calibrationMode: 'SHADOW'; sdaAuthority: false;
      } | null;
    }>;
  };
  onMode: (mode: MarketSelectionMode) => void;
  onInstrument: (market: 'JP' | 'US', symbol: string) => void;
  onHorizon: (horizon: MarketHorizon) => void;
  onNavigate: (key: RouteKey) => void;
  onNavigateToAsset?: (symbol: string, section?: string) => void;
  onNavigateToSettings?: (section: SettingsSection) => void;
  aiButton: React.ReactNode;
}

const ACTION_TONE = {
  BUY: 'var(--value-positive)', HOLD: 'var(--accent)', WAIT: 'var(--amber, #fbbf24)',
  REDUCE: 'var(--event-high)', EXIT: 'var(--value-negative)',
};
const MARKET_STANCE = {
  BUY: 'BUY', HOLD: 'HOLD', WAIT: 'WAIT', REDUCE: 'REDUCE', EXIT: 'EXIT',
};
// v13.5.54 (owner 2026-09-04: 「データ一部不足とは何か？全て与えているはず」).
// Every one of these is an ARGUS-side freshness or authority state — none of
// them says the owner failed to supply anything. Naming them is the whole
// point; an unmapped code still shows its raw form rather than disappearing.
const DATA_PARTIAL_REASON_JA: Record<string, string> = {
  watchlist_polling_partial: '銘柄クォートの一部が未取得',
  important_events_unread: '重要イベント情報が未取得',
  downside_unread: '急落インシデント情報が未取得',
  flow_authority_stale: '資金フロー証拠の鮮度切れ',
  supply_demand_authority_stale: '需給証拠の鮮度切れ',
  fx_authority_missing: '為替の正本が未取得',
  session_authority_missing: '市場セッション正本が未取得',
  quote_authority_missing: '判断に使えるクォートが未取得',
  visibility_limited: '可視性ガードにより表示を制限中',
};
const DATA_NOTE_JA: Record<string, string> = {
  flow_previous_value_closed_session: '資金フローは休場中のため前回値',
  flow_no_records_now: '資金フロー: 現在は帰属できる動きなし（休場中は通常）',
  supply_previous_value_closed_session: '需給は休場中のため前回値',
};
const SEVEN_SIGN_MEANING: Record<number, string> = {
  1: '強いRisk Off', 2: 'REDUCE寄り', 3: '新規回避', 4: 'WAIT',
  5: '条件付きBUY寄り', 6: 'BUY寄り', 7: '最高クラスBUY期待値',
};
const SEVEN_SIGN_REASON_JA: Record<string, string> = {
  decision_data_gated: '判断データ不足（DATA_GATED）',
  calibration_shadow: '校正シャドー検証中',
  calibration_missing: '校正データ未提供',
  calibration_data_gated: '校正データ不足',
  calibration_non_monotonic: '校正期待値の単調性未達',
  calibration_sample_insufficient: '校正サンプル数不足',
  calibration_not_out_of_sample: 'アウトオブサンプル検証未達',
  calibration_holdout_mutable: 'ホールドアウト不変性未達',
  calibration_artifact_not_verified: '校正アーティファクト未検証',
  reason_unavailable: '理由コード未提供',
};
const NEXT_REVIEW_REASON_JA: Record<string, string> = {
  'resolve.freshness_unknown': '正本データの更新時刻を確認',
  'resolve.market_truth_missing': '市場データの正本を取得',
  'resolve.prediction_ledger_missing': '市場スナップショットの更新後に再作成',
  'resolve.risk_evidence_missing': 'リスク証拠を更新',
  'resolve.scenario_event_missing': '重要イベント情報を更新',
  'resolve.sho_evidence_missing': 'チャート分析証拠を更新',
  'resolve.input_invalid': '判断入力を再取得',
  risk_reassessment: 'リスク条件を再確認',
  sho_revalidation: 'チャート分析証拠を再検証',
  evidence_refresh: '正本証拠を更新',
};
// v13.5.36 (external review item A): MARKET VIEW (SHO) / ACTION (SDA)
// separation. The strip renders the document-level SHO consumer projection —
// reversal + downside axis states and D01-D07 family states — directly under
// the SDA action so the owner sees "what the market looks like" and "what we
// do" as two explicitly different authorities. The projection carries
// actionAuthority:false by construction and is never an SDA input.
// v13.5.36 (owner: 「言葉の意味がわからない」): reason codes rendered in plain
// Japanese. Spec-by-design states (owner context, prediction ledger auth) must
// not read as errors. Unknown codes fall through readably.
// The code space is closed: `{market_truth|prediction_ledger|sho}_{missing|
// stale|conflict}` from referenceReasons, `quality_{partial|missing|conflict}`,
// `freshness_{stale|unknown}`, `owner_context_unknown`, `risk_evidence_empty`,
// `risk_{missing|conflict}.<factorId>`, plus the server's own quality codes
// (risk_evidence_missing / scenario_event_missing / sho_evidence_missing).
// Every one of them is spelled out here — the owner reported seeing raw
// `freshness_unknown` / `quality_missing` / `risk_evidence_missing` /
// `scenario_event_missing` / `sho_evidence_missing` on 2026-09-04.
const MISSING_REASON_JA: Record<string, string> = {
  freshness_stale: 'データ鮮度が低下（次の更新待ち）',
  freshness_unknown: 'データの鮮度を確認できない（更新時刻が未取得）',
  market_truth_stale: '市場データの鮮度が低下（市場終了後は終値基準で継続）',
  market_truth_missing: '市場データ未取得',
  market_truth_conflict: '市場データが食い違っています（照合待ち）',
  owner_context_unknown: '保有情報は端末内のみで参照（設計どおり・エラーではありません）',
  // v13.5.53: this reference is the SDA's EPHEMERAL prediction CONTEXT, built
  // from the market snapshot — not the durable Layer-2B ledger, and never
  // gated on owner authentication. Production on 2026-09-04 showed why the old
  // wording mattered: verificationFailures.predictionLedger was
  // "market_truth_reference_unavailable", i.e. the context could not be built
  // because the market snapshot was not fresh, yet the owner was told their
  // authentication was not set up and sent to fix a problem that did not exist.
  // State the fact; the market_truth_* line beside it carries the cause.
  prediction_ledger_missing: '予測コンテキストを作成できていません',
  prediction_ledger_stale: '予測台帳の鮮度が低下（次の記録待ち）',
  prediction_ledger_conflict: '予測台帳の記録が食い違っています（照合待ち）',
  quality_partial: 'データが一部不足',
  quality_missing: '判断に必要なデータが未取得',
  quality_conflict: '判断に必要なデータが食い違っています（照合待ち）',
  risk_evidence_empty: 'リスク入力が空（銘柄別の値が未取得）',
  risk_evidence_missing: 'リスク証拠が未取得',
  scenario_event_missing: '条件・イベントの証拠が未取得',
  sho_missing: 'チャート証拠が未取得',
  sho_stale: 'チャート証拠の鮮度が低下（次の更新待ち）',
  sho_conflict: 'チャート証拠が食い違っています（照合待ち）',
  sho_evidence_missing: 'チャート証拠が未取得',
  'risk_missing.discipline.required_authority':
    '銘柄別の価格権限なし（市場終了中または取得待ち）',
};
// An unmapped code must never reach the owner as bare English, and its meaning
// must not be invented either: say what is true (something is still missing)
// and keep the raw code visible as a code for support. `data-reason-code`
// still carries the exact identifier for tests and diagnostics.
const missingReasonJa = (line: string): string => {
  const mapped = MISSING_REASON_JA[line];
  if (mapped) return mapped;
  if (line.startsWith('risk_missing.')) {
    return `リスク入力の不足（${line.slice('risk_missing.'.length)}）`;
  }
  if (line.startsWith('risk_conflict.')) {
    return `リスク入力の食い違い（${line.slice('risk_conflict.'.length)}）`;
  }
  return `追加の証拠待ち（コード: ${line}）`;
};
const dissentReasonJa = (line: string): string =>
  line.startsWith('context_missing_advisory')
    ? '文脈証拠が不足しているという参考意見（最終判断は変えません）'
    : line;

const SHO_STATE_JA: Record<string, string> = {
  MIXED: '混在', FRAGILE: '脆弱', DOWNSIDE_TRIGGERED: '下方シグナル点灯',
  SELL_OFF_ACTIVE: '売り圧継続', REVERSAL_EARLY: '反転初動',
  TECHNICAL_REBOUND: 'テクニカル反発', RECOVERY_TEST: '回復試験',
  CONFIRMED_ADVANCE: '上昇確認', FALSE_RALLY: 'だまし上げ警戒',
};
const SHO_FAMILY_JA: Record<string, string> = {
  D01: '信用残', D02: '1570倍率', D03: '相対力', D04: 'EPS基準',
  D05: '海外フロー', D06: 'VIX', D07: '決算反応',
};
const familyStateJa = (row: { status?: string; conditionMet?: boolean | null }): string => {
  if (row.status === 'LICENSE_BLOCKED') return '要ライセンス';
  if (row.status !== 'AVAILABLE') return '欠測';
  return row.conditionMet === true ? '成立' : row.conditionMet === false ? '不成立' : '判定不能';
};
// v13.5.36 MARKET SITUATION BRIEF (owner 2026-08-26): NOW/WHY/NEXT — the
// deterministic composer selects verified facts; AI only compresses them
// (numbers/probabilities can never be invented — server-side validator).
const MarketBriefCard: React.FC = () => {
  const { brief } = useMarketBrief();
  if (!brief || brief.status === 'unavailable') return null;
  const now = brief.aiText?.nowJa ?? brief.now;
  const why = brief.aiText?.whyJa ?? brief.why;
  const next = brief.aiText?.nextJa ?? brief.next;
  return <div className="at-brief" data-argus-contract="market-brief-v1"
    aria-label="今の市場（売買権限なし）">
    <small>今の市場 — 検証済み事実の要約{brief.aiText ? '（AI圧縮・参考）' : ''}</small>
    <div className="at-brief__rows">
      <div><b>今</b><span>{now}</span></div>
      <div><b>理由</b><span>{why}</span></div>
      <div><b>次に確認</b><span>{next}</span></div>
    </div>
    <div className="at-brief__chips">
      <span>チャート <b>{brief.chips.chart}</b></span>
      <span>ニュース <b>{brief.chips.news}</b></span>
      <span>次イベント <b>{brief.chips.nextEvent}</b></span>
      <span>主リスク <b>{brief.chips.mainRisk}</b></span>
    </div>
  </div>;
};

const MarketViewStrip: React.FC<{ jpNames?: Record<string, string> }> = ({ jpNames }) => {
  const evidence = useDecisionEvidence();
  const projection = evidence.marketView?.projection;
  if (!projection || projection.actionAuthority !== false) return null;
  const reversal = projection.reversal;
  // v13.5.38 MARKET SIGNALS: the same seven families in the owner vocabulary
  // (SIG-01..07) with a count recomputed from the per-signal states shown.
  // v13.5.38 TACHIBANA LIVE: Japanese-equity live evidence (shadow, read-only).
  const tachibana = tachibanaLiveView(
    (evidence.marketView?.japaneseLive ?? null) as TachibanaLiveDocument | null);
  return <div className="at-marketview" data-argus-contract="sho-market-view-v1"
    aria-label="市場観（行動権限なし）">
    <small>市場観（検証前の参考情報） — 売買の最終判断とは別枠</small>
    <div className="mv-tachibana" data-argus-contract="tachibana-live-v1"
      data-tachibana-status={tachibana.status} data-tachibana-present={tachibana.present ? '1' : '0'}>
      <div className="mv-tachibana__head">
        <b>TACHIBANA LIVE</b>
        <GlossaryTip glossaryKey={tachibana.glossaryKey}>
          <i data-status={tachibana.status}>{tachibana.statusJa}</i>
        </GlossaryTip>
        <span>{tachibana.reasonJa}</span>
        {tachibana.updatedAt && <span>更新 {tachibana.updatedAt}</span>}
      </div>
      {/* v13.5.60 (owner iPhone review 2026-09-07): Today carries ONE line of
          what the Holdings page cannot say at a glance — how many holdings are
          on a current price right now and which moved most — named by company,
          never by code. Per-symbol prices, VWAP and the book live on Holdings. */}
      {tachibana.rows.length > 0 && <span className="mv-tachibana__compact"
        data-argus-contract="tachibana-live-compact-v1">
        {(() => {
          const current = tachibana.rows.filter((row) => row.price !== null && row.freshness === 'FRESH');
          const mover = [...tachibana.rows].filter((row) => row.changePct !== null)
            .sort((left, right) => Math.abs(right.changePct ?? 0) - Math.abs(left.changePct ?? 0))[0];
          const moverName = mover ? (jpNames?.[mover.symbol] ?? '保有銘柄') : null;
          return <>
            保有{tachibana.rows.length}銘柄のうち現在値 {current.length}件
            {mover && moverName && <> · 最大変動 <b>{moverName}</b> {formatPct(mover.changePct)}</>}
            {' · 個別の価格は Holdings · 提供元 TACHIBANA'}
          </>;
        })()}
      </span>}
      <span className="mv-tachibana__note">{tachibana.authorityJa}</span>
    </div>
    {/* v13.5.59 (owner iPhone): MARKET SIGNALS is rendered ONCE, at the top of
        the Primary Action (tap to expand). The seven family chips that repeated
        the same conditions here are gone; only the SHO reversal/downside states
        stay, since they are a different judgment. */}
    <div className="mv-states">
      <span>反転: <GlossaryTip glossaryKey={reversal?.reversalState
        ? (REVERSAL_STATE_GLOSSARY[reversal.reversalState] ?? '') : 'recovery_pending'}>
        <b>{reversal?.reversalState
          ? (SHO_STATE_JA[reversal.reversalState] ?? reversal.reversalState) : 'データ待ち'}</b>
      </GlossaryTip></span>
      <span>下方: <GlossaryTip glossaryKey={reversal?.downsideState
        ? (REVERSAL_STATE_GLOSSARY[reversal.downsideState] ?? '') : 'recovery_pending'}>
        <b>{reversal?.downsideState
          ? (SHO_STATE_JA[reversal.downsideState] ?? reversal.downsideState) : 'データ待ち'}</b>
      </GlossaryTip></span>
    </div>
    <span className="mv-note">市場観は行動権限を持たない（各項目は検証前・確率は主張しない）</span>
  </div>;
};

// v13.5.36 NEWS/EVENT SIGNAL (owner spec 2026-08-23): the independent news
// direction axis rendered BESIDE the SHO market view and the SDA action —
// three separate judgments, never one blended score. A chart view and a news
// view that disagree stay visibly different; cancellation into a vague
// composite is structurally impossible because nothing here is summed.
// v13.5.60 (owner: 「方向性不明。なぜか？」): UNCLEAR is a verdict — this news
// does not decide up or down — not a missing value, and the label says so.
const NEWS_DIRECTION_JA: Record<string, string> = {
  BULLISH: '強気', BEARISH: '弱気', MIXED: '混在', UNCLEAR: '方向判定不能',
};
const NEWS_TARGET_JA: Record<string, string> = {
  broadMarket: '市場全体', japanEquities: '日本株', growth: 'グロース',
  semiconductors: '半導体', banks: '銀行', exporters: '輸出', energy: 'エネルギー',
};
const NEWS_CONSTRAINT_JA: Record<string, string> = {
  NO_CONSTRAINT: '制約なし', CAUTION: '買い急がず市場反応を確認',
  BLOCK_NEW_BUY: '新規買い停止（確認済み逆風）',
  RISK_REVIEW_REQUIRED: 'リスク再確認（重大・確認済み）',
};
const newsAgeJa = (event: { ageMinutes?: number }): string | null => {
  const minutes = event.ageMinutes;
  if (typeof minutes !== 'number' || minutes < 0) return null;
  if (minutes < 60) return `${minutes}分前`;
  if (minutes < 48 * 60) return `${Math.round(minutes / 60)}時間前`;
  return `${Math.round(minutes / 1440)}日前`;
};
const NewsSignalStrip: React.FC = () => {
  const news = useNewsIntelligence();
  // v13.5.36 (external review): staleness is the backend's UPPERCASE enum,
  // re-evaluated at read time; ordering prefers severity then the RECEIPT
  // instant (processedAt reorders on backfill/reprocess and is not used).
  // v13.5.61 (owner: 「方向判定不能とはなぜか」): a multi-topic digest mail has no
  // single direction; when a directional single item of the same severity
  // exists it leads, and the digest headline is shown as its first item.
  const directional = (event: { impactDirection?: { primaryDirection?: string } }) =>
    event.impactDirection?.primaryDirection && event.impactDirection.primaryDirection !== 'UNCLEAR' ? 1 : 0;
  const material = (news.view?.events ?? [])
    .filter((event) => (event.severity === 'HIGH' || event.severity === 'CRITICAL')
      && String(event.staleness).toUpperCase() !== 'STALE')
    .sort((left, right) => (right.severity === 'CRITICAL' ? 1 : 0)
      - (left.severity === 'CRITICAL' ? 1 : 0)
      || directional(right) - directional(left)
      || (isDigestHeadline(left.headlineJa) ? 1 : 0) - (isDigestHeadline(right.headlineJa) ? 1 : 0)
      || String(right.sourceReceivedAt ?? '').localeCompare(
        String(left.sourceReceivedAt ?? '')));
  const top = material[0];
  const degraded = news.status === 'error' && news.view != null;
  if (news.status === 'loading') return null;
  if (!top) {
    // A feed that has never been read cannot report an absence: with
    // status 'error' and no retained view there is nothing to say 「なし」 about.
    const unread = news.status === 'error' && news.view == null;
    return <div className="at-newssignal is-quiet" aria-label="ニュース/イベント">
      <small>ニュース/イベント</small>
      <span>{unread ? 'ニュースを取得できていません（重大ニュースが無いという意味ではありません）'
        : '直近の重大ニュースなし'}</span>
      {degraded && <em className="ns-degraded">更新失敗・前回取得分を表示</em>}</div>;
  }
  const direction = top.impactDirection;
  const primary = direction?.primaryDirection ?? 'UNCLEAR';
  const bearishTargets = Object.entries(direction?.directionByTarget ?? {})
    .filter(([, value]) => value === 'BEARISH')
    .map(([target]) => NEWS_TARGET_JA[target] ?? target);
  const bullishTargets = Object.entries(direction?.directionByTarget ?? {})
    .filter(([, value]) => value === 'BULLISH')
    .map(([target]) => NEWS_TARGET_JA[target] ?? target);
  return <div className={`at-newssignal is-${primary.toLowerCase()}`}
    data-argus-contract="news-event-signal-v1" aria-label="ニュース/イベント判断">
    <small>ニュースの方向 — チャート観とは独立
      {material.length > 1 && ` · 重大${material.length}件(最重要を表示)`}
      {(news.view?.pendingTranslationCount ?? 0) > 0
        && ` · 要約処理中${news.view?.pendingTranslationCount}通`}
      {degraded && ' · ⚠ 更新失敗・前回取得分'}</small>
    <div className="ns-head">
      <GlossaryTip glossaryKey={primary === 'MIXED' ? 'news_mixed' : ''}>
        <b>{NEWS_DIRECTION_JA[primary] ?? primary}</b>
      </GlossaryTip>
      {primary === 'UNCLEAR' && <span className="ns-unclear">このニュースからは上下を決めない</span>}
      <i>{top.severity}</i>
      <GlossaryTip glossaryKey={top.confirmationState === 'MARKET_CONFIRMED'
        ? 'market_confirmed' : 'market_confirmation_pending'}>
        <em>{top.confirmationState === 'MARKET_CONFIRMED'
          ? '市場確認済み' : '市場確認待ち'}</em>
      </GlossaryTip>
      {newsAgeJa(top) && <span>{newsAgeJa(top)}</span>}
      {direction?.timeHorizon && direction.timeHorizon !== 'UNCLEAR'
        && <span>想定時間軸 {direction.timeHorizon}</span>}
    </div>
    <span className="ns-title">{displayNewsHeadline(top.headlineJa)}</span>
    {(bearishTargets.length > 0 || bullishTargets.length > 0)
      && <span className="ns-targets">
        {bearishTargets.length > 0 && `逆風: ${bearishTargets.join('・')}`}
        {bearishTargets.length > 0 && bullishTargets.length > 0 && ' ／ '}
        {bullishTargets.length > 0 && `追い風: ${bullishTargets.join('・')}`}
      </span>}
    {direction && direction.transmissionChain.length > 0
      && <span className="ns-chain">{direction.transmissionChain.join(' → ')}</span>}
    <span className="ns-note">
      {NEWS_CONSTRAINT_JA[top.executionConstraint ?? 'NO_CONSTRAINT']
        ?? '制約なし'} · ニュースは売買権限を持たない</span>
  </div>;
};

const nextReviewLabel = (code: string | undefined): string | undefined => {
  if (!code) return undefined;
  if (NEXT_REVIEW_REASON_JA[code]) return NEXT_REVIEW_REASON_JA[code];
  // Canonical reason codes remain in the signed decision object for audit.
  // Owner UI must not expose an internal resolver token as an instruction.
  return code.startsWith('resolve.') ? '不足している正本証拠を更新' : '判断条件を再確認';
};
const fmt = (v: number) => v >= 1000 ? v.toLocaleString('ja-JP', { maximumFractionDigits: 1 }) : v.toFixed(2);
const fmtMove = (v: number, suffix = '') => `${fmt(v)}${suffix}`;
const macroTone = (move: { value: number; previous?: number | null }): string =>
  typeof move.previous === 'number' && Number.isFinite(move.previous) && move.previous !== move.value
    ? (move.value > move.previous ? 'is-positive' : 'is-negative') : 'is-neutral';
const shortDate = (value?: string | null) => value ? value.slice(5).replace('-', '/') : '';
const zoneLabel = (kind: '支持' | '抵抗', status: string) =>
  `${kind}${status === 'reclaimed' ? '（回復）' : status === 'broken' ? '（突破済み）' : ''}`;

interface PriceLabel { key: string; label: string; value: number; priority: number; tone: string }

export function layoutPriceLabels(labels: PriceLabel[], toY: (value: number) => number,
  minY = 16, maxY = 308, gap = 17): Array<PriceLabel & { y: number }> {
  const accepted: Array<PriceLabel & { y: number }> = [];
  for (const label of [...labels].sort((a, b) => a.priority - b.priority || b.value - a.value)) {
    let y = Math.max(minY, Math.min(maxY, toY(label.value)));
    for (const row of accepted) {
      if (Math.abs(y - row.y) < gap) y = row.y + (y >= row.y ? gap : -gap);
    }
    y = Math.max(minY, Math.min(maxY, y));
    accepted.push({ ...label, y });
  }
  return accepted.sort((a, b) => a.y - b.y);
}

export function formatInstrumentPrice(value: number, instrumentId: string): string {
  const isJp = instrumentId.startsWith('JP:') || /:\d{4}:/.test(instrumentId);
  return value.toLocaleString(isJp ? 'ja-JP' : 'en-US', {
    minimumFractionDigits: isJp ? 0 : 2,
    maximumFractionDigits: isJp ? (value < 100 ? 1 : 0) : 2,
  });
}

const ProjectionChart: React.FC<{
  projection: TodayProjection;
  snapshotId: string | null;
  responseSnapshotId: string | null;
  snapshotState: string;
  revalidationState: string;
  source?: 'verified-snapshot' | 'headline' | null;
  onActivate?: () => void;
}> = ({ projection, snapshotId, responseSnapshotId, snapshotState,
  revalidationState, source, onActivate }) => {
  const all = projection.history.map((point) => point.value).concat([
    projection.baseLow, projection.baseHigh, projection.upside, projection.downside, projection.invalidation,
    ...(projection.support ? [projection.support.low, projection.support.high] : []),
    ...(projection.resistance ? [projection.resistance.low, projection.resistance.high] : []),
  ]);
  const lo = Math.min(...all), hi = Math.max(...all), span = hi - lo || 1;
  const x = (index: number) => 28 + index / Math.max(1, projection.history.length - 1) * 460;
  const y = (value: number) => 16 + (hi - value) / span * 292;
  const path = projection.history.map((point, index) => `${index ? 'L' : 'M'}${x(index).toFixed(1)},${y(point.value).toFixed(1)}`).join(' ');
  const currentX = 488, forecastX = 570;
  const median = (projection.baseLow + projection.baseHigh) / 2;
  const markerX = (date: string) => {
    const index = projection.history.findIndex((point) => point.date === date);
    return index < 0 ? null : x(index);
  };
  const recent = projection.history.slice(-20);
  const swingHigh = recent.reduce((best, point) => point.value > best.value ? point : best, recent[0]);
  const swingLow = recent.reduce((best, point) => point.value < best.value ? point : best, recent[0]);
  const priceLabels = layoutPriceLabels([
    { key: 'current', label: quoteDisplayLabel(projection.quoteState), value: projection.current, priority: 0, tone: 'current' },
    { key: 'invalid', label: '無効', value: projection.invalidation, priority: 1, tone: 'invalid' },
    { key: 'upper', label: '上限', value: projection.upside, priority: 2, tone: 'upper' },
    { key: 'lower', label: '下限', value: projection.downside, priority: 3, tone: 'lower' },
    ...(projection.support ? [{ key: 'support', label: zoneLabel('支持', projection.support.status), value: projection.support.high,
      priority: 4, tone: 'support' }] : []),
    ...(projection.resistance ? [{ key: 'resistance', label: zoneLabel('抵抗', projection.resistance.status), value: projection.resistance.low,
      priority: 5, tone: 'resistance' }] : []),
    { key: 'swing-high', label: '高値', value: swingHigh.value, priority: 6, tone: 'swing' },
    { key: 'swing-low', label: '安値', value: swingLow.value, priority: 7, tone: 'swing' },
  ], y);
  const displayProbabilities = projection.directionProbabilities
    ?? projection.referenceDirectionProbabilities;
  const strongest = displayProbabilities
    ? (Object.entries(displayProbabilities)
      .sort((a, b) => b[1] - a[1])[0]?.[0] ?? '') : '';
  return <div className="at-projection" role={onActivate ? 'link' : undefined}
    data-argus-contract="today-projection-state-v1"
    data-projection-state="available"
    data-projection-source={source ?? undefined}
    data-projection-snapshot-id={snapshotId ?? undefined}
    data-projection-response-snapshot-id={responseSnapshotId ?? undefined}
    data-projection-snapshot-state={snapshotState}
    data-projection-revalidation-state={revalidationState}
    tabIndex={onActivate ? 0 : undefined} onClick={onActivate}
    onKeyDown={onActivate ? (event) => {
      if (event.key === 'Enter' || event.key === ' ') onActivate();
    } : undefined}>
    <div className="at-proj-heading"><b>{projection.label}｜{projection.horizon}見通し</b>
      <span>{projection.proxyFor ? 'ETF PROXY · ' : ''}{shortDate(projection.asOf)} {quoteDisplayLabel(projection.quoteState)}・{projection.timeframeLabel} · 過去{projection.history.length}日｜予測{projection.horizonDays}日</span>
      {/* v13.5.54: the drawn series is the index; the decision still anchors on
          the verified ETF snapshot, and the owner is told which is which. */}
      {projection.disclosureJa && <em className="at-proj-disclosure">{projection.disclosureJa}</em>}</div>
    <svg viewBox="0 0 720 330" role="img" aria-label={`${projection.label} 実績と${projection.horizonDays}営業日シナリオ`}>
      <defs><linearGradient id="at-band" x1="0" x2="1"><stop offset="0" stopColor="#facc15" stopOpacity=".1"/><stop offset="1" stopColor="#facc15" stopOpacity=".35"/></linearGradient></defs>
      {[.25, .5, .75].map((ratio) => <line key={ratio} x1="28" x2="570"
        y1={16 + ratio * 292} y2={16 + ratio * 292} className="at-proj-grid" />)}
      {projection.support && <rect x="28" width="542" y={y(projection.support.high)}
        height={Math.max(1, y(projection.support.low) - y(projection.support.high))} className="at-proj-support" />}
      {projection.resistance && <rect x="28" width="542" y={y(projection.resistance.high)}
        height={Math.max(1, y(projection.resistance.low) - y(projection.resistance.high))} className="at-proj-resistance" />}
      <line x1="28" x2={forecastX} y1={y(projection.upside)} y2={y(projection.upside)} className="at-proj-up" />
      <line x1="28" x2={forecastX} y1={y(projection.downside)} y2={y(projection.downside)} className="at-proj-down" />
      <line x1={currentX} x2={forecastX} y1={y(projection.invalidation)} y2={y(projection.invalidation)} className="at-proj-inv" />
      <path d={`M${currentX},${y(projection.current)} L${forecastX},${y(projection.baseHigh)} L${forecastX},${y(projection.baseLow)} Z`} fill="url(#at-band)" />
      <path d={path} className="at-proj-actual" />
      {projection.history.map((point, index) => <circle key={`tip:${point.date}`}
        cx={x(index)} cy={y(point.value)} r="7" className="at-proj-tooltip-point">
        <title>{`${point.date} 実績 · 終値 ${formatInstrumentPrice(point.value, projection.instrumentId)} · 高値 ${formatInstrumentPrice(point.high, projection.instrumentId)} · 安値 ${formatInstrumentPrice(point.low, projection.instrumentId)} · 出来高 ${point.volume == null ? '未取得' : point.volume.toLocaleString('ja-JP')}`}</title>
      </circle>)}
      <line x1={currentX} x2={currentX} y1="10" y2="314" className="at-proj-boundary" />
      <text x={currentX - 8} y="12" textAnchor="end" className="at-proj-side-label">実績</text>
      <text x={currentX + 8} y="12" className="at-proj-side-label">予測</text>
      <circle cx={currentX} cy={y(projection.current)} r="4.2" className="at-proj-current" />
      <path d={`M${currentX},${y(projection.current)} C${currentX + 28},${y(projection.current)} ${forecastX - 24},${y(median)} ${forecastX},${y(median)}`} className="at-proj-base" />
      <circle cx={forecastX} cy={y(median)} r="7" className="at-proj-tooltip-point">
        <title>{`${projection.horizonDays}営業日先 予測 · 本線 ${formatInstrumentPrice(projection.baseLow, projection.instrumentId)}–${formatInstrumentPrice(projection.baseHigh, projection.instrumentId)}`}</title>
      </circle>
      {projection.eventMarkers.map((marker) => { const mx = markerX(marker.date); return mx == null ? null
        : <g key={marker.id}><line x1={mx} x2={mx} y1="16" y2="308" className="at-proj-event-line" />
          <circle cx={mx} cy="20" r="3" className="at-proj-event" /></g>; })}
      {projection.turningPointMarkers.map((point) => { const mx = markerX(point.date); return mx == null ? null
        : <path key={point.id} d={`M${mx - 5},300 L${mx},288 L${mx + 5},300 Z`} className="at-proj-turn" />; })}
      <circle cx={x(projection.history.indexOf(swingHigh))} cy={y(swingHigh.value)} r="3" className="at-proj-swing" />
      <circle cx={x(projection.history.indexOf(swingLow))} cy={y(swingLow.value)} r="3" className="at-proj-swing" />
      {priceLabels.map((row) => <g key={row.key} className={`at-proj-chip is-${row.tone}`}>
        <line x1="570" x2="588" y1={y(row.value)} y2={row.y} />
        <rect x="588" y={row.y - 8} width="126" height="16" rx="3" />
        <text x="594" y={row.y + 4}>{row.label} {formatInstrumentPrice(row.value, projection.instrumentId)}</text>
      </g>)}
    </svg>
    <div className="at-proj-levels"><span className="up">上限 <b>{formatInstrumentPrice(projection.upside, projection.instrumentId)}</b></span>
      <span>本線 <b>{formatInstrumentPrice(projection.baseLow, projection.instrumentId)}–{formatInstrumentPrice(projection.baseHigh, projection.instrumentId)}</b></span>
      <span className="down">下限 <b>{formatInstrumentPrice(projection.downside, projection.instrumentId)}</b></span>
      <span className="invalid">無効 <b>{formatInstrumentPrice(projection.invalidation, projection.instrumentId)}</b></span></div>
    {displayProbabilities ? <div className={`at-proj-prob ${
      projection.directionProbabilities ? 'is-verified' : 'is-reference'}`}>
      {/* v13.5.36 (external review): the reference-mode numbers are DEMOTED —
          the ablation showed no out-of-sample edge over the base rate, so the
          lead line says so plainly and the digits render muted/uncolored.
          Verified mode (a future state gated on positive OOS skill) keeps
          the prominent treatment. */}
      <span>{projection.directionProbabilities
        ? `${projection.horizonDays}D 終値方向（検証済み）`
        : `類似局面の分布 — 予測力は未確認（${projection.horizonDays}D・参考のみ）`}</span>
      {(['UP', 'RANGE', 'DOWN'] as const).map((key) => <span key={key}
        className={`${key.toLowerCase()} ${strongest === key ? 'is-max' : ''}`}>{key} <b>{displayProbabilities[key]}%</b></span>)}
      <em>実効n={projection.effectiveSampleCount} · BSS {
        projection.brierSkill == null ? '—' : projection.brierSkill.toFixed(3)}
        {!projection.directionProbabilities && ` · ${projection.probabilityTruth.uncertaintyJa}`}
      </em></div>
      : <div className="at-proj-prob is-suppressed"><b>確率は非表示</b>
        <span>{projection.probabilityTruth.directionalLeanJa} · 根拠{projection.probabilityTruth.evidenceStrength}
          · 実効n={projection.probabilityTruth.effectiveN ?? projection.effectiveSampleCount}
          · {projection.probabilityTruth.uncertaintyJa} · {projection.probabilityTruth.label}</span></div>}
    <div className="at-proj-meta"><b>{projection.directionLabel}</b><span>{projection.horizon} · 反応{projection.reactionDelay == null ? '—' : `${projection.reactionDelay.toFixed(1)}日`}</span><small>{projection.shoConditioningJa ? `${projection.shoConditioningJa} · 実測根拠` : '実測と校正済み根拠'}</small></div>
  </div>;
};

export const ArgusTodayPanel: React.FC<Props> = ({
  view, instruments, selectedSymbol, horizon, chartLoad,
  projectionSource, freshnessNoteJa, shock, newsIntel,
  onMode, onInstrument, onHorizon, onNavigate, onNavigateToAsset, onNavigateToSettings, aiButton, jpNameBySymbol,
}) => {
  const projection = view.projectionsByHorizon[`${horizon}D`] ?? view.projection;
  // v13.5.39: the top command area renders MARKET SIGNALS (SIG-01..07, x / 7)
  // from the real market-view projection — the seven-signal system the owner
  // reads first.  The SDA Seven Sign level stays as a secondary line.
  const decisionEvidence = useDecisionEvidence();
  const topSignals = marketSignalsView(decisionEvidence.marketView?.projection ?? null);
  const actionCopy = {
    BUY: '条件内で新規または追加を検討',
    HOLD: '保有を維持し、判断更新条件を待つ',
    WAIT: '今は動かず、必要な正本証拠の更新を待つ',
    REDUCE: '保有リスクを減らす',
    EXIT: '保有の解消を優先する',
  }[view.finalAction];
  const target = view.canonicalDecision.targets[0];
  const invalidation = view.canonicalDecision.invalidation;
  // v13.5.59 (owner): tapping an event jumps to the events summary itself,
  // not merely to the Alerts tab.
  const openEventDetails = () => {
    onNavigate('notifications');
    const jump = () => document.getElementById('important-events')
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    window.setTimeout(jump, 350);
    window.setTimeout(jump, 1000);
  };
  // v13.5.60 (owner iPhone review 2026-09-07): 重大ニュース and 市場リスク are
  // one block, directly under the decision (they qualify it), listing up to
  // five items instead of one; a tap lands on the matching Alerts section.
  const openNewsDetails = (anchorId?: string) => {
    onNavigate('notifications');
    const jump = () => (document.getElementById(anchorId ?? '')
      ?? document.getElementById('news-intel'))
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    window.setTimeout(jump, 350);
    window.setTimeout(jump, 1000);
  };
  const materialMailEvents = newsIntel.events.filter((event) =>
    event.severity === 'HIGH' || event.severity === 'CRITICAL')
    .filter((event, index, rows) => rows.findIndex((candidate) =>
      candidate.eventType === event.eventType && candidate.source === event.source) === index);
  type NewsRowMemory = Props['newsIntel']['events'][number]['eventMemory'];
  const newsRows: Array<{ id: string; severity: string; kind: '市場リスク' | '重大ニュース';
    headlineJa: string; whyJa: string; metaJa: string; eventMemory: NewsRowMemory }> = [
    ...shock.events.map((event) => ({
      id: event.eventId, severity: event.severity, kind: '市場リスク' as const,
      headlineJa: event.headlineJa, whyJa: event.whyJa, eventMemory: null,
      metaJa: `${event.sources.map((source) => source.name).join(' · ')}${event.asOf ? ` · ${event.asOf}` : ''}`,
    })),
    ...materialMailEvents.map((event) => ({
      id: event.eventId, severity: event.severity, kind: '重大ニュース' as const,
      headlineJa: displayNewsHeadline(event.headlineJa), whyJa: event.whyJa, eventMemory: event.eventMemory,
      metaJa: `${event.source} · ${event.sourceReceivedAt
        ? new Date(event.sourceReceivedAt).toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit' }) : '—'}`
        + ` · ${event.confirmationState === 'MARKET_CONFIRMED' ? '市場確認済み' : '市場確認待ち'}`,
    })),
  ].sort((left, right) => (right.severity === 'CRITICAL' ? 1 : 0) - (left.severity === 'CRITICAL' ? 1 : 0));
  const NEWS_ROWS_CAP = 5;
  React.useEffect(() => {
    try {
      sessionStorage.setItem('argus.todayDecisionMirror', JSON.stringify({
        schemaVersion: 'argus-today-decision-mirror-v1',
        market: view.selectedMarket, selectionMode: view.selectionMode,
        finalAction: view.finalAction, actionScore: view.actionScore,
        decisionId: view.canonicalDecision.decisionId,
        authorityPolicyId: view.canonicalDecision.identities.authorityPolicyId,
        sevenSign: view.canonicalDecision.sevenSign,
        symbol: view.selectedInstrument?.symbol ?? projection?.symbol ?? null,
        instrumentId: projection?.instrumentId ?? null,
        horizon: projection?.horizonDays ?? 5,
        updatedAt: new Date().toISOString(),
      }));
    } catch { /* navigation mirror is best effort and contains no owner data */ }
  }, [projection, view.actionScore, view.canonicalDecision, view.finalAction, view.selectedInstrument,
    view.selectedMarket, view.selectionMode]);
  // The verified warm cache remains the visible authority during background
  // revalidation. Publish an accepted response ID only when the rendered
  // snapshot has atomically moved to that exact identity.
  const coherentResponseSnapshotId = chartLoad.responseSnapshotId === chartLoad.snapshotId
    ? chartLoad.responseSnapshotId : null;
  const revalidationState = chartLoad.snapshotState === 'CACHE_READY_REVALIDATING'
    ? chartLoad.snapshotId ? 'background' : 'invalid'
    : chartLoad.snapshotState === 'NO_CACHE_LOADING' ? 'cold-loading'
    : chartLoad.snapshotState === 'CURRENT_READY' ? 'settled'
    : ['ERROR_WITH_CACHE', 'STALE_FALLBACK'].includes(chartLoad.snapshotState)
      ? 'cached-safe' : 'unavailable';
  return <div className="argus-today"
    data-argus-contract="canonical-market-snapshot-v1"
    data-canonical-snapshot-id={chartLoad.snapshotId ?? undefined}
    data-canonical-response-snapshot-id={coherentResponseSnapshotId ?? undefined}
    data-canonical-response-verification={coherentResponseSnapshotId ? 'verified' : 'unverified'}
    data-canonical-snapshot-state={chartLoad.snapshotState}
    data-canonical-verification={chartLoad.snapshotId ? 'verified' : 'unverified'}
    // v13.5.57: the contract names the DECISION SUBJECT (the verified ETF
    // snapshot), never the series being drawn. Since the headline draws the
    // index, projection.symbol reads N225/NDX while the SDA subject and the
    // release-acceptance contract are 1321/1306/SPY/QQQ.
    data-canonical-instrument={selectedSymbol}
    data-canonical-horizon={`${projection?.horizonDays ?? horizon}D`}>
    <article className={`at-decision at-primary-hero card is-${view.finalAction.toLowerCase()}`}
      aria-label="A.R.G.U.S. Primary Action">
      <div className="at-call">
        {/* v13.5.54: name the instrument the DECISION is anchored on, not the
            series being drawn. Since the headline chart switched to the index,
            view.selectedInstrument follows the projection — reading
            「PRIMARY ACTION · JP N225」 while the SDA subject is 1321 is exactly
            the confusion the index disclosure exists to prevent. */}
        {/* v13.5.61 (owner: 「数字の表示は止めること」): the subject is named in
            words on Today; its code stays on the Holdings page and in the
            data-canonical-instrument contract attribute. */}
        <small>PRIMARY ACTION · {view.selectedMarket}{' '}
          {subjectDisplayName(view.canonicalDecision.subject?.instrumentId
            || view.selectedInstrument?.symbol || '', view.selectedInstrument?.label)}</small>
        <strong style={{ color: ACTION_TONE[view.finalAction] }}>{MARKET_STANCE[view.finalAction]}</strong>
        <span className={`at-authority is-${view.canonicalDecision.status.toLowerCase()}`}>
          {view.canonicalDecision.status === 'EVALUATED' ? '確認済み' : '判断データ確認中'}</span>
      </div>
      <p className="at-impact-copy">{actionCopy}</p>
      {/* v13.5.2: Seven Sign is COMPACT — one summary line + seven chips,
          with meanings and the exact machine reason codes expanding on tap.
          All truthful canonical states are preserved; while everything is
          DATA_GATED the surface stays two short rows instead of a wall.
          Nothing here is computed client-side — it renders the SDA
          projection. */}
      {/* v13.5.59: confidence and data status qualify the decision, so they
          sit directly under it — before the signals, not at the bottom. */}
      <div className="at-kpis"><span>確度 <b>{Math.round(view.canonicalDecision.confidence.valueBps / 100)}%</b></span>
        <span>DATA <b className={`is-${view.dataStatus.tone}`}>● {view.dataStatus.label}</b></span>
        {/* v13.5.60 (owner iPhone review): the reasons behind a non-LIVE DATA
            state are ARGUS-side fetch/freshness facts, not trading information —
            they open on tap instead of occupying the decision area. */}
        {(view.dataQualityReasonCodes.length > 0 || view.dataQualityNotes.length > 0)
          && <details className="at-data-detail">
          <summary>{view.dataQualityReasonCodes.length > 0 ? '何が不足か' : '補足'}を見る</summary>
          {view.dataQualityReasonCodes.length > 0 && <span className="at-data-why">
            {view.dataQualityReasonCodes
              .map((code) => DATA_PARTIAL_REASON_JA[code] ?? `未定義の不足理由（コード: ${code}）`)
              .join(' · ')}（いずれもARGUS側の取得・鮮度の状態です）</span>}
          {view.dataQualityNotes.length > 0 && <span className="at-data-why at-data-note">
            {view.dataQualityNotes.map((code) => DATA_NOTE_JA[code] ?? code).join(' · ')}</span>}
          {/* v13.5.61 (owner: 「どうなれば BUY になるのか」): the exact gate, in words. */}
          <span className="at-data-why at-buy-conditions">BUYが出る条件: ①リスク制約なし ②SHO状態が反転初期・自律反発・回復試験・上昇確認のいずれかで検証済み ③検証済みSHO買い成立レジストリの本番採用（現在は未採用＝構造的に無効） ④保有側の追加許可</span>
        </details>}
        <span className="at-buy-note">BUYは検証完了まで出ません（方針・現在は構造的に無効）</span></div>
      <details className="at-seven" data-argus-contract="seven-sign-ladder-v1"
        data-seven-status={view.canonicalDecision.sevenSign.status}
        data-seven-level={view.actionScore ?? undefined}
        data-market-signals-active={topSignals?.activeCount ?? undefined}
        data-market-signals-total={topSignals?.total ?? undefined}>
        <summary aria-label={topSignals
          ? `Market Signals ${topSignals.countLabel} · Seven Sign ${view.actionScore ?? '未確定'} / 7 · ${view.canonicalDecision.sevenSign.status}`
          : `Seven Sign ${view.actionScore ?? '未確定'} / 7 · ${view.canonicalDecision.sevenSign.status}`}>
          <small>MARKET SIGNALS</small>
          <b data-argus-contract="market-signals-top-v1">
            {topSignals ? topSignals.countLabel : '— / 7'}</b>
          <span className="at-seven-status">
            {topSignals ? `点灯 ${topSignals.activeCount} · ` : ''}
            {view.actionScore == null ? 'Calibration pending · ' : ''}
            判断レベル {view.actionScore == null ? '— / 7' : `${view.actionScore} / 7`}
            {topSignals ? '' : ` · ${view.canonicalDecision.sevenSign.status}`}</span>
          <span className="at-seven-chips" aria-hidden="true">
            {topSignals
              ? topSignals.signals.map((row) => <i key={row.id}
                className={row.state === 'ACTIVE' ? 'is-current' : ''}
                data-signal-id={row.id} data-signal-state={row.state}
                title={`${row.id} ${row.nameJa} ${row.stateJa}`}>{row.id.slice(-1)}</i>)
              : [1, 2, 3, 4, 5, 6, 7].map((level) => <i key={level}
                className={level === view.actionScore ? 'is-current' : ''}
                data-seven-sign-level={level}>{level}</i>)}
          </span>
        </summary>
        <div className="at-seven-detail">
          {topSignals && <div className="at-seven-signals" data-argus-contract="market-signals-top-detail-v1">
            {topSignals.signals.map((row) => <GlossaryTip key={row.id} glossaryKey={row.glossaryKey}>
              <i data-signal-id={row.id} data-signal-state={row.state}>
                {row.id} {row.nameJa} <b>{row.stateJa}</b></i>
            </GlossaryTip>)}
            <small>点灯 = 条件成立のみ数える（判定不能・欠測・古い・要ライセンスは数えない）</small>
          </div>}
          <p className="at-seven-gated">判断レベル（SEVEN SIGN・売買判断側の校正段階）:</p>
          <ul>
            {[1, 2, 3, 4, 5, 6, 7].map((level) => <li key={level}
              className={level === view.actionScore ? 'is-current' : ''}>
              <b>{level}</b> {SEVEN_SIGN_MEANING[level]}
              {level === view.actionScore && <i> ◀ 現在</i>}
            </li>)}
          </ul>
          {view.actionScore == null && <p className="at-seven-gated">
            現在のレベルは未確定（{view.canonicalDecision.sevenSign.status}）：
            {(view.canonicalDecision.sevenSign.reasonCodes.length
              ? view.canonicalDecision.sevenSign.reasonCodes
              : ['reason_unavailable']).map((code) =>
              SEVEN_SIGN_REASON_JA[code] ?? code).join(' / ')}
          </p>}
          {view.actionScore != null
            && view.canonicalDecision.sevenSign.status !== 'PRODUCTION'
            && <p className="at-seven-gated">
            {view.canonicalDecision.sevenSign.status === 'SHADOW'
              ? '校正はシャドー検証中（本番採用前）'
              : '校正データ不足のため参考レベル'}
            {view.canonicalDecision.sevenSign.reasonCodes.length > 0
              && ` · ${view.canonicalDecision.sevenSign.reasonCodes.map((code) =>
                SEVEN_SIGN_REASON_JA[code] ?? code).join(' / ')}`}
          </p>}
        </div>
      </details>
      <div className="at-action-plan" aria-label="行動条件">
        <div><b>今すること</b><span>{actionCopy}</span></div>
        <div><b>目標</b><span>{target ? `${target.value} ${target.unit}` : '検証済み目標なし'}</span></div>
        <div><b>無効化</b><span>{invalidation ? `${invalidation.value} ${invalidation.unit}` : '検証済み無効化条件なし'}</span></div>
        <div><b>次の確認</b><span>{nextReviewLabel(view.canonicalDecision.nextReviewConditionCodes[0])
          ?? (view.nextEvent ? `${view.nextEvent.code} ${formatEventTime(view.nextEvent.at, view.nextEvent.dateOnly)}` : '正本証拠の更新')}</span></div>
      </div>
      <MarketBriefCard />
    </article>

    <section className="at-event card at-news-top" aria-label="重大ニュース・市場リスク"
      data-argus-contract="today-material-news-v1" data-news-count={newsRows.length}>
      <div className="at-head"><b>重大ニュース・市場リスク</b>
        <span>{newsRows.length > NEWS_ROWS_CAP ? `${NEWS_ROWS_CAP} / ${newsRows.length}件` : `${newsRows.length}件`}</span></div>
      <NewsSignalStrip />
      {newsRows.length > 0 && <div className="at-news-rows">
        {newsRows.slice(0, NEWS_ROWS_CAP).map((row) => <button type="button" key={row.id}
          className="at-news-row" data-shock-severity={row.severity}
          onClick={() => openNewsDetails(`news-${row.id}`)}>
          <span className="at-news-row__head"><mark data-severity={row.severity}>{row.severity}</mark>
            <i>{row.kind}</i><b>{row.headlineJa}</b></span>
          <span className="at-news-row__why">{row.whyJa}</span>
          {/* Causal event memory (SHADOW): the flag-recovery / analog evidence
              line stays with the news it qualifies; never an SDA input. */}
          {row.eventMemory && <span className="at-event-memory"
            data-event-memory-status={row.eventMemory.status}
            data-flag-recovery={row.eventMemory.flagRecovery ? 'true' : 'false'}
            data-calibration-mode={row.eventMemory.calibrationMode}>
            <b>{row.eventMemory.flagRecovery ? 'フラグ回収' : 'イベント記憶'}</b>
            {' '}{row.eventMemory.openedDaysAgo != null && row.eventMemory.openedDaysAgo > 0
              ? `${row.eventMemory.openedDaysAgo}日前から監視 · ` : ''}{row.eventMemory.status}
            {row.eventMemory.analogEvidence && ` · 類似 ${row.eventMemory.analogEvidence.independentEpisodeCount} 独立事例`
              + (row.eventMemory.analogEvidence.insufficientEvidence
                ? ' · 根拠不足' : ` · ${row.eventMemory.analogEvidence.confidence}`)}
            {' · 校正 SHADOW · 判断権限なし'}
          </span>}
          <em>{row.metaJa} · タップで詳細</em>
        </button>)}
      </div>}
      {shock.status === 'data' && shock.events.length === 0 && materialMailEvents.length === 0
        && <p className="at-shock-clear">突発の市場ショック: 現在なし
          （監視中: 中央銀行 · 雇用/物価 · 地政学 · 企業イベント）·
          予定されている経済イベントは NEXT EVENT に表示されます</p>}
      {shock.status === 'error' && <p className="at-shock-clear">市場ショック監視: 取得できません</p>}
      {view.news.length > 0 && <button type="button" className="at-news-more"
        onClick={() => openNewsDetails()}>一般ニュース {view.news.length}件 · Alerts で見る ↗</button>}
    </section>

    <section className="at-event card" aria-label="NEXT EVENT">
      <div className="at-head"><b>NEXT EVENT</b>{view.nextEvent && <span>{view.nextEvent.impact.toUpperCase()}</span>}</div>
      {view.nextEvent ? <button type="button" onClick={openEventDetails}>
        <strong>{view.nextEvent.code}</strong><time>{formatEventTime(view.nextEvent.at, view.nextEvent.dateOnly)}</time>
        {view.nextEvent.descriptionJa && <small>{view.nextEvent.descriptionJa.slice(0, 32)}</small>}
      </button> : <p className="at-quiet">{view.eventsAuthorityUnknown
        ? 'イベント情報を取得できていません（予定がないという意味ではありません）'
        : '直近の重要イベントなし'}</p>}
      {/* v13.5.54: a release that just fired must not vanish from Today the
          moment it happens — that is when the owner most needs it. */}
      {view.releasedEvent && <p className="at-released">
        <b>発表済み</b> {view.releasedEvent.code}
        <time>{formatEventTime(view.releasedEvent.at, view.releasedEvent.dateOnly)}</time>
        <span>{view.releasedEvent.lifecycleTier === 'RECENT'
          ? '結果あり' : '結果待ち'}</span>
      </p>}
      <div className="at-coming"><b>COMING 30D</b>
        {view.comingEvents.length
          ? view.comingEvents.map((event) => <span key={event.id}>{event.code} {formatEventTime(event.at, event.dateOnly).split(' ')[0]}</span>)
          : <span>{view.eventsAuthorityUnknown ? '取得待ち' : '予定なし'}</span>}
      </div>
    </section>

    {/* v13.5.0 restoration: the market block — session lamps, the four
        headline charts, and the projection — is the product, so it is always
        visible. Only system/verification detail stays behind the disclosure
        below. */}
    <section className="at-market card" aria-label="市場データ">
      <section className="at-lamps" aria-label="市場セッション">
        {view.sessionLamps.map((lamp) => <span key={lamp.key} className={`is-${lamp.tone}`}>
          <i aria-hidden />{lamp.label}
        </span>)}
      </section>
      <div className="at-mode" role="group" aria-label="表示市場">
        {(['AUTO', 'JP', 'US'] as MarketSelectionMode[]).map((mode) => <button type="button" key={mode}
          aria-pressed={view.selectionMode === mode} className={view.selectionMode === mode ? 'active' : ''}
          onClick={() => onMode(mode)}>{mode}</button>)}
        <span>SELECTED {view.selectedMarket}</span>{view.globalRisk && <em>GLOBAL {view.globalRisk}</em>}
      </div>
      {/* v13.5.1: four lightweight NAME selectors only. All chart, price,
          and probability information lives in the single selected projection
          chart below — no duplicated mini-charts or probability chips. */}
      <div className="at-index-strip at-index-strip--selectors"
        role="group" aria-label="銘柄選択">
        {instruments.map((instrument) => <button type="button"
          key={instrument.symbol}
          data-argus-control="market-instrument"
          data-instrument={instrument.symbol}
          aria-pressed={instrument.symbol === selectedSymbol}
          onClick={() => onInstrument(instrument.market, instrument.symbol)}
          className={instrument.symbol === selectedSymbol ? 'is-selected' : ''}
          title={`${instrument.fullLabel} · underlying ${instrument.underlying}`}>
          <span className="at-index-name">{instrument.shortLabel}</span>
          {/* v13.5.54: the tab now names the INDEX, so badging it "ETF" read as
              a contradiction. The badge carries the instrument the decision is
              still anchored on instead. */}
          {/* v13.5.61 (owner): no codes on Today — the badge says what the
              decision subject IS (the index-tracking ETF), not its number. */}
          <small className="at-index-type">連動ETF</small>
        </button>)}
      </div>
      {freshnessNoteJa && <p className="at-freshness-note">{freshnessNoteJa}</p>}
      <div className="at-chart-controls">
        <div className="at-chart-status" data-snapshot-state={chartLoad.snapshotState}
          data-snapshot-id={chartLoad.snapshotId ?? undefined}>
          <span>{chartLoad.error && projectionSource
            ? 'ライブ再検証は失敗中 · 検証済みデータを表示しています'
            : chartLoad.statusText}</span>
          {projection && chartLoad.loading && chartLoad.loaderVisible &&
            <TriangleStepLoader compact label="" />}
          {chartLoad.error && <button type="button" onClick={chartLoad.retry}>再試行</button>}
        </div>
        <div className="at-horizon" role="group" aria-label="予測期間">{([1, 5, 20] as const).map((value) =>
          <button type="button" key={value} aria-pressed={horizon === value}
            data-argus-control="canonical-horizon" data-horizon={`${value}D`}
            onClick={() => onHorizon(value)}>{value}D</button>)}</div>
      </div>
      {projection ? <ProjectionChart projection={projection}
        snapshotId={chartLoad.snapshotId}
        responseSnapshotId={coherentResponseSnapshotId}
        snapshotState={chartLoad.snapshotState}
        revalidationState={revalidationState}
        source={projectionSource} />
        : <div className="at-projection-missing" aria-busy={chartLoad.loading}
          data-argus-contract="today-projection-state-v1"
          data-projection-state="missing"
          data-projection-snapshot-id={chartLoad.snapshotId ?? undefined}
          data-projection-response-snapshot-id={coherentResponseSnapshotId ?? undefined}
          data-projection-snapshot-state={chartLoad.snapshotState}
          data-projection-revalidation-state={revalidationState}>
        {chartLoad.loaderVisible
          ? <TriangleStepLoader label={chartLoad.slowInitial
            ? '初回データを準備中' : 'データ確認中'} />
          : <span aria-hidden className="at-projection-placeholder" />}
        {!chartLoad.loading && <span>{chartLoad.error ? '取得できません' : '実測OHLCV確認待ち'}</span>}
        {chartLoad.error && <button type="button" onClick={chartLoad.retry}>再試行</button>}
      </div>}
      {view.factors.length > 0 && <div className="at-factors">{view.factors.map((factor) =>
        <span key={factor.key} className={factor.state === '↑' || factor.state === 'LOW' ? 'is-positive'
          : factor.state === '↓' || factor.state === 'HIGH' ? 'is-negative' : 'is-neutral'}>{factor.key} <b>{factor.state}</b></span>)}</div>}
      {view.failedRallyState && view.failedRallyState.state !== 'NONE' && <div className="at-failed-rally">
        <b>上昇失速パターン　{view.failedRallyState.state === 'CONFIRMED' ? '観測済み' : '候補'}</b>
        <span>将来リターンのSkill未検証</span>
      </div>}
    </section>

    {/* v13.5.59: reading order top-down — decision → signals → what is
        coming → the market itself → then the reference market view and the
        news axis, then verification detail. */}
    {/* v13.5.61: the reference market view and the same market's 需給 sit
        together — JP with JP, US with US — instead of alternating. */}
    {/* v13.5.61 (owner): Japan first, then the US — each market's live line,
        market view, 需給 and (for the US) MACRO in its own block, never mixed. */}
    <section className="at-event card at-context" aria-label="市場観・需給（参考）">
      <div className="at-context__block" data-market="JP">
        <small className="at-context__title">日本株</small>
        <MarketViewStrip jpNames={jpNameBySymbol} />
        {view.positioningByMarket.JP.length > 0 && <div className="at-positioning">
          <small>JP 需給</small>
          <div className="at-position-rows">
            {view.positioningByMarket.JP.map((row) => <div key={row.key} className={`is-${row.tone ?? 'neutral'}`}>
              <b>{row.label}</b><span>{row.value}</span>{row.detail && <em>{row.detail}</em>}</div>)}
          </div>
        </div>}
      </div>
      <div className="at-context__block" data-market="US">
        <small className="at-context__title">米国株</small>
        {view.positioningByMarket.US.length > 0 && <div className="at-positioning at-positioning--us">
          <small>US 需給</small>
          <div className="at-position-rows">
            {view.positioningByMarket.US.map((row) => <div key={row.key} className={`is-${row.tone ?? 'neutral'}`}>
              <b>{row.label}</b><span>{row.value}</span>{row.detail && <em>{row.detail}</em>}</div>)}
          </div>
        </div>}
        {view.macroMoves.length > 0 && <div className="at-macro">
          <small>MACRO</small>
          <div className="at-rows at-macro-rows">
            {view.macroMoves.map((move) => <div key={move.id} className={macroTone(move)}>
              <b>{move.label}</b><span>{fmtMove(move.value, move.suffix)}</span>
              <em>{move.directionLabel ?? '→'} · {shortDate(move.asOf)}</em></div>)}
          </div>
        </div>}
        {view.positioningByMarket.US.length === 0 && view.macroMoves.length === 0
          && <span className="at-quiet">米国株の需給・MACROは取得待ち</span>}
      </div>
    </section>

    <details className="at-evidence card">
      <summary>根拠・市場データ・システム情報</summary>
      <div className="at-details">
        <div><b>AUTHORITY</b><span>{view.canonicalDecision.identities.authorityPolicyId ?? 'unavailable'}</span></div>
        <div><b>DECISION ID</b><span>{view.canonicalDecision.decisionId}</span></div>
        <div><b>DATA QUALITY</b><span>{view.dataStatus.label}</span></div>
        <div><b>BACKUP</b><span>{view.systemStatus.backup}</span></div>
        <div><b>RULE</b><span>{view.systemStatus.rule}</span></div>
        <div><b>SOURCE</b><span>{[...new Set(view.factors.map((factor) => factor.source).filter(Boolean))].join(' / ') || '—'}</span></div>
        {projection && <><div><b>PROJECTION</b><span>{projection.methodLabel}</span></div>
          <div><b>REPLAY</b><span>類似{projection.rawSampleCount} · episode {projection.episodeCount} · 実効{projection.effectiveSampleCount}</span></div>
          <div><b>CALIBRATION</b><span>{projection.calibrationStatus}
            {projection.modelBrier == null ? '' : ` · Brier ${projection.modelBrier.toFixed(3)}`}
            {projection.brierSkill == null ? ' · Skillなし/基準予測以下' : ` · BSS ${projection.brierSkill.toFixed(3)}`}</span></div>
          <div><b>EXPECTED 5D</b><span>{projection.expectedValue?.expectedReturn == null ? '未算出'
            : `EV ${(projection.expectedValue.expectedReturn * 100).toFixed(2)}% · q10 ${((projection.expectedValue.q10 ?? 0) * 100).toFixed(2)}% · R/R ${projection.expectedValue.rewardRisk?.toFixed(2) ?? '—'}`}</span></div>
          <div><b>INSTRUMENT</b><span>{projection.assetType}{projection.proxyFor ? ` · ETF PROXY for ${projection.proxyFor}` : ''} · {projection.licenseStatus}</span></div></>}
        {projection && <div><b>HISTORY</b><span>{projection.sourceHistoryCount.toLocaleString('ja-JP')}営業日
          {projection.historyStart ? ` · ${projection.historyStart}–${projection.historyEnd ?? '現在'}` : ''}
          {projection.sourceHistoryCount < 2_000 ? ' · 10年未達' : ' · 約10年'}</span></div>}
        {view.canonicalDecision.missingReasonCodes.map((line) => <p
          key={`missing:${line}`} data-reason-code={line}>不足: {missingReasonJa(line)}</p>)}
        {view.canonicalDecision.dissentReasonCodes.map((line) => <p
          key={`dissent:${line}`} data-reason-code={line}>補足意見: {dissentReasonJa(line)}</p>)}
        <div className="at-detail-actions">{aiButton}<button type="button"
          onClick={() => onNavigateToSettings
            ? onNavigateToSettings('recovery') : onNavigate('settings')}>Settings / Recovery</button></div>
      </div>
    </details>

    {view.holdingsReview.length > 0 && <section className="at-priorities card" aria-label="OWNER PRIORITIES">
      <div className="at-head"><b>OWNER PRIORITIES</b><span>MAX 3</span></div>
      {view.holdingsReview.map((item) => {
        const content = <>
          <span className="at-priority-title">
            <b>{item.name?.trim() || item.symbol}</b><em>{item.isHeld ? '保有' : 'WATCH'}</em>
            <mark className={`is-${(item.impact ?? 'Neutral').toLowerCase()}`}>{item.impact ?? 'Neutral'}</mark>
            <strong>{item.actionJa ?? item.statusJa}</strong>
          </span>
          <span className="at-priority-impact">{item.reasonJa}</span>
          <small>次に確認: {item.checkNextJa || '証拠更新待ち'}
            {item.whatWouldChangeJa ? ` · 判断更新: ${item.whatWouldChangeJa}` : ''}</small>
        </>;
        return onNavigateToAsset ? <button type="button" key={item.symbol}
          onClick={() => onNavigateToAsset(item.symbol)}>{content}</button>
          : <div key={item.symbol}>{content}</div>;
      })}
    </section>}



  </div>;
};

const Compact: React.FC<{ title: string; children: React.ReactNode; className?: string;
  onActivate?: () => void }> = ({ title, children, className = '', onActivate }) =>
  <section className={`at-compact card ${className}`} role={onActivate ? 'link' : undefined}
    tabIndex={onActivate ? 0 : undefined} onClick={onActivate}
    onKeyDown={onActivate ? (event) => { if (event.key === 'Enter' || event.key === ' ') onActivate(); } : undefined}>
    <h3>{title}{onActivate && <span aria-hidden>↗</span>}</h3>{children}</section>;

export default ArgusTodayPanel;
