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
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    start = text.find("{")
    end   = text.rfind("}")
    if start == -1 or end == -1:
        return {}
    chunk = text[start:end+1]
    chunk = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", chunk)
    chunk = re.sub(r"(?<!\\)[\n\r]", " ", chunk)
    try:
        return json.loads(chunk)
    except Exception:
        chunk2 = re.sub(r"[\n\r\t]", " ", chunk)
        try:
            return json.loads(chunk2)
        except Exception as e:
            raise ValueError(f"JSON parse failed: {e}\nChunk: {chunk2[:300]}")

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
body{background:#1a1a1a;color:#c8c8c8;font-family:"JetBrains Mono",monospace;padding:18px 20px;min-height:100vh}
::-webkit-scrollbar{width:4px}::-webkit-scrollbar-track{background:#1a1a1a}::-webkit-scrollbar-thumb{background:#4a4a4a;border-radius:2px}
@keyframes blink{0%,100%{opacity:1}50%{opacity:0}}
@keyframes spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}
@keyframes fadeIn{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:translateY(0)}}
.cursor{display:inline-block;width:7px;height:13px;background:#74fafd;margin-left:2px;animation:blink 1s step-end infinite;vertical-align:middle}
.spinner{display:inline-block;width:11px;height:11px;border:2px solid #4a4a4a;border-top-color:#74fafd;border-radius:50%;animation:spin .6s linear infinite;margin-right:5px;vertical-align:middle}
header{display:flex;align-items:center;margin-bottom:20px;padding-bottom:12px;border-bottom:1px solid #333}
.logo{color:#74fafd;font-size:17px;font-weight:700;letter-spacing:3px}
.sub{color:#4a4a4a;font-size:10px;margin-top:1px}
.clock-box{margin-left:auto;text-align:right}
.clock-box .time{color:#3d9ea1;font-size:11px}
.clock-box .online{color:#4ec94e;font-size:10px;margin-top:2px}
.lbl{color:#4a4a4a;font-size:10px;letter-spacing:1px;margin-bottom:6px}
.phase-bar{display:flex;gap:4px;margin-bottom:8px}
.ph{flex:1;padding:8px;border-radius:3px}
.ph.done{background:#2e2e2e;border:1px solid #3d9ea1}
.ph.pending{background:#242424;border:1px solid #333}
.ph-time{font-size:10px;margin-bottom:3px}
.ph.done .ph-time{color:#74fafd}.ph.pending .ph-time{color:#4a4a4a}
.ph-name{font-size:11px;font-weight:500;margin-bottom:2px}
.ph.done .ph-name{color:#c8c8c8}.ph.pending .ph-name{color:#4a4a4a}
.ph-cnt{font-size:10px}
.ph.done .ph-cnt{color:#3d9ea1}.ph.pending .ph-cnt{color:#4a4a4a}
.manual{margin-bottom:20px}
.btn-row{display:flex;gap:5px}
.ph-btn{flex:1;padding:8px 4px;background:#242424;border:1px solid #333;border-radius:3px;cursor:pointer;color:#c8c8c8;font-size:10px;font-family:"JetBrains Mono",monospace;transition:all .15s}
.ph-btn:hover:not(:disabled){background:#2e2e2e;border-color:#74fafd;color:#74fafd}
.ph-btn:disabled{cursor:not-allowed;color:#4a4a4a}
.ph-btn .ic{margin-bottom:3px}
.all-btn{flex:1.6;padding:8px;background:#242424;border:2px solid #3d9ea1;border-radius:3px;cursor:pointer;color:#74fafd;font-size:10px;font-family:"JetBrains Mono",monospace;font-weight:700;transition:all .15s}
.all-btn:hover:not(:disabled){background:#74fafd;color:#1a1a1a}
.all-btn:disabled{cursor:not-allowed;color:#4a4a4a;border-color:#333}
.sentinel{display:flex;align-items:center;gap:12px;padding:7px 12px;background:#242424;border:1px solid #333;border-radius:3px;margin-bottom:12px;font-size:11px;flex-wrap:wrap}
.sentinel.alert{border-color:#f44747}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:16px}
.info-box{padding:8px 12px;background:#242424;border:1px solid #333;border-radius:3px}
.info-lbl{color:#3d9ea1;font-size:10px;margin-bottom:3px}
.info-val{color:#c8c8c8;font-size:11px;line-height:1.5}
.sec-title{color:#74fafd;font-size:11px;font-weight:700;margin-bottom:8px;letter-spacing:1px}
.card{padding:12px 14px;background:#242424;border:1px solid #333;border-radius:3px;cursor:pointer;margin-bottom:8px;animation:fadeIn .3s ease;transition:border-color .15s}
.card:hover{border-color:#3d9ea1}.card.sel{background:#2e2e2e;border-color:#74fafd}
.card-hd{display:flex;align-items:center;gap:8px;margin-bottom:7px}
.c-code{color:#74fafd;font-weight:700;font-size:13px}
.c-name{color:#c8c8c8;font-weight:500;font-size:13px}
.c-tgt{margin-left:auto;color:#ce9178;font-size:12px;font-weight:700}
.stars-row{display:flex;gap:12px;align-items:center;margin-bottom:7px;font-size:11px}
.stars{color:#74fafd;letter-spacing:1px}
.phil{margin-left:auto;color:#4a4a4a;font-size:10px}.phil span{color:#74fafd}
.reason{color:#c8c8c8;font-size:11px;line-height:1.6;margin-bottom:5px}
.reason em{color:#3d9ea1;font-style:normal}
.stoploss{color:#ce9178;font-size:11px}.stoploss em{color:#3d9ea1;font-style:normal}
.quote{padding-top:7px;border-top:1px solid #333;color:#4a4a4a;font-size:10px;margin-top:7px}
.sel-banner{margin-top:10px;padding:6px;background:#74fafd;color:#1a1a1a;font-size:11px;font-weight:700;border-radius:2px;text-align:center}
.log-box{height:155px;overflow-y:auto;padding:10px 12px;background:#1a1a1a;border:1px solid #333;border-radius:3px;font-size:11px;line-height:1.9}
</style></head><body>
<header><div><div class="logo">STOCK SCANNER</div><div class="sub">\u65e5\u672c\u682a\u66b4\u9a30\u30b9\u30ad\u30e3\u30ca\u30fc v2.0</div></div>
<div class="clock-box"><div class="time" id="clk">--:--:-- JST</div><div class="online">&#9679; ONLINE</div></div></header>
<div class="lbl">-- \u30b9\u30ad\u30e3\u30f3\u9032\u6357 --</div>
<div class="phase-bar" id="phBar"></div>
<div class="manual"><div class="lbl">-- \u624b\u52d5\u30b9\u30ad\u30e3\u30f3 --</div>
<div class="btn-row">
<button class="ph-btn" onclick="run(1)" id="b1"><div class="ic">&#128225;</div><div>Ph.1 \u5e83\u57df</div></button>
<button class="ph-btn" onclick="run(2)" id="b2"><div class="ic">&#128300;</div><div>Ph.2 \u518d\u8a55\u4fa1</div></button>
<button class="ph-btn" onclick="run(3)" id="b3"><div class="ic">&#9889;</div><div>Ph.3 \u30af\u30ed\u30b9</div></button>
<button class="ph-btn" onclick="run(4)" id="b4"><div class="ic">&#127942;</div><div>Ph.4 TOP3</div></button>
<button class="ph-btn" onclick="run(5)" id="b5"><div class="ic">&#128200;</div><div>Ph.5 \u521d\u52d5</div></button>
<button class="all-btn" onclick="run(0)" id="b0"><div class="ic">&#128640;</div><div>\u5168\u30d5\u30a7\u30fc\u30ba\u5b9f\u884c</div></button>
</div></div>
<div class="sentinel" id="sent"><span style="color:#74fafd;font-weight:700;min-width:140px">&#9632; SENTINEL: HOLD</span><span style="color:#ce9178;letter-spacing:3px">&#9617;&#9617;&#9617;&#9617;&#9617;</span><span style="color:#4a4a4a">(0/5)</span><span style="color:#3d9ea1">-- \u8aad\u307f\u8fbc\u307f\u4e2d...</span></div>
<div class="grid2">
<div class="info-box"><div class="info-lbl">&#128202; \u5730\u5408\u3044</div><div class="info-val" id="mkt">-</div></div>
<div class="info-box"><div class="info-lbl">&#127760; \u30de\u30af\u30ed</div><div class="info-val" id="mac">-</div></div></div>
<div class="sec-title">-- \u672c\u65e5\u306eTOP3\u5019\u88dc -- \u30bf\u30c3\u30d7\u3057\u30661\u9298\u67c4\u3092\u9078\u629e --</div>
<div id="top3"><div style="color:#4a4a4a;font-size:11px;padding:12px">\u30b9\u30ad\u30e3\u30f3\u7d50\u679c\u304c\u3042\u308a\u307e\u305b\u3093\u3002\u624b\u52d5\u30b9\u30ad\u30e3\u30f3\u3092\u5b9f\u884c\u3057\u3066\u304f\u3060\u3055\u3044\u3002</div></div>
<div class="lbl" style="margin-top:16px">-- SCAN LOG --</div>
<div class="log-box" id="log"><div style="color:#3d9ea1">\u8d77\u52d5\u4e2d...<span class="cursor"></span></div></div>
<script>
let sel=null,busy=false;
const medals=['\U0001f947','\U0001f948','\U0001f949'],lc=['#74fafd','#3d9ea1','#4a4a4a'];
setInterval(()=>{document.getElementById('clk').textContent=new Date().toLocaleTimeString('ja-JP',{hour:'2-digit',minute:'2-digit',second:'2-digit'})+' JST';},1000);
async function fetchState(){try{const d=await(await fetch('/api/state')).json();render(d);}catch(e){}}
function render(d){
  const phases=[{id:1,label:'\u5e83\u57df\u30b9\u30ad\u30e3\u30f3',time:'08:00',count:'50->20\u9298\u67c4'},{id:2,label:'\u518d\u30b9\u30b3\u30a2\u30ea\u30f3\u30b0',time:'08:20',count:'20->10\u9298\u67c4'},{id:3,label:'\u30af\u30ed\u30b9\u30c1\u30a7\u30c3\u30af',time:'08:40',count:'10->5\u9298\u67c4'},{id:4,label:'\u6700\u7d42TOP3\u901a\u77e5',time:'08:55',count:'TOP3\u78ba\u5b9a'},{id:5,label:'\u521d\u52d5\u78ba\u8a3c',time:'09:05',count:'\u7b54\u3048\u5408\u308f\u305b'}];
  const cp=d.phase||0;
  document.getElementById('phBar').innerHTML=phases.map(p=>{const done=p.id<=cp;return '<div class="ph '+(done?'done':'pending')+'"><div class="ph-time">'+(done?'&#9608;':'&#9617;')+' '+p.time+'</div><div class="ph-name">'+p.label+'</div><div class="ph-cnt">'+p.count+'</div></div>';}).join('');
  const s=d.sentinel||{action:'HOLD',reason:'\u30c7\u30fc\u30bf\u306a\u3057',risk_level:0};
  const risk=s.risk_level||0,bars='&#9608;'.repeat(risk)+'&#9617;'.repeat(5-risk),isA=s.action==='SELL_ALL';
  const se=document.getElementById('sent');se.className='sentinel'+(isA?' alert':'');
  se.innerHTML='<span style="color:'+(isA?'#f44747':'#74fafd')+';font-weight:700;min-width:140px">'+(isA?'&#9650; SELL_ALL':'&#9632; SENTINEL: HOLD')+'</span><span style="color:'+(isA?'#f44747':'#ce9178')+';letter-spacing:3px">'+bars+'</span><span style="color:#4a4a4a">('+risk+'/5)</span><span style="color:#3d9ea1;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">-- '+s.reason+'</span>';
  document.getElementById('mkt').textContent=d.market_condition||'-';
  document.getElementById('mac').textContent=d.macro_summary||'-';
  const top3=d.top3_final||[];
  document.getElementById('top3').innerHTML=top3.length===0?'<div style="color:#4a4a4a;font-size:11px;padding:12px">\u30b9\u30ad\u30e3\u30f3\u7d50\u679c\u304c\u3042\u308a\u307e\u305b\u3093\u3002\u624b\u52d5\u30b9\u30ad\u30e3\u30f3\u3092\u5b9f\u884c\u3057\u3066\u304f\u3060\u3055\u3044\u3002</div>':top3.map((s,i)=>{const stars='\u2605'.repeat(s.confidence||0)+'\u2606'.repeat(5-(s.confidence||0));const phil=s.philosophy||{};const isSel=sel===s.code;return '<div class="card '+(isSel?'sel':'')+'" onclick="pick(\''+s.code+'\')" style="border-left:3px solid '+(isSel?'#74fafd':lc[i])+'"><div class="card-hd"><span style="font-size:14px">'+medals[i]+'</span><span class="c-code">\u300a'+s.code+'\u300b</span><span class="c-name">'+s.name+'</span><span class="c-tgt">'+(s.target||'')+'</span></div><div class="stars-row"><span style="color:#4a4a4a">\u78ba\u4fe1\u5ea6</span><span class="stars">'+stars+'</span><span class="phil">\u601d\u60f3 <span>'+(phil.score||'-')+'/100</span></span></div><div class="reason"><em>\u6839\u62e0: </em>'+(s.buy_reason||'')+'</div><div class="stoploss"><em>\u640d\u5207\u308a: </em>'+(s.sell_trigger||'')+'</div>'+(phil.quote?'<div class="quote">\u300c'+phil.quote+'\u300d</div>':'')+(isSel?'<div class="sel-banner">&#10003; \u3053\u306e\u9298\u67c4\u3092\u9078\u629e -- 9:00 \u5bc4\u308a\u4ed8\u304d\u3067\u8cb7\u3044</div>':'')+'</div>';}).join('');
  const logs=d.log||[];
  if(logs.length>0){const lb=document.getElementById('log');lb.innerHTML=logs.map(l=>{let c='color:#3d9ea1';if(l.includes('ERROR'))c='color:#f44747';else if(l.includes('\u2705')||l.includes('\u5b8c\u4e86'))c='color:#4ec94e';else if(l.includes('TOP3'))c='color:#74fafd';return '<div style="'+c+';animation:fadeIn .2s ease">'+l+'</div>';}).join('')+'<div style="color:#74fafd">&gt; <span class="cursor"></span></div>';lb.scrollTop=lb.scrollHeight;}
}
function pick(code){sel=sel===code?null:code;fetchState();}
async function run(id){
  if(busy)return;busy=true;
  ['b0','b1','b2','b3','b4','b5'].forEach(b=>{const e=document.getElementById(b);if(e)e.disabled=true;});
  const target=id===0?document.getElementById('b0'):document.getElementById('b'+id);
  if(target)target.innerHTML='<div class="ic"><span class="spinner"></span></div><div>'+(id===0?'\u5b9f\u884c\u4e2d...':'Ph.'+id+' \u5b9f\u884c\u4e2d...')+'</div>';
  try{await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phase:id})});await new Promise(r=>setTimeout(r,1000));await fetchState();}catch(e){}
  busy=false;
  [{id:'b1',ic:'&#128225;',lb:'Ph.1 \u5e83\u57df'},{id:'b2',ic:'&#128300;',lb:'Ph.2 \u518d\u8a55\u4fa1'},{id:'b3',ic:'&#9889;',lb:'Ph.3 \u30af\u30ed\u30b9'},{id:'b4',ic:'&#127942;',lb:'Ph.4 TOP3'},{id:'b5',ic:'&#128200;',lb:'Ph.5 \u521d\u52d5'}].forEach(p=>{const e=document.getElementById(p.id);if(e)e.innerHTML='<div class="ic">'+p.ic+'</div><div>'+p.lb+'</div>';});
  const ab=document.getElementById('b0');if(ab)ab.innerHTML='<div class="ic">&#128640;</div><div>\u5168\u30d5\u30a7\u30fc\u30ba\u5b9f\u884c</div>';
  ['b0','b1','b2','b3','b4','b5'].forEach(b=>{const e=document.getElementById(b);if(e)e.disabled=false;});
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
