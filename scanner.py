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
    stock_map = {s.get("Code",""): s for s in stocks}
    candidates = []
    for q in quotes:
        code    = q.get("Code","")
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
                f"\u5fc5\u305aJSON\u306e\u307f\u3067\u56de\u7b54\u3002\u30b3\u30fc\u30c9\u30d6\u30ed\u30c3\u30af\u4e0d\u8981\uff1a"
                f"{{\"top20\":[{{\"code\":\"\u30b3\u30fc\u30c9\",\"name\":\"\u540d\u524d\",\"score\":85,\"reason\":\"\u7406\u7531\",\"theme\":\"\u30c6\u30fc\u30de\"}}],"
                f"\"market_condition\":\"\u5730\u5408\u3044\",\"macro_summary\":\"\u30de\u30af\u30ed\"}}"}])
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
    for stock in top10[:5]:
        code = stock.get("code",""); name = stock.get("name","")
        add_log(f"\U0001f9e0 [{code}] {name} \u601d\u60f3\u30b9\u30b3\u30a2...")
        doc_id = get_edinet_doc_id(code)
        if doc_id:
            text = get_edinet_text(doc_id)
            score, reason, quote = score_philosophy(code, name, text)
            philosophy_results[code] = {"score":score,"reason":reason,"quote":quote}
            add_log(f"\u2192 {score}/100")
        else:
            philosophy_results[code] = {"score":50,"reason":"EDINET\u672a\u767a\u898b","quote":""}
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

def phase5_post_open():
    add_log("\U0001f4c8 Ph.5:\u521d\u52d5\u78ba\u8a3c\u30b9\u30ad\u30e3\u30f3")
    state = load_state()
    if not state or state.get("aborted") or state.get("phase",0) < 4:
        add_log("\u26a0\ufe0f Ph.4\u30c7\u30fc\u30bf\u306a\u3057"); return
    top3         = state.get("top3_final",[])
    news         = get_news()
    twitter      = get_twitter_buzz()
    sentinel_now = sentinel_check(news, twitter)
    if sentinel_now.get("action") == "SELL_ALL":
        push_notify("\U0001f6a8 \u7dca\u6025\u5168\u6c7a\u6e08",
            f"\u30bb\u30f3\u30c1\u30cd\u30eb\u767a\u52d5\uff01\n{sentinel_now.get('reason','')}\n\u4eca\u3059\u3050\u5168\u3066\u58f2\u308c\uff01",
            priority="urgent")
        add_log("\U0001f6a8 SELL_ALL\u767a\u52d5\uff01"); return
    top3_text = "\n".join([
        f"\u300a{s['code']}\u300b{s['name']} \u76ee\u6a19:{s.get('target','')} \u6839\u62e0:{s['buy_reason']}"
        for s in top3])
    news_text = "\n".join([f"- {n.get('title','')}" for n in news[:10]])
    try:
        res = claude.messages.create(model="claude-haiku-4-5-20251001", max_tokens=800,
            messages=[{"role":"user","content":
                f"\u5bc4\u308a\u4ed8\u304d\u5f8c5\u5206\u306e\u521d\u52d5\u8a55\u4fa1\u3002\n"
                f"\u3010TOP3\u3011{top3_text}\n\u3010\u30cb\u30e5\u30fc\u30b9\u3011{news_text or '\u306a\u3057'}\n"
                f"\u5fc5\u305aJSON\u306e\u307f:{{\"evaluations\":[{{\"code\":\"\u30b3\u30fc\u30c9\","
                f"\"status\":\"HOLD\",\"message\":\"\u521d\u52d5\u30b3\u30e1\u30f3\u30c8\","
                f"\"action_advice\":\"\u30a2\u30c9\u30d0\u30a4\u30b9\"}}],\"overall\":\"\u7dcf\u8a55\"}}"}])
        t      = res.content[0].text if res.content else "{}"
        result = safe_json(t)
        evals  = result.get("evaluations",[])
        msg    = f"\U0001f4c8 \u521d\u52d5\u78ba\u8a3c\n{result.get('overall','')}\n\n"
        for e in evals:
            icon = "\u2705" if e.get("status")=="HOLD" else "\u26a0\ufe0f"
            msg += f"{icon}\u300a{e.get('code','')}\u300b{e.get('message','')}\n\u2192 {e.get('action_advice','')}\n"
        push_notify("\U0001f4c8 \u521d\u52d5\u78ba\u8a3c", msg)
        add_log(f"\u2705 Ph.5\u5b8c\u4e86: {result.get('overall','')}")
    except Exception as e: add_log(f"[ERROR] Ph.5: {e}")

HTML = """<!DOCTYPE html>
<html lang="ja"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>STOCK SCANNER</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#1a1a1a;color:#c8c8c8;font-family:"JetBrains Mono",monospace;padding:16px 20px;min-height:100vh}
::-webkit-scrollbar{width:4px}::-webkit-scrollbar-track{background:#1a1a1a}::-webkit-scrollbar-thumb{background:#4a4a4a;border-radius:2px}
@keyframes blink{0%,100%{opacity:1}50%{opacity:0}}
@keyframes spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}
@keyframes fadeIn{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:translateY(0)}}
.cursor{display:inline-block;width:7px;height:13px;background:#74fafd;animation:blink 1s step-end infinite;vertical-align:middle}
.spinner{display:inline-block;width:11px;height:11px;border:2px solid #4a4a4a;border-top-color:#74fafd;border-radius:50%;animation:spin .6s linear infinite;margin-right:5px;vertical-align:middle}
header{display:flex;align-items:center;margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid #333}
.logo{color:#74fafd;font-size:17px;font-weight:700;letter-spacing:3px}
.sub{color:#4a4a4a;font-size:10px;margin-top:1px}
.clock-box{margin-left:auto;text-align:right}
.clock-box .time{color:#3d9ea1;font-size:11px}
.clock-box .online{color:#4ec94e;font-size:10px;margin-top:2px}
.lbl{color:#4a4a4a;font-size:10px;letter-spacing:1px;margin-bottom:6px}
/* フェーズバー */
.phase-bar{display:flex;gap:4px;margin-bottom:16px}
.ph{flex:1;padding:7px 8px;border-radius:3px;transition:all .3s;cursor:default}
.ph.done{background:#2e2e2e;border:1px solid #3d9ea1}
.ph.active{background:#1e3535;border:2px solid #74fafd}
.ph.pending{background:#242424;border:1px solid #333}
.ph-dot{width:7px;height:7px;border-radius:50%;display:inline-block;margin-right:5px;vertical-align:middle}
.ph.done .ph-dot{background:#4ec94e}.ph.active .ph-dot{background:#74fafd;animation:blink 1s step-end infinite}.ph.pending .ph-dot{background:#4a4a4a}
.ph-time{font-size:9px;margin-bottom:2px}
.ph.done .ph-time{color:#3d9ea1}.ph.active .ph-time{color:#74fafd}.ph.pending .ph-time{color:#4a4a4a}
.ph-name{font-size:10px;font-weight:500;margin-bottom:1px}
.ph.done .ph-name{color:#c8c8c8}.ph.active .ph-name{color:#74fafd}.ph.pending .ph-name{color:#4a4a4a}
.ph-cnt{font-size:9px}
.ph.done .ph-cnt{color:#4ec94e}.ph.active .ph-cnt{color:#74fafd}.ph.pending .ph-cnt{color:#4a4a4a}
/* ボタン */
.btn-row{display:flex;gap:5px;margin-bottom:16px}
.ph-btn{flex:1;padding:7px 4px;background:#242424;border:1px solid #333;border-radius:3px;cursor:pointer;color:#c8c8c8;font-size:10px;font-family:"JetBrains Mono",monospace;transition:all .15s;text-align:center}
.ph-btn:hover:not(:disabled){background:#2e2e2e;border-color:#74fafd;color:#74fafd}
.ph-btn:disabled{cursor:not-allowed;color:#4a4a4a}
.all-btn{flex:1.6;padding:7px;background:#242424;border:2px solid #3d9ea1;border-radius:3px;cursor:pointer;color:#74fafd;font-size:10px;font-family:"JetBrains Mono",monospace;font-weight:700;transition:all .15s}
.all-btn:hover:not(:disabled){background:#74fafd;color:#1a1a1a}
.all-btn:disabled{cursor:not-allowed;color:#4a4a4a;border-color:#333}
/* センチネル */
.sentinel-box{background:#242424;border:1px solid #333;border-radius:3px;margin-bottom:12px}
.sentinel-header{display:flex;align-items:center;gap:10px;padding:7px 12px;cursor:pointer;font-size:11px;flex-wrap:wrap}
.sentinel-header:hover{background:#2e2e2e}
.sentinel-body{padding:8px 12px;border-top:1px solid #333;font-size:11px;color:#c8c8c8;line-height:1.7;display:none}
.sentinel-body.open{display:block;animation:fadeIn .2s ease}
.sentinel-box.alert{border-color:#f44747}
/* 地合い・マクロ */
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:14px}
.info-box{padding:8px 12px;background:#242424;border:1px solid #333;border-radius:3px}
.info-lbl{color:#3d9ea1;font-size:10px;margin-bottom:3px}
.info-val{color:#c8c8c8;font-size:11px;line-height:1.5}
/* フェーズ別銘柄タブ */
.stock-tabs{display:flex;gap:4px;margin-bottom:8px}
.tab-btn{padding:5px 10px;background:#242424;border:1px solid #333;border-radius:3px;cursor:pointer;color:#4a4a4a;font-size:10px;font-family:"JetBrains Mono",monospace;transition:all .15s}
.tab-btn.active{background:#2e2e2e;border-color:#74fafd;color:#74fafd}
.tab-btn:disabled{cursor:not-allowed}
/* 銘柄カード */
.card{padding:12px 14px;background:#242424;border:1px solid #333;border-left:3px solid #333;border-radius:3px;cursor:pointer;margin-bottom:6px;animation:fadeIn .3s ease;transition:border-color .15s}
.card:hover{border-color:#3d9ea1}
.card.sel{background:#2e2e2e;border-color:#74fafd;border-left-color:#74fafd}
.card-hd{display:flex;align-items:center;gap:8px;margin-bottom:6px}
.c-rank{color:#4a4a4a;font-size:11px;min-width:20px}
.c-code{color:#74fafd;font-weight:700;font-size:13px}
.c-name{color:#c8c8c8;font-weight:500;font-size:12px}
.c-tgt{margin-left:auto;color:#ce9178;font-size:11px;font-weight:700}
.c-chg{font-size:11px;color:#4ec94e;margin-left:6px}
.meta-row{display:flex;gap:12px;align-items:center;margin-bottom:5px;font-size:10px;flex-wrap:wrap}
.stars{color:#74fafd;letter-spacing:1px;font-size:11px}
.phil-score{color:#4a4a4a;font-size:10px}
.phil-score span{color:#74fafd}
.tag{background:#2e2e2e;border:1px solid #333;border-radius:2px;padding:1px 5px;color:#3d9ea1;font-size:9px}
.reason{color:#c8c8c8;font-size:11px;line-height:1.6;margin-bottom:4px}
.reason em{color:#3d9ea1;font-style:normal}
.stoploss{color:#ce9178;font-size:10px}
.stoploss em{color:#3d9ea1;font-style:normal}
.phil-quote{padding-top:6px;border-top:1px solid #333;color:#4a4a4a;font-size:10px;margin-top:6px;line-height:1.5}
/* アクションバナー */
.action-banner{margin-top:10px;padding:10px;background:#1e2e1e;border:1px solid #4ec94e;border-radius:3px;animation:fadeIn .2s ease}
.action-title{color:#74fafd;font-size:11px;font-weight:700;margin-bottom:8px}
.action-row{display:flex;gap:6px;flex-wrap:wrap}
.action-btn{padding:6px 12px;border-radius:3px;cursor:pointer;font-size:11px;font-family:"JetBrains Mono",monospace;font-weight:700;border:none;transition:all .15s}
.action-btn.primary{background:#74fafd;color:#1a1a1a}
.action-btn.primary:hover{background:#4ec94e}
.action-btn.secondary{background:#242424;border:1px solid #3d9ea1;color:#3d9ea1}
.action-btn.secondary:hover{border-color:#74fafd;color:#74fafd}
.action-btn.danger{background:#242424;border:1px solid #f44747;color:#f44747}
.action-note{font-size:10px;color:#4a4a4a;margin-top:6px;line-height:1.5}
/* スキャンログ */
.log-box{height:140px;overflow-y:auto;padding:10px 12px;background:#1a1a1a;border:1px solid #333;border-radius:3px;font-size:11px;line-height:1.9}
</style></head><body>
<header>
  <div><div class="logo">STOCK SCANNER</div><div class="sub">\u65e5\u672c\u682a\u66b4\u9a30\u30b9\u30ad\u30e3\u30ca\u30fc v2.0</div></div>
  <div class="clock-box"><div class="time" id="clk">--:--:-- JST</div><div class="online">&#9679; ONLINE</div></div>
</header>

<!-- スキャン進捗 -->
<div class="lbl">-- \u30b9\u30ad\u30e3\u30f3\u9032\u6357 --</div>
<div class="phase-bar" id="phBar"></div>

<!-- 手動スキャン -->
<div class="lbl">-- \u624b\u52d5\u30b9\u30ad\u30e3\u30f3--</div>
<div class="btn-row">
  <button class="ph-btn" onclick="run(1)" id="b1">&#128225;<br>Ph.1</button>
  <button class="ph-btn" onclick="run(2)" id="b2">&#128300;<br>Ph.2</button>
  <button class="ph-btn" onclick="run(3)" id="b3">&#9889;<br>Ph.3</button>
  <button class="ph-btn" onclick="run(4)" id="b4">&#127942;<br>Ph.4</button>
  <button class="ph-btn" onclick="run(5)" id="b5">&#128200;<br>Ph.5</button>
  <button class="all-btn" onclick="run(0)" id="b0">&#128640;<br>\u5168\u30d5\u30a7\u30fc\u30ba</button>
</div>

<!-- センチネル（アコーディオン） -->
<div class="sentinel-box" id="sentBox">
  <div class="sentinel-header" id="sentHeader" onclick="toggleSentinel()">
    <span id="sentStatus" style="color:#74fafd;font-weight:700;min-width:150px">&#9632; SENTINEL: HOLD</span>
    <span id="sentBars" style="color:#ce9178;letter-spacing:3px">&#9617;&#9617;&#9617;&#9617;&#9617;</span>
    <span id="sentRisk" style="color:#4a4a4a">(0/5)</span>
    <span id="sentShort" style="color:#3d9ea1;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">-- \u8aad\u307f\u8fbc\u307f\u4e2d...</span>
    <span style="color:#4a4a4a;font-size:10px" id="sentArrow">&#9660;</span>
  </div>
  <div class="sentinel-body" id="sentBody"></div>
</div>

<!-- 地合い・マクロ -->
<div class="grid2">
  <div class="info-box"><div class="info-lbl">&#128202; \u5730\u5408\u3044</div><div class="info-val" id="mkt">-</div></div>
  <div class="info-box"><div class="info-lbl">&#127760; \u30de\u30af\u30ed</div><div class="info-val" id="mac">-</div></div>
</div>

<!-- SCAN LOG -->
<div class="lbl">-- SCAN LOG --</div>
<div class="log-box" id="log"><div style="color:#3d9ea1">\u8d77\u52d5\u4e2d...<span class="cursor"></span></div></div>

<!-- 本日の候補（フェーズ別タブ） -->
<div class="lbl" style="margin-top:14px">-- \u672c\u65e5\u306e\u5019\u88dc\u9298\u67c4 --</div>
<div class="stock-tabs" id="stockTabs"></div>
<div id="stockList"><div style="color:#4a4a4a;font-size:11px;padding:12px">\u30b9\u30ad\u30e3\u30f3\u7d50\u679c\u304c\u3042\u308a\u307e\u305b\u3093\u3002</div></div>

<script>
let sel=null, busy=false, sentOpen=false, curTab=4;
const medals=['&#127941;','&#127942;','&#127943;'];
const lc=['#74fafd','#3d9ea1','#4a4a4a'];

setInterval(()=>{
  document.getElementById('clk').textContent=
    new Date().toLocaleTimeString('ja-JP',{hour:'2-digit',minute:'2-digit',second:'2-digit'})+' JST';
},1000);

function toggleSentinel(){
  sentOpen=!sentOpen;
  document.getElementById('sentBody').classList.toggle('open',sentOpen);
  document.getElementById('sentArrow').textContent=sentOpen?'&#9650;':'&#9660;';
}

async function fetchState(){
  try{
    const d=await(await fetch('/api/state')).json();
    render(d);
  }catch(e){}
}

function render(d){
  const cp=d.phase||0;
  const phases=[
    {id:1,label:'\u5e83\u57df\u30b9\u30ad\u30e3\u30f3',time:'08:00',count:'50\u219220\u9298\u67c4'},
    {id:2,label:'\u518d\u30b9\u30b3\u30a2',time:'08:20',count:'20\u219210\u9298\u67c4'},
    {id:3,label:'\u30af\u30ed\u30b9\u30c1\u30a7\u30c3\u30af',time:'08:40',count:'10\u21925\u9298\u67c4'},
    {id:4,label:'\u6700\u7d42TOP3',time:'08:55',count:'TOP3\u78ba\u5b9a'},
    {id:5,label:'\u521d\u52d5\u78ba\u8a3c',time:'09:05',count:'\u7b54\u3048\u5408\u308f\u305b'}
  ];
  document.getElementById('phBar').innerHTML=phases.map(p=>{
    const cls=p.id<cp?'done':p.id===cp?'active':'pending';
    return '<div class="ph '+cls+'"><div class="ph-time"><span class="ph-dot"></span>'+p.time+'</div>'
      +'<div class="ph-name">'+p.label+'</div>'
      +'<div class="ph-cnt">'+p.count+'</div></div>';
  }).join('');

  // センチネル
  const s=d.sentinel||{action:'HOLD',reason:'\u30c7\u30fc\u30bf\u306a\u3057',risk_level:0};
  const risk=s.risk_level||0;
  const bars='&#9608;'.repeat(risk)+'&#9617;'.repeat(5-risk);
  const isA=s.action==='SELL_ALL';
  document.getElementById('sentBox').className='sentinel-box'+(isA?' alert':'');
  document.getElementById('sentStatus').style.color=isA?'#f44747':'#74fafd';
  document.getElementById('sentStatus').textContent=(isA?'\u26a0 SELL_ALL':'\u25a0 SENTINEL: HOLD');
  document.getElementById('sentBars').style.color=isA?'#f44747':'#ce9178';
  document.getElementById('sentBars').innerHTML=bars;
  document.getElementById('sentRisk').textContent='('+risk+'/5)';
  document.getElementById('sentShort').textContent='-- '+(s.reason||'').substring(0,40)+(s.reason&&s.reason.length>40?'...':'');
  document.getElementById('sentBody').innerHTML='<strong style="color:#3d9ea1">\u5206\u6790\u7d50\u679c:</strong><br>'+
    (s.reason||'\u30c7\u30fc\u30bf\u306a\u3057')+'<br><br>'+
    '<strong style="color:#3d9ea1">\u30ea\u30b9\u30af\u30ec\u30d9\u30eb:</strong> '+
    '<span style="color:'+(risk>=4?'#f44747':risk>=3?'#ce9178':'#4ec94e')+'">'+risk+'/5</span>';

  // 地合い・マクロ
  document.getElementById('mkt').textContent=d.market_condition||'\u30c7\u30fc\u30bf\u306a\u3057';
  document.getElementById('mac').textContent=d.macro_summary||'\u30c7\u30fc\u30bf\u306a\u3057';

  // ログ
  const logs=d.log||[];
  if(logs.length>0){
    const lb=document.getElementById('log');
    lb.innerHTML=logs.map(l=>{
      let c='color:#3d9ea1';
      if(l.includes('ERROR')||l.includes('\u30a8\u30e9\u30fc'))c='color:#f44747';
      else if(l.includes('\u2705')||l.includes('\u5b8c\u4e86'))c='color:#4ec94e';
      else if(l.includes('TOP3'))c='color:#74fafd';
      else if(l.includes('SELL_ALL'))c='color:#f44747';
      return '<div style="'+c+';animation:fadeIn .2s ease">'+l+'</div>';
    }).join('')+'<div style="color:#74fafd">&gt; <span class="cursor"></span></div>';
    lb.scrollTop=lb.scrollHeight;
  }

  // フェーズタブと銘柄リスト
  const tabs=[];
  if(d.top20&&d.top20.length>0) tabs.push({id:1,label:'Ph.1 '+d.top20.length+'\u9298\u67c4',stocks:d.top20,type:'top20'});
  if(d.top10&&d.top10.length>0) tabs.push({id:2,label:'Ph.2 '+d.top10.length+'\u9298\u67c4',stocks:d.top10,type:'top10'});
  if(d.top5&&d.top5.length>0)   tabs.push({id:3,label:'Ph.3 '+d.top5.length+'\u9298\u67c4',stocks:d.top5,type:'top5'});
  if(d.top3_final&&d.top3_final.length>0) tabs.push({id:4,label:'Ph.4 TOP3',stocks:d.top3_final,type:'top3',final:true});

  if(tabs.length===0){
    document.getElementById('stockTabs').innerHTML='';
    document.getElementById('stockList').innerHTML='<div style="color:#4a4a4a;font-size:11px;padding:12px">\u30b9\u30ad\u30e3\u30f3\u7d50\u679c\u304c\u3042\u308a\u307e\u305b\u3093\u3002</div>';
    return;
  }

  // 最新のタブを自動選択
  if(!tabs.find(t=>t.id===curTab)) curTab=tabs[tabs.length-1].id;
  document.getElementById('stockTabs').innerHTML=tabs.map(t=>
    '<button class="tab-btn'+(t.id===curTab?' active':'')+'" onclick="switchTab('+t.id+')">'
    +(t.final?'&#127942; ':'')+t.label+'</button>'
  ).join('');

  const curTabData=tabs.find(t=>t.id===curTab);
  if(curTabData) renderStocks(curTabData.stocks, curTabData.final, d.philosophy||{});
}

function switchTab(id){
  curTab=id;
  fetchState();
}

function renderStocks(stocks, isFinal, philosophy){
  const html=stocks.map((s,i)=>{
    const isSel=sel===s.code;
    const stars='\u2605'.repeat(s.confidence||0)+'\u2606'.repeat(5-(s.confidence||0));
    const phil=philosophy[s.code]||{};
    const philScore=phil.score||s.philosophy_score||'-';
    const changeRate=s.change_rate!=null?'<span class="c-chg">'+( s.change_rate>=0?'+':'')+s.change_rate+'%</span>':'';
    const medal=isFinal&&i<3?medals[i]+' ':'';
    const borderColor=isFinal?(i===0?'#74fafd':i===1?'#3d9ea1':'#4a4a4a'):'#3d9ea1';

    let actionHtml='';
    if(isSel){
      actionHtml='<div class="action-banner">'+
        '<div class="action-title">&#10003; '+s.code+' '+s.name+' \u3092\u9078\u629e\u4e2d</div>'+
        '<div class="action-row">'+
          '<button class="action-btn primary" onclick="copyOrder(\''+s.code+'\',\''+s.name+'\')">&#128203; \u30b3\u30fc\u30c9\u30b3\u30d4\u30fc</button>'+
          '<button class="action-btn secondary" onclick="openSBI(\''+s.code+'\')">&#128279; SBI\u3067\u691c\u7d22</button>'+
          '<button class="action-btn secondary" onclick="openKabutan(\''+s.code+'\')">&#128202; \u682a\u305f\u3093</button>'+
          '<button class="action-btn danger" onclick="sel=null;fetchState()">\u30ad\u30e3\u30f3\u30bb\u30eb</button>'+
        '</div>'+
        '<div class="action-note">\u25b6 SBI\u8a3c\u5238\u306f\u30a2\u30d7\u30ea\u304b\u3089\u624b\u52d5\u3067\u767a\u6ce8\u3002\u5bc4\u308a\u4ed8\u304d9:00\u3001\u6210\u884c\u6210\u884c\u6210\u884c\u30af\u30ea\u30c3\u30af\u3067\u5fc5\u305a\u78ba\u8a8d\u3057\u3066\u304f\u3060\u3055\u3044\u3002</div>'+
      '</div>';
    }

    return '<div class="card'+(isSel?' sel':'')+'" data-code="'+s.code+'" style="border-left-color:'+borderColor+'">'+
      '<div class="card-hd">'+
        '<span class="c-rank">'+(isFinal?medal:'#'+(i+1)+'</span>')+'</span>'+
        '<span class="c-code">\u300a'+s.code+'\u300b</span>'+
        '<span class="c-name">'+s.name+'</span>'+
        changeRate+
        '<span class="c-tgt">'+(s.target||s.theme||'')+'</span>'+
      '</div>'+
      '<div class="meta-row">'+
        (s.confidence?'<span class="stars">'+stars+'</span>':'') +
        '<span class="phil-score">\u601d\u60f3 <span>'+philScore+'/100</span></span>'+
        '<span style="color:#74fafd;font-size:10px">\u30b9\u30b3\u30a2: '+(s.score||s.final_score||'-')+'</span>'+
        (s.theme?'<span class="tag">'+s.theme+'</span>':'')+
      '</div>'+
      '<div class="reason"><em>\u6839\u62e0: </em>'+(s.reason||s.buy_reason||'')+'</div>'+
      (s.sell_trigger?'<div class="stoploss"><em>\u640d\u5207\u308a: </em>'+s.sell_trigger+'</div>':'')+
      (phil.quote?'<div class="phil-quote">\u300c'+phil.quote+'\u300d</div>':'')+
      actionHtml+
    '</div>';
  }).join('');
  document.getElementById('stockList').innerHTML=html||'<div style="color:#4a4a4a;font-size:11px;padding:12px">\u30c7\u30fc\u30bf\u306a\u3057</div>';
}

function copyOrder(code, name){
  navigator.clipboard.writeText(code).then(()=>{
    alert('\u300a'+code+'\u300b '+name+'\n\u8a3c\u5238\u30b3\u30fc\u30c9\u3092\u30b3\u30d4\u30fc\u3057\u307e\u3057\u305f\u3002\nSBI\u8a3c\u5238\u30a2\u30d7\u30ea\u3067\u8cfc\u5165\u30d5\u30ed\u30fc\u306b\u5165\u308a\u3001\u5bc4\u308a\u4ed8\u304d\u6210\u884c\u3067\u767a\u6ce8\u3057\u3066\u304f\u3060\u3055\u3044\u3002');
  });
}

function openSBI(code){
  window.open('https://site3.sbisec.co.jp/ETGate/?_ControlID=WPLETsiR001Control&_PageID=WPLETsiR001Idtl20&_DataStoreID=DSWPLETsiR001Control&_ActionID=stockDetail&i_stock_analog='+code,'_blank');
}

function openKabutan(code){
  window.open('https://kabutan.jp/stock/?code='+code,'_blank');
}

document.addEventListener('click',function(e){
  const card=e.target.closest('[data-code]');
  if(card&&!e.target.closest('button')){
    const code=card.dataset.code;
    sel=sel===code?null:code;
    fetchState();
  }
});

async function run(id){
  if(busy)return;busy=true;
  ['b0','b1','b2','b3','b4','b5'].forEach(b=>{const e=document.getElementById(b);if(e)e.disabled=true;});
  const target=id===0?document.getElementById('b0'):document.getElementById('b'+id);
  const labels={0:'&#128640;<br>\u5b9f\u884c\u4e2d...',1:'&#128225;<br>Ph.1',2:'&#128300;<br>Ph.2',3:'&#9889;<br>Ph.3',4:'&#127942;<br>Ph.4',5:'&#128200;<br>Ph.5'};
  if(target)target.innerHTML='<span class="spinner"></span>\u5b9f\u884c\u4e2d...';
  try{
    await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phase:id})});
    await new Promise(r=>setTimeout(r,1000));
    await fetchState();
  }catch(e){}
  busy=false;
  ['b0','b1','b2','b3','b4','b5'].forEach(b=>{
    const e=document.getElementById(b);
    if(e)e.innerHTML=labels[parseInt(b.replace('b',''))];
    if(e)e.disabled=false;
  });
}

fetchState();setInterval(fetchState,5000);
</script></body></html>"""

@app.route("/")
def index():
    return HTML

@app.route("/api/state")
def api_state():
    state = load_state()
    state["log"] = LOG_BUFFER[-50:]
    return jsonify(state)

PHASE_RUNNING = False

@app.route("/api/run", methods=["POST"])
def api_run():
    global PHASE_RUNNING
    if PHASE_RUNNING:
        return jsonify({"status":"already_running"})
    data  = request.get_json()
    phase = data.get("phase", 0)
    def run_bg():
        global PHASE_RUNNING
        PHASE_RUNNING = True
        try:
            if phase==0:
                phase1_broad_scan();phase2_rescore();phase3_crosscheck()
                phase4_final_top3();phase5_post_open()
            elif phase==1: phase1_broad_scan()
            elif phase==2: phase2_rescore()
            elif phase==3: phase3_crosscheck()
            elif phase==4: phase4_final_top3()
            elif phase==5: phase5_post_open()
        finally:
            PHASE_RUNNING = False
    threading.Thread(target=run_bg, daemon=True).start()
    return jsonify({"status":"started","phase":phase})

def wait_until_8am():
    jst = pytz.timezone("Asia/Tokyo")
    now = datetime.now(jst)
    target = now.replace(hour=8, minute=0, second=0, microsecond=0)
    wait_sec = (target - now).total_seconds()
    if wait_sec > 0:
        add_log(f"\u23f3 8:00\u307e\u3067 {int(wait_sec)}\u79d2 \u5f85\u6a5f\u4e2d...")
        time.sleep(wait_sec)
    add_log("\U0001f680 Stock Scanner \u30b9\u30ad\u30e3\u30f3\u958b\u59cb\uff01")

def scheduler_loop():
    wait_until_8am()
    schedule.every().day.at("08:00").do(phase1_broad_scan)
    schedule.every().day.at("08:20").do(phase2_rescore)
    schedule.every().day.at("08:40").do(phase3_crosscheck)
    schedule.every().day.at("08:55").do(phase4_final_top3)
    schedule.every().day.at("09:05").do(phase5_post_open)
    add_log("\U0001f4c5 \u30b9\u30b1\u30b8\u30e5\u30fc\u30e9\u30fc\u8d77\u52d5: 08:00/08:20/08:40/08:55/09:05")
    phase1_broad_scan()
    while True:
        schedule.run_pending()
        time.sleep(30)

if __name__ == "__main__":
    add_log("=" * 40)
    add_log("  \U0001f4c8 Stock Scanner \u8d77\u52d5")
    add_log("=" * 40)
    threading.Thread(target=scheduler_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
