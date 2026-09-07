export interface MarketLedgerHistoryPoint {
  periodEnd: string; value: number | null; unit: string; availableFrom?: string;
}
export interface MarketLedgerBacktestSummary {
  average1dPct?: number | null; average5dPct?: number | null; average20dPct?: number | null;
  hitRate5d?: number | null; falsePositiveRate5d?: number | null;
  maxDrawdownPct?: number | null; maxRisePct?: number | null; noFutureLeakage?: boolean;
}
export interface MarketLedgerRow {
  seriesId: string; labelJa: string; latestValue: number | null; unit: string;
  previousChange: number | null; fourPeriodDirection: 'up' | 'down' | 'flat' | null;
  fourPeriodTotal: number | null; consecutiveDirectionCount: number | null;
  thresholdDistance: number | null; thresholdSide: 'above' | 'below' | null;
  thresholdStreak: number | null;
  historicalPercentile: number | null; periodEnd: string | null; availableFrom: string | null;
  status: string; acquisition: string; sourceKind: string; history: MarketLedgerHistoryPoint[];
}
export interface TurningPoint {
  id: string; ruleId: string; ruleVersion: string; detectedAt: string;
  effectiveFrom: string; availableFrom: string; detectionMode: 'live' | 'retrospective';
  facts: string[]; direction: string; severity: string; classification: string;
  subsequentOutcome: string;
}
export interface MarketCalendarState {
  market: string; marketDate: string; isTradingDay: boolean; session: string;
  holidayName: string | null; nextTradingDay: string | null; timezone: string;
  regularOpenJst?: string; regularCloseJst?: string; earlyClose?: boolean;
  calendarVersion?: string; officialCalendar?: string;
  sessionObservedAt: string; sessionValidUntil: string;
}
export interface MarketLedgerPayload {
  schemaVersion: string; asOf: string;
  summary: Record<string, string>;
  valuationSummary: { epsPreviousChange: number | null; eps5Change: number | null;
    eps20Change: number | null; per18Level: number | null; per21Level: number | null;
    per21RecentPeak: number | null; per21ChangeFromPeak: number | null; labelJa: string };
  flowCaveatJa: string;
  table: MarketLedgerRow[];
  derivedMetrics: Array<{ metricId: string; value: number | null; unit: string; asOf: string }>;
  turningPoints: TurningPoint[]; observationCount: number; stateHash: string;
  remoteReadBack: { verificationStatus: string; lastVerifiedReadBackAt: string | null };
  phase3: {
    schemaVersion: string; methodVersion: string; asOf: string;
    sections: Array<{ order: number; name: string; status: string }>;
    dailyChanges: Array<{ id: string; status: string; category: string; summaryJa: string; effectiveFrom: string }>;
    anomalyDesk: Array<{ id: string; facts: string[]; expectedRelationship: string;
      observedRelationship: string; discrepancy: string; confidence: string;
      possibleExplanations: string[]; causeUnconfirmed: boolean }>;
    decisionChangeConditions: Array<{ type: string; conditionJa: string; status: string }>;
    today: Array<Record<string, unknown>>; calendar: Record<string, MarketCalendarState>; noteJa: string;
  };
  sourceOfTruthMatrix: Array<{ dataId: string; primary: string; fallback: string; frequency: string;
    delay: string; license: string; currentStatus: string }>;
  heuristics: Array<{ ruleId: string; ruleName: string; classification: string; sampleSize: number;
    historicalTendency: string; currentState: string; supportingFacts: string[];
    failureConditions: string[]; lastTriggered: string | null;
    outcomeSummary: string | MarketLedgerBacktestSummary; methodVersion: string }>;
  backtestPolicy: { method: string; noFutureLeakage: boolean; minimumValidatedSamples: number; status: string };
  automaticAiCalls: number;
  noteJa: string;
}
export interface CostPolicyPayload {
  mode: 'DETERMINISTIC' | 'EVENT_OPT_IN' | 'MANUAL' | 'RESEARCH_BENCHMARK' | 'SCHEDULED_AI';
  eventOptIn: boolean;
  automaticAiEnabled: boolean; todayRuns: Record<'openai' | 'gemini' | 'anthropic', number>;
  todayEstimatedCostUsd: number; monthEstimatedCostUsd: number;
  lastExecutionReason: string | null; nextAllowedAiExecution: string; messageJa: string; asOf: string;
  // v13.5.63 (GPT review item 4): key / permission / budget / last refusal as separate facts.
  lastExecutionPurpose?: string | null; lastExecutionAt?: string | null; lastExecutionProvider?: string | null;
  lastSkip?: { purpose?: string; reason?: string; provider?: string; at?: string } | null;
  scheduledLane?: { dailyBudgetUsd: number; spentTodayUsd: number; remainingUsd: number; eventReserveUsd: number;
    eventRemainingUsd: number; newsRemainingUsd: number; eventRunsToday: number; eventRunsPerDay: number; eventLaneOpen: boolean } | null;
  openaiKeyConfigured?: boolean | null;
}
