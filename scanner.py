import os
import time
import schedule
import requests
import anthropic
from datetime import datetime, timedelta
import json

# ============================================================
# 設定
# ============================================================
JQUANTS_REFRESH_TOKEN = os.environ.get("JQUANTS_REFRESH_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "")
X_BEARER_TOKEN = os.environ.get("X_API_BEARER_TOKEN", "")

claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ============================================================
# J-Quants APIトークン取得
# ============================================================
def get_jquants_id_token():
    """API KeyからIDトークンを取得"""
    url = "https://api.jquants.com/v1/token/auth_refresh"
    params = {"refreshtoken": JQUANTS_REFRESH_TOKEN.strip()}
    res = requests.post(url, params=params)
    if res.status_code == 200:
        return res.json().get("idToken")
    print(f"[ERROR] J-Quants token error: {res.status_code} {res.text}")
    return None

# ============================================================
# 銘柄一覧取得
# ============================================================
def get_listed_stocks(id_token):
    """上場銘柄一覧を取得"""
    url = "https://api.jquants.com/v1/listed/info"
    headers = {"Authorization": f"Bearer {id_token}"}
    res = requests.get(url, headers=headers)
    if res.status_code == 200:
        return res.json().get("info", [])
    print(f"[ERROR] Listed stocks error: {res.status_code}")
    return []

# ============================================================
# 株価・売買代金取得
# ============================================================
def get_daily_quotes(id_token, date_str):
    """指定日の全銘柄株価を取得"""
    url = "https://api.jquants.com/v1/prices/daily_quotes"
    headers = {"Authorization": f"Bearer {id_token}"}
    params = {"date": date_str}
    res = requests.get(url, headers=headers, params=params)
    if res.status_code == 200:
        return res.json().get("daily_quotes", [])
    print(f"[ERROR] Daily quotes error: {res.status_code}")
    return []

# ============================================================
# 信用残データ取得
# ============================================================
def get_margin_trading(id_token, date_str):
    """信用取引データを取得"""
    url = "https://api.jquants.com/v1/markets/trading_by_type"
    headers = {"Authorization": f"Bearer {id_token}"}
    params = {"date": date_str}
    res = requests.get(url, headers=headers, params=params)
    if res.status_code == 200:
        return res.json()
    return {}

# ============================================================
# ニュース取得
# ============================================================
def get_news_japan():
    """日本株関連ニュースを取得"""
    if not NEWS_API_KEY:
        return []
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": "日本株 OR 東証 OR 株価 OR 決算 OR 上方修正",
        "language": "jp",
        "sortBy": "publishedAt",
        "pageSize": 50,
        "apiKey": NEWS_API_KEY
    }
    res = requests.get(url, params=params)
    if res.status_code == 200:
        return res.json().get("articles", [])
    return []

def get_news_english():
    """英語の日本株・マクロニュースを取得"""
    if not NEWS_API_KEY:
        return []
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": "Japan stock OR Nikkei OR BOJ OR Trump tariff OR semiconductor",
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 30,
        "apiKey": NEWS_API_KEY
    }
    res = requests.get(url, params=params)
    if res.status_code == 200:
        return res.json().get("articles", [])
    return []

# ============================================================
# X(Twitter) 取得
# ============================================================
def get_twitter_buzz():
    """イナゴ・話題銘柄をXから取得"""
    if not X_BEARER_TOKEN:
        return []
    url = "https://api.twitter.com/2/tweets/search/recent"
    headers = {"Authorization": f"Bearer {X_BEARER_TOKEN}"}
    params = {
        "query": "日本株 暴騰 OR 爆上げ OR ストップ高 OR 急騰 -is:retweet lang:ja",
        "max_results": 50,
        "tweet.fields": "created_at,public_metrics"
    }
    res = requests.get(url, headers=headers, params=params)
    if res.status_code == 200:
        return res.json().get("data", [])
    print(f"[WARNING] X API error: {res.status_code}")
    return []

# ============================================================
# ふるい①：基本フィルター
# ============================================================
def filter_basic(quotes, listed_stocks):
    """
    除外条件：
    - 株価1,000円以下
    - 売買代金5億円以下
    - 時価総額5,000億円以上の超大型株
    """
    # 銘柄マスター作成
    stock_map = {s["Code"]: s for s in listed_stocks}
    
    filtered = []
    for q in quotes:
        code = q.get("Code", "")
        close = q.get("Close", 0) or 0
        volume = q.get("Volume", 0) or 0
        trading_value = close * volume  # 売買代金の近似値
        
        # 株価フィルター
        if close < 1000:
            continue
        
        # 売買代金フィルター（5億円 = 500,000,000）
        if trading_value < 500_000_000:
            continue
        
        # 超大型株除外（時価総額の代わりに売買代金上限で近似）
        # 売買代金が極端に大きい超大型株を除外
        if trading_value > 50_000_000_000:  # 500億円以上
            continue
            
        filtered.append(q)
    
    print(f"[ふるい①] {len(quotes)}銘柄 → {len(filtered)}銘柄")
    return filtered

# ============================================================
# ふるい②：テクニカルフィルター（出来高急増）
# ============================================================
def filter_technical(quotes_today, quotes_prev):
    """出来高が直近平均の2倍以上・前日比プラス"""
    prev_map = {q["Code"]: q for q in quotes_prev}
    
    filtered = []
    for q in quotes_today:
        code = q.get("Code", "")
        prev = prev_map.get(code)
        if not prev:
            continue
        
        # 前日比プラス
        close_today = q.get("Close", 0) or 0
        close_prev = prev.get("Close", 0) or 0
        if close_prev <= 0 or close_today <= close_prev:
            continue
        
        # 出来高急増（前日比1.5倍以上）
        vol_today = q.get("Volume", 0) or 0
        vol_prev = prev.get("Volume", 1) or 1
        if vol_today < vol_prev * 1.5:
            continue
        
        q["change_rate"] = (close_today - close_prev) / close_prev * 100
        q["volume_ratio"] = vol_today / vol_prev
        filtered.append(q)
    
    print(f"[ふるい②テクニカル] → {len(filtered)}銘柄")
    return filtered

# ============================================================
# AI分析：Claude による最終スコアリング
# ============================================================
def ai_scoring(candidates, news_ja, news_en, twitter_data):
    """Claude APIで上位銘柄をスコアリング"""
    if not candidates:
        return []
    
    # ニュースをテキスト化
    news_text = ""
    for n in (news_ja + news_en)[:20]:
        news_text += f"- {n.get('title', '')}\n"
    
    # Xの話題をテキスト化
    twitter_text = ""
    for t in twitter_data[:20]:
        twitter_text += f"- {t.get('text', '')[:100]}\n"
    
    # 候補銘柄をテキスト化
    candidates_text = ""
    for q in candidates[:30]:
        candidates_text += (
            f"銘柄コード: {q.get('Code')} "
            f"終値: {q.get('Close')}円 "
            f"前日比: +{q.get('change_rate', 0):.1f}% "
            f"出来高倍率: {q.get('volume_ratio', 0):.1f}倍\n"
        )
    
    today = datetime.now().strftime("%Y年%m月%d日")
    
    prompt = f"""あなたは日本株デイトレードの専門AIです。
本日（{today}）の寄り付き（9:00）で買い、当日中に売る1銘柄を選んでください。

【候補銘柄（テクニカル条件通過済み）】
{candidates_text}

【本日のニュース】
{news_text}

【X(Twitter)の話題】
{twitter_text}

以下の条件で最も有望な銘柄TOP3を選んでください：
1. カタリスト（材料）が明確にある
2. 需給が良い（空売り多い・信用倍率低い）
3. イナゴ・SNS話題性がある
4. マクロ環境と合っている

回答はJSON形式のみで返してください：
{{
  "top3": [
    {{
      "code": "銘柄コード",
      "name": "銘柄名",
      "expected_return": "予想上昇率（例: +12%）",
      "main_reason": "暴騰の主因（1行）",
      "risk": "リスク・注意事項（1行）",
      "confidence": 4,
      "confidence_max": 5
    }}
  ],
  "market_condition": "本日の地合い判定（1行）",
  "macro_summary": "マクロ情報要約（1行）"
}}"""

    try:
        res = claude.messages.create(
            model="claude-opus-4-6",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        text = res.content[0].text
        # JSONを抽出
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
    except Exception as e:
        print(f"[ERROR] Claude API error: {e}")
    
    return {}

# ============================================================
# 通知送信（現在はコンソール出力・後でFirebase追加）
# ============================================================
def send_notification(result):
    """通知を送信（現在はログ出力）"""
    if not result or "top3" not in result:
        print("[通知] 候補銘柄なし")
        return
    
    top = result["top3"][0]  # 本命1銘柄
    market = result.get("market_condition", "")
    macro = result.get("macro_summary", "")
    
    message = f"""
━━━━━━━━━━━━━━━━━━━━
🚀 本日の本命銘柄 【{top['code']}】{top.get('name', '')}
━━━━━━━━━━━━━━━━━━━━
📈 予想上昇率: {top['expected_return']}
💡 主因: {top['main_reason']}
⚠️  リスク: {top['risk']}
⭐ 確信度: {'★' * top['confidence']}{'☆' * (top['confidence_max'] - top['confidence'])}
📊 地合い: {market}
🌐 マクロ: {macro}
━━━━━━━━━━━━━━━━━━━━
"""
    print(message)
    
    # TODO: Firebase Push通知をここに追加

# ============================================================
# メインスキャン処理
# ============================================================
def run_scan(label="定時スキャン"):
    """メインのスキャン処理"""
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] {label} 開始")
    
    # J-Quantsトークン取得
    id_token = get_jquants_id_token()
    if not id_token:
        print("[ERROR] J-Quantsトークン取得失敗")
        return
    
    # 日付設定
    today = datetime.now().strftime("%Y%m%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    
    # データ取得
    print("  📊 銘柄一覧取得中...")
    listed = get_listed_stocks(id_token)
    
    print("  📊 株価データ取得中...")
    quotes_today = get_daily_quotes(id_token, today)
    quotes_prev = get_daily_quotes(id_token, yesterday)
    
    print("  📰 ニュース取得中...")
    news_ja = get_news_japan()
    news_en = get_news_english()
    
    print("  🐦 X(Twitter)データ取得中...")
    twitter = get_twitter_buzz()
    
    # フィルタリング
    step1 = filter_basic(quotes_today, listed)
    step2 = filter_technical(step1, quotes_prev)
    
    if not step2:
        print("[WARNING] 候補銘柄が0件です")
        return
    
    # AI分析
    print("  🤖 Claude AI分析中...")
    result = ai_scoring(step2, news_ja, news_en, twitter)
    
    # 通知送信
    send_notification(result)
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] スキャン完了\n")

# ============================================================
# スケジューラー設定
# ============================================================
def setup_schedule():
    """スキャンスケジュールを設定"""
    schedule.every().day.at("08:00").do(run_scan, label="第1スキャン（夜間ニュース・米国市場）")
    schedule.every().day.at("08:20").do(run_scan, label="第2スキャン（信用残・空売り）")
    schedule.every().day.at("08:40").do(run_scan, label="第3スキャン（先物・為替）")
    schedule.every().day.at("08:54").do(run_scan, label="★最終スキャン（本命通知）")
    schedule.every().day.at("09:05").do(run_scan, label="寄り付き後スキャン")
    
    print("スケジューラー起動:")
    print("  08:00 第1スキャン")
    print("  08:20 第2スキャン")
    print("  08:40 第3スキャン")
    print("  08:54 ★最終通知（本命1銘柄）")
    print("  09:05 寄り付き後スキャン")
    print("")

# ============================================================
# メイン
# ============================================================
if __name__ == "__main__":
    print("=" * 50)
    print("  日本株暴騰スキャナー 起動")
    print("=" * 50)
    
    setup_schedule()
    
    # 起動時に即座にテストスキャン
    print("起動テストスキャン実行中...")
    run_scan(label="起動テスト")
    
    # スケジューラーループ
    while True:
        schedule.run_pending()
        time.sleep(30)
