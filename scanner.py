import os, time, schedule, requests, anthropic, json, zipfile, io, threading, re
import pytz
from datetime import datetime, timedelta
from flask import Flask, jsonify, request

JQUANTS_API_KEY   = os.environ.get("JQUANTS_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
NEWS_API_KEY      = os.environ.get("NEWS_API_KEY", "")
X_BEARER_TOKEN    = os.environ.get("X_API_BEARER_TOKEN", "")
EDINET_API_KEY    = os.environ.get("EDINET_API_KEY", "")
NTFY_CHANNEL      = "mitsugu-stock-scanner"
PORT              = int(os.environ.get("PORT", 8080))

claude     = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
STATE_FILE = "/tmp/scan_state.json"
app        = Flask(__name__)

def safe_json(text):
    import re as _re, json as _json
    text = _re.sub(r"```json\s*", "", text)
    text = _re.sub(r"```\s*", "", text)
    start = text.find("{")
    if start == -1:
        return {}
    text = text[start:]
    end = text.rfind("}")
    if end == -1:
        return {}
    chunk = text[:end+1]
    # 方法1: json-repair
    try:
        from json_repair import repair_json
        return _json.loads(repair_json(chunk))
    except ImportError:
        pass
    except Exception:
        pass
    # 方法2: 文字列内の改行を除去
    try:
        fixed = []
        in_str = False
        i = 0
        while i < len(chunk):
            c = chunk[i]
            if c == "\\" and i+1 < len(chunk):
                fixed.append(c)
                fixed.append(chunk[i+1])
                i += 2
                continue
            if c == '"':
                in_str = not in_str
            if in_str and c in ("\n","\r","\t"):
                fixed.append(" ")
            else:
                fixed.append(c)
            i += 1
        return _json.loads("".join(fixed))
    except Exception:
        pass
    # 方法3: 全改行除去
    try:
        return _json.loads(_re.sub(r"[\n\r\t]", " ", chunk))
    except Exception as e:
        add_log(f"[safe_json ERROR] {str(e)[:80]}")
        return {}


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
    res = requests.get("https://api.jquants.com/v2/equities/master", headers=jquants_headers())
    return res.json().get("data", []) if res.status_code == 200 else []

def get_daily_quotes(date_str=None):
    if not date_str:
        today = datetime.now()
        for i in range(1, 7):
            c = today - timedelta(days=i)
            if c.weekday() < 5:
                date_str = c.strftime("%Y%m%d"); break
    res = requests.get("https://api.jquants.com/v2/equities/bars/daily",
        headers=jquants_headers(), params={"date": date_str})
    return res.json().get("data", []) if res.status_code == 200 else []

def filter_hot_stocks(quotes, stocks):
    def norm(c): return c[:-1] if len(c)==5 else c
    stock_map = {norm(s.get("Code","")): s for s in stocks}
    candidates = []
    for q in quotes:
        code    = q.get("Code","")[:-1] if len(q.get("Code",""))==5 else q.get("Code","")
        open_p  = q.get("O") or q.get("Open") or 0
        close_p = q.get("C") or q.get("Close") or 0
        high_p  = q.get("H") or q.get("High") or 0
        low_p   = q.get("L") or q.get("Low") or 0
        volume  = q.get("Vo") or q.get("Volume") or 0
        if open_p <= 0 or close_p <= 0: continue
        change_rate = (close_p - open_p) / open_p * 100
        swing = (high_p - low_p) / low_p * 100 if low_p > 0 else 0
        si = stock_map.get(code, {})
        candidates.append({"code":code,"name":si.get("CoName",""),"close":close_p,
            "change_rate":round(change_rate,2),"volume":int(volume),
            "swing":round(swing,2),"market":si.get("MktNm",""),"sector":si.get("S17Nm","")})
    candidates.sort(key=lambda x: x["change_rate"], reverse=True)
    return candidates[:50]

def get_news():
    if not NEWS_API_KEY: return []
    res = requests.get("https://newsapi.org/v2/everything",
        params={"q":"japan stock OR nikkei OR BOJ","language":"en",
                "sortBy":"publishedAt","pageSize":20,"apiKey":NEWS_API_KEY})
    return res.json().get("articles",[]) if res.status_code==200 else []

def get_twitter_buzz():
    if not X_BEARER_TOKEN: return []
    res = requests.get("https://api.twitter.com/2/tweets/search/recent",
        headers={"Authorization":f"Bearer {X_BEARER_TOKEN}"},
        params={"query":"\u65e5\u672c\u682a \u66b4\u9a30 OR \u6025\u9a30 -is:retweet lang:ja","max_results":20})
    return res.json().get("data",[]) if res.status_code==200 else []

def get_edinet_doc_id(securities_code):
    try:
        today = datetime.now()
        for days_back in range(0, 365, 30):
            date = (today - timedelta(days=days_back)).strftime("%Y-%m-%d")
            res = requests.get("https://disclosure.edinet-fsa.go.jp/api/v2/documents.json",
                params={"date":date,"type":2,"Subscription-Key":EDINET_API_KEY}, timeout=10)
            if res.status_code != 200: continue
            for doc in res.json().get("results",[]):
                sc = doc.get("secCode","")
                if sc and sc.startswith(str(securities_code)[:4]) and doc.get("formCode") in ["030000","043000"]:
                    return doc.get("docID")
    except: pass
    return None

def get_edinet_text(doc_id):
    try:
        res = requests.get(f"https://disclosure.edinet-fsa.go.jp/api/v2/documents/{doc_id}",
            params={"type":5,"Subscription-Key":EDINET_API_KEY}, timeout=30)
        if res.status_code == 200:
            z = zipfile.ZipFile(io.BytesIO(res.content))
            for name in z.namelist():
                if name.endswith(".txt") or "honbun" in name.lower():
                    text = z.read(name).decode("utf-8", errors="ignore")
                    for kw in ["\u4ee3\u8868\u53d6\u7de0\u5f79","\u7d4c\u55b6\u7406\u5ff5","\u3054\u3042\u3044\u3055\u3064","\u793e\u9577\u30e1\u30c3\u30bb\u30fc\u30b8","\u4f01\u696d\u7406\u5ff5"]:
                        idx = text.find(kw)
                        if idx > 0: return text[max(0,idx-100):idx+3000]
                    return text[:5000]
    except: pass
    return None

def score_philosophy(code, company_name, text):
    if not text: return 50, "\u53d6\u5f97\u5931\u6557", ""
    try:
        res = claude.messages.create(model="claude-opus-4-6", max_tokens=500,
            messages=[{"role":"user","content":
                f"\u4f01\u696d\u306e\u7d4c\u55b6\u601d\u60f3\u3092\u8a55\u4fa1\u3002\u3010{company_name}({code})\u3011\n{text[:3000]}\n"
                f"\u9ad870\u70b9\u4ee5\u4e0a:\u72ec\u81ea\u54f2\u5b66\u3002\u4f4e30\u70b9\u4ee5\u4e0b:\u5b9a\u578b\u6587\u3002\n"
                f"JSON\u306e\u307f:{{\"score\":75,\"reason\":\"\u7406\u7531\",\"philosophy_quote\":\"\u4e00\u6587\"}}"}])
        t = res.content[0].text if res.content else "{}"
        d = safe_json(t)
        return d.get("score",50), d.get("reason",""), d.get("philosophy_quote","")
    except: return 50, "\u5931\u6557", ""

def sentinel_check(news, twitter):
    news_text    = "\n".join([f"- {n.get('title','')}" for n in news[:20]])
    twitter_text = "\n".join([f"- {t.get('text','')[:100]}" for t in twitter[:10]])
    try:
        res = claude.messages.create(model="claude-haiku-4-5-20251001", max_tokens=300,
            messages=[{"role":"user","content":
                f"\u5168\u6c7a\u6e08\u30bb\u30f3\u30c1\u30cd\u30eb\u3002\n"
                f"\u30cb\u30e5\u30fc\u30b9:{news_text or '\u306a\u3057'}\nX:{twitter_text or '\u306a\u3057'}\n"
                f"\u5730\u653f\u5b66\u6025\u5909\u30fb\u91d1\u878d\u5371\u6a5f\u30fb\u65e5\u9280\u7dca\u6025\u306e\u307fSELL_ALL\u3002\n"
                f"JSON\u306e\u307f:{{\"action\":\"HOLD\",\"reason\":\"\u7406\u7531\",\"risk_level\":1}}"}])
        t = res.content[0].text if res.content else "{}"
        return safe_json(t)
    except: return {"action":"HOLD","reason":"\u5224\u5b9a\u5931\u6557","risk_level":1}

def push_notify(title, msg, priority="default"):
    try:
        requests.post(f"https://ntfy.sh/{NTFY_CHANNEL}",
            data=msg.encode("utf-8"), headers={"Title":title,"Priority":priority})
    except: pass

LOG_BUFFER = []
def add_log(msg):
    jst  = pytz.timezone("Asia/Tokyo")
    ts   = datetime.now(jst).strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    LOG_BUFFER.append(line)
    if len(LOG_BUFFER) > 200: LOG_BUFFER.pop(0)
    print(line)

def phase1_broad_scan():
    add_log("\U0001f4e1 Ph.1:\u5e83\u57df\u30b9\u30ad\u30e3\u30f3\u958b\u59cb")
    clear_state()
    stocks     = get_listed_stocks()
    quotes     = get_daily_quotes()
    candidates = filter_hot_stocks(quotes, stocks)
    add_log(f"\u6025\u9a30\u5019\u88dc: {len(candidates)}\u9298\u67c4")
    news     = get_news()
    twitter  = get_twitter_buzz()
    sentinel = sentinel_check(news, twitter)
    risk     = sentinel.get("risk_level",1)
    add_log(f"\u30bb\u30f3\u30c1\u30cd\u30eb: {sentinel.get('action')} {'\u2588'*risk+'\u2591'*(5-risk)} ({risk}/5)")
    if sentinel.get("action") == "SELL_ALL":
        push_notify("\U0001f6a8 \u5168\u6c7a\u6e08\u30a2\u30e9\u30fc\u30c8",
            f"\u30bb\u30f3\u30c1\u30cd\u30eb\u767a\u52d5\uff01\n{sentinel.get('reason','')}", priority="urgent")
        save_state({"sentinel":sentinel,"aborted":True,"log":LOG_BUFFER[-20:]}); return
    add_log("\U0001f916 Claude AI\u5206\u6790\u4e2d\uff0850\u219220\u9298\u67c4\uff09...")
    cand_text = "\n".join([
        f"{q['code']} {q['name']} \u524d\u65e5\u6bd4:{q['change_rate']:+.1f}% \u51fa\u6765\u9ad8:{q['volume']:,} {q['sector']}"
        for q in candidates[:50]])
    news_text    = "\n".join([f"- {n.get('title','')}" for n in news[:15]])
    twitter_text = "\n".join([f"- {t.get('text','')[:80]}" for t in twitter[:10]])
    try:
        res = claude.messages.create(model="claude-opus-4-6", max_tokens=1500,
            messages=[{"role":"user","content":
                f"\u65e5\u672c\u682a\u30c7\u30a4\u30c8\u30ec\u30fc\u30c9AI\u300250\u9298\u67c4\u304b\u3089\u6025\u9a30\u4e0a\u4f2020\u9298\u67c4\u3092\u9078\u629e\u3002\n"
                f"\u3010\u682a\u4fa1\u3011\n{cand_text}\n"
                f"\u3010\u30cb\u30e5\u30fc\u30b9\u3011{news_text or '\u306a\u3057'}\n"
                f"\u3010X\u3011{twitter_text or '\u306a\u3057'}\n"
                f"\u5fc5\u305aJSON\u306e\u307f\u3067\u56de\u7b54\u3002\u30b3\u30fc\u30c9\u30d6\u30ed\u30c3\u30af\u4e0d\u8981\u3002"
                f"market_condition\u306f\u5f53\u65e5\u306e\u5730\u5408\u3044\u3092\u7c21\u6f54\u306b20\u5b57\u4ee5\u5185\u3067\u8868\u73fe\u3002macro_summary\u306f\u30de\u30af\u30ed\u72b6\u6cc1\u3092\u7c21\u6f54\u306b20\u5b57\u4ee5\u5185\u3067\u3002\n"
                f"{{\"top20\":[{{\"code\":\"4890\",\"name\":\"\u5764\u7530\u30e9\u30dc\",\"score\":92,\"reason\":\"\u7406\u7531\",\"theme\":\"\u30d0\u30a4\u30aa\"}}],"
                f"\"market_condition\":\"\u65e5\u7d4c\u5e73\u5747\u5c0f\u5e45\u9ad8\u3001\u534a\u5c0e\u4f53\u5f37\",\"macro_summary\":\"\u7c73\u56fd\u91d1\u5229\u843d\u3061\u8d85\u3048\u6c17\u5406\u6c17\"}}"}])
        t      = res.content[0].text if res.content else "{}"
        result = safe_json(t)
        top20  = result.get("top20",[])
        add_log(f"\u2705 Ph.1\u5b8c\u4e86 \u2014 {len(top20)}\u9298\u67c4\u3092\u9078\u51fa")
        save_state({"phase":1,"top20":top20,
            "market_condition":result.get("market_condition",""),
            "macro_summary":result.get("macro_summary",""),
            "news":[n.get("title","") for n in news[:10]],
            "twitter":[t.get("text","")[:100] for t in twitter[:10]],
            "sentinel":sentinel,"log":LOG_BUFFER[-20:]})
        push_notify("\U0001f4e1 Ph.1\u5b8c\u4e86",
            f"{len(top20)}\u9298\u67c4\u9078\u51fa\n\u5730\u5408\u3044: {result.get('market_condition','')}")
    except Exception as e: add_log(f"[ERROR] Ph.1: {e}")

def phase2_rescore():
    add_log("\U0001f52c Ph.2:\u518d\u30b9\u30b3\u30a2\u30ea\u30f3\u30b0")
    state = load_state()
    if not state or state.get("aborted") or state.get("phase",0) < 1:
        add_log("\u26a0\ufe0f Ph.1\u30c7\u30fc\u30bf\u306a\u3057"); return
    top20 = state.get("top20",[])
    if not top20: return
    cand_text = "\n".join([
        f"{s['code']} {s['name']} score:{s['score']} {s.get('theme','')} {s['reason']}"
        for s in top20])
    news_text = "\n".join([f"- {n}" for n in state.get("news",[])])
    try:
        res = claude.messages.create(model="claude-opus-4-6", max_tokens=1200,
            messages=[{"role":"user","content":
                f"20\u9298\u67c4\u3092\u7cbe\u67fb\u3057\u4e0a\u4f2010\u9298\u67c4\u306b\u7d5e\u308b\u3002\n{cand_text}\n"
                f"\u5730\u5408\u3044:{state.get('market_condition','')}\n\u30cb\u30e5\u30fc\u30b9:{news_text or '\u306a\u3057'}\n"
                f"\u5fc5\u305aJSON\u306e\u307f:{{\"top10\":[{{\"code\":\"\u30b3\u30fc\u30c9\",\"name\":\"\u540d\u524d\","
                f"\"score\":90,\"reason\":\"\u7406\u7531\",\"risk\":\"\u30ea\u30b9\u30af\",\"confidence\":4}}],"
                f"\"eliminated\":\"\u9664\u5916\u7406\u7531\"}}"}])
        t      = res.content[0].text if res.content else "{}"
        result = safe_json(t)
        top10  = result.get("top10",[])
        add_log(f"\u2705 Ph.2\u5b8c\u4e86 \u2014 {len(top10)}\u9298\u67c4")
        state.update({"phase":2,"top10":top10,"log":LOG_BUFFER[-20:]}); save_state(state)
        push_notify("\U0001f52c Ph.2\u5b8c\u4e86", f"20\u2192{len(top10)}\u9298\u67c4")
    except Exception as e: add_log(f"[ERROR] Ph.2: {e}")

def phase3_crosscheck():
    add_log("\u26a1 Ph.3:\u30af\u30ed\u30b9\u30c1\u30a7\u30c3\u30af")
    state = load_state()
    if not state or state.get("aborted") or state.get("phase",0) < 2:
        add_log("\u26a0\ufe0f Ph.2\u30c7\u30fc\u30bf\u306a\u3057"); return
    top10 = state.get("top10",[])
    if not top10: return
    philosophy_results = {}
    import concurrent.futures as _cf
    def _score_one(stock):
        c = stock.get("code",""); n = stock.get("name","")
        add_log(f"\U0001f9e0 [{c}] {n} \u601d\u60f3\u30b9\u30b3\u30a2...")
        doc_id = get_edinet_doc_id(c)
        if doc_id:
            text = get_edinet_text(doc_id)
            score, reason, quote = score_philosophy(c, n, text)
            add_log(f"\u2192 {c}: {score}/100")
            return c, {"score":score,"reason":reason,"quote":quote}
        return c, {"score":50,"reason":"EDINET\u672a\u767a\u898b","quote":""}
    # 上位3銘柄のみ並列スコアリング（速度優先）
    with _cf.ThreadPoolExecutor(max_workers=3) as ex:
        for c, r in ex.map(_score_one, top10[:3]):
            philosophy_results[c] = r
    for stock in top10[3:]:
        philosophy_results[stock.get("code","")] = {"score":50,"reason":"\u30b9\u30ad\u30c3\u30d7","quote":""}
    cand_text = "\n".join([
        f"{s['code']} {s['name']} score:{s['score']} \u78ba\u4fe1:{s.get('confidence',3)}/5 "
        f"\u601d\u60f3:{philosophy_results.get(s['code'],{}).get('score','-')}/100"
        for s in top10])
    try:
        res = claude.messages.create(model="claude-opus-4-6", max_tokens=1200,
            messages=[{"role":"user","content":
                f"\u53b3\u683c\u306a\u30af\u30ed\u30b9\u30c1\u30a7\u30c3\u30af\u3067\u4e0a\u4f205\u9298\u67c4\u3092\u9078\u629e\u3002\n{cand_text}\n"
                f"\u5730\u5408\u3044:{state.get('market_condition','')}\n"
                f"\u5fc5\u305aJSON\u306e\u307f:{{\"top5\":[{{\"code\":\"\u30b3\u30fc\u30c9\",\"name\":\"\u540d\u524d\","
                f"\"final_score\":95,\"buy_reason\":\"\u6839\u62e0\",\"sell_trigger\":\"\u640d\u5207\u308a\","
                f"\"target\":\"+10%\",\"confidence\":5}}],\"crosscheck_summary\":\"\u7dcf\u8a55\"}}"}])
        t      = res.content[0].text if res.content else "{}"
        result = safe_json(t)
        top5   = result.get("top5",[])
        add_log(f"\u2705 Ph.3\u5b8c\u4e86 \u2014 {len(top5)}\u9298\u67c4")
        state.update({"phase":3,"top5":top5,"philosophy":philosophy_results,
            "crosscheck_summary":result.get("crosscheck_summary",""),
            "log":LOG_BUFFER[-20:]}); save_state(state)
        push_notify("\u26a1 Ph.3\u5b8c\u4e86",
            f"10\u2192{len(top5)}\u9298\u67c4\n{result.get('crosscheck_summary','')}")
    except Exception as e: add_log(f"[ERROR] Ph.3: {e}")

def phase4_final_top3():
    add_log("\U0001f3c6 Ph.4:\u6700\u7d42TOP3\u6c7a\u5b9a")
    state = load_state()
    if not state or state.get("aborted") or state.get("phase",0) < 3:
        add_log("\u26a0\ufe0f Ph.3\u30c7\u30fc\u30bf\u306a\u3057"); return
    top5       = state.get("top5",[])
    philosophy = state.get("philosophy",{})
    sentinel   = state.get("sentinel",{})
    risk       = sentinel.get("risk_level",1)
    top3       = top5[:3]
    medals     = ["\U0001f947","\U0001f948","\U0001f949"]
    for i, s in enumerate(top3, 1):
        code  = s.get("code","")
        stars = "\u2605"*s.get("confidence",3)+"\u2606"*(5-s.get("confidence",3))
        phil  = philosophy.get(code,{})
        msg   = (f"{medals[i-1]} \u7b2c{i}\u5019\u88dc\n\u300a{code}\u300b{s['name']}\n"
                 f"\u78ba\u4fe1\u5ea6:{stars}\n\u76ee\u6a19:{s.get('target','')}\n"
                 f"\u6839\u62e0:{s['buy_reason']}\n\u640d\u5207\u308a:{s.get('sell_trigger','')}\n"
                 f"\u601d\u60f3:{phil.get('score','-')}/100")
        push_notify(f"\U0001f3c6 TOP3#{i} \u300a{code}\u300b{s['name']}", msg,
            priority="high" if i==1 else "default")
        time.sleep(1)
    summary = "\U0001f3c6 \u672c\u65e5\u306eTOP3\u78ba\u5b9a\n"
    summary += "".join([f"{medals[i]}\u300a{s['code']}\u300b{s['name']} {s.get('target','')}\n"
                        for i,s in enumerate(top3)])
    summary += (f"\n\u5730\u5408\u3044:{state.get('market_condition','')}\n"
                f"\u30ea\u30b9\u30af:{'\u2588'*risk+'\u2591'*(5-risk)}({risk}/5)\n\n"
                f"\U0001f446 1\u9298\u67c4\u3092\u9078\u3093\u3067\u5bc4\u308a\u4ed8\u304d(9:00)\u3067\u8cb7\u3044\uff01")
    push_notify("\U0001f3c6 \u672c\u65e5\u306eTOP3", summary, priority="high")
    add_log("\u2705 Ph.4\u5b8c\u4e86 \u2014 TOP3\u901a\u77e5\u9001\u4fe1\u6e08\u307f")
    state.update({"phase":4,"top3_final":top3,"log":LOG_BUFFER[-20:]}); save_state(state)

def get_realtime_prices(codes):
    """JQuantsのリアルタイムに近い当日価格を取得"""
    jst = pytz.timezone("Asia/Tokyo")
    today = datetime.now(jst).strftime("%Y%m%d")
    prices = {}
    for code in codes:
        try:
            res = requests.get("https://api.jquants.com/v2/equities/prices/daily",
                headers=jquants_headers(),
                params={"code": code + "0", "date": today}, timeout=8)
            if res.status_code == 200:
                data = res.json().get("daily_quotes", [])
                if data:
                    d = data[-1]
                    op = d.get("OpenPrice") or d.get("Open") or 0
                    cl = d.get("MorningSessionClose") or d.get("Close") or d.get("ClosePrice") or op
                    vo = d.get("Volume") or 0
                    chg = round((cl - op) / op * 100, 2) if op > 0 else 0
                    prices[code] = {"open": op, "current": cl, "volume": int(vo),
                                    "change_pct": chg, "change_yen": round(cl - op, 1) if op > 0 else 0}
        except Exception as e:
            add_log(f"[price] {code}: {e}")
    return prices

def phase5_post_open():
    add_log("\U0001f4c8 Ph.5:\u521d\u52d5\u78ba\u8a3c\u30b9\u30ad\u30e3\u30f3")
    state = load_state()
    if not state or state.get("aborted") or state.get("phase",0) < 4:
        add_log("\u26a0\ufe0f Ph.4\u30c7\u30fc\u30bf\u306a\u3057"); return
    top3         = state.get("top3_final",[])
    codes        = [s.get("code","") for s in top3]
    # リアルタイム株価取得
    add_log("\U0001f4ca \u682a\u4fa1\u53d6\u5f97\u4e2d...")
    prices = get_realtime_prices(codes)
    for s in top3:
        c = s.get("code","")
        if c in prices:
            p = prices[c]
            chg = p["change_pct"]
            arrow = "\U0001f4c8" if chg >= 0 else "\U0001f4c9"
            sign = "+" if chg >= 0 else ""
            add_log(f"  {arrow} \u300a{c}\u300b {sign}{chg}% ({sign}{p['change_yen']}\u5186) \u73fe\u5728\u5c71:{p['current']}")
    news         = get_news()
    twitter      = get_twitter_buzz()
    sentinel_now = sentinel_check(news, twitter)
    if sentinel_now.get("action") == "SELL_ALL":
        push_notify("\U0001f6a8 \u7dca\u6025\u5168\u6c7a\u6e08",
            f"\u30bb\u30f3\u30c1\u30cd\u30eb\u767a\u52d5\uff01\n{sentinel_now.get('reason','')}\n\u4eca\u3059\u3050\u5168\u3066\u58f2\u308c\uff01",
            priority="urgent")
        add_log("\U0001f6a8 SELL_ALL\u767a\u52d5\uff01"); return
    # 株価情報をプロンプトに含める
    top3_text = "\n".join([
        f"\u300a{s['code']}\u300b{s['name']} \u76ee\u6a19:{s.get('target','')} \u6839\u62e0:{s['buy_reason']}"
        + (f" \u73fe\u5728:{prices[s['code']]['change_pct']:+.1f}%" if s['code'] in prices else "")
        for s in top3])
    news_text = "\n".join([f"- {n.get('title','')}" for n in news[:10]])
    try:
        res = claude.messages.create(model="claude-haiku-4-5-20251001", max_tokens=800,
            messages=[{"role":"user","content":
                f"\u5bc4\u308a\u4ed8\u304d\u5f8c\u306e\u521d\u52d5\u8a55\u4fa1\u3002\u682a\u4fa1\u5909\u52d5\u3082\u8003\u616e\u3057\u3066\u3002\n"
                f"\u3010TOP3+\u682a\u4fa1\u3011{top3_text}\n\u3010\u30cb\u30e5\u30fc\u30b9\u3011{news_text or '\u306a\u3057'}\n"
                f"\u5fc5\u305aJSON\u306e\u307f:{{\"evaluations\":[{{\"code\":\"\u30b3\u30fc\u30c9\","
                f"\"status\":\"HOLD\",\"message\":\"\u521d\u52d5\u30b3\u30e1\u30f3\u30c8 \u682a\u4fa1\u52d5\u5411\u3082\u542b\u3080\","
                f"\"action_advice\":\"\u30a2\u30c9\u30d0\u30a4\u30b9\"}}],\"overall\":\"\u7dcf\u8a55\"}}"}])
        t      = res.content[0].text if res.content else "{}"
        result = safe_json(t)
        evals  = result.get("evaluations",[])
        msg    = f"\U0001f4c8 \u521d\u52d5\u78ba\u8a3c\n{result.get('overall','')}\n\n"
        for e in evals:
            icon = "\u2705" if e.get("status")=="HOLD" else "\u26a0\ufe0f"
            msg += f"{icon}\u300a{e.get('code','')}\u300b{e.get('message','')}\n\u2192 {e.get('action_advice','')}\n"
        state["phase"] = 5
        state["post_open_result"] = result
        state["realtime_prices"] = prices
        save_state(state)
        push_notify("\U0001f4c8 \u521d\u52d5\u78ba\u8a3c", msg)
        add_log(f"\u2705 Ph.5\u5b8c\u4e86: {result.get('overall','')}")
    except Exception as e: add_log(f"[ERROR] Ph.5: {e}")

HTML = """<!DOCTYPE html>
<html lang="ja"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>STOCK SCANNER</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#1a1a1a;color:#c8c8c8;font-family:"JetBrains Mono",monospace;padding:16px 20px;min-height:100vh}
::-webkit-scrollbar{width:4px}::-webkit-scrollbar-track{background:#1a1a1a}::-webkit-scrollbar-thumb{background:#4a4a4a;border-radius:2px}
@keyframes blink{0%,100%{opacity:1}50%{opacity:0}}
@keyframes spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}
@keyframes fadeIn{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:translateY(0)}}
.cursor{display:inline-block;width:7px;height:13px;background:#74fafd;animation:blink 1s step-end infinite;vertical-align:middle}
.spinner{display:inline-block;width:11px;height:11px;border:2px solid #4a4a4a;border-top-color:#74fafd;border-radius:50%;animation:spin .6s linear infinite;vertical-align:middle}
header{display:flex;align-items:center;margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid #333}
.logo{color:#74fafd;font-size:17px;font-weight:700;letter-spacing:3px}
.sub{color:#4a4a4a;font-size:10px;margin-top:1px}
.clock-box{margin-left:auto;text-align:right}
.clock-box .time{color:#3d9ea1;font-size:11px}
.lbl{color:#4a4a4a;font-size:10px;letter-spacing:1px;margin-bottom:6px}
.phase-bar{display:flex;gap:4px;margin-bottom:16px}
.ph{flex:1;padding:7px 8px;border-radius:3px;transition:all .3s}
.ph.done{background:#2e2e2e;border:1px solid #3d9ea1}
.ph.active{background:#1e3535;border:2px solid #74fafd}
.ph.pending{background:#242424;border:1px solid #333}
.ph-dot{width:7px;height:7px;border-radius:50%;display:inline-block;margin-right:4px;vertical-align:middle}
.ph.done .ph-dot{background:#4ec94e}.ph.active .ph-dot{background:#74fafd;animation:blink 1s step-end infinite}.ph.pending .ph-dot{background:#4a4a4a}
.ph-time{font-size:9px;margin-bottom:2px}.ph.done .ph-time{color:#3d9ea1}.ph.active .ph-time{color:#74fafd}.ph.pending .ph-time{color:#4a4a4a}
.ph-name{font-size:10px;font-weight:500;margin-bottom:1px}.ph.done .ph-name{color:#c8c8c8}.ph.active .ph-name{color:#74fafd}.ph.pending .ph-name{color:#4a4a4a}
.ph-cnt{font-size:9px}.ph.done .ph-cnt{color:#4ec94e}.ph.active .ph-cnt{color:#74fafd}.ph.pending .ph-cnt{color:#4a4a4a}
.btn-row{display:flex;gap:5px;margin-bottom:16px}
.ph-btn{flex:1;padding:8px 4px;background:#242424;border:1px solid #333;border-radius:3px;cursor:pointer;color:#c8c8c8;font-size:10px;font-family:"JetBrains Mono",monospace;transition:all .15s;text-align:center;line-height:1.6}
.ph-btn:hover:not(:disabled){background:#2e2e2e;border-color:#74fafd;color:#74fafd}
.ph-btn:disabled{cursor:not-allowed;color:#4a4a4a;border-color:#333}
.sentinel-box{background:#242424;border:1px solid #333;border-radius:3px;margin-bottom:12px}
.sentinel-header{display:flex;align-items:center;gap:10px;padding:8px 12px;cursor:pointer;font-size:11px;flex-wrap:wrap;user-select:none}
.sentinel-header:hover{background:#2e2e2e}
.sentinel-body{padding:10px 12px;border-top:1px solid #333;font-size:11px;color:#c8c8c8;line-height:1.8;display:none}
.sentinel-body.open{display:block;animation:fadeIn .2s ease}
.sentinel-box.alert{border-color:#f44747}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:14px}
.info-box{padding:8px 12px;background:#242424;border:1px solid #333;border-radius:3px}
.info-lbl{color:#3d9ea1;font-size:10px;margin-bottom:3px}
.info-val{color:#c8c8c8;font-size:11px;line-height:1.5}
.stock-tabs{display:flex;gap:4px;margin-bottom:8px;flex-wrap:nowrap;overflow-x:auto;-webkit-overflow-scrolling:touch}
.tab-btn{padding:5px 10px;background:#242424;border:1px solid #333;border-radius:3px;cursor:pointer;color:#4a4a4a;font-size:10px;font-family:"JetBrains Mono",monospace;transition:all .15s}
.tab-btn.active{background:#2e2e2e;border-color:#74fafd;color:#74fafd}
.card{padding:12px 14px;background:#242424;border:1px solid #333;border-left:3px solid #3d9ea1;border-radius:3px;cursor:pointer;margin-bottom:6px;animation:fadeIn .3s ease;transition:background .15s}
.card:hover{background:#2a2a2a}
.card.sel{background:#2e2e2e;border-color:#74fafd;border-left-color:#74fafd}
.card-hd{display:flex;align-items:center;gap:8px;margin-bottom:6px;flex-wrap:wrap}
.c-code{color:#74fafd;font-weight:700;font-size:13px}
.c-name{color:#c8c8c8;font-weight:500;font-size:12px}
.c-tgt{margin-left:auto;color:#ce9178;font-size:11px}
.c-chg{font-size:11px;color:#4ec94e}
.meta-row{display:flex;gap:10px;align-items:center;margin-bottom:5px;font-size:10px;flex-wrap:wrap}
.stars{color:#74fafd;letter-spacing:1px;font-size:12px}
.phil-score{color:#4a4a4a}.phil-score span{color:#74fafd}
.tag{background:#2e2e2e;border:1px solid #333;border-radius:2px;padding:1px 5px;color:#3d9ea1;font-size:9px}
.reason{color:#c8c8c8;font-size:11px;line-height:1.6;margin-bottom:4px}
.reason em,.stoploss em{color:#3d9ea1;font-style:normal}
.stoploss{color:#ce9178;font-size:10px}
.phil-quote{padding-top:6px;border-top:1px solid #333;color:#4a4a4a;font-size:10px;margin-top:6px;line-height:1.5}
.action-banner{margin-top:10px;padding:10px;background:#1a2a1a;border:1px solid #4ec94e;border-radius:3px;animation:fadeIn .2s ease}
.action-title{color:#74fafd;font-size:11px;font-weight:700;margin-bottom:8px}
.action-row{display:flex;gap:6px;flex-wrap:wrap}
.action-btn{padding:6px 12px;border-radius:3px;cursor:pointer;font-size:11px;font-family:"JetBrains Mono",monospace;font-weight:700;border:none;transition:all .15s}
.action-btn.primary{background:#74fafd;color:#1a1a1a}.action-btn.primary:hover{background:#4ec94e}
.action-btn.secondary{background:#242424;border:1px solid #3d9ea1;color:#3d9ea1}.action-btn.secondary:hover{border-color:#74fafd;color:#74fafd}
.action-btn.cancel{background:#242424;border:1px solid #4a4a4a;color:#4a4a4a}.action-btn.cancel:hover{border-color:#f44747;color:#f44747}
.action-note{font-size:10px;color:#4a4a4a;margin-top:6px;line-height:1.5}
.log-box{height:140px;overflow-y:auto;padding:10px 12px;background:#1a1a1a;border:1px solid #333;border-radius:3px;font-size:11px;line-height:1.9}
.ph.scanning .ph-dot{background:#74fafd;box-shadow:0 0 6px #74fafd;animation:blink 0.8s step-end infinite}
.ph.scanning .ph-name{color:#74fafd}
.ph.scanning .ph-cnt{color:#74fafd}
.ph.scanning .ph-time{color:#74fafd}
.ph5-result{margin-bottom:14px;padding:10px 12px;background:#1e2a1e;border:1px solid #2d4a2d;border-radius:3px;animation:fadeIn .3s}
.ph5-overall{color:#4ec94e;font-size:11px;font-weight:700;margin-bottom:6px}
.ph5-eval{margin:4px 0;font-size:10px;color:#c8c8c8;line-height:1.5}
.ph5-eval .ev-code{color:#74fafd;font-weight:700}
.ph5-eval .ev-advice{color:#3d9ea1;margin-left:8px}
.price-tag{font-size:11px;color:#74fafd;margin-left:auto;font-weight:700}
.price-chg-up{color:#4ec94e}.price-chg-dn{color:#f44747}
</style></head><body>
<header>
  <div><div class="logo" onclick="location.reload()" style="cursor:pointer">STOCK SCANNER</div><div class="sub">日本株暴騰スキャナー v2.0</div></div>
  <div class="clock-box" style="margin-left:auto;text-align:right">
  <div class="time" id="clk">--:--:-- JST</div>
  <div id="statusBadge" style="font-size:11px;font-weight:700;color:#4ec94e;margin-top:2px;transition:all .3s;letter-spacing:1px">&#9679; ONLINE</div>
</div>
</header>
<div style="display:flex;align-items:center;margin-bottom:6px">
  <span class="lbl" style="margin:0">-- フェーズ進捗 --</span>
  <span id="marketSession" style="margin-left:auto;font-size:10px;color:#4a4a4a">市場判定中...</span>
</div>
<div class="phase-bar" id="phBar"></div>
<div class="lbl">-- 手動スキャン --</div>
<div class="btn-row">
  <button class="ph-btn" id="b1" data-phase="1">&#128225;<br>Ph.1</button>
  <button class="ph-btn" id="b2" data-phase="2">&#128300;<br>Ph.2</button>
  <button class="ph-btn" id="b3" data-phase="3">&#9889;<br>Ph.3</button>
  <button class="ph-btn" id="b4" data-phase="4">&#127942;<br>Ph.4</button>
  <button class="ph-btn" id="b5" data-phase="5">&#128200;<br>Ph.5</button>
  <button class="ph-btn" id="b0" data-phase="0" style="letter-spacing:.5px">&#128640;<br>All Ph.</button>
  <button class="ph-btn" id="bReset" onclick="resetScan()" style="border-color:#4a4a4a;color:#4a4a4a">&#8635;<br>リセット</button>
</div>
<div class="sentinel-box" id="sentBox">
  <div class="sentinel-header" id="sentHdr">
    <span id="sentStatus" style="color:#74fafd;font-weight:700;min-width:150px">&#9632; SENTINEL: HOLD</span>
    <span id="sentBars" style="color:#ce9178;letter-spacing:3px">&#9617;&#9617;&#9617;&#9617;&#9617;</span>
    <span id="sentRisk" style="color:#4a4a4a">(0/5)</span>
    <span id="sentShort" style="color:#3d9ea1;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">-- 読み込み中...</span>
    <span id="sentArr" style="color:#4a4a4a;font-size:10px">&#9660;</span>
  </div>
  <div class="sentinel-body" id="sentBody"></div>
</div>
<div class="grid2">
  <div class="info-box"><div class="info-lbl">&#128202; 地合い</div><div class="info-val" id="mkt">-</div></div>
  <div class="info-box"><div class="info-lbl">&#127760; マクロ</div><div class="info-val" id="mac">-</div></div>
</div>
<div class="lbl">-- SCAN LOG --</div>
<div class="log-box" id="log"><span style="color:#3d9ea1">起動中...<span class="cursor"></span></span></div>
<div class="lbl" style="margin-top:14px">-- 本日の候補銘柄 --</div>
<div class="stock-tabs" id="stockTabs"></div>
<div id="stockList"><div style="color:#4a4a4a;font-size:11px;padding:12px">スキャン結果がありません。</div></div>
<script>
var sel=null,busy=false,sentOpen=false,curTab=4,lastState={};
var scanningPhase=0; // 実行中のフェーズ番号（0=待機中）
var scanStartTime=0;
var progressInterval=null;

// スキャン進捗の概算時間（秒）
var phaseEstimates={1:90,2:60,3:60,4:45,5:30,0:300};

function startProgressTimer(phaseId){
  scanningPhase=phaseId;
  scanStartTime=Date.now();
  var estimate=phaseEstimates[phaseId]||90;
  if(progressInterval)clearInterval(progressInterval);
  progressInterval=setInterval(function(){
    var elapsed=(Date.now()-scanStartTime)/1000;
    var pct=Math.min(95,Math.round(elapsed/estimate*100));
    var badge=document.getElementById('statusBadge');
    if(badge&&scanningPhase>0){
      badge.innerHTML='<span style="display:inline-block;width:7px;height:7px;border:2px solid #333;border-top-color:#74fafd;border-radius:50%;animation:spin .6s linear infinite;vertical-align:middle;margin-right:4px"></span>'
        +(phaseId===0?'SCANNING':('Ph.'+phaseId+' SCANNING'))+' '+pct+'%';
      badge.style.color='#74fafd';
    }
  },500);
}

function stopProgressTimer(){
  scanningPhase=0;
  if(progressInterval){clearInterval(progressInterval);progressInterval=null;}
}
var medals=['&#127941;','&#127942;','&#127943;'];
var lc=['#74fafd','#3d9ea1','#4a4a4a'];
var btnLabels={0:'&#128640;<br>All Ph.',1:'&#128225;<br>Ph.1',2:'&#128300;<br>Ph.2',3:'&#9889;<br>Ph.3',4:'&#127942;<br>Ph.4',5:'&#128200;<br>Ph.5'};

setInterval(function(){
  var now=new Date();
  var jst=new Date(now.getTime()+9*3600*1000);
  var t=jst.toISOString().substring(11,19)+' JST';
  document.getElementById('clk').textContent=t;
  // 市場セッション判定
  var h=jst.getUTCHours(),m=jst.getUTCMinutes(),dow=jst.getUTCDay();
  var ms=document.getElementById('marketSession');
  if(ms){
    var session,scolor;
    if(dow===0||dow===6){session='● 休場（土日）';scolor='#4a4a4a';}
    else if(h<8){session='● プレ（～8:00）';scolor='#4a4a4a';}
    else if(h<9){session='● 前場準備（8:00～）';scolor='#ce9178';}
    else if(h===9&&m<30||h===9&&m>=0&&h<11){session='🔴 前場LIVE';scolor='#f44747';}
    else if(h===11&&m>=30||h===12){session='● 昼休み';scolor='#4a4a4a';}
    else if(h>=13&&h<15){session='🔴 後場LIVE';scolor='#f44747';}
    else if(h>=15){session='● 後場終了';scolor='#3d9ea1';}
    else{session='● 取引中';scolor='#4ec94e';}
    ms.textContent=session;ms.style.color=scolor;
  }
},1000);

// 5秒ごとに自動でstate取得（Ph.5結果リアルタイム更新 + スケジュール実行時も反映）
setInterval(function(){
  if(!busy)fetchState();
},5000);

document.getElementById('sentHdr').addEventListener('click',function(){
  sentOpen=!sentOpen;
  document.getElementById('sentBody').classList.toggle('open',sentOpen);
  document.getElementById('sentArr').innerHTML=sentOpen?'&#9650;':'&#9660;';
});

document.querySelectorAll('[data-phase]').forEach(function(btn){
  btn.addEventListener('click',function(){run(parseInt(this.dataset.phase));});
});

async function resetScan(){
  if(busy)return;
  if(!confirm('スキャンデータをリセットしPh.1からやり直しますか？'))return;
  try{
    await fetch('/api/reset',{method:'POST'});
    sel=null;curTab=1;lastState={};destroyCharts();
    await fetchState();
  }catch(e){}
}

async function fetchState(){
  try{
    var r=await fetch('/api/state');
    var d=await r.json();
    lastState=d;
    render(d);
  }catch(e){}
}

function render(d){
  var cp=d.phase||0;
  var phases=[
    {id:1,label:'広域スキャン',time:'08:00',count:'50→20銘柄'},
    {id:2,label:'再スコア',time:'08:20',count:'20→10銘柄'},
    {id:3,label:'クロスチェック',time:'08:40',count:'10→5銘柄'},
    {id:4,label:'最索TOP3',time:'08:55',count:'TOP3確定'},
    {id:5,label:'初動確証',time:'09:05',count:'答え合わせ'}
  ];
  // 完了済みフェーズのボタンをハイライト
  ['b1','b2','b3','b4','b5'].forEach(function(bid){
    var e=document.getElementById(bid);
    if(!e)return;
    var pid=parseInt(bid.replace('b',''));
    if(scanningPhase>0&&pid===scanningPhase){
      e.style.borderColor='#74fafd';e.style.color='#74fafd'; // 実行中
    } else if(pid<=cp){
      e.style.borderColor='#4ec94e';e.style.color='#4ec94e'; // 完了
    } else {
      e.style.borderColor='#333';e.style.color='#c8c8c8'; // 未実行
    }
  });
  document.getElementById('phBar').innerHTML=phases.map(function(p){
    var cls=p.id<=cp?'done':(scanningPhase===p.id?'scanning':'pending');
    var elapsed=(Date.now()-scanStartTime)/1000;
    var estimate=phaseEstimates[p.id]||90;
    var pct=Math.min(95,Math.round(elapsed/estimate*100));
    var nameHtml=p.label+(cls==='scanning'?'<br><span style="font-size:9px;letter-spacing:1px;animation:blink 1s step-end infinite">SCANNING '+pct+'%</span>':'');
    return '<div class="ph '+cls+'"><div class="ph-time"><span class="ph-dot"></span>'+p.time+'</div><div class="ph-name">'+nameHtml+'</div><div class="ph-cnt">'+p.count+'</div></div>';
  }).join('');

  var s=d.sentinel||{action:'HOLD',reason:'データなし',risk_level:0};
  var risk=s.risk_level||0;
  var isA=s.action==='SELL_ALL';
  var filled='&#9608;'.repeat(risk);
  var empty='&#9617;'.repeat(5-risk);
  document.getElementById('sentBox').className='sentinel-box'+(isA?' alert':'');
  document.getElementById('sentStatus').style.color=isA?'#f44747':'#74fafd';
  document.getElementById('sentStatus').textContent=(isA?'⚠ SELL_ALL':'■ SENTINEL: HOLD');
  document.getElementById('sentBars').style.color=isA?'#f44747':'#ce9178';
  document.getElementById('sentBars').innerHTML=filled+empty;
  document.getElementById('sentRisk').textContent='('+risk+'/5)';
  var reason=s.reason||'';
  document.getElementById('sentShort').textContent='-- '+reason.substring(0,50)+(reason.length>50?'...':'');
  document.getElementById('sentBody').innerHTML='<strong style="color:#3d9ea1">分析:</strong><br>'+reason;

  document.getElementById('mkt').textContent=d.market_condition||'データなし';
  document.getElementById('mac').textContent=d.macro_summary||'データなし';

  // ステータスバッジ更新（スキャン中でない時）
  var badge=document.getElementById('statusBadge');
  if(badge&&scanningPhase===0){
    if(!d.server_ready&&d.boot_pct!==undefined&&d.boot_pct<100){
      badge.innerHTML='⏳ 起動中... '+d.boot_pct+'%';badge.style.color='#ce9178';
    } else if(cp>=5){badge.innerHTML='&#9679; Ph.5 DONE';badge.style.color='#4ec94e';}
    else if(cp>=4){badge.innerHTML='&#9679; Ph.4 DONE';badge.style.color='#4ec94e';}
    else if(cp>=1){badge.innerHTML='&#9679; Ph.'+cp+' DONE';badge.style.color='#3d9ea1';}
    else{badge.innerHTML='&#9679; STANDBY OK';badge.style.color='#4ec94e';}
  }

  var logs=d.log||[];
  if(logs.length>0){
    var lb=document.getElementById('log');
    lb.innerHTML=logs.map(function(l){
      var c='color:#3d9ea1';
      if(l.indexOf('ERROR')>=0)c='color:#f44747';
      else if(l.indexOf('100%')>=0||l.indexOf('起動完了')>=0)c='color:#4ec94e;font-weight:700';
      else if(l.indexOf('%]')>=0||l.indexOf('起動中')>=0)c='color:#ce9178';
      else if(l.indexOf('━')>=0)c='color:#333';
      else if(l.indexOf('✅')>=0||l.indexOf('完了')>=0)c='color:#4ec94e';
      else if(l.indexOf('TOP3')>=0)c='color:#74fafd';
      else if(l.indexOf('💡')>=0)c='color:#f0a500';
      return '<div style="'+c+'">'+l+'</div>';
    }).join('')+'<div style="color:#74fafd">&gt; <span class="cursor"></span></div>';
    lb.scrollTop=lb.scrollHeight;
  }

  var tabs=[];
  if(d.top20&&d.top20.length)tabs.push({id:1,label:'Ph.1 '+d.top20.length+'銘柄',stocks:d.top20,final:false});
  if(d.top10&&d.top10.length)tabs.push({id:2,label:'Ph.2 '+d.top10.length+'銘柄',stocks:d.top10,final:false});
  if(d.top5&&d.top5.length)tabs.push({id:3,label:'Ph.3 '+d.top5.length+'銘柄',stocks:d.top5,final:false});
  if(d.top3_final&&d.top3_final.length)tabs.push({id:4,label:'🏆 Ph.4 TOP3',stocks:d.top3_final,final:true});
  if(cp>=5||d.post_open_result)tabs.push({id:5,label:'📈 Ph.5 初動',stocks:[],final:false,isPh5:true});
  // フェーズ進行に応じて自動でタブを切り替え
  var autoTab=cp>=5&&tabs.find(function(t){return t.id===5;})?5:
              cp>=4&&tabs.find(function(t){return t.id===4;})?4:
              cp>=3&&tabs.find(function(t){return t.id===3;})?3:
              cp>=2&&tabs.find(function(t){return t.id===2;})?2:
              cp>=1&&tabs.find(function(t){return t.id===1;})?1:curTab;
  if(!tabs.find(function(t){return t.id===curTab;})){curTab=autoTab;}
  else if(autoTab>curTab){curTab=autoTab;}  // 新しいフェーズが来たら自動前進

  document.getElementById('stockTabs').innerHTML=tabs.map(function(t){
    var extra=t.isPh5?' style="border-color:#f0a500;color:#f0a500"':'';
    return '<button class="tab-btn'+(t.id===curTab?' active':'')+'" data-tab="'+t.id+'"'+extra+'>'+t.label+'</button>';
  }).join('');
  document.querySelectorAll('[data-tab]').forEach(function(btn){
    btn.addEventListener('click',function(){if(curTab!==5)stopPh5Interval();curTab=parseInt(this.dataset.tab);render(lastState);});
  });

  var cur=tabs.find(function(t){return t.id===curTab;});
  if(cur&&cur.isPh5){
    renderPh5Tab(d);
  } else if(cur){
    renderStocks(cur.stocks,cur.final,d.philosophy||{},d.realtime_prices||{});
  } else {
    document.getElementById('stockList').innerHTML='<div style="color:#4a4a4a;font-size:11px;padding:12px">スキャン結果がありません。</div>';
  }
}

function destroyCharts(){
  // 既存チャートを全て破棄
  Object.keys(Chart.instances||{}).forEach(function(k){
    try{Chart.instances[k].destroy();}catch(e){}
  });
}

var ph5PriceInterval=null;
function stopPh5Interval(){
  if(ph5PriceInterval){clearInterval(ph5PriceInterval);ph5PriceInterval=null;}
}

function renderPh5Tab(d){
  stopPh5Interval();
  destroyCharts();
  var top3=d.top3_final||[];
  var prices=d.realtime_prices||{};
  var result=d.post_open_result||{};
  var evals=result.evaluations||[];
  var evalMap={};
  evals.forEach(function(e){evalMap[e.code]=e;});

  // ヘッダー
  var html='<div style="margin-bottom:10px;padding:8px 12px;background:#1a2a1a;border:1px solid #2d4a2d;border-radius:3px">'
    +'<div style="color:#f0a500;font-size:10px;font-weight:700;margin-bottom:3px">📈 初動確認 — Ph.5 リアルタイム</div>'
    +'<div style="color:#4ec94e;font-size:10px" id="ph5Overall">'+(result.overall||'AI評価取得中...')+'</div>'
    +'</div>';

  // 各銘柄カード（チャートCanvas含む）
  var medals=['🥇','🥈','🥉'];
  var borderColors=['#74fafd','#3d9ea1','#4a4a4a'];
  top3.forEach(function(s,i){
    var code=s.code;
    var p=prices[code]||{};
    var ev=evalMap[code]||{};
    var hasPrice=(p.change_pct!==undefined);
    var up=hasPrice&&p.change_pct>=0;
    var chgColor=hasPrice?(up?'#4ec94e':'#f44747'):'#4a4a4a';
    var sign=hasPrice?(up?'+':''):'';
    var evIcon=ev.status==='HOLD'?'✅':(ev.status==='SELL'?'⚠️':'—');
    html+='<div style="margin-bottom:14px;background:#1e1e1e;border:1px solid #2a2a2a;border-left:3px solid '+borderColors[i]+';border-radius:3px;overflow:hidden">'
      // 銘柄ヘッダ
      +'<div style="padding:10px 12px 6px;display:flex;align-items:center;gap:6px">'
      +'<span>'+medals[i]+'</span>'
      +'<span style="color:#74fafd;font-weight:700;font-size:13px">《'+code+'》</span>'
      +'<span style="color:#c8c8c8;font-size:11px">'+s.name+'</span>'
      +'<span id="price_'+code+'" style="margin-left:auto;font-size:20px;font-weight:900;color:'+chgColor+'">'
        +(hasPrice?sign+p.change_pct.toFixed(2)+'%':'---')
      +'</span>'
      +'</div>'
      // 価格詳細行
      +'<div style="padding:0 12px 6px;display:flex;gap:12px;font-size:10px">'
      +'<span style="color:#4a4a4a">現在値: <span id="cur_'+code+'" style="color:#c8c8c8">'+(hasPrice?p.current:'--')+'</span>円</span>'
      +'<span style="color:#4a4a4a">前日比: <span id="yen_'+code+'" style="color:'+chgColor+'">'+(hasPrice?sign+p.change_yen.toFixed(0):'--')+'</span>円</span>'
      +'<span style="color:#4a4a4a">出来高: <span style="color:#c8c8c8">'+(hasPrice?p.volume.toLocaleString():'--')+'</span></span>'
      +'</div>'
      // AI評価
      +(ev.message?'<div style="padding:0 12px 6px;font-size:10px">'+evIcon+' <span style="color:#c8c8c8">'+ev.message+'</span>'
        +(ev.action_advice?'<span style="color:#3d9ea1"> → '+ev.action_advice+'</span>':'')+'</div>':'')
      // チャート切り替えタブ
      +'<div style="padding:0 12px 6px;display:flex;gap:6px">'
      +'<button onclick="loadChart(+this.dataset.code,this.dataset.t)" data-code="'+code+'" data-t="daily" id="btn_daily_'+code+'" style="font-family:monospace;font-size:9px;padding:3px 8px;background:#2a2a2a;border:1px solid #74fafd;color:#74fafd;border-radius:2px;cursor:pointer">1日足</button>'
      +'</div>'
      // チャートCanvas (価格+BB+MA)
      +'<div style="padding:0 12px 6px;position:relative;height:160px">'
      +'<canvas id="chart_'+code+'"></canvas>'
      +'</div>'
      // RSICanvas
      +'<div style="padding:0 12px 8px;position:relative;height:60px">'
      +'<div style="font-size:9px;color:#4a4a4a;margin-bottom:2px">RSI(14)</div>'
      +'<canvas id="rsi_'+code+'"></canvas>'
      +'</div>'
      +'</div>';
  });

  if(!top3.length) html='<div style="color:#4a4a4a;font-size:11px;padding:12px">Ph.4完了後にPh.5を実行してください</div>';
  document.getElementById('stockList').innerHTML=html;

  // 各銘柄のチャートを非同期で描画
  top3.forEach(function(s){ loadChart(s.code,'daily'); });

  // リアルタイム価格を30秒ごとに更新
  ph5PriceInterval=setInterval(function(){
    top3.forEach(function(s){
      fetch('/api/price_now/'+s.code).then(function(r){return r.json();}).then(function(p){
        if(!p||p.change_pct===undefined) return;
        var up=p.change_pct>=0;
        var c=up?'#4ec94e':'#f44747';
        var sign=up?'+':'';
        var el=document.getElementById('price_'+s.code);
        if(el){el.textContent=sign+p.change_pct.toFixed(2)+'%';el.style.color=c;}
        var ce=document.getElementById('cur_'+s.code);
        if(ce)ce.textContent=p.current;
        var ye=document.getElementById('yen_'+s.code);
        if(ye){ye.textContent=sign+p.change_yen.toFixed(0);ye.style.color=c;}
      }).catch(function(){});
    });
  },30000);
}

var chartInstances={};
function loadChart(code,type){
  // ボタン状態
  ['daily'].forEach(function(t){
    var b=document.getElementById('btn_'+t+'_'+code);
    if(b){b.style.borderColor=t===type?'#74fafd':'#333';b.style.color=t===type?'#74fafd':'#4a4a4a';}
  });

  fetch('/api/chart/'+code).then(function(r){return r.json();}).then(function(data){
    var rows=data.daily||[];
    if(!rows.length) return;

    var labels=rows.map(function(r){return r.date;});
    var closes=rows.map(function(r){return r.close;});
    var volumes=rows.map(function(r){return r.volume;});

    // 移動平均線計算
    function ma(arr,n){
      return arr.map(function(_,i){
        if(i<n-1)return null;
        var s=arr.slice(i-n+1,i+1).reduce(function(a,b){return a+b;},0);
        return Math.round(s/n*10)/10;
      });
    }
    // ボリンジャーバンド計算(20日)
    function bb(arr,n){
      var mid=ma(arr,n);
      var upper=arr.map(function(_,i){
        if(i<n-1)return null;
        var sl=arr.slice(i-n+1,i+1);
        var m=mid[i];
        var sd=Math.sqrt(sl.reduce(function(s,v){return s+(v-m)*(v-m);},0)/n);
        return Math.round((m+2*sd)*10)/10;
      });
      var lower=arr.map(function(_,i){
        if(i<n-1)return null;
        var sl=arr.slice(i-n+1,i+1);
        var m=mid[i];
        var sd=Math.sqrt(sl.reduce(function(s,v){return s+(v-m)*(v-m);},0)/n);
        return Math.round((m-2*sd)*10)/10;
      });
      return {mid:mid,upper:upper,lower:lower};
    }
    // RSI計算(14日)
    function rsi(arr,n){
      return arr.map(function(_,i){
        if(i<n)return null;
        var gains=0,losses=0;
        for(var j=i-n+1;j<=i;j++){
          var d=arr[j]-arr[j-1];
          if(d>0)gains+=d; else losses-=d;
        }
        if(losses===0)return 100;
        var rs=gains/losses;
        return Math.round(100-100/(1+rs)*10)/10;
      });
    }

    var ma5=ma(closes,5),ma25=ma(closes,25);
    var bband=bb(closes,20);
    var rsiData=rsi(closes,14);

    // 価格チャート破棄
    if(chartInstances['main_'+code]) try{chartInstances['main_'+code].destroy();}catch(e){}
    if(chartInstances['rsi_'+code]) try{chartInstances['rsi_'+code].destroy();}catch(e){}

    var ctx=document.getElementById('chart_'+code);
    var rctx=document.getElementById('rsi_'+code);
    if(!ctx||!rctx) return;

    chartInstances['main_'+code]=new Chart(ctx,{
      type:'line',
      data:{labels:labels,datasets:[
        {label:'BB上限',data:bband.upper,borderColor:'rgba(116,250,253,0.3)',borderWidth:1,pointRadius:0,fill:'+1',backgroundColor:'rgba(116,250,253,0.04)'},
        {label:'BB中心',data:bband.mid,borderColor:'rgba(116,250,253,0.5)',borderWidth:1,borderDash:[3,3],pointRadius:0,fill:false},
        {label:'BB下限',data:bband.lower,borderColor:'rgba(116,250,253,0.3)',borderWidth:1,pointRadius:0,fill:false},
        {label:'終値',data:closes,borderColor:'#c8c8c8',borderWidth:2,pointRadius:0,fill:false},
        {label:'MA5',data:ma5,borderColor:'#f0a500',borderWidth:1.5,pointRadius:0,fill:false},
        {label:'MA25',data:ma25,borderColor:'#4ec94e',borderWidth:1.5,pointRadius:0,fill:false},
      ]},
      options:{responsive:true,maintainAspectRatio:false,animation:false,
        plugins:{legend:{display:false}},
        scales:{
          x:{ticks:{color:'#4a4a4a',font:{size:9},maxTicksLimit:8},grid:{color:'#2a2a2a'}},
          y:{position:'right',ticks:{color:'#4a4a4a',font:{size:9}},grid:{color:'#2a2a2a'}}
        }
      }
    });

    chartInstances['rsi_'+code]=new Chart(rctx,{
      type:'line',
      data:{labels:labels,datasets:[
        {label:'RSI',data:rsiData,borderColor:'#ce9178',borderWidth:1.5,pointRadius:0,fill:false},
      ]},
      options:{responsive:true,maintainAspectRatio:false,animation:false,
        plugins:{legend:{display:false}},
        scales:{
          x:{display:false},
          y:{position:'right',min:0,max:100,
            ticks:{color:'#4a4a4a',font:{size:9},stepSize:50},
            grid:{color:function(ctx){
              return ctx.tick.value===70||ctx.tick.value===30?'rgba(244,71,71,0.3)':'#2a2a2a';
            }}
          }
        }
      }
    });
  }).catch(function(e){console.error('chart err',e);});
}


function renderStocks(stocks,isFinal,philosophy,prices){
  prices=prices||{};
  var html=stocks.map(function(s,i){
    var isSel=sel===s.code;
    var conf=s.confidence||0;
    var stars='★'.repeat(conf)+'☆'.repeat(5-conf);
    var phil=philosophy[s.code]||{};
    var philScore=phil.score||s.philosophy_score||'-';
    var score=s.score||s.final_score||'-';
    var chg=s.change_rate!=null?(s.change_rate>=0?'+':'')+s.change_rate+'%':'';
    var bl=isFinal?(i===0?'#74fafd':i===1?'#3d9ea1':'#4a4a4a'):'#3d9ea1';
    var prefix=isFinal&&i<3?medals[i]+' ':'#'+(i+1)+' ';

    var actionHtml='';
    if(isSel){
      actionHtml='<div class="action-banner">'
        +'<div class="action-title">&#10003; 《'+s.code+'》 '+s.name+'</div>'
        +'<div class="action-row">'
        +'<button class="action-btn primary" data-action="copy" data-code="'+s.code+'">&#128203; コードコピー</button>'
        +'<button class="action-btn secondary" data-action="kabutan" data-code="'+s.code+'">&#128202; 株たん</button>'
        +'<button class="action-btn cancel" data-action="cancel">✕</button>'
        +'</div>'
        +'<div class="action-note">▶ コードをコピーしてSBIアプリで検索、寄り付き9:00で発注。</div>'
        +'</div>';
    }
    return '<div class="card'+(isSel?' sel':'')+'" data-code="'+s.code+'" style="border-left-color:'+bl+'">'
      +'<div class="card-hd">'
      +'<span style="color:#4a4a4a;font-size:11px;min-width:24px">'+prefix+'</span>'
      +'<span class="c-code">《'+s.code+'》</span>'
      +'<span class="c-name">'+s.name+'</span>'
      +(chg?'<span class="c-chg">'+chg+'</span>':'')
      +'<span class="c-tgt">'+(s.target||s.theme||'')+'</span>'
      +'</div>'
      +'<div class="meta-row">'
      +(conf?'<span class="stars">'+stars+'</span>':'')
      +'<span class="phil-score">思想 <span>'+philScore+'/100</span></span>'
      +'<span style="color:#74fafd;font-size:10px">スコア: '+score+'</span>'
      +(s.theme?'<span class="tag">'+s.theme+'</span>':'')
      +'</div>'
      +'<div class="reason"><em>根拠: </em>'+(s.reason||s.buy_reason||'')+'</div>'
      +(s.sell_trigger?'<div class="stoploss"><em>損切り: </em>'+s.sell_trigger+'</div>':'')
      +(phil.quote?'<div class="phil-quote">「'+phil.quote+'」</div>':'')
      +actionHtml
      +'</div>';
  }).join('');
  document.getElementById('stockList').innerHTML=html||'<div style="color:#4a4a4a;font-size:11px;padding:12px">データなし</div>';
}

document.addEventListener('click',function(e){
  var ab=e.target.closest('[data-action]');
  if(ab){
    var act=ab.dataset.action;
    if(act==='copy'){
      navigator.clipboard.writeText(ab.dataset.code);
      var m=document.createElement('div');m.style.cssText='position:fixed;top:20px;right:20px;background:#4ec94e;color:#1a1a1a;padding:10px 16px;border-radius:3px;font-family:monospace;font-size:12px;font-weight:700;z-index:9999';m.textContent='《'+ab.dataset.code+'》 コピー完了 – SBIアプリで発注';document.body.appendChild(m);setTimeout(function(){m.remove();},2500);
    }else if(act==='sbi'){
      window.open('https://site3.sbisec.co.jp/ETGate/?_ControlID=WPLETsiR001Control&_PageID=WPLETsiR001Idtl20&i_stock_analog='+ab.dataset.code,'_blank');
    }else if(act==='kabutan'){
      window.open('https://kabutan.jp/stock/?code='+ab.dataset.code,'_blank');
    }else if(act==='cancel'){
      sel=null;render(lastState);
    }
    return;
  }
  var card=e.target.closest('[data-code]');
  if(card&&!e.target.closest('[data-action]')){
    var code=card.dataset.code;
    sel=sel===code?null:code;
    render(lastState);
  }
});

async function run(id){
  if(busy)return;busy=true;
  var prevPhase=lastState.phase||0;
  startProgressTimer(id===0?1:id);
  document.querySelectorAll('[data-phase]').forEach(function(b){b.disabled=true;});
  var target=document.getElementById('b'+id);
  if(target)target.innerHTML='<span class="spinner"></span>実行中';
  try{
    await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phase:id})});
    var timeout=0;
    while(timeout<300){
      await new Promise(function(r){setTimeout(r,3000);});
      timeout+=3;
      var resp=await fetch('/api/state');
      var d2=await resp.json();
      lastState=d2;render(d2);
      var np=d2.phase||0;
      // Ph.5はログで完了を検知（phaseが5になるか、ph5結果が届いたら）
      var ph5done=(id===5)&&(d2.post_open_result!=null||np>=5);
      var done=id===0?np>=5:ph5done||(np>prevPhase||np>=id);
      if(done)break;
    }
  }catch(e){}
  stopProgressTimer();busy=false;
  document.querySelectorAll('[data-phase]').forEach(function(b){
    var pid=parseInt(b.dataset.phase);
    b.innerHTML=btnLabels[pid];b.disabled=false;
  });
  await fetchState();
}

// ページ読み込み時に即座にfetchState（起動ログをすぐ表示）
(async function init(){
  // 最初は起動待ち状態を表示
  var badge=document.getElementById('statusBadge');
  if(badge){badge.innerHTML='⏳ 起動中... 0%';badge.style.color='#ce9178';}
  // サーバーが応答するまでリトライ
  var attempts=0;
  while(attempts<30){
    try{
      var r=await fetch('/api/state');
      if(r.ok){
        var d=await r.json();
        lastState=d;
        render(d);
        // 起動完了まで進捗を表示し続ける
        if(!d.server_ready){
          var bootPoll=setInterval(async function(){
            try{
              var r2=await fetch('/api/state');
              var d2=await r2.json();
              lastState=d2;render(d2);
              if(d2.server_ready){clearInterval(bootPoll);}
            }catch(e){}
          },1500);
        }
        break;
      }
    }catch(e){}
    attempts++;
    if(badge){badge.innerHTML='⏳ 起動中... '+(attempts*3)+'%';badge.style.color='#ce9178';}
    await new Promise(function(r){setTimeout(r,1000);});
  }
})();
</script>
</body></html>
"""

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Flask Routes
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.route("/")
def index():
    return HTML

@app.route("/api/state")
def api_state():
    state = load_state()
    state["log"] = LOG_BUFFER[-50:]
    # 起動完了判定
    state["server_ready"] = any("起動完了" in l or "100%" in l for l in LOG_BUFFER)
    state["boot_pct"] = 100 if state["server_ready"] else (50 if LOG_BUFFER else 0)
    return jsonify(state)

@app.route("/api/run", methods=["POST"])
def api_run():
    data = request.get_json(force=True, silent=True) or {}
    phase = data.get("phase", 0)

    def run_bg():
        if phase == 0:
            phase1_broad_scan()
            phase2_rescore()
            phase3_crosscheck()
            phase4_final_top3()
        elif phase == 1:
            phase1_broad_scan()
        elif phase == 2:
            phase2_rescore()
        elif phase == 3:
            phase3_crosscheck()
        elif phase == 4:
            phase4_final_top3()
        elif phase == 5:
            phase5_post_open()

    threading.Thread(target=run_bg, daemon=True).start()
    return jsonify({"status": "started", "phase": phase})

@app.route("/api/reset", methods=["POST"])
def api_reset():
    clear_state()
    LOG_BUFFER.clear()
    return jsonify({"status": "reset"})

@app.route("/api/logs")
def api_logs():
    return jsonify({"logs": LOG_BUFFER[-100:]})

@app.route("/api/chart/<code>")
def api_chart(code):
    """日足チャートデータ（過去60日）とリアルタイム価格を返す"""
    jst = pytz.timezone("Asia/Tokyo")
    today = datetime.now(jst)
    rows = []
    # 過去60営業日分取得
    checked = 0
    for i in range(1, 90):
        d = today - timedelta(days=i)
        if d.weekday() >= 5: continue
        date_str = d.strftime("%Y%m%d")
        try:
            res = requests.get("https://api.jquants.com/v2/equities/prices/daily",
                headers=jquants_headers(),
                params={"code": code + "0", "date": date_str}, timeout=6)
            if res.status_code == 200:
                data = res.json().get("daily_quotes", [])
                if data:
                    q = data[0]
                    op = q.get("OpenPrice") or q.get("Open") or 0
                    hi = q.get("HighPrice") or q.get("High") or 0
                    lo = q.get("LowPrice") or q.get("Low") or 0
                    cl = q.get("ClosePrice") or q.get("Close") or 0
                    vo = q.get("Volume") or 0
                    if cl > 0:
                        rows.append({"date": d.strftime("%m/%d"), "open": op,
                            "high": hi, "low": lo, "close": cl, "volume": int(vo)})
                        checked += 1
                        if checked >= 30: break
        except: pass
    rows.reverse()  # 古い順に
    return jsonify({"code": code, "daily": rows})

@app.route("/api/price_now/<code>")
def api_price_now(code):
    """現在価格のみ高速取得"""
    prices = get_realtime_prices([code])
    return jsonify(prices.get(code, {}))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Scheduler
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def run_scheduler():
    schedule.every().day.at("08:00").do(lambda: (
        phase1_broad_scan(),
        phase2_rescore(),
        phase3_crosscheck(),
        phase4_final_top3()
    ))
    schedule.every().day.at("09:05").do(phase5_post_open)
    schedule.every().day.at("09:30").do(phase5_post_open)
    schedule.every().day.at("10:00").do(phase5_post_open)

    add_log("⏰ スケジューラー起動 (08:00/09:05/09:30/10:00 JST)")
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    add_log("🚀 Stock Scanner 起動中...")
    threading.Thread(target=run_scheduler, daemon=True).start()
    add_log("🟢 [100%] 起動完了 — IDLING (スキャン待機中)")
    add_log("💡 Ph.1を押してスキャン開始 / 自動: 毎朝08:00 JST")
    app.run(host="0.0.0.0", port=PORT, debug=False)
