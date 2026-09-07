// v13.5.62 (GPT review item 2): the eight-symbol bound is the backend's
// per-request cap, not the device's universe. Pure helper so it can be tested
// without the polling hook.
export const DECISION_EVIDENCE_BATCH = 8;
export function decisionEvidenceBatches(symbols: readonly string[]): string[][] {
  const out: string[][] = [];
  for (let i = 0; i < symbols.length; i += DECISION_EVIDENCE_BATCH) {
    out.push(symbols.slice(i, i + DECISION_EVIDENCE_BATCH));
  }
  return out;
}
