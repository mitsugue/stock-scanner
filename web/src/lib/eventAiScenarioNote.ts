// v13.5.59 (owner: 「AIシナリオ待ちとはなぜだ」) / v13.5.62 (GPT review item 6:
// 「未生成時は実際の理由と次回実行予定を表示」) / v13.5.63 (GPT review item 4:
// 「キーの有無、予算、実行許可、失敗理由を分けて確認」「画面で計算した予定枠と
// 実際の実行予定を区別」). The pre-release AI scenario is produced by the
// scheduled macro-event-analysis workflow (weekdays, every two hours at :35
// UTC) under the cost policy. When nothing has been generated the line states
// each gate separately — key, permission, budget, last run, last refusal —
// and labels the next slot as a COMPUTED cron slot, not a promise.

export interface EventAiCostFacts {
  eventOptIn?: boolean;
  mode?: string;
  lastExecutionReason?: string | null;
  lastExecutionPurpose?: string | null;
  lastExecutionAt?: string | null;
  lastSkip?: { purpose?: string; reason?: string; at?: string } | null;
  scheduledLane?: {
    dailyBudgetUsd?: number; spentTodayUsd?: number; eventRemainingUsd?: number;
    eventRunsToday?: number; eventRunsPerDay?: number; eventLaneOpen?: boolean;
  } | null;
  openaiKeyConfigured?: boolean | null;
}

/** Next weekday :35 slot of an even UTC hour, as an ISO instant. */
export function nextEventAiSlot(now: Date = new Date()): Date {
  const t = new Date(now.getTime());
  t.setUTCSeconds(0, 0);
  for (let i = 0; i < 24 * 4; i += 1) {
    const candidate = new Date(Date.UTC(t.getUTCFullYear(), t.getUTCMonth(), t.getUTCDate(), t.getUTCHours(), 35));
    if (candidate.getTime() > now.getTime() && candidate.getUTCHours() % 2 === 0
        && candidate.getUTCDay() >= 1 && candidate.getUTCDay() <= 5) return candidate;
    t.setUTCHours(t.getUTCHours() + 1);
  }
  return t;
}

const SKIP_REASON_JA: Record<string, string> = {
  scheduled_daily_budget_exhausted: '日次予算を使い切った',
  scheduled_event_runs_exhausted: '本日のイベント実行回数上限',
  scheduled_scope_required: '自動実行の対象外の用途',
  event_opt_in_disabled: 'イベントAIがオフ',
  deterministic_mode: '自動AIは停止中（DETERMINISTIC）',
  manual_only: '手動実行のみのモード',
  cost_unknown: '見積コスト不明',
  event_budget_exceeded: '1件あたりの予算超過',
  provider_disabled: 'プロバイダ無効',
};

function jstClock(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const t = new Date(iso);
  if (!Number.isFinite(t.getTime())) return null;
  return t.toLocaleString('ja-JP', { timeZone: 'Asia/Tokyo', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) + ' JST';
}

export function eventAiScenarioNote(
  cost: EventAiCostFacts | null | undefined,
  now: Date = new Date(),
): string {
  if (cost && cost.eventOptIn === false) {
    return 'AIシナリオは未実行（コスト方針でイベントAIがオフ。Settings › コスト方針で変更できます）';
  }
  if (cost && cost.mode === 'SCHEDULED_AI') {
    const slot = nextEventAiSlot(now).toLocaleString('ja-JP', { timeZone: 'Asia/Tokyo', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    const parts: string[] = ['AIシナリオ未生成'];
    if (cost.openaiKeyConfigured === true) parts.push('鍵 設定済');
    else if (cost.openaiKeyConfigured === false) parts.push('鍵 未設定（OpenAI）');
    parts.push(`実行許可 ${cost.eventOptIn ? 'ON' : 'OFF'}`);
    const lane = cost.scheduledLane;
    if (lane && typeof lane.dailyBudgetUsd === 'number') {
      const spent = typeof lane.spentTodayUsd === 'number' ? lane.spentTodayUsd : 0;
      const left = typeof lane.eventRemainingUsd === 'number' ? lane.eventRemainingUsd : Math.max(0, lane.dailyBudgetUsd - spent);
      parts.push(`予算 残$${left.toFixed(2)}（本日$${spent.toFixed(2)}/$${lane.dailyBudgetUsd.toFixed(2)}）`);
      if (typeof lane.eventRunsToday === 'number' && typeof lane.eventRunsPerDay === 'number') {
        parts.push(`イベント実行 ${lane.eventRunsToday}/${lane.eventRunsPerDay}回`);
      }
    }
    const lastPurpose = cost.lastExecutionPurpose ?? cost.lastExecutionReason ?? null;
    const lastAt = jstClock(cost.lastExecutionAt);
    parts.push(lastPurpose ? `直近のAI実行: ${lastPurpose}${lastAt ? ` ${lastAt}` : ''}` : '直近のAI実行なし');
    if (cost.lastSkip && cost.lastSkip.reason) {
      const why = SKIP_REASON_JA[cost.lastSkip.reason] ?? cost.lastSkip.reason;
      const at = jstClock(cost.lastSkip.at);
      parts.push(`直近の見送り: ${cost.lastSkip.purpose ?? ''} ${why}${at ? ` ${at}` : ''}`.replace(/\s+/g, ' ').trim());
    }
    parts.push(`次のcron予定枠（計算値） ${slot} JST（平日2時間おき・実行は方針の許可次第）`);
    return parts.join(' · ');
  }
  return 'AIシナリオ 生成待ち…';
}

export interface EventAiRunMeta {
  requestedModel?: string; returnedModel?: string; fallbackModel?: string;
  completedAt?: string; inputTokens?: number; outputTokens?: number; estUsd?: number;
}

/** v13.5.63 (GPT review item 6): what generated the saved scenario, on tap. */
export function eventAiRunMetaJa(generatedAt: string | null | undefined, ai: EventAiRunMeta | null | undefined): string | null {
  const when = jstClock(ai?.completedAt ?? generatedAt);
  if (!when && !ai) return null;
  const parts: string[] = [];
  if (when) parts.push(`生成 ${when}`);
  if (ai?.requestedModel) {
    const answered = ai.returnedModel && ai.returnedModel !== ai.requestedModel
      ? `（応答 ${ai.returnedModel}）` : ai.returnedModel ? '（応答一致）' : '（応答モデル未記録）';
    parts.push(`モデル ${ai.requestedModel}${answered}`);
  } else if (ai?.fallbackModel) {
    parts.push(`モデル 代替 ${ai.fallbackModel}`);
  } else {
    parts.push('モデル 記録なし（v13.5.63より前の生成）');
  }
  if (ai?.fallbackModel && ai.requestedModel) parts.push(`代替 ${ai.fallbackModel}`);
  if (typeof ai?.estUsd === 'number') parts.push(`推定 $${ai.estUsd.toFixed(4)}`);
  if (typeof ai?.inputTokens === 'number' && typeof ai?.outputTokens === 'number') {
    parts.push(`${ai.inputTokens}+${ai.outputTokens} tok`);
  }
  return parts.join(' · ');
}
