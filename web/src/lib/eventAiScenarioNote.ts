// v13.5.59 (owner: 「AIシナリオ待ちとはなぜだ」). The pre-release AI scenario is
// produced by the scheduled event analysis, which the cost policy can switch
// off (eventOptIn=false: only news summaries run). "Waiting" was never true
// in that state — say what is actually the case.
export function eventAiScenarioNote(cost: { eventOptIn?: boolean; mode?: string } | null | undefined): string {
  if (cost && cost.eventOptIn === false) {
    return 'AIシナリオは未実行（コスト方針でイベントAIがオフ。Settings › コスト方針で変更できます）';
  }
  if (cost && cost.mode === 'SCHEDULED_AI') return 'AIシナリオは日次予算内で順次生成（次回の予定枠で更新）';
  return 'AIシナリオ 生成待ち…';
}
