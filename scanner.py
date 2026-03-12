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

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# EDINET 思想スコア
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_edinet_doc_id(securities_code):
    """銘柄コードからEDINETの最新有報IDを取得"""
    try:
        today = datetime.now()
        for days_back in range(0, 365, 30):
            date = (today - timedelta(days=days_back)).strftime("%Y-%m-%d")
            url = f"https://disclosure.edinet-fsa.go.jp/api/v2/documents.json"
            params = {
                "date": date,
                "type": 2,
                "Subscription-Key": EDINET_API_KEY
            }
            res = requests.get(url, params=params, timeout=10)
            if res.status_code != 200:
                continue
            docs = res.json().get("results", [])
            for doc in docs:
                if doc.get("secCode") and doc.get("secCode", "").startswith(str(securities_code)[:4]):
                    if doc.get("formCode") in ["030000", "043000"]:  # 有報・四半期報告書
                        return doc.get("docID")
    except Exception as e:
        print(f"[ERROR] EDINET doc search: {e}")
    return None

def get_edinet_text(doc_id):
    """EDINETから書類テキストを取得"""
    try:
        url = f"https://disclosure.edinet-fsa.go.jp/api/v2/documents/{doc_id}"
        params = {"type": 5, "Subscription-Key": EDINET_API_KEY}  # type5=テキスト
        res = requests.get(url, params=params, timeout=30)
        if res.status_code == 200:
            # ZIPファイルを展開
            z = zipfile.ZipFile(io.BytesIO(res.content))
            for name in z.namelist():
                if name.endswith(".txt") or "honbun" in name.lower():
                    text = z.read(name).decode("utf-8", errors="ignore")
                    # 経営理念・社長メッセージ部分を抽出（最初の5000文字）
                    for keyword in ["代表取締役", "経営理念", "ごあいさつ", "社長メッセージ", "企業理念"]:
                        idx = text.find(keyword)
                        if idx > 0:
                            return text[max(0, idx-100):idx+3000]
                    return text[:5000]
    except Exception as e:
        print(f"[ERROR] EDINET text: {e}")
    return None

def score_philosophy(code, company_name, text):
    """Claudeが経営思想を0-100点でスコアリング"""
    if not text:
        return 50, "有報テキスト取得失敗のためデフォルトスコア"
    
    prompt = f"""あなたは企業の「経営思想」を評価する専門家です。
以下は【{company_name}（{code}）】の有価証券報告書の一部です。

{text[:3000]}

以下の基準で「経営思想スコア」を0〜100点で採点してください。

【高スコア（70点以上）の条件】
- 経営トップの言葉に、事業への深い思いや社会的使命感がある
- 単なる利益追求でなく、なぜその事業をやるかの哲学がある
- 流行りのビジネスモデルに乗っているだけでなく、独自の価値観がある

【低スコア（30点以下）の条件】
- 「市場成長を取り込む」「効率化」など定型文だけで思想が見えない
- 経営理念が形式的で、誰が書いても変わらないような内容
- トレンド追随型で経営の根本に信念が感じられない

必ずJSON形式のみで回答してください：
{{
  "score": 75,
  "reason": "スコアの理由を1〜2行で",
  "philosophy_quote": "有報から最も思想を表す一文（なければ空文字）"
}}"""
    
    try:
        res = claude.messages.create(
            model="claude-opus-4-6",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        text_res = res.content[0].text
        start = text_res.find("{")
        end = text_res.rfind("}") + 1
        if start >= 0:
            return_data = json.loads(text_res[start:end])
            return return_data.get("score", 50), return_data.get("reason", ""), return_data.get("philosophy_quote", "")
    except Exception as e:
        print(f"[ERROR] Philosophy scoring: {e}")
    return 50, "スコアリング失敗", ""

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 全決済センチネル
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def sentinel_check(news, twitter):
    """地政学・マクロリスクを検知し全決済アラートを出す冷徹な番兵"""
    news_text = "\n".join([f"- {n.get('title','')}" for n in news[:20]])
    twitter_text = "\n".join([f"- {t.get('text','')[:100]}" for t in twitter[:10]])
    
    prompt = f"""あなたは株式市場の「全決済センチネル（冷徹な番兵）」です。
感情は一切持たず、リスクのみを判定します。

【最新ニュース】
{news_text if news_text else "ニュースなし"}

【X最新情報】
{twitter_text if twitter_text else "X情報なし"}

以下のいずれかを検知した場合のみ「全決済」を発動してください：
- 地政学リスクの急変（戦争勃発・核の脅威・テロ等）
- 世界的な金融危機の兆候（リーマン級・コロナ級）
- 日銀の緊急政策変更・円の急変動
- 市場の需給崩壊（サーキットブレーカー・取引停止等）

「半分売り」「様子見」などの曖昧な提案は禁止。
全決済か、継続かの二択のみ。

必ずJSON形式のみで回答してください：
{{
  "action": "HOLD" または "SELL_ALL",
  "reason": "判定理由を1行で",
  "risk_level": 1から5の数字（5が最高リスク）
}}"""
    
    try:
        res = claude.messages.create(
            model="claude-opus-4-6",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        text_res = res.content[0].text
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
        f"銘柄:{q.get('Code','?')} 社名:{q.get('CoName','?')}"
        for q in candidates[:20]
    ])
    prompt = f"""あなたは日本株デイトレードの専門AIです。
本日の寄り付き（9:00）で買い、当日中に売る本命1銘柄を選んでください。

【候補銘柄】
{candidates_text}

【ニュース】
{news_text if news_text else "なし"}

【X話題】
{twitter_text if twitter_text else "なし"}

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

def send_notification(result, sentinel, philosophy_results):
    print()
    
    # ━━ センチネルアラート ━━
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
    else:
        print(f"🛡️  センチネル: HOLD継続 / リスク {risk_bar} ({risk}/5) / {sentinel.get('reason','')}")
    
    if not result or "top3" not in result:
        print("[通知] 候補銘柄なし")
        return
    
    top = result["top3"][0]
    stars = "★" * top["confidence"] + "☆" * (top["confidence_max"] - top["confidence"])
    
    # ━━ 思想スコア ━━
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
    
    print("  📰 ニュース取得中...")
    news = get_news()
    
    print("  🐦 X取得中...")
    twitter = get_twitter_buzz()
    
    # センチネルチェック（最優先）
    print("  🛡️  センチネル判定中...")
    sentinel = sentinel_check(news, twitter)
    
    if sentinel.get("action") == "SELL_ALL":
        send_notification({}, sentinel, {})
        return
    
    if not stocks:
        print("[WARNING] 銘柄データ取得失敗")
        return
    
    print("  🤖 Claude AI分析中...")
    result = ai_scoring(stocks[:50], news, twitter)
    
    # 上位候補の思想スコアを取得
    philosophy_results = {}
    if result and "top3" in result:
        for stock in result["top3"][:2]:  # 上位2銘柄のみEDINETチェック
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
