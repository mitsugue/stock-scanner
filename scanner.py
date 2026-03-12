import os
import time
import schedule
import requests
import anthropic
from datetime import datetime, timedelta
import json
import zipfile
import io
import pytz

JQUANTS_API_KEY  = os.environ.get("JQUANTS_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
NEWS_API_KEY     = os.environ.get("NEWS_API_KEY", "")
X_BEARER_TOKEN   = os.environ.get("X_API_BEARER_TOKEN", "")
EDINET_API_KEY   = os.environ.get("EDINET_API_KEY", "")
NTFY_CHANNEL     = "mitsugu-stock-scanner"

claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
STATE_FILE = "/tmp/scan_state.json"

def wait_until_8am():
    jst = pytz.timezone('Asia/Tokyo')
    now = datetime.now(jst)
    target = now.replace(hour=8, minute=0, second=0, microsecond=0)
    wait_sec = (target - now).total_seconds()
    if wait_sec > 0:
        print(f"[{now.strftime('%H:%M:%S')}] ⏳ 8:00まで {int(wait_sec)}秒 待機中...")
        time.sleep(wait_sec)
    print("[08:00:00] 🚀 Stock Scanner スキャン開始！")

def save_state(data):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def clear_state():
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)

def jquants_headers():
    return {"x-api-key": JQUANTS_API_KEY.strip()}

def get_listed_stocks():
    url = "https://api.jquants.com/v2/equities/master"
    res = requests.get(url, headers=jquants_headers())
    if res.status_code == 200:
        return res.json().get("data", [])
    print(f"[ERROR] Listed stocks: {res.status_code}")
    return []

def get_daily_quotes(date_str=None):
    if not date_str:
        today = datetime.now()
        for i in range(1, 7):
            candidate = today - timedelta(days=i)
            if candidate.weekday() < 5:
                date_str = candidate.strftime("%Y%m%d")
                break
    url = "https://api.jquants.com/v2/equities/bars/daily"
    res = requests.get(url, headers=jquants_headers(), params={"date": date_str})
    if res.status_code == 200:
        return res.json().get("data", [])
    print(f"[ERROR] Daily quotes: {res.status_code}")
    return []

def filter_hot_stocks(quotes, stocks):
    stock_map = {s.get("Code", ""): s for s in stocks}
    candidates = []
    for q in quotes:
        code = q.get("Code", "")
        open_p  = q.get("O") or q.get("Open") or 0
        close_p = q.get("C") or q.get("Close") or 0
        high_p  = q.get("H") or q.get("High") or 0
        low_p   = q.get("L") or q.get("Low") or 0
        volume  = q.get("Vo") or q.get("Volume") or 0
        if open_p <= 0 or close_p <= 0:
            continue
        change_rate = (close_p - open_p) / open_p * 100
        swing = (high_p - low_p) / low_p * 100 if low_p > 0 else 0
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
    candidates.sort(key=lambda x: x["change_rate"], reverse=True)
    return candidates[:50]

def get_news():
    if not NEWS_API_KEY:
        return []
    res = requests.get("https://newsapi.org/v2/everything", params={
        "q": "japan stock OR nikkei OR BOJ OR geopolitical risk",
        "language": "en", "sortBy": "publishedAt",
        "pageSize": 20, "apiKey": NEWS_API_KEY
    })
    return res.json().get("articles", []) if res.status_code == 200 else []

def get_twitter_buzz():
    if not X_BEARER_TOKEN:
        return []
    res = requests.get(
        "https://api.twitter.com/2/tweets/search/recent",
        headers={"Authorization": f"Bearer {X_BEARER_TOKEN}"},
        params={"query": "日本株 暴騰 OR 急騰 -is:retweet lang:ja", "max_results": 20}
    )
    return res.json().get("data", []) if res.status_code == 200 else []

def get_edinet_doc_id(securities_code):
    try:
        today = datetime.now()
        for days_back in range(0, 365, 30):
            date = (today - timedelta(days=days_back)).strftime("%Y-%m-%d")
            res = requests.get(
                "https://disclosure.edinet-fsa.go.jp/api/v2/documents.json",
                params={"date": date, "type": 2, "Subscription-Key": EDINET_API_KEY},
                timeout=10
            )
            if res.status_code != 200:
                continue
            for doc in res.json().get("results", []):
                sec_code = doc.get("secCode", "")
                if sec_code and sec_code.startswith(str(securities_code)[:4]):
                    if doc.get("formCode") in ["030000", "043000"]:
                        return doc.get("docID")
    except Exception as e:
        print(f"[ERROR] EDINET: {e}")
    return None

def get_edinet_text(doc_id):
    try:
        res = requests.get(
            f"https://disclosure.edinet-fsa.go.jp/api/v2/documents/{doc_id}",
            params={"type": 5, "Subscription-Key": EDINET_API_KEY}, timeout=30
        )
        if res.status_code == 200:
            z = zipfile.ZipFile(io.BytesIO(res.content))
            for name in z.namelist():
                if name.endswith(".txt") or "honbun" in name.lower():
                    text = z.read(name).decode("utf-8", errors="ignore")
                    for kw in ["代表取締役", "経営理念", "ごあいさつ", "社長メッセージ", "企業理念"]:
                        idx = text.find(kw)
                        if idx > 0:
                            return text[max(0, idx-100):idx+3000]
                    return text[:5000]
    except Exception as e:
        print(f"[ERROR] EDINET text: {e}")
    return None

def score_philosophy(code, company_name, text):
    if not text:
        return 50, "有報テキスト取得失敗", ""
    try:
        res = claude.messages.create(
            model="claude-opus-4-6", max_tokens=500,
            messages=[{"role": "user", "content": f"""企業の経営思想を評価してください。
【{company_name}（{code}）】有価証券報告書より：
{text[:3000]}
【高70点以上】深い思い・社会的使命感・独自の哲学
【低30点以下】定型文のみ・誰が書いても同じ内容
JSON形式のみで回答：{{"score":75,"reason":"理由1〜2行","philosophy_quote":"最も思想を表す一文"}}"""}]
        )
        t = res.content[0].text if res.content else "{}"
        d = json.loads(t[t.find("{"):t.rfind("}")+1])
        return d.get("score", 50), d.get("reason", ""), d.get("philosophy_quote", "")
    except Exception as e:
        print(f"[ERROR] Philosophy: {e}")
    return 50, "スコアリング失敗", ""

def sentinel_check(news, twitter):
    news_text    = "\n".join([f"- {n.get('title','')}" for n in news[:20]])
    twitter_text = "\n".join([f"- {t.get('text','')[:100]}" for t in twitter[:10]])
    try:
        res = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=300,
            messages=[{"role": "user", "content": f"""株式市場の全決済センチネルです。
【最新ニュース】{news_text or "なし"}
【X情報】{twitter_text or "なし"}
以下を検知した場合のみSELL_ALL：地政学リスク急変・世界的金融危機・日銀緊急政策・市場需給崩壊
HOLDかSELL_ALLの二択のみ。
JSON形式のみ：{{"action":"HOLD","reason":"判定理由1行","risk_level":1}}"""}]
        )
        t = res.content[0].text if res.content else "{}"
        return json.loads(t[t.find("{"):t.rfind("}")+1])
    except:
        return {"action": "HOLD", "reason": "判定失敗", "risk_level": 1}

def push_notify(title, msg, priority="default"):
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_CHANNEL}",
            data=msg.encode("utf-8"),
            headers={"Title": title, "Priority": priority}
        )
    except Exception as e:
        print(f"[ERROR] ntfy: {e}")

def phase1_broad_scan():
    print(f"\n{'='*50}\n  📡 フェーズ1：広域スキャン [{datetime.now().strftime('%H:%M:%S')}]\n{'='*50}")
    clear_state()
    print("  📊 銘柄・株価データ取得中...")
    stocks = get_listed_stocks()
    quotes = get_daily_quotes()
    candidates = filter_hot_stocks(quotes, stocks)
    print(f"  急騰候補: {len(candidates)}銘柄")
    print("  📰 ニュース・X取得中...")
    news    = get_news()
    twitter = get_twitter_buzz()
    print("  🛡️  センチネル判定中...")
    sentinel = sentinel_check(news, twitter)
    risk = sentinel.get("risk_level", 1)
    risk_bar = "🔴"*risk + "⚪"*(5-risk)
    print(f"  センチネル: {sentinel.get('action')} / リスク{risk_bar}({risk}/5)")
    if sentinel.get("action") == "SELL_ALL":
        msg = f"🚨全決済センチネル発動！\n理由: {sentinel.get('reason','')}\nリスク: {risk_bar}({risk}/5)"
        push_notify("🚨 全決済アラート", msg, priority="urgent")
        save_state({"sentinel": sentinel, "aborted": True})
        return
    print("  🤖 AI初期スコアリング中（50→20銘柄）...")
    cand_text = "\n".join([
        f"{q['code']} {q['name']} 前日比:{q['change_rate']:+.1f}% 出来高:{q['volume']:,} 値幅:{q['swing']:.1f}% {q['sector']}"
        for q in candidates[:50]
    ])
    news_text    = "\n".join([f"- {n.get('title','')}" for n in news[:15]])
    twitter_text = "\n".join([f"- {t.get('text','')[:80]}" for t in twitter[:10]])
    try:
        res = claude.messages.create(
            model="claude-opus-4-6", max_tokens=1500,
            messages=[{"role": "user", "content": f"""日本株デイトレードAIです。
以下50銘柄から「今日S高・急騰しそうな上位20銘柄」を選んでください。
【前日株価データ】\n{cand_text}
【最新ニュース】{news_text or "なし"}
【X話題】{twitter_text or "なし"}
選定基準：前日比プラス・出来高急増・ニュース連動・高ボラ・テーマ性
JSON形式のみで回答：
{{"top20":[{{"code":"コード","name":"銘柄名","score":85,"reason":"選定理由1行","theme":"テーマ"}}],"market_condition":"地合い判定1行","macro_summary":"マクロ要約1行"}}"""}]
        )
        t = res.content[0].text if res.content else "{}"
        result = json.loads(t[t.find("{"):t.rfind("}")+1])
        top20 = result.get("top20", [])
        print(f"  ✅ フェーズ1完了: {len(top20)}銘柄を次フェーズへ引き継ぎ")
        for i, s in enumerate(top20[:5], 1):
            print(f"     {i}. 【{s['code']}】{s['name']} (score:{s['score']}) {s['reason']}")
        save_state({
            "phase": 1, "top20": top20,
            "market_condition": result.get("market_condition", ""),
            "macro_summary": result.get("macro_summary", ""),
            "news": [n.get("title","") for n in news[:10]],
            "twitter": [t.get("text","")[:100] for t in twitter[:10]],
            "sentinel": sentinel,
        })
        push_notify("📡 フェーズ1完了",
            f"広域スキャン完了: {len(top20)}銘柄を選出\n地合い: {result.get('market_condition','')}\n8:20に絞り込み開始")
    except Exception as e:
        print(f"  [ERROR] フェーズ1: {e}")

def phase2_rescore():
    print(f"\n{'='*50}\n  🔬 フェーズ2：再スコアリング [{datetime.now().strftime('%H:%M:%S')}]\n{'='*50}")
    state = load_state()
    if not state or state.get("aborted") or state.get("phase", 0) < 1:
        print("  ⚠️  フェーズ1のデータなし → スキップ")
        return
    top20 = state.get("top20", [])
    if not top20:
        return
    print(f"  フェーズ1から{len(top20)}銘柄を引き継ぎ")
    cand_text = "\n".join([
        f"{s['code']} {s['name']} score:{s['score']} テーマ:{s.get('theme','')} 理由:{s['reason']}"
        for s in top20
    ])
    news_text    = "\n".join([f"- {n}" for n in state.get("news", [])])
    twitter_text = "\n".join([f"- {t}" for t in state.get("twitter", [])])
    try:
        res = claude.messages.create(
            model="claude-opus-4-6", max_tokens=1200,
            messages=[{"role": "user", "content": f"""フェーズ1の20銘柄を精査し上位10銘柄に絞ってください。
【フェーズ1選出銘柄】\n{cand_text}
【地合い】{state.get('market_condition','')}
【マクロ】{state.get('macro_summary','')}
【ニュース】{news_text or "なし"}
再評価基準：複数テーマ連動・踏み上げポテンシャル・セクターローテーション・フェーズ1スコアの批判的再検証
JSON形式のみ：
{{"top10":[{{"code":"コード","name":"銘柄名","score":90,"reason":"再評価理由1行","risk":"主なリスク","confidence":4}}],"eliminated":"除外理由1行"}}"""}]
        )
        t = res.content[0].text if res.content else "{}"
        result = json.loads(t[t.find("{"):t.rfind("}")+1])
        top10 = result.get("top10", [])
        print(f"  ✅ フェーズ2完了: {len(top10)}銘柄に絞り込み")
        for i, s in enumerate(top10[:5], 1):
            stars = "★"*s.get("confidence",3) + "☆"*(5-s.get("confidence",3))
            print(f"     {i}. 【{s['code']}】{s['name']} {stars} {s['reason']}")
        state.update({"phase": 2, "top10": top10})
        save_state(state)
        push_notify("🔬 フェーズ2完了",
            f"20→{len(top10)}銘柄に絞り込み\nTOP3: " + " / ".join([f"【{s['code']}】{s['name']}" for s in top10[:3]]) + "\n8:40に最終絞り込み開始")
    except Exception as e:
        print(f"  [ERROR] フェーズ2: {e}")

def phase3_crosscheck():
    print(f"\n{'='*50}\n  ⚡ フェーズ3：クロスチェック [{datetime.now().strftime('%H:%M:%S')}]\n{'='*50}")
    state = load_state()
    if not state or state.get("aborted") or state.get("phase", 0) < 2:
        print("  ⚠️  フェーズ2のデータなし → スキップ")
        return
    top10 = state.get("top10", [])
    if not top10:
        return
    print(f"  フェーズ2から{len(top10)}銘柄を引き継ぎ")
    print("  🧠 EDINET思想スコア取得中...")
    philosophy_results = {}
    for stock in top10[:5]:
        code = stock.get("code", "")
        name = stock.get("name", "")
        print(f"     [{code}] {name} 取得中...")
        doc_id = get_edinet_doc_id(code)
        if doc_id:
            text = get_edinet_text(doc_id)
            score, reason, quote = score_philosophy(code, name, text)
            philosophy_results[code] = {"score": score, "reason": reason, "quote": quote}
            print(f"     → {score}/100")
        else:
            philosophy_results[code] = {"score": 50, "reason": "EDINET未発見", "quote": ""}
    cand_text = "\n".join([
        f"{s['code']} {s['name']} score:{s['score']} 確信:{s.get('confidence',3)}/5 リスク:{s.get('risk','')} 理由:{s['reason']}"
        + (f" 思想:{philosophy_results.get(s['code'],{}).get('score','-')}/100" if s['code'] in philosophy_results else "")
        for s in top10
    ])
    try:
        res = claude.messages.create(
            model="claude-opus-4-6", max_tokens=1200,
            messages=[{"role": "user", "content": f"""厳格なクロスチェックで上位5銘柄を選んでください。
【フェーズ2選出銘柄】\n{cand_text}
【地合い】{state.get('market_condition','')}
チェック項目：①フェーズ1-2で一貫して高スコアか ②リスク許容範囲か ③思想スコア50以下は除外 ④同セクター重複は最上位のみ ⑤地合い・マクロ逆行なし
JSON形式のみ：
{{"top5":[{{"code":"コード","name":"銘柄名","final_score":95,"buy_reason":"買いの根拠2行","sell_trigger":"損切りポイント","target":"+10%","confidence":5}}],"crosscheck_summary":"クロスチェック総評1行"}}"""}]
        )
        t = res.content[0].text if res.content else "{}"
        result = json.loads(t[t.find("{"):t.rfind("}")+1])
        top5 = result.get("top5", [])
        print(f"  ✅ フェーズ3完了: {len(top5)}銘柄に絞り込み")
        for i, s in enumerate(top5, 1):
            stars = "★"*s.get("confidence",3) + "☆"*(5-s.get("confidence",3))
            print(f"     {i}. 【{s['code']}】{s['name']} {stars} {s.get('target','')} {s['buy_reason'][:30]}")
        state.update({"phase": 3, "top5": top5, "philosophy": philosophy_results,
                      "crosscheck_summary": result.get("crosscheck_summary", "")})
        save_state(state)
        push_notify("⚡ フェーズ3完了",
            f"10→{len(top5)}銘柄に絞り込み\n{result.get('crosscheck_summary','')}\n8:55に最終TOP3通知")
    except Exception as e:
        print(f"  [ERROR] フェーズ3: {e}")

def phase4_final_top3():
    print(f"\n{'='*50}\n  🏆 フェーズ4：最終TOP3決定 [{datetime.now().strftime('%H:%M:%S')}]\n{'='*50}")
    state = load_state()
    if not state or state.get("aborted") or state.get("phase", 0) < 3:
        print("  ⚠️  フェーズ3のデータなし → スキップ")
        return
    top5 = state.get("top5", [])
    if not top5:
        return
    sentinel  = state.get("sentinel", {})
    philosophy = state.get("philosophy", {})
    risk = sentinel.get("risk_level", 1)
    risk_bar = "🔴"*risk + "⚪"*(5-risk)
    top3 = top5[:3]
    print(f"  🛡️  センチネル: HOLD / リスク{risk_bar}({risk}/5)")
    print(f"  ━━━━ 本日のTOP3候補 ━━━━")
    for i, s in enumerate(top3, 1):
        code = s.get("code","")
        stars = "★"*s.get("confidence",3) + "☆"*(5-s.get("confidence",3))
        phil = philosophy.get(code, {})
        print(f"  {'🥇' if i==1 else '🥈' if i==2 else '🥉'} 第{i}候補 【{code}】{s['name']} {stars}")
        print(f"     目標:{s.get('target','')} / 損切り:{s.get('sell_trigger','')}")
        print(f"     根拠:{s['buy_reason']}")
        print(f"     思想:{phil.get('score','-')}/100 「{phil.get('quote','')}」")
    # 1銘柄ずつ通知
    for i, s in enumerate(top3, 1):
        code = s.get("code","")
        stars = "★"*s.get("confidence",3) + "☆"*(5-s.get("confidence",3))
        phil = philosophy.get(code, {})
        msg = f"{'🥇' if i==1 else '🥈' if i==2 else '🥉'} 第{i}候補\n【{code}】{s['name']}\n確信度: {stars}\n目標: {s.get('target','')}\n根拠: {s['buy_reason']}\n損切り: {s.get('sell_trigger','')}\n思想: {phil.get('score','-')}/100"
        push_notify(f"🏆 TOP3 #{i} 【{code}】{s['name']}", msg, priority="high" if i==1 else "default")
        time.sleep(1)
    # まとめ通知
    summary = "🏆 本日のTOP3確定\n"
    for i, s in enumerate(top3, 1):
        summary += f"{'🥇' if i==1 else '🥈' if i==2 else '🥉'}【{s['code']}】{s['name']} {s.get('target','')}\n"
    summary += f"\n地合い: {state.get('market_condition','')}\nリスク: {risk_bar}({risk}/5)\n\n👆 1銘柄を選んで寄り付き（9:00）で買い！"
    push_notify("🏆 本日のTOP3（あなたが選ぶ）", summary, priority="high")
    state.update({"phase": 4, "top3_final": top3})
    save_state(state)
    print(f"  ✅ フェーズ4完了: TOP3通知送信済み")

def phase5_post_open():
    print(f"\n{'='*50}\n  📈 フェーズ5：初動確証 [{datetime.now().strftime('%H:%M:%S')}]\n{'='*50}")
    state = load_state()
    if not state or state.get("aborted") or state.get("phase", 0) < 4:
        print("  ⚠️  フェーズ4のデータなし → スキップ")
        return
    top3 = state.get("top3_final", [])
    print("  🛡️  センチネル再判定中...")
    news    = get_news()
    twitter = get_twitter_buzz()
    sentinel_now = sentinel_check(news, twitter)
    if sentinel_now.get("action") == "SELL_ALL":
        risk = sentinel_now.get("risk_level", 5)
        risk_bar = "🔴"*risk + "⚪"*(5-risk)
        push_notify("🚨 緊急！全決済アラート",
            f"🚨 寄り付き後センチネル発動！\n理由: {sentinel_now.get('reason','')}\nリスク: {risk_bar}({risk}/5)\n今すぐ全て売れ！", priority="urgent")
        print("  🚨 SELL_ALL発動！緊急通知送信")
        return
    top3_text = "\n".join([f"【{s['code']}】{s['name']} 目標:{s.get('target','')} 根拠:{s['buy_reason']}" for s in top3])
    news_text = "\n".join([f"- {n.get('title','')}" for n in news[:10]])
    try:
        res = claude.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=800,
            messages=[{"role": "user", "content": f"""寄り付き後5分の初動評価です。
【事前選出TOP3銘柄】\n{top3_text}
【寄り付き後の最新ニュース】\n{news_text or "なし"}
【地合い】{state.get('market_condition','')}
各銘柄：予想通り動いているか(HOLD推奨)・想定外の動き(早期撤退推奨)
JSON形式のみ：
{{"evaluations":[{{"code":"コード","status":"HOLD","message":"初動コメント1行","action_advice":"アドバイス1行"}}],"overall":"総評1行"}}"""}]
        )
        t = res.content[0].text if res.content else "{}"
        result = json.loads(t[t.find("{"):t.rfind("}")+1])
        evals = result.get("evaluations", [])
        msg = f"📈 初動確証スキャン\n{result.get('overall','')}\n\n"
        for e in evals:
            icon = "✅" if e.get("status") == "HOLD" else "⚠️"
            msg += f"{icon}【{e.get('code','')}】{e.get('message','')}\n→ {e.get('action_advice','')}\n"
        push_notify("📈 初動確証スキャン", msg)
        print(f"  ✅ フェーズ5完了: {result.get('overall','')}")
    except Exception as e:
        print(f"  [ERROR] フェーズ5: {e}")

def setup_schedule():
    schedule.every().day.at("08:00").do(phase1_broad_scan)
    schedule.every().day.at("08:20").do(phase2_rescore)
    schedule.every().day.at("08:40").do(phase3_crosscheck)
    schedule.every().day.at("08:55").do(phase4_final_top3)
    schedule.every().day.at("09:05").do(phase5_post_open)
    print("📅 スケジュール:")
    print("   08:00 フェーズ1：広域スキャン（50→20銘柄）")
    print("   08:20 フェーズ2：再スコアリング（20→10銘柄）")
    print("   08:40 フェーズ3：クロスチェック（10→5銘柄）")
    print("   08:55 フェーズ4：最終TOP3通知📱")
    print("   09:05 フェーズ5：初動確証・売りアラート")

if __name__ == "__main__":
    print("=" * 50)
    print("  📈 Stock Scanner 起動")
    print("=" * 50)
    wait_until_8am()
    setup_schedule()
    phase1_broad_scan()
    while True:
        schedule.run_pending()
        time.sleep(30)
