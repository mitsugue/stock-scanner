import os
import time
import schedule
import requests
import anthropic
from datetime import datetime, timedelta
import json

JQUANTS_API_KEY = os.environ.get("JQUANTS_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "")
X_BEARER_TOKEN = os.environ.get("X_API_BEARER_TOKEN", "")

claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

def get_auth_header():
    return {"Authorization": f"Bearer {JQUANTS_API_KEY.strip()}"}

def get_listed_stocks():
    url = "https://api.jquants.com/v1/listed/info"
    res = requests.get(url, headers=get_auth_header())
    if res.status_code == 200:
        return res.json().get("info", [])
    print(f"[ERROR] Listed stocks: {res.status_code} {res.text[:100]}")
    return []

def get_daily_quotes(date_str):
    url = "https://api.jquants.com/v1/prices/daily_quotes"
    res = requests.get(url, headers=get_auth_header(), params={"date": date_str})
    if res.status_code == 200:
        return res.json().get("daily_quotes", [])
    print(f"[ERROR] Daily quotes: {res.status_code} {res.text[:100]}")
    return []

def get_news():
    if not NEWS_API_KEY:
        return []
    url = "https://newsapi.org/v2/everything"
    params = {"q": "日本株 OR 東証 OR 決算", "language": "jp", "sortBy": "publishedAt", "pageSize": 20, "apiKey": NEWS_API_KEY}
    res = requests.get(url, params=params)
    if res.status_code == 200:
        return res.json().get("articles", [])
    return []

def get_twitter_buzz():
    if not X_BEARER_TOKEN:
        return []
    url = "https://api.twitter.com/2/tweets/search/recent"
    headers = {"Authorization": f"Bearer {X_BEARER_TOKEN}"}
    params = {"query": "日本株 暴騰 OR 急騰 -is:retweet lang:ja", "max_results": 20}
    res = requests.get(url, headers=headers, params=params)
    if res.status_code == 200:
        return res.json().get("data", [])
    return []

def filter_stocks(quotes_today, quotes_prev):
    prev_map = {q["Code"]: q for q in quotes_prev}
    filtered = []
    for q in quotes_today:
        close = q.get("Close", 0) or 0
        volume = q.get("Volume", 0) or 0
        if close < 1000:
            continue
        if close * volume < 500_000_000:
            continue
        prev = prev_map.get(q["Code"])
        if not prev:
            continue
        close_prev = prev.get("Close", 0) or 0
        if close_prev <= 0 or close <= close_prev:
            continue
        vol_prev = prev.get("Volume", 1) or 1
        if volume < vol_prev * 1.5:
            continue
        q["change_rate"] = (close - close_prev) / close_prev * 100
        q["volume_ratio"] = volume / vol_prev
        filtered.append(q)
    return filtered

def ai_scoring(candidates, news, twitter):
    if not candidates:
        return {}
    news_text = "\n".join([f"- {n.get('title','')}" for n in news[:15]])
    twitter_text = "\n".join([f"- {t.get('text','')[:80]}" for t in twitter[:10]])
    candidates_text = "\n".join([
        f"銘柄:{q.get('Code')} 終値:{q.get('Close')}円 前日比:+{q.get('change_rate',0):.1f}% 出来高倍率:{q.get('volume_ratio',0):.1f}倍"
        for q in candidates[:20]
    ])
    prompt = f"""あなたは日本株デイトレードの専門AIです。
本日の寄り付き（9:00）で買い、当日中に売る本命1銘柄を選んでください。

【候補銘柄】
{candidates_text}

【ニュース】
{news_text}

【X話題】
{twitter_text}

以下のJSON形式のみで回答してください：
{{
  "top3": [
    {{
      "code": "銘柄コード",
      "name": "銘柄名",
      "expected_return": "+12%",
      "main_reason": "暴騰の主因1行",
      "risk": "リスク1行",
      "confidence": 4,
      "confidence_max": 5
    }}
  ],
  "market_condition": "地合い判定1行",
  "macro_summary": "マクロ要約1行"
}}"""
    try:
        res = claude.messages.create(
            model="claude-opus-4-6",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        text = res.content[0].text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0:
            return json.loads(text[start:end])
    except Exception as e:
        print(f"[ERROR] Claude: {e}")
    return {}

def send_notification(result):
    if not result or "top3" not in result:
        print("[通知] 候補銘柄なし")
        return
    top = result["top3"][0]
    stars = "★" * top["confidence"] + "☆" * (top["confidence_max"] - top["confidence"])
    print(f"""
━━━━━━━━━━━━━━━━━━━━
🚀 本命銘柄 【{top["code"]}】{top.get("name","")}
📈 予想: {top["expected_return"]}
💡 主因: {top["main_reason"]}
⚠️  リスク: {top["risk"]}
⭐ 確信度: {stars}
📊 地合い: {result.get("market_condition","")}
🌐 マクロ: {result.get("macro_summary","")}
━━━━━━━━━━━━━━━━━━━━""")

def run_scan(label="スキャン"):
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] {label} 開始")
    today = datetime.now().strftime("%Y%m%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    print("  📊 株価データ取得中...")
    quotes_today = get_daily_quotes(today)
    quotes_prev = get_daily_quotes(yesterday)
    print(f"  取得: 本日{len(quotes_today)}件 前日{len(quotes_prev)}件")
    print("  📰 ニュース取得中...")
    news = get_news()
    print("  🐦 X取得中...")
    twitter = get_twitter_buzz()
    print("  🔍 フィルタリング中...")
    candidates = filter_stocks(quotes_today, quotes_prev)
    print(f"  候補: {len(candidates)}銘柄")
    if not candidates:
        print("[WARNING] 候補銘柄なし")
        return
    print("  🤖 Claude AI分析中...")
    result = ai_scoring(candidates, news, twitter)
    send_notification(result)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {label} 完了\n")

def setup_schedule():
    schedule.every().day.at("08:00").do(run_scan, label="第1スキャン")
    schedule.every().day.at("08:20").do(run_scan, label="第2スキャン")
    schedule.every().day.at("08:40").do(run_scan, label="第3スキャン")
    schedule.every().day.at("08:54").do(run_scan, label="★最終通知")
    schedule.every().day.at("09:05").do(run_scan, label="寄り付き後")
    print("スケジューラー起動: 08:00/08:20/08:40/08:54/09:05")

if __name__ == "__main__":
    print("=" * 50)
    print("  日本株暴騰スキャナー 起動")
    print("=" * 50)
    setup_schedule()
    print("起動テストスキャン実行中...")
    run_scan(label="起動テスト")
    while True:
        schedule.run_pending()
        time.sleep(30)
