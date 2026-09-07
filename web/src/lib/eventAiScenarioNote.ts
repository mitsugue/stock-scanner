// v13.5.59 (owner: 「AIシナリオ待ちとはなぜだ」) / v13.5.62 (GPT review item 6:
// 「未生成時は実際の理由と次回実行予定を表示」). The pre-release AI scenario is
// produced by the scheduled macro-event-analysis workflow (weekdays, every two
// hours at :35 UTC) under the cost policy. When nothing has been generated
// the line states the policy's own last decision and the next slot in JST.

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

export function eventAiScenarioNote(
  cost: { eventOptIn?: boolean; mode?: string; lastExecutionReason?: string | null } | null | undefined,
  now: Date = new Date(),
): string {
  if (cost && cost.eventOptIn === false) {
    return 'AIシナリオは未実行（コスト方針でイベントAIがオフ。Settings › コスト方針で変更できます）';
  }
  if (cost && cost.mode === 'SCHEDULED_AI') {
    const slot = nextEventAiSlot(now).toLocaleString('ja-JP', { timeZone: 'Asia/Tokyo', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    const reason = cost.lastExecutionReason ? `直近のAI実行: ${cost.lastExecutionReason}` : '直近のAI実行なし';
    return `AIシナリオ未生成 · 次回の予定枠 ${slot} JST（平日2時間おき）· ${reason}`;
  }
  return 'AIシナリオ 生成待ち…';
}
