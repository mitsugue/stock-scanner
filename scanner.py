import os
import time
import schedule
import requests
import anthropic
from datetime import datetime, timedelta
import json
import zipfile
import io

JQUANTS_API_KEY = os.environ.get("JQUANTS_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "")
X_BEARER_TOKEN = os.environ.get("X_API_BEARER_TOKEN", "")
EDINET_API_KEY = os.environ.get("EDINET_API_KEY", "")

claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

def jquants_headers():
    return {"x-api-key": JQUANTS_API_KEY.strip()}

def get_listed_stocks():
    url = "https://api.jquants.com/v2/equities/master"
    res = requests.get(url, headers=jquants_headers())
    if res.status_code == 200:
        return res.json().get("data", [])
    print(f"[ERROR] Listed stocks: {res.status_code} {res.text[:200]}")
    return []

def get_daily_quotes(date_str=None):
    """前日の株価データ（前日比・出来高・値動き）を取得"""
    if not date_str:
        # 直近の営業日を取得
        today = datetime.now()
        for i in range(1, 7):
            candidate = today - timedelta(days=i)
            if candidate.weekday() < 5:  # 平日
                date_str = candidate.strftime("%Y%m%d")
                break
    
    url = "https://api.jquants.com/v2/equities/bars/daily"
    params = {"date": date_str}
    res = requests.get(url, headers=jquants_headers(), params=params)
    if res.status_code == 200:
        return res.json().get("data", [])
    print(f"[ERROR] Daily quotes: {res.status_code} {res.text[:200]}")
    return []

def filter_hot_stocks(quotes, stocks):
    """株価データから急騰候補を絞り込む"""
    # 銘柄マスターをコードで引けるように辞書化
    stock_map = {s.get("Code", ""): s for s in stocks}
    
    candidates = []
    for q in quotes:
        code = q.get("Code", "")
        open_p = q.get("O") or q.get("Open") or 0
        close_p = q.get("C") or q.get("Close") or 0
        high_p = q.get("H") or q.get("High") or 0
        low_p = q.get("L") or q.get("Low") or 0
        volume = q.get("Vo") or q.get("Volume") or 0
        
        if open_p <= 0 or close_p <= 0:
            continue
        
        # 前日比計算
        change_rate = (close_p - open_p) / open_p * 100
        
        # 高値・安値の振れ幅
        swing = (high_p - low_p) / low_p * 100 if low_p > 0 else 0
        
        # 銘柄情報を付加
        stock_info = stock_map.get(code, {})
        
        candidates.append({
            "code": code,
            "name": stock_info.get("CoName", ""),
            "close": close_p,
            "change_rate": round(change_rate, 2),
            "volume": int(volume),
            "swing": round(swing, 2),
            "market": stock_info.get("MktNm", ""),
            "sector": stock_info.get("S17Nm", ""),
        })
    
    # 急騰・高ボラティリティ順にソート
    candidates.sort(key=lambda x: x["change_rate"], reverse=True)
    
    return candidates[:50]  # 上位50銘柄

def get_news():
    if not NEWS_API_KEY:
        return []
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": "japan stock OR nikkei OR BOJ OR geopolitical risk",
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 20,
        "apiKey": NEWS_API_KEY
    }
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

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# EDINET 思想スコア
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_edinet_doc_id(securities_code):
    try:
        today = datetime.now()
        for days_back in range(0, 365, 30):
            date = (today - timedelta(days=days_back)).strftime("%Y-%m-%d")
            url = "https://disclosure.edinet-fsa.go.jp/api/v2/documents.json"
            params = {"date": date, "type": 2, "Subscription-Key": EDINET_API_KEY}
            res = requests.get(url, params=params, timeout=10)
            if res.status_code != 200:
                continue
            docs = res.json().get("results", [])
            for doc in docs:
                sec_code = doc.get("secCode")
                if sec_code and sec_code.startswith(str(securities_code)[:4]):
                    if doc.get("formCode") in ["030000", "043000"]:
                        return doc.get("docID")
    except Exception as e:
        print(f"[ERROR] EDINET doc search: {e}")
    return None

def get_edinet_text(doc_id):
    try:
        url = f"https://disclosure.edinet-fsa.go.jp/api/v2/documents/{doc_id}"
        params = {"type": 5, "Subscription-Key": EDINET_API_KEY}
        res = requests.get(url, params=params, timeout=30)
        if res.status_code == 200:
            z = zipfile.ZipFile(io.BytesIO(res.content))
            for name in z.namelist():
                if name.endswith(".txt") or "honbun" in name.lower():
                    text = z.read(name).decode("utf-8", errors="ignore")
                    for keyword in ["代表取締役", "経営理念", "ごあいさつ", "社長メッセージ", "企業理念"]:
                        idx = text.find(keyword)
                        if idx > 0:
                            return text[max(0, idx-100):idx+3000]
                    return text[:5000]
    except Exception as e:
        print(f"[ERROR] EDINET text: {e}")
    return None

def score_philosophy(code, company_name, text):
    if not text:
        return 50, "有報テキスト取得失敗", ""
    prompt = f"""あなたは企業の「経営思想」を評価する専門家です。
以下は【{company_name}（{code}）】の有価証券報告書の一部です。

{text[:3000]}

【高スコア70点以上】経営トップの深い思い・社会的使命感・独自の哲学がある
【低スコア30点以下】定型文のみ・トレンド追随型・誰が書いても同じ内容

必ずJSON形式のみで回答：
{{"score": 75, "reason": "理由1〜2行", "philosophy_quote": "最も思想を表す一文"}}"""
    try:
        res = claude.messages.create(
            model="claude-opus-4-6",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        text_res = res.content[0].text if res.content else "{}"
        start = text_res.find("{")
        end = text_res.rfind("}") + 1
        if start >= 0:
            d = json.loads(text_res[start:end])
            return d.get("score", 50), d.get("reason", ""), d.get("philosophy_quote", "")
    except Exception as e:
        print(f"[ERROR] Philosophy scoring: {e}")
    return 50, "スコアリング失敗", ""

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 全決済センチネル
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def sentinel_check(news, twitter):
    news_text = "\n".join([f"- {n.get('title','')}" for n in news[:20]])
    twitter_text = "\n".join([f"- {t.get('text','')[:100]}" for t in twitter[:10]])
    prompt = f"""あなたは株式市場の「全決済センチネル（冷徹な番兵）」です。

【最新ニュース】
{news_text if news_text else "ニュースなし"}

【X最新情報】
{twitter_text if twitter_text else "なし"}

以下を検知した場合のみ「全決済」を発動：
- 地政学リスクの急変（戦争勃発・核の脅威）
- 世界的な金融危機の兆候
- 日銀の緊急政策変更・円の急変動
- 市場の需給崩壊

「半分売り」「様子見」は禁止。HOLD か SELL_ALL の二択のみ。

必ずJSON形式のみで回答：
{{"action": "HOLD", "reason": "判定理由1行", "risk_level": 1}}"""
    try:
        res = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        text_res = res.content[0].text if res.content else "{}"
        start = text_res.find("{")
        end = text_res.rfind("}") + 1
        if start >= 0:
            return json.loads(text_res[start:end])
    except Exception as e:
        print(f"[ERROR] Sentinel: {e}")
    return {"action": "HOLD", "reason": "判定失敗", "risk_level": 1}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# メインAI分析
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def ai_scoring(candidates, news, twitter):
    if not candidates:
        return {}
    news_text = "\n".join([f"- {n.get('title','')}" for n in news[:15]])
    twitter_text = "\n".join([f"- {t.get('text','')[:80]}" for t in twitter[:10]])
    candidates_text = "\n".join([
        f"銘柄:{q['code']} 社名:{q['name']} 前日比:{q['change_rate']:+.1f}% 終値:{q['close']}円 出来高:{q['volume']:,} 値幅:{q['swing']:.1f}% 市場:{q['market']} 業種:{q['sector']}"
        for q in candidates[:30]
    ])
    prompt = f"""あなたは日本株デイトレードの専門AIです。
本日の寄り付き（9:00）で買い、当日中に売る本命1銘柄を選んでください。

【前日株価データ（急騰候補上位30銘柄）】
{candidates_text}

【最新ニュース（英語）】
{news_text if news_text else "なし"}

【X話題】
{twitter_text if twitter_text else "なし"}

選定基準：
- 前日比プラスで出来高が急増している銘柄
- ニュース・X話題と連動している銘柄
- 値幅（ボラティリティ）が高く短期トレードに向く銘柄

必ずJSON形式のみで回答：
{{
  "top3": [
    {{
      "code": "銘柄コード",
      "name": "銘柄名",
      "expected_return": "+8%",
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
        text = res.content[0].text if res.content else "{}"
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0:
            return json.loads(text[start:end])
    except Exception as e:
        print(f"[ERROR] Claude: {e}")
    return {}

def send_notification(result, sentinel, philosophy_results):
    print()
    risk = sentinel.get("risk_level", 1)
    action = sentinel.get("action", "HOLD")
    risk_bar = "🔴" * risk + "⚪" * (5 - risk)

    if action == "SELL_ALL":
        print(f"""
🚨🚨🚨 全決済センチネル発動 🚨🚨🚨
━━━━━━━━━━━━━━━━━━━━
今すぐ全て売れ！
理由: {sentinel.get('reason','')}
リスク: {risk_bar} ({risk}/5)
━━━━━━━━━━━━━━━━━━━━""")
        return

    print(f"🛡️  センチネル: HOLD / リスク {risk_bar} ({risk}/5) / {sentinel.get('reason','')}")

    if not result or "top3" not in result:
        print("[通知] 候補銘柄なし")
        return

    top = result["top3"][0]
    stars = "★" * top["confidence"] + "☆" * (top["confidence_max"] - top["confidence"])
    code = top.get("code", "")
    phil = philosophy_results.get(code, {})
    phil_score = phil.get("score", "-")
    phil_reason = phil.get("reason", "未取得")
    phil_quote = phil.get("quote", "")

    print(f"""
━━━━━━━━━━━━━━━━━━━━
🚀 本命銘柄 【{code}】{top.get("name","")}
📈 予想: {top["expected_return"]}
💡 主因: {top["main_reason"]}
⚠️  リスク: {top["risk"]}
⭐ 確信度: {stars}
🧠 思想スコア: {phil_score}/100 ─ {phil_reason}
📜 経営者の言葉: 「{phil_quote}」
📊 地合い: {result.get("market_condition","")}
🌐 マクロ: {result.get("macro_summary","")}
━━━━━━━━━━━━━━━━━━━━""")

def run_scan(label="スキャン"):
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] {label} 開始")

    print("  📊 銘柄一覧取得中...")
    stocks = get_listed_stocks()
    print(f"  銘柄数: {len(stocks)}")

    print("  📈 前日株価データ取得中...")
    quotes = get_daily_quotes()
    print(f"  株価データ: {len(quotes)}件")

    print("  🔍 急騰候補フィルタリング中...")
    candidates = filter_hot_stocks(quotes, stocks)
    print(f"  候補: {len(candidates)}銘柄")
    if candidates:
        print(f"  TOP3: {candidates[0]['name'] if candidates else 'なし'}")

    print("  📰 ニュース取得中...")
    news = get_news()
    print(f"  ニュース: {len(news)}件")

    print("  🐦 X取得中...")
    twitter = get_twitter_buzz()

    print("  🛡️  センチネル判定中...")
    sentinel = sentinel_check(news, twitter)

    if sentinel.get("action") == "SELL_ALL":
        send_notification({}, sentinel, {})
        return

    if not candidates:
        print("[WARNING] 候補銘柄なし")
        return
    print("  🤖 Claude AI分析中...")
    result = ai_scoring(candidates, news, twitter)

    philosophy_results = {}
    if result and "top3" in result:
        for stock in result["top3"][:2]:
            code = stock.get("code", "")
            name = stock.get("name", "")
            print(f"  🧠 {name}({code}) 思想スコア取得中...")
            doc_id = get_edinet_doc_id(code)
            if doc_id:
                text = get_edinet_text(doc_id)
                score, reason, quote = score_philosophy(code, name, text)
                philosophy_results[code] = {"score": score, "reason": reason, "quote": quote}
                print(f"  → 思想スコア: {score}/100")
            else:
                philosophy_results[code] = {"score": 50, "reason": "EDINET書類未発見", "quote": ""}

    send_notification(result, sentinel, philosophy_results)
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
