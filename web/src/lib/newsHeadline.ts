// v13.5.61 (owner iPhone review 2026-09-07): a Nikkei digest mail arrives as
// 「日経ニュースメール 9/7 夕版 ━ 注目ニュース ━━━━━━━ ◆円半年ぶりに…」. The mail
// header is not the news. For DISPLAY only, keep the first headline after the
// first ◆ marker; the stored event text is untouched (evidence stays verbatim).
export function displayNewsHeadline(raw: string | null | undefined): string {
  const text = String(raw ?? '').trim();
  if (!text) return '';
  const marker = text.indexOf('◆');
  if (marker < 0) return text;
  const body = text.slice(marker + 1).trim();
  if (!body) return text;
  // the digest joins several items with further ◆ markers; show only the first
  const next = body.indexOf('◆');
  const first = (next > 0 ? body.slice(0, next) : body).trim();
  return first.replace(/[（(]有料会員限定[）)]/g, '').trim() || text;
}

/** A digest headline (mail header before ◆) is not a single news item. */
export function isDigestHeadline(raw: string | null | undefined): boolean {
  const text = String(raw ?? '');
  return /ニュースメール/.test(text) && text.includes('◆');
}
