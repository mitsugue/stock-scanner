import os, time, schedule, requests, anthropic, json, zipfile, io, threading
import re
import pytz
from datetime import datetime, timedelta
from flask import Flask, jsonify, request

JQUANTS_API_KEY   = os.environ.get('JQUANTS_API_KEY', '')
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
NEWS_API_KEY      = os.environ.get('NEWS_API_KEY', '')
X_BEARER_TOKEN    = os.environ.get('X_API_BEARER_TOKEN', '')
EDINET_API_KEY    = os.environ.get('EDINET_API_KEY', '')
NTFY_CHANNEL      = 'mitsugu-stock-scanner'
PORT              = int(os.environ.get('PORT', 8080))

claude     = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
STATE_FILE = '/tmp/scan_state.json'
app        = Flask(__name__)

def save_state(data):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_state():
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

def clear_state():
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)

def jquants_headers():
    return {'x-api-key': JQUANTS_API_KEY.strip()}

def get_listed_stocks():
    res = requests.get('https://api.jquants.com/v2/equities/master', headers=jquants_headers())
    return res.json().get('data', []) if res.status_code == 200 else []

def get_daily_quotes(date_str=None):
    if not date_str:
        today = datetime.now()
        for i in range(1, 7):
            c = today - timedelta(days=i)
            if c.weekday() < 5:
                date_str = c.strftime('%Y%m%d'); break
    res = requests.get('https://api.jquants.com/v2/equities/bars/daily', headers=jquants_headers(), params={'date': date_str})
    return res.json().get('data', []) if res.status_code == 200 else []

def filter_hot_stocks(quotes, stocks):
    stock_map = {s.get('Code',''): s for s in stocks}
    candidates = []
    for q in quotes:
        code = q.get('Code','')
        open_p  = q.get('O') or q.get('Open') or 0
        close_p = q.get('C') or q.get('Close') or 0
        high_p  = q.get('H') or q.get('High') or 0
        low_p   = q.get('L') or q.get('Low') or 0
        volume  = q.get('Vo') or q.get('Volume') or 0
        if open_p <= 0 or close_p <= 0: continue
        change_rate = (close_p - open_p) / open_p * 100
        swing = (high_p - low_p) / low_p * 100 if low_p > 0 else 0
        si = stock_map.get(code, {})
        candidates.append({'code':code,'name':si.get('CoName',''),'close':close_p,'change_rate':round(change_rate,2),'volume':int(volume),'swing':round(swing,2),'market':si.get('MktNm',''),'sector':si.get('S17Nm','')})
    candidates.sort(key=lambda x: x['change_rate'], reverse=True)
    return candidates[:50]

def get_news():
    if not NEWS_API_KEY: return []
    res = requests.get('https://newsapi.org/v2/everything', params={'q':'japan stock OR nikkei OR BOJ','language':'en','sortBy':'publishedAt','pageSize':20,'apiKey':NEWS_API_KEY})
    return res.json().get('articles',[]) if res.status_code==200 else []

def get_twitter_buzz():
    if not X_BEARER_TOKEN: return []
    res = requests.get('https://api.twitter.com/2/tweets/search/recent', headers={'Authorization':f'Bearer {X_BEARER_TOKEN}'}, params={'query':'日本株 暴騰 OR 急騰 -is:retweet lang:ja','max_results':20})
    return res.json().get('data',[]) if res.status_code==200 else []

def get_edinet_doc_id(securities_code):
    try:
        today = datetime.now()
        for days_back in range(0, 365, 30):
            date = (today - timedelta(days=days_back)).strftime('%Y-%m-%d')
            res = requests.get('https://disclosure.edinet-fsa.go.jp/api/v2/documents.json', params={'date':date,'type':2,'Subscription-Key':EDINET_API_KEY}, timeout=10)
            if res.status_code != 200: continue
            for doc in res.json().get('results',[]):
                sc = doc.get('secCode','')
                if sc and sc.startswith(str(securities_code)[:4]) and doc.get('formCode') in ['030000','043000']:
                    return doc.get('docID')
    except: pass
    return None

def get_edinet_text(doc_id):
    try:
        res = requests.get(f'https://disclosure.edinet-fsa.go.jp/api/v2/documents/{doc_id}', params={'type':5,'Subscription-Key':EDINET_API_KEY}, timeout=30)
        if res.status_code == 200:
            z = zipfile.ZipFile(io.BytesIO(res.content))
            for name in z.namelist():
                if name.endswith('.txt') or 'honbun' in name.lower():
                    text = z.read(name).decode('utf-8', errors='ignore')
                    for kw in ['代表取締役','経営理念','ごあいさつ','社長メッセージ','企業理念']:
                        idx = text.find(kw)
                        if idx > 0: return text[max(0,idx-100):idx+3000]
                    return text[:5000]
    except: pass
    return None

def score_philosophy(code, company_name, text):
    if not text: return 50, '有報テキスト取得失敗', ''
    try:
        res = claude.messages.create(model='claude-opus-4-6', max_tokens=500, messages=[{'role':'user','content':f'企業の経営思想を評価。【{company_name}（{code}）】\n{text[:3000]}\n高70点以上：独自の哲学。低30点以下：定型文のみ。\nJSON形式のみ：{{"score":75,"reason":"理由","philosophy_quote":"一文"}}'}])
        t = res.content[0].text if res.content else '{}'
        d = json.loads(t[t.find('{'):t.rfind('}')+1])
        return d.get('score',50), d.get('reason',''), d.get('philosophy_quote','')
    except: return 50, 'スコアリング失敗', ''

def sentinel_check(news, twitter):
    news_text = '\n'.join([f'- {n.get("title","")}' for n in news[:20]])
    twitter_text = '\n'.join([f'- {t.get("text","")[:100]}' for t in twitter[:10]])
    try:
        res = claude.messages.create(model='claude-haiku-4-5-20251001', max_tokens=300, messages=[{'role':'user','content':f'全決済センチネル。\nニュース：{news_text or "なし"}\nX：{twitter_text or "なし"}\n地政学急変・金融危機・日銀緊急・需給崩壊のみSELL_ALL。\nJSON：{{"action":"HOLD","reason":"理由","risk_level":1}}'}])
        t = res.content[0].text if res.content else '{}'
        return json.loads(t[t.find('{'):t.rfind('}')+1])
    except: return {'action':'HOLD','reason':'判定失敗','risk_level':1}

def push_notify(title, msg, priority='default'):
    try: requests.post(f'https://ntfy.sh/{NTFY_CHANNEL}', data=msg.encode('utf-8'), headers={'Title':title,'Priority':priority})
    except: pass

LOG_BUFFER = []
def add_log(msg):
    jst = pytz.timezone('Asia/Tokyo')
    ts = datetime.now(jst).strftime('%H:%M:%S')
    line = f'[{ts}] {msg}'
    LOG_BUFFER.append(line)
    if len(LOG_BUFFER) > 200: LOG_BUFFER.pop(0)
    print(line)

def phase1_broad_scan():
    add_log('📡 フェーズ1：広域スキャン開始')
    clear_state()
    stocks = get_listed_stocks()
    quotes = get_daily_quotes()
    candidates = filter_hot_stocks(quotes, stocks)
    add_log(f'急騰候補: {len(candidates)}銘柄')
    news = get_news(); twitter = get_twitter_buzz()
    sentinel = sentinel_check(news, twitter)
    risk = sentinel.get('risk_level',1)
    add_log(f'センチネル: {sentinel.get("action")} {chr(9608)*risk+chr(9617)*(5-risk)} ({risk}/5)')
    if sentinel.get('action') == 'SELL_ALL':
        push_notify('🚨 全決済アラート', f'センチネル発動！\n{sentinel.get("reason","")}', priority='urgent')
        save_state({'sentinel':sentinel,'aborted':True,'log':LOG_BUFFER[-20:]}); return
    add_log('🤖 Claude AI分析中（50→20銘柄）...')
    cand_text = '\n'.join([f"{q['code']} {q['name']} 前日比:{q['change_rate']:+.1f}% 出来高:{q['volume']:,} 値幅:{q['swing']:.1f}% {q['sector']}" for q in candidates[:50]])
    news_text = '\n'.join([f"- {n.get('title','')}" for n in news[:15]])
    twitter_text = '\n'.join([f"- {t.get('text','')[:80]}" for t in twitter[:10]])
    try:
        res = claude.messages.create(model='claude-opus-4-6', max_tokens=1500, messages=[{'role':'user','content':f'日本株デイトレードAI。50銘柄から急騰しそうな上传20銘柄を選択。\n【株価】\n{cand_text}\n【ニュース】{news_text or "なし"}\n【X】{twitter_text or "なし"}\nJSON形式のみ：{{"top20":[{{"code":"コード","name":"銘柄名","score":85,"reason":"理由","theme":"テーマ"}}],"market_condition":"地合い","macro_summary":"マクロ"}}'}])
        t = res.content[0].text if res.content else '{}'
        result = json.loads(t[t.find('{'):t.rfind('}')+1])
        top20 = result.get('top20',[])
        add_log(f'✅ フェーズ1完了 — {len(top20)}銘柄を選出')
        save_state({'phase':1,'top20':top20,'market_condition':result.get('market_condition',''),'macro_summary':result.get('macro_summary',''),'news':[n.get('title','') for n in news[:10]],'twitter':[t.get('text','')[:100] for t in twitter[:10]],'sentinel':sentinel,'log':LOG_BUFFER[-20:]})
        push_notify('📡 フェーズ1完了', f'{len(top20)}銘柄を選出\n地合い: {result.get("market_condition","")}')
    except Exception as e: add_log(f'[ERROR] フェーズ1: {e}')

def phase2_rescore():
    add_log('🔬 フェーズ2：再スコアリング開始')
    state = load_state()
    if not state or state.get('aborted') or state.get('phase',0) < 1: add_log('⚠️ フェーズ1データなし'); return
    top20 = state.get('top20',[])
    if not top20: return
    cand_text = '\n'.join([f"{s['code']} {s['name']} score:{s['score']} テーマ:{s.get('theme','')} 理由:{s['reason']}" for s in top20])
    news_text = '\n'.join([f'- {n}' for n in state.get('news',[])])
    try:
        res = claude.messages.create(model='claude-opus-4-6', max_tokens=1200, messages=[{'role':'user','content':f'フェーズ1の20銘柄を精査し上传10銘柄に絞る。\n【銘柄】\n{cand_text}\n【地合い】{state.get("market_condition","")}\n【ニュース】{news_text or "なし"}\nJSON形式のみ：{{"top10":[{{"code":"コード","name":"銘柄名","score":90,"reason":"理由","risk":"リスク","confidence":4}}],"eliminated":"除外理由"}}'}])
        t = res.content[0].text if res.content else '{}'
        result = json.loads(t[t.find('{'):t.rfind('}')+1])
        top10 = result.get('top10',[])
        add_log(f'✅ フェーズ2完了 — {len(top10)}銘柄に絞り込み')
        state.update({'phase':2,'top10':top10,'log':LOG_BUFFER[-20:]}); save_state(state)
        push_notify('🔬 フェーズ2完了', f'20→{len(top10)}銘柄\n' + ' / '.join([f'《{s["code"]}》{s["name"]}' for s in top10[:3]]))
    except Exception as e: add_log(f'[ERROR] フェーズ2: {e}')

def phase3_crosscheck():
    add_log('⚡ フェーズ3：クロスチェック開始')
    state = load_state()
    if not state or state.get('aborted') or state.get('phase',0) < 2: add_log('⚠️ フェーズ2データなし'); return
    top10 = state.get('top10',[])
    if not top10: return
    philosophy_results = {}
    for stock in top10[:5]:
        code = stock.get('code',''); name = stock.get('name','')
        add_log(f'🧠 [{code}] {name} 思想スコア取得中...')
        doc_id = get_edinet_doc_id(code)
        if doc_id:
            text = get_edinet_text(doc_id)
            score, reason, quote = score_philosophy(code, name, text)
            philosophy_results[code] = {'score':score,'reason':reason,'quote':quote}
            add_log(f'→ {score}/100')
        else:
            philosophy_results[code] = {'score':50,'reason':'EDINET未発見','quote':''}
    cand_text = '\n'.join([f"{s['code']} {s['name']} score:{s['score']} 確信:{s.get('confidence',3)}/5 リスク:{s.get('risk','')} 理由:{s['reason']}" + (f" 思想:{philosophy_results.get(s['code'],{}).get('score','-')}/100" if s['code'] in philosophy_results else '') for s in top10])
    try:
        res = claude.messages.create(model='claude-opus-4-6', max_tokens=1200, messages=[{'role':'user','content':f'厳格なクロスチェックで上传5銘柄を選択。\n【銘柄】\n{cand_text}\n【地合い】{state.get("market_condition","")}\nJSON形式のみ：{{"top5":[{{"code":"コード","name":"銘柄名","final_score":95,"buy_reason":"根拠","sell_trigger":"損切り","target":"+10%","confidence":5}}],"crosscheck_summary":"総評"}}'}])
        t = res.content[0].text if res.content else '{}'
        result = json.loads(t[t.find('{'):t.rfind('}')+1])
        top5 = result.get('top5',[])
        add_log(f'✅ フェーズ3完了 — {len(top5)}銘柄に絞り込み')
        state.update({'phase':3,'top5':top5,'philosophy':philosophy_results,'crosscheck_summary':result.get('crosscheck_summary',''),'log':LOG_BUFFER[-20:]}); save_state(state)
        push_notify('⚡ フェーズ3完了', f'10→{len(top5)}銘柄\n{result.get("crosscheck_summary","")}')
    except Exception as e: add_log(f'[ERROR] フェーズ3: {e}')

def phase4_final_top3():
    add_log('🏆 フェーズ4：最終TOP3決定')
    state = load_state()
    if not state or state.get('aborted') or state.get('phase',0) < 3: add_log('⚠️ フェーズ3データなし'); return
    top5 = state.get('top5',[]); philosophy = state.get('philosophy',{})
    sentinel = state.get('sentinel',{}); risk = sentinel.get('risk_level',1)
    top3 = top5[:3]
    for i, s in enumerate(top3, 1):
        code = s.get('code',''); stars = '★'*s.get('confidence',3)+'☆'*(5-s.get('confidence',3))
        phil = philosophy.get(code,{})
        m1 = '🥇' if i==1 else '🥈' if i==2 else '🥉'
        msg = f'{m1} 第{i}候補\n《{code}》{s["name"]}\n確信度: {stars}\n目標: {s.get("target","")}\n根拠: {s["buy_reason"]}\n損切り: {s.get("sell_trigger","")}\n思想: {phil.get("score","-")}/100'
        push_notify(f'🏆 TOP3 #{i} 《{code}》{s["name"]}', msg, priority='high' if i==1 else 'default')
        time.sleep(1)
    medals2 = ['🥇','🥈','🥉']
    summary = '🏆 本日のTOP3確定\n' + ''.join([f'{medals2[i]}《{s["code"]}》{s["name"]} {s.get("target","")}\n' for i,s in enumerate(top3)])
    summary += f'\n地合い: {state.get("market_condition","")}\nリスク: {chr(9608)*risk+chr(9617)*(5-risk)}({risk}/5)\n\n👆 1銘柄を選んで寄り付き（9:00）で買い！'
    push_notify('🏆 本日のTOP3（あなたが選ぶ）', summary, priority='high')
    add_log('✅ フェーズ4完了 — TOP3通知送信済み')
    state.update({'phase':4,'top3_final':top3,'log':LOG_BUFFER[-20:]}); save_state(state)

def phase5_post_open():
    add_log('📈 フェーズ5：初動確証スキャン開始')
    state = load_state()
    if not state or state.get('aborted') or state.get('phase',0) < 4: add_log('⚠️ フェーズ4データなし'); return
    top3 = state.get('top3_final',[]); news = get_news(); twitter = get_twitter_buzz()
    sentinel_now = sentinel_check(news, twitter)
    if sentinel_now.get('action') == 'SELL_ALL':
        push_notify('🚨 緊急！全決済アラート', f'寄り付き後センチネル発動！\n{sentinel_now.get("reason","")}\n今すぐ全て売れ！', priority='urgent')
        add_log('🚨 SELL_ALL発動！'); return
    top3_text = '\n'.join([f'《{s["code"]}》{s["name"]} 目標:{s.get("target","")} 根拠:{s["buy_reason"]}' for s in top3])
    news_text = '\n'.join([f"- {n.get('title','')}" for n in news[:10]])
    try:
        res = claude.messages.create(model='claude-haiku-4-5-20251001', max_tokens=800, messages=[{'role':'user','content':f'寄り付き後5分の初動評価。\n【TOP3】\n{top3_text}\n【ニュース】\n{news_text or "なし"}\nJSON形式のみ：{{"evaluations":[{{"code":"コード","status":"HOLD","message":"初動コメント","action_advice":"アドバイス"}}],"overall":"総評"}}'}])
        t = res.content[0].text if res.content else '{}'
        result = json.loads(t[t.find('{'):t.rfind('}')+1])
        evals = result.get('evaluations',[])
        msg = f'📈 初動確証\n{result.get("overall","")}\n\n' + ''.join([f'{chr(9989) if e.get("status")=="HOLD" else chr(9888)}《{e.get("code","")}》{e.get("message","")}\n→ {e.get("action_advice","")}\n' for e in evals])
        push_notify('📈 初動確証スキャン', msg)
        add_log(f'✅ フェーズ5完了: {result.get("overall","")}')
    except Exception as e: add_log(f'[ERROR] フェーズ5: {e}')

HTML = open(__file__.replace('scanner.py','_html_template.html') if False else '/dev/null').read() if False else r"""
<!DOCTYPE html><html lang="ja"><head>
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
.spinner{display:inline-block;width:11px;height:11px;border:2px solid #4a4a4a;border-top-color:#74fafd;border-radius:50%;animation:spin 0.6s linear infinite;margin-right:5px;vertical-align:middle}
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
<header><div><div class="logo">STOCK SCANNER</div><div class="sub">日本株暴騰スキャナー v2.0</div></div>
<div class="clock-box"><div class="time" id="clk">--:--:-- JST</div><div class="online">● ONLINE</div></div></header>
<div class="lbl">━━ スキャン進捗 ━━</div>
<div class="phase-bar" id="phBar"></div>
<div class="manual"><div class="lbl">━━ 手動スキャン ━━</div>
<div class="btn-row">
<button class="ph-btn" onclick="run(1)" id="b1"><div class="ic">📡</div><div>Ph.1 広域</div></button>
<button class="ph-btn" onclick="run(2)" id="b2"><div class="ic">🔬</div><div>Ph.2 再評価</div></button>
<button class="ph-btn" onclick="run(3)" id="b3"><div class="ic">⚡</div><div>Ph.3 クロス</div></button>
<button class="ph-btn" onclick="run(4)" id="b4"><div class="ic">🏆</div><div>Ph.4 TOP3</div></button>
<button class="ph-btn" onclick="run(5)" id="b5"><div class="ic">📈</div><div>Ph.5 初動</div></button>
<button class="all-btn" onclick="run(0)" id="b0"><div class="ic">🚀</div><div>全フェーズ実行</div></button></div></div>
<div class="sentinel" id="sent"><span style="color:#74fafd;font-weight:700;min-width:140px">■ SENTINEL: HOLD</span><span style="color:#ce9178;letter-spacing:3px">░░░░░</span><span style="color:#4a4a4a">(0/5)</span><span style="color:#3d9ea1">— 読み込み中...</span></div>
<div class="grid2"><div class="info-box"><div class="info-lbl">📊 地合い</div><div class="info-val" id="mkt">-</div></div>
<div class="info-box"><div class="info-lbl">🌐 マクロ</div><div class="info-val" id="mac">-</div></div></div>
<div class="sec-title">━━ 本日のTOP3候補 — タップして1銘柄を選択 ━━</div>
<div id="top3"><div style="color:#4a4a4a;font-size:11px;padding:12px">スキャン結果がありません。手動スキャンを実行してください。</div></div>
<div class="lbl" style="margin-top:16px">━━ SCAN LOG ━━</div>
<div class="log-box" id="log"><div style="color:#3d9ea1">起動中...<span class="cursor"></span></div></div>
<script>
let sel=null,busy=false;
const medals=['🥇','🥈','🥉'],lc=['#74fafd','#3d9ea1','#4a4a4a'];
setInterval(()=>{document.getElementById('clk').textContent=new Date().toLocaleTimeString('ja-JP',{hour:'2-digit',minute:'2-digit',second:'2-digit'})+' JST';},1000);
async function fetchState(){try{const d=await(await fetch('/api/state')).json();render(d);}catch(e){}}
function render(d){
  const phases=[{id:1,label:'広域スキャン',time:'08:00',count:'50→20銘柄'},{id:2,label:'再スコアリング',time:'08:20',count:'20→10銘柄'},{id:3,label:'クロスチェック',time:'08:40',count:'10→5銘柄'},{id:4,label:'最終TOP3通知',time:'08:55',count:'TOP3確定'},{id:5,label:'初動確証',time:'09:05',count:'答え合わせ'}];
  const cp=d.phase||0;
  document.getElementById('phBar').innerHTML=phases.map(p=>{const done=p.id<=cp;return '<div class="ph '+(done?'done':'pending')+'"><div class="ph-time">'+(done?'█':'░')+' '+p.time+'</div><div class="ph-name">'+p.label+'</div><div class="ph-cnt">'+p.count+'</div></div>';}).join('');
  const s=d.sentinel||{action:'HOLD',reason:'データなし',risk_level:0};
  const risk=s.risk_level||0,bars='█'.repeat(risk)+'░'.repeat(5-risk),isA=s.action==='SELL_ALL';
  const se=document.getElementById('sent');se.className='sentinel'+(isA?' alert':'');
  se.innerHTML='<span style="color:'+(isA?'#f44747':'#74fafd')+';font-weight:700;min-width:140px">'+(isA?'▲ SELL_ALL':'■ SENTINEL: HOLD')+'</span><span style="color:'+(isA?'#f44747':'#ce9178')+';letter-spacing:3px">'+bars+'</span><span style="color:#4a4a4a">('+risk+'/5)</span><span style="color:#3d9ea1;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">— '+s.reason+'</span>';
  document.getElementById('mkt').textContent=d.market_condition||'-';
  document.getElementById('mac').textContent=d.macro_summary||'-';
  const top3=d.top3_final||[];
  document.getElementById('top3').innerHTML=top3.length===0?'<div style="color:#4a4a4a;font-size:11px;padding:12px">スキャン結果がありません。手動スキャンを実行してください。</div>':top3.map((stock,i)=>{
    const stars='★'.repeat(stock.confidence||0)+'☆'.repeat(5-(stock.confidence||0));
    const phil=stock.philosophy||{};const isSel=sel===stock.code;
    return '<div class="card '+(isSel?'sel':'')+' " onclick="pick(\''+stock.code+'\')" style="border-left:3px solid '+(isSel?'#74fafd':lc[i])+'">'+'<div class="card-hd"><span style="font-size:14px">'+medals[i]+'</span><span class="c-code">《'+stock.code+'》</span><span class="c-name">'+stock.name+'</span><span class="c-tgt">'+(stock.target||'')+'</span></div>'+'<div class="stars-row"><span style="color:#4a4a4a">確信度</span><span class="stars">'+stars+'</span><span class="phil">思想スコア <span>'+(phil.score||'-')+'/100</span></span></div>'+'<div class="reason"><em>根拠: </em>'+(stock.buy_reason||'')+'</div>'+'<div class="stoploss"><em>損切り: </em>'+(stock.sell_trigger||'')+'</div>'+(phil.quote?'<div class="quote">「'+phil.quote+'」</div>':'')+(isSel?'<div class="sel-banner">✓ この銘柄を選択 — 9:00 寄り付きで買い</div>':'')+'</div>';
  }).join('');
  const logs=d.log||[];
  if(logs.length>0){const lb=document.getElementById('log');lb.innerHTML=logs.map(l=>{let c='color:#3d9ea1';if(l.includes('ERROR'))c='color:#f44747';else if(l.includes('✅')||l.includes('完了'))c='color:#4ec94e';else if(l.includes('TOP3'))c='color:#74fafd';return '<div style="'+c+';animation:fadeIn .2s ease">'+l+'</div>';}).join('')+'<div style="color:#74fafd">&gt; <span class="cursor"></span></div>';lb.scrollTop=lb.scrollHeight;}
}
function pick(code){sel=sel===code?null:code;fetchState();}
async function run(id){
  if(busy)return;busy=true;
  ['b0','b1','b2','b3','b4','b5'].forEach(b=>{const e=document.getElementById(b);if(e)e.disabled=true;});
  const target=id===0?document.getElementById('b0'):document.getElementById('b'+id);
  if(target)target.innerHTML='<div class="ic"><span class="spinner"></span></div><div>'+(id===0?'実行中...':'Ph.'+id+' 実行中...')+'</div>';
  try{await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phase:id})});await new Promise(r=>setTimeout(r,1000));await fetchState();}catch(e){}
  busy=false;
  [{id:'b1',ic:'📡',lb:'Ph.1 広域'},{id:'b2',ic:'🔬',lb:'Ph.2 再評価'},{id:'b3',ic:'⚡',lb:'Ph.3 クロス'},{id:'b4',ic:'🏆',lb:'Ph.4 TOP3'},{id:'b5',ic:'📈',lb:'Ph.5 初動'}].forEach(p=>{const e=document.getElementById(p.id);if(e)e.innerHTML='<div class="ic">'+p.ic+'</div><div>'+p.lb+'</div>';});
  const ab=document.getElementById('b0');if(ab)ab.innerHTML='<div class="ic">🚀</div><div>全フェーズ実行</div>';
  ['b0','b1','b2','b3','b4','b5'].forEach(b=>{const e=document.getElementById(b);if(e)e.disabled=false;});
}
fetchState();setInterval(fetchState,5000);
</script></body></html>"""

@app.route('/')
def index():
    return HTML

@app.route('/api/state')
def api_state():
    state = load_state()
    state['log'] = LOG_BUFFER[-50:]
    return jsonify(state)

PHASE_RUNNING = False

@app.route('/api/run', methods=['POST'])
def api_run():
    global PHASE_RUNNING
    if PHASE_RUNNING:
        return jsonify({'status':'already_running'})
    data = request.get_json()
    phase = data.get('phase', 0)
    def run_bg():
        global PHASE_RUNNING
        PHASE_RUNNING = True
        try:
            if phase==0: phase1_broad_scan();phase2_rescore();phase3_crosscheck();phase4_final_top3();phase5_post_open()
            elif phase==1: phase1_broad_scan()
            elif phase==2: phase2_rescore()
            elif phase==3: phase3_crosscheck()
            elif phase==4: phase4_final_top3()
            elif phase==5: phase5_post_open()
        finally:
            PHASE_RUNNING = False
    threading.Thread(target=run_bg, daemon=True).start()
    return jsonify({'status':'started','phase':phase})

def wait_until_8am():
    jst = pytz.timezone('Asia/Tokyo')
    now = datetime.now(jst)
    target = now.replace(hour=8, minute=0, second=0, microsecond=0)
    wait_sec = (target - now).total_seconds()
    if wait_sec > 0:
        add_log(f'⏳ 8:00まで {int(wait_sec)}秒 待機中...')
        time.sleep(wait_sec)
    add_log('🚀 Stock Scanner スキャン開始！')

def scheduler_loop():
    wait_until_8am()
    schedule.every().day.at('08:00').do(phase1_broad_scan)
    schedule.every().day.at('08:20').do(phase2_rescore)
    schedule.every().day.at('08:40').do(phase3_crosscheck)
    schedule.every().day.at('08:55').do(phase4_final_top3)
    schedule.every().day.at('09:05').do(phase5_post_open)
    add_log('📅 スケジューラー起動: 08:00/08:20/08:40/08:55/09:05')
    phase1_broad_scan()
    while True:
        schedule.run_pending()
        time.sleep(30)

if __name__ == '__main__':
    add_log('=' * 40)
    add_log('  📈 Stock Scanner 起動')
    add_log('=' * 40)
    threading.Thread(target=scheduler_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=PORT)
