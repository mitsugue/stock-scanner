// V11.12.0 — Action Priority Engine (device-local TS port of
// argus_action_priority.py — the scoring source of truth). ここでは実保有
// (数量・比率・買い増し余地)を加味して「今日これを見る」を計算する。
// 注意配分であり売買指示ではない。保有情報は端末外に出ない。

export type PriorityRank = 'P0' | 'P1' | 'P2' | 'P3' | 'Watch' | 'Ignore' | 'Unknown';
export type ActionLabel = 'CHECK_NOW' | 'WAIT_EVENT' | 'AVOID_CHASE' | 'ADD_ONLY_ON_PULLBACK'
  | 'SMALL_ADD_ALLOWED' | 'MONITOR' | 'REVIEW_POSITION' | 'INVESTIGATE'
  | 'IGNORE_TODAY' | 'NO_ACTION' | 'UNKNOWN';

export const RANK_JA: Record<PriorityRank, string> = {
  P0: '最優先確認', P1: '今日の優先', P2: '重要(急がない)', P3: '参考',
  Watch: '監視', Ignore: '今日は重要度低', Unknown: '判定保留',
};
export const LABEL_JA: Record<ActionLabel, string> = {
  CHECK_NOW: 'いま確認', WAIT_EVENT: 'イベント待ち', AVOID_CHASE: '追いかけ買い注意',
  ADD_ONLY_ON_PULLBACK: '買うなら押し目限定', SMALL_ADD_ALLOWED: '小さく買い増し可',
  MONITOR: '監視継続', REVIEW_POSITION: 'ポジション点検', INVESTIGATE: '要調査',
  IGNORE_TODAY: '今日は放置可', NO_ACTION: '対応不要', UNKNOWN: '判定保留',
};
export const RANK_TONE: Record<PriorityRank, string> = {
  P0: 'var(--value-negative)', P1: 'var(--amber, #fbbf24)', P2: 'var(--accent)',
  P3: 'var(--text-muted)', Watch: 'var(--text-muted)', Ignore: 'var(--text-faint)',
  Unknown: 'var(--text-faint)',
};

export interface APInputs {
  symbol: string; market: string; assetName: string;
  isHeld: boolean; weightPct?: number | null; concentrationRisk?: string | null;
  positionRiskLevel?: string | null; readiness?: string | null;
  sdRank?: string | null; sdCondition?: string | null;
  flowClass?: string | null; instStance?: string | null; instDirect?: boolean;
  /** true=event known / false=calendar known clear / null=calendar UNKNOWN. */
  eventPending?: boolean | null; eventName?: string | null;
  /** true/false=regime known / null=regime UNKNOWN (owner spec §2). */
  regimeRiskOff?: boolean | null; changePct?: number | null; priorRunupPct?: number | null;
  dataMissing?: string[]; dqContradictedAvoidChase?: boolean; dqSupported?: boolean;
  /** v13.5.61: the portfolio risk types behind positionRiskLevel (drawdown / concentration / …). */
  positionRiskTypes?: string[]; plPct?: number | null;
}

// v13.5.61 (owner: 「BAD は何を根拠に出しているのか」): name the basis instead of
// 「リスク信号」. Every entry maps a positionExposure riskType to plain words.
export const POSITION_RISK_BASIS_JA: Record<string, (i: { plPct?: number | null }) => string> = {
  drawdown: (i) => `含み損${typeof i.plPct === 'number' ? ` ${i.plPct.toFixed(1)}%` : ''}（−25%基準）`,
  concentration: () => '一銘柄への集中',
  theme_overcrowding: () => 'テーマ集中',
  held_flow_risk: () => '大口フローの悪化',
  supply_demand: () => '需給ランクの悪化',
  event_risk: () => 'イベント接近',
};
export function positionRiskBasisJa(i: { positionRiskTypes?: string[]; plPct?: number | null }): string {
  const parts = (i.positionRiskTypes ?? []).map((type) => POSITION_RISK_BASIS_JA[type]?.(i) ?? '').filter(Boolean);
  return parts.join('・');
}

export interface APItem {
  symbol: string; market: string; assetName: string;
  priorityRank: PriorityRank; priorityRankJa: string; priorityScore: number;
  category: string; actionLabel: ActionLabel; actionLabelJa: string;
  titleJa: string; whyJa: string; checkNextJa: string; whatWouldChangeJa: string;
  blockingReason: string; isHeld: boolean; confidence: number;
}

export function buildItem(i: APInputs): APItem {
  let score = 0; let adverse = 0;
  let category = 'no_action'; let label: ActionLabel = 'NO_ACTION'; let blocking = 'none';
  const missing = i.dataMissing ?? [];
  // Tri-state context (owner spec 2026-08-22 §2): UNKNOWN is never collapsed
  // to FALSE — absent calendar/regime knowledge stays visible and withholds
  // add-side labels instead of silently reading as "clear".
  const eventPending = i.eventPending === true ? true
    : i.eventPending === false ? false : null;
  const regimeRiskOff = i.regimeRiskOff === true ? true
    : i.regimeRiskOff === false ? false : null;
  const unknownContext: string[] = [];
  if (eventPending === null) unknownContext.push('イベント情報未確認');
  if (regimeRiskOff === null) unknownContext.push('レジーム情報未確認');
  const sdRank = i.sdRank ?? '', sdCond = i.sdCondition ?? '', flow = i.flowClass ?? '';

  if (i.isHeld) {
    score += 30;
    if ((i.weightPct ?? 0) >= 25) score += 10;
    if (i.concentrationRisk === 'critical') score += 15;
    else if (i.concentrationRisk === 'high') score += 8;
  }
  if (flow === 'panic_selling' || flow === 'distribution') {
    score += i.isHeld ? 25 : 12; adverse++;
    category = 'flow_watch'; label = i.isHeld ? 'CHECK_NOW' : 'MONITOR';
  }
  if (sdRank === 'D' || sdRank === 'E') {
    score += i.isHeld ? 20 : 10; adverse++;
    if (category === 'no_action') { category = 'supply_demand_watch'; label = 'MONITOR'; }
    blocking = 'supply_demand_bad';
  }
  if (i.positionRiskLevel === 'high' || i.positionRiskLevel === 'critical') {
    score += 20; adverse++; category = 'held_risk';
  }
  if ((i.changePct ?? 0) <= -5 && i.isHeld) { score += 15; adverse++; category = 'held_risk'; }
  if (regimeRiskOff === true && i.isHeld && adverse) {
    score += 8;
    if (blocking === 'none') blocking = 'regime_headwind';
  }
  if (i.readiness === 'avoid_chase' || flow === 'retail_chase' || (i.priorRunupPct ?? 0) >= 15) {
    score += i.isHeld ? 15 : 12;
    category = 'avoid_chase'; label = 'AVOID_CHASE';
    if (blocking === 'none' && (i.priorRunupPct ?? 0) >= 15) blocking = 'overextended';
  }
  if (sdCond === 'squeeze_prone') {
    score += 10;
    if (category === 'no_action' || category === 'supply_demand_watch') {
      category = 'avoid_chase'; label = 'AVOID_CHASE';
    }
  }
  if (i.readiness === 'add_only_on_pullback'
    || (['S', 'A', 'B'].includes(sdRank) && sdCond !== 'squeeze_prone'
      && i.readiness !== 'wait' && i.readiness !== 'unknown')) {
    score += 10;
    if (category === 'no_action') { category = 'add_only_on_pullback'; label = 'ADD_ONLY_ON_PULLBACK'; }
  }
  if (i.readiness === 'add_allowed_small' && !adverse && eventPending === false && category === 'no_action') {
    score += 6;
    if (sdCond === 'improving_but_heavy') {
      category = 'add_only_on_pullback'; label = 'ADD_ONLY_ON_PULLBACK';   // 重い間は全緑にしない
    } else {
      category = 'add_candidate'; label = 'SMALL_ADD_ALLOWED';
    }
  }
  if (i.instStance === 'bullish' || i.instStance === 'bearish') {
    score += i.instDirect ? 8 : 3;
    if (category === 'no_action') { category = 'institutional_watch'; label = 'MONITOR'; }
  }
  if (eventPending) {
    score += i.isHeld ? 15 : 8;
    blocking = 'event_pending';
    if (['SMALL_ADD_ALLOWED', 'ADD_ONLY_ON_PULLBACK', 'NO_ACTION', 'MONITOR'].includes(label)) {
      category = 'event_wait'; label = 'WAIT_EVENT';
    }
  } else if (eventPending === null
      && (label === 'SMALL_ADD_ALLOWED' || label === 'ADD_ONLY_ON_PULLBACK')) {
    // Calendar knowledge is absent — an add-side label needs a KNOWN clear
    // calendar, so the judgment is withheld instead of silently granted.
    if (blocking === 'none') blocking = 'unknown';
    category = 'unknown'; label = 'UNKNOWN';
  }
  if (missing.length) {
    score += i.isHeld ? 15 : 4;
    // Missing price/session/FX is absence of entry/add authority for held and
    // unheld assets alike. Defensive CHECK/WAIT/AVOID labels remain visible.
    if (category === 'no_action'
        || label === 'SMALL_ADD_ALLOWED' || label === 'ADD_ONLY_ON_PULLBACK') {
      category = 'data_missing';
      label = i.isHeld ? 'INVESTIGATE' : 'UNKNOWN';
    }
    // A concrete data-missing reason outranks the generic calendar-unknown
    // blocker when both apply.
    if (blocking === 'none' || blocking === 'unknown') {
      blocking = missing.join(' ').includes('保有数量') ? 'missing_position_data' : 'data_stale';
    }
  }
  let dqAdj = 0;
  if (i.dqContradictedAvoidChase) dqAdj = -0.05;
  else if (i.dqSupported) dqAdj = 0.05;

  let rank: PriorityRank;
  if (i.isHeld && adverse >= 2 && score >= 70) rank = 'P0';
  else if (i.isHeld && eventPending === true && adverse >= 1 && score >= 60) rank = 'P0';
  else if (score >= 45) rank = 'P1';
  else if (score >= 25) rank = 'P2';
  else if (score >= 12) rank = 'P3';
  else rank = score >= 6 ? 'Watch' : 'Ignore';
  if (i.isHeld && rank === 'Ignore') rank = 'Watch';
  if (rank === 'P0' && (label === 'MONITOR' || label === 'NO_ACTION')) label = 'CHECK_NOW';
  if (rank === 'Ignore') { category = 'no_action'; label = 'IGNORE_TODAY'; }

  const confidence = Math.min(0.85, Math.max(0.2, 0.35 + score / 200 + dqAdj
    - ((missing.length || unknownContext.length) ? 0.1 : 0)));
  const t = texts(rank, category, i, sdRank, sdCond, missing);
  return {
    symbol: i.symbol.toUpperCase(), market: i.market, assetName: i.assetName,
    priorityRank: rank, priorityRankJa: RANK_JA[rank], priorityScore: Math.round(score * 10) / 10,
    category, actionLabel: label, actionLabelJa: LABEL_JA[label],
    titleJa: t.title, whyJa: t.why, checkNextJa: t.check, whatWouldChangeJa: t.change,
    blockingReason: blocking, isHeld: i.isHeld, confidence: Math.round(confidence * 100) / 100,
  };
}

function texts(rank: PriorityRank, category: string, i: APInputs,
  sdRank: string, sdCond: string, missing: string[]) {
  const name = i.assetName || i.symbol;
  const heldJa = i.isHeld ? '保有中の' : '';
  switch (category) {
    case 'held_risk': return {
      title: `最優先確認：${heldJa}${name}にリスク信号が重なっています`,
      why: `${heldJa}${name}${(i.changePct ?? 0) <= -5 ? `が${i.changePct!.toFixed(1)}%と大きく動き、` : 'に'}${sdRank === 'D' || sdRank === 'E' || i.flowClass === 'panic_selling' || i.flowClass === 'distribution' ? '需給・フローの悪化が重なっています。' : positionRiskBasisJa(i) ? `保有リスクが出ています（根拠: ${positionRiskBasisJa(i)}）。` : 'リスク信号が出ています。'}`,
      check: 'まず下落理由(原因の詳細)と大口フローの継続を確認',
      change: '売り圧力の推定が消えるか、公式材料で原因が確定すれば優先度は下がります',
    };
    case 'event_wait': { const ev = i.eventName || '重要イベント'; return {
      title: `イベント待ち：${ev}の結果を見てから`,
      why: `${ev}の発表前のため、${name}の積極的な判断は結果と初動反応を確認してからが安全です。`,
      check: `${ev}の結果発表と直後の金利・指数反応を確認`,
      change: 'イベント通過後、反応が想定内なら通常の優先度に戻ります',
    }; }
    case 'avoid_chase': return {
      title: `追いかけ注意：${name}`,
      why: sdCond === 'squeeze_prone'
        ? '踏み上げ余地はありますが、買い戻し主導の可能性があり新規の大口買いとは未確定です。'
        : '急伸直後で高値掴みのリスクが高い局面です。この上昇を追う前に保有比率と需給を確認してください。',
      check: '出来高を伴う押し目が来るか、上昇の主体が入れ替わるかを確認',
      change: '押し目形成または実測フローでの大口買い確認で評価が変わります',
    };
    case 'add_only_on_pullback': return {
      title: `押し目限定候補：${name}`,
      why: `${['S', 'A', 'B'].includes(sdRank) ? `需給ランク${sdRank}で土台は悪くありませんが、` : ''}既に上昇している場合は追わず、出来高を伴う押し目を待つ方が安全です。`,
      check: '押し目の深さと出来高、需給の次回更新を確認',
      change: '需給悪化またはイベント接近で候補から外れます',
    };
    case 'add_candidate': return {
      title: `小さく買い増し可：${name}`,
      why: '明確なブロック要因はありません。ただし一度に大きく買わず、小さく分けるのが基本です。',
      check: '翌営業日の継続性(出来高を伴うか)を確認',
      change: '需給・フロー・イベントのいずれかが悪化すれば見送りへ',
    };
    case 'data_missing': return {
      title: `データ確認：${heldJa}${name}`,
      why: `判定に必要なデータが不足しています(${missing.slice(0, 2).join(' / ')})。`,
      check: 'データ更新後(平日の巡回)に再確認',
      change: 'データ取得後に通常の優先度判定に戻ります',
    };
    case 'supply_demand_watch': case 'flow_watch': return {
      title: `需給/フロー注意：${name}`,
      why: `${sdRank ? `需給ランク${sdRank}` : 'フロー'}に注意信号が出ています。`,
      check: '戻り局面で売りが出るか、翌営業日の継続を確認',
      change: '信号が2営業日続けば優先度を上げ、消えれば下げます',
    };
    case 'unknown': return {
      title: `判定保留：${name}`,
      why: 'イベントカレンダーの状態が未確認のため、追加系の判定を保留しています(不明はクリア扱いにしません)。',
      check: 'カレンダー/レジーム情報の回復後に再判定',
      change: '不明が解消すれば通常の優先度判定に戻ります',
    };
    default: return rank === 'Ignore' ? {
      title: `今日は重要度低：${name}`,
      why: '大きな材料・需給変化・保有リスクがありません。',
      check: '定例の巡回のみで十分です',
      change: '±2%超の動き・イベント・需給変化で再浮上します',
    } : {
      title: `${RANK_JA[rank]}：${name}`,
      why: '複数レイヤーの信号を統合した優先度です。',
      check: '各レイヤーの詳細(需給/フロー/イベント)を確認',
      change: '主要な信号の変化で優先度が変わります',
    };
  }
}

const ORDER: PriorityRank[] = ['P0', 'P1', 'P2', 'P3', 'Watch', 'Ignore', 'Unknown'];
export function rankItems(items: APItem[], cap = 12): APItem[] {
  return [...items].sort((a, b) =>
    ORDER.indexOf(a.priorityRank) - ORDER.indexOf(b.priorityRank)
    || b.priorityScore - a.priorityScore).slice(0, cap);
}

export function briefJa(items: APItem[]): string {
  const p0 = items.filter((x) => x.priorityRank === 'P0').length;
  const p1 = items.filter((x) => x.priorityRank === 'P1').length;
  if (!p0 && !p1) return '今日は最優先の確認事項はありません。';
  const top = items.find((x) => x.priorityRank === 'P0' || x.priorityRank === 'P1');
  return `今日は P0 ${p0}件 / P1 ${p1}件。まず「${top?.titleJa ?? ''}」から。`;
}

/** Pro Handoff / AI Review — device-local held-aware priority lines. */
export function apHandoffTextJa(items: APItem[]): string {
  if (!items.length) return '';
  const L = ['## Action Priority (device-local, held-aware)', briefJa(items)];
  for (const it of items.filter((x) => x.priorityRank === 'P0' || x.priorityRank === 'P1').slice(0, 5)) {
    L.push(`- [${it.priorityRank}${it.isHeld ? '・保有' : ''}] ${it.titleJa} — ${it.whyJa.slice(0, 70)}`);
  }
  L.push('注意: 注意配分の優先度であり売買指示ではない。');
  return L.join('\n');
}
