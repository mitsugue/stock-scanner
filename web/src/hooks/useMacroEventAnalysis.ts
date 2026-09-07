import { useEffect, useState } from 'react';

// C.A.O.S. macro-event pre/post analysis (v11.3.2) — GET /api/argus/macro-event-analysis.
// Cache-only on the backend; pre views are durable so the post answer-check is real.

export interface MacroPre {
  generatedAt?: string; summaryJa?: string; argusScenarioJa?: string;
  marketPricingJa?: string; whatWouldSurpriseJa?: string;
  assetsToWatch?: string[]; confidence?: number | null; limitationsJa?: string[];
  // v13.5.63 (GPT review item 6): the model that was asked / answered, when, cost.
  ai?: { requestedModel?: string; returnedModel?: string; fallbackModel?: string; completedAt?: string;
    inputTokens?: number; outputTokens?: number; estUsd?: number } | null;
}
export interface MacroPost {
  generatedAt?: string | null;
  ai?: { requestedModel?: string; returnedModel?: string; fallbackModel?: string; completedAt?: string;
    inputTokens?: number; outputTokens?: number; estUsd?: number } | null;
  verdict?: 'not_available' | 'hit' | 'partial' | 'miss' | 'not_scoreable';
  answerCheckJa?: string; marketReactionJa?: string;
  portfolioImpactJa?: string; whatChangedJa?: string; limitationsJa?: string[];
}
export interface MacroAnalysis {
  eventId?: string; eventCode?: string; phase?: string;
  pre?: MacroPre; post?: MacroPost;
  actual?: { available?: boolean; headline?: string | null; source?: string | null };
}

export function useMacroEventAnalysis(): Record<string, MacroAnalysis> {
  const [byKey, setByKey] = useState<Record<string, MacroAnalysis>>({});
  useEffect(() => {
    const backend = import.meta.env.VITE_ARGUS_BACKEND_URL as string | undefined;
    if (!backend) return;
    let alive = true;
    const url = backend.replace(/\/$/, '') + '/api/argus/macro-event-analysis?limit=30';
    const load = () => fetch(url).then((r) => r.json())
      .then((d) => {
        if (!alive || !d || !Array.isArray(d.items)) return;
        const m: Record<string, MacroAnalysis> = {};
        for (const it of d.items as MacroAnalysis[]) {
          if (it.eventId) m[it.eventId] = it;
          if (it.eventCode && !m[it.eventCode]) m[it.eventCode] = it;
        }
        setByKey(m);
      })
      .catch(() => { /* keep last */ });
    load();
    const iv = setInterval(load, 120_000);
    return () => { alive = false; clearInterval(iv); };
  }, []);
  return byKey;
}
