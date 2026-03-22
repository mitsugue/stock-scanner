# STOCK SCANNER v2.0 - 米国株AIプレデター (velvet-razor)
# US Market High-Resolution AI Scanner
import os, time, requests, anthropic, json, threading, re, math, statistics
try:
    from google import genai as google_genai
    from google.genai import types as genai_types
except Exception:
    google_genai = None
    genai_types  = None
try:
    from moomoo import OpenQuoteContext, OpenSecTradeContext, RET_OK
    MOOMOO_AVAILABLE = True
except ImportError:
    MOOMOO_AVAILABLE = False
import pytz
from datetime import datetime, timedelta
from flask import Flask, jsonify, request
from collections import deque

# ━━━ Environment Variables ━━━
FINNHUB_API_KEY   = os.environ.get("FINNHUB_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY", "")
NEWS_API_KEY      = os.environ.get("NEWS_API_KEY", "")
NTFY_CHANNEL      = os.environ.get("NTFY_CHANNEL", "mitsugu-stock-scanner")
MOOMOO_HOST       = os.environ.get("MOOMOO_HOST", "127.0.0.1")
MOOMOO_PORT       = int(os.environ.get("MOOMOO_PORT", 11111))
PORT              = int(os.environ.get("PORT", 8080))

# ━━━ DST Auto-Detection & Market Time ━━━
TZ_ET  = pytz.timezone("US/Eastern")
TZ_JST = pytz.timezone("Asia/Tokyo")

def is_dst_now():
    return bool(datetime.now(TZ_ET).dst())

def get_jst_schedule():
    dst = is_dst_now()
    offset = 13 if dst else 14
    return {
        "ph1":   f"{8+offset:02d}:30",
        "ph2":   f"{8+offset:02d}:50",
        "ph3":   f"{9+offset:02d}:10",
        "ph4":   f"{9+offset:02d}:20",
        "ph5_1": f"{9+offset:02d}:30",
        "ph5_2": f"{10+offset:02d}:00",
    }

MARKET_OPEN_ET  = (9, 30)
MARKET_CLOSE_ET = (16, 0)

# ━━━ Exit State Machine Constants ━━━
EXIT_STATE_OPEN_DISCOVERY  = "S0"
EXIT_STATE_SHAKEOUT        = "S1"
EXIT_STATE_HEALTHY_UPTREND = "S2"
EXIT_STATE_DISTRIBUTION    = "S3"
EXIT_STATE_THESIS_BROKEN   = "S4"
EXIT_STATE_PARABOLIC       = "S5"

GRADE_KEYWORDS = {
    "A": ["earnings beat","raised guidance","buyback","record revenue","dividend increase"],
    "B": ["AI","semiconductor","defense","cloud","data center","EV","GLP-1"],
    "C": ["theme","momentum","trending","sector rotation"],
    "D": ["meme","short squeeze","penny","speculative"],
}
WHALE_FIRMS = ["Goldman Sachs","JP Morgan","Morgan Stanley","Bank of America",
               "Citigroup","Wells Fargo","UBS","Deutsche Bank","Barclays"]

# ━━━ Global State ━━━
LOG_BUFFER = deque(maxlen=200)
PRICE_HISTORY = {}
CHART_CACHE = {}
SYMBOL_CACHE = None
SYMBOL_CACHE_TIME = 0
SCHEDULED_RUN = False
BACKGROUND_TASK_RUNNING = False
MOOMOO_QUOTE_CTX = None
MOOMOO_TRADE_CTX = None
_finnhub_calls = deque(maxlen=60)

def finnhub_rate_limit():
    now = time.time()
    while _finnhub_calls and _finnhub_calls[0] < now - 60:
        _finnhub_calls.popleft()
    if len(_finnhub_calls) >= 55:
        wait = 60 - (now - _finnhub_calls[0])
        if wait > 0:
            time.sleep(wait)
    _finnhub_calls.append(time.time())
# ━━━ HTML UI (US Market Version) ━━━
HTML = """<!DOCTYPE html>
<html lang="ja"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>US STOCK SCANNER v2.0</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;700&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d0d0d;color:#c8c8c8;font-family:'JetBrains Mono',monospace;font-size:12px;padding:10px;max-width:600px;margin:0 auto;-webkit-text-size-adjust:100%}
header{display:flex;align-items:center;margin-bottom:10px;padding-bottom:8px;border-bottom:1px solid #1e1e1e}
.logo{font-size:16px;font-weight:700;color:#74fafd;letter-spacing:2px;cursor:pointer}
.sub{font-size:10px;color:#4a4a4a;margin-top:2px}
.time{font-size:14px;color:#74fafd;font-weight:700;letter-spacing:1px}
.lbl{color:#4a4a4a;font-size:10px;margin:8px 0 4px;letter-spacing:2px;text-transform:uppercase}
.phase-bar{display:flex;gap:3px;margin-bottom:8px}
.ph{flex:1;height:6px;background:#1a1a1a;border-radius:1px;position:relative;overflow:hidden;transition:background .3s}
.ph.done{background:#4ec94e}.ph.active{background:#74fafd;animation:pulse .8s infinite alternate}
@keyframes pulse{from{opacity:1}to{opacity:.4}}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes fadeIn{from{opacity:0}to{opacity:1}}
.btn-row{display:flex;gap:4px;margin-bottom:8px;flex-wrap:wrap}
.ph-btn{flex:1;min-width:50px;padding:8px 2px;background:#1a1a1a;border:1px solid #2a2a2a;color:#74fafd;font-family:inherit;font-size:10px;text-align:center;cursor:pointer;border-radius:3px;line-height:1.4;transition:all .15s;-webkit-tap-highlight-color:transparent}
.ph-btn:active{background:#2a2a2a;transform:scale(.96)}
.ph-btn:disabled{opacity:.3;cursor:default}
.spinner{display:inline-block;width:10px;height:10px;border:2px solid #333;border-top-color:#74fafd;border-radius:50%;animation:spin .6s linear infinite;vertical-align:middle;margin-right:4px}
.sentinel-box{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:3px;margin-bottom:8px;font-size:10px;overflow:hidden}
.sentinel-header{display:flex;align-items:center;gap:8px;padding:8px 10px;cursor:pointer}
.sentinel-body{max-height:0;overflow:hidden;transition:max-height .3s;padding:0 10px;font-size:10px;color:#888}
.sentinel-body.open{max-height:400px;padding:8px 10px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:6px}
.info-box{background:#1a1a1a;border:1px solid #1e1e1e;border-radius:3px;padding:8px 10px}
.info-lbl{font-size:10px;color:#4a4a4a;margin-bottom:4px}
.info-val{font-size:11px;color:#c8c8c8;line-height:1.5;word-break:break-all}
.log-box{background:#0a0a0a;border:1px solid #1e1e1e;border-radius:3px;padding:8px 10px;max-height:200px;overflow-y:auto;font-size:10px;line-height:1.6;color:#888;-webkit-overflow-scrolling:touch}
.cursor{display:inline-block;width:6px;height:12px;background:#74fafd;animation:pulse 1s infinite;vertical-align:text-bottom;margin-left:2px}
.stock-tabs{display:flex;gap:2px;margin-bottom:6px;flex-wrap:wrap}
.stock-tabs button{padding:5px 10px;background:#1a1a1a;border:1px solid #2a2a2a;color:#888;font-family:inherit;font-size:10px;cursor:pointer;border-radius:3px;transition:all .15s}
.stock-tabs button.on{background:#2a2a2a;color:#74fafd;border-color:#74fafd}
.card{background:#1a1a1a;border:1px solid #2a2a2a;border-left:3px solid #3d9ea1;border-radius:3px;padding:10px;margin-bottom:6px;cursor:pointer;transition:all .15s}
.card.sel{border-color:#74fafd;background:#1e2a2e}
.card-hd{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.c-code{color:#74fafd;font-weight:700;font-size:12px}
.c-name{color:#c8c8c8;font-size:11px}
.c-chg{font-size:11px;font-weight:700;margin-left:auto}
.c-tgt{color:#4a4a4a;font-size:10px}
.meta-row{display:flex;align-items:center;gap:8px;margin-top:4px;flex-wrap:wrap}
.stars{color:#f0a500;font-size:10px}
.tag{background:#1e2a1e;color:#4ec94e;font-size:9px;padding:1px 6px;border-radius:2px}
.reason,.stoploss{font-size:10px;margin-top:4px;line-height:1.5;color:#888}
.stoploss{color:#f44747}
.reason em,.stoploss em{font-style:normal;color:#4a4a4a}
.action-banner{margin-top:8px;padding:8px;background:#0d1a1e;border:1px solid #74fafd;border-radius:3px}
.action-title{color:#74fafd;font-size:11px;font-weight:700;margin-bottom:6px}
.action-row{display:flex;gap:4px;flex-wrap:wrap}
.action-btn{padding:6px 12px;border:1px solid #2a2a2a;border-radius:3px;font-family:inherit;font-size:10px;cursor:pointer;transition:all .15s}
.action-btn.primary{background:#1e3a3e;color:#74fafd;border-color:#74fafd}
.action-btn.secondary{background:#1a1a1a;color:#c8c8c8}
.action-btn.cancel{background:#1a1a1a;color:#888;border-color:#444}
.action-note{font-size:9px;color:#4a4a4a;margin-top:6px}
.ob-panel{margin-top:6px;padding:6px;background:#0a0a0a;border:1px solid #1e1e1e;border-radius:3px;font-size:9px}
.ob-title{color:#4a4a4a;font-size:9px;margin-bottom:4px;letter-spacing:1px}
.ob-row{display:flex;gap:4px;margin:1px 0}
.ob-bid{color:#4ec94e}.ob-ask{color:#f44747}.ob-vol{color:#4a4a4a;margin-left:auto}
.margin-alert{background:#2a1a1a;border:1px solid #f44747;border-radius:3px;padding:6px 8px;margin-top:6px;font-size:10px;color:#f44747}
.ph5-result{margin-bottom:14px;padding:10px 12px;background:#1e2a1e;border:1px solid #2d4a2d;border-radius:3px;animation:fadeIn .3s}
.ph5-overall{color:#4ec94e;font-size:11px;font-weight:700;margin-bottom:6px}
.ph5-eval{margin:4px 0;font-size:10px;color:#c8c8c8;line-height:1.5}
.ph5-eval .ev-code{color:#74fafd;font-weight:700}
.ph5-eval .ev-advice{color:#3d9ea1;margin-left:8px}
.price-tag{font-size:11px;color:#74fafd;margin-left:auto;font-weight:700}
.price-chg-up{color:#4ec94e}.price-chg-dn{color:#f44747}
</style></head><body>
<header>
  <div><div class="logo" id="logoBtn" onclick="location.reload()">US STOCK SCANNER</div><div class="sub">velvet-razor v2.0 — US Market AI Predator</div></div>
  <div style="margin-left:auto;text-align:right">
  <div class="time" id="clk">--:--:-- ET</div>
  <div id="statusBadge" style="font-size:11px;font-weight:700;color:#4ec94e;margin-top:2px">&#9679; ONLINE</div>
</div>
</header>
<div style="display:flex;align-items:center;margin-bottom:6px">
  <span class="lbl" style="margin:0">-- PHASE PROGRESS --</span>
  <span id="marketSession" style="margin-left:auto;font-size:10px;color:#4a4a4a">Detecting...</span>
</div>
<div class="phase-bar" id="phBar"></div>
<div class="lbl">-- MANUAL SCAN --</div>
<div class="btn-row">
  <button class="ph-btn" id="b1" data-phase="1">&#128225;<br>Ph.1</button>
  <button class="ph-btn" id="b2" data-phase="2">&#128300;<br>Ph.2</button>
  <button class="ph-btn" id="b3" data-phase="3">&#9889;<br>Ph.3</button>
  <button class="ph-btn" id="b4" data-phase="4">&#127942;<br>Ph.4</button>
  <button class="ph-btn" id="b5" data-phase="5">&#128200;<br>Ph.5</button>
  <button class="ph-btn" id="b0" data-phase="0">&#128640;<br>All Ph.</button>
  <button class="ph-btn" id="bReset" onclick="resetScan()" style="border-color:#666;color:#888">&#8635;<br>Reset</button>
</div>
<div class="sentinel-box" id="sentBox">
  <div class="sentinel-header" id="sentHdr">
    <span id="sentStatus" style="color:#74fafd;font-weight:700;min-width:150px">&#9632; SENTINEL: HOLD</span>
    <span id="sentBars" style="color:#ce9178;letter-spacing:3px">&#9617;&#9617;&#9617;&#9617;&#9617;</span>
    <span id="sentRisk" style="color:#4a4a4a">(0/5)</span>
    <span id="sentShort" style="color:#3d9ea1;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">-- Loading...</span>
    <span id="sentArr" style="color:#4a4a4a;font-size:10px">&#9660;</span>
  </div>
  <div class="sentinel-body" id="sentBody"></div>
</div>
<div class="grid2">
  <div class="info-box"><div class="info-lbl">&#128202; Market</div><div class="info-val" id="mkt">-</div></div>
  <div class="info-box"><div class="info-lbl">&#127760; Macro</div><div class="info-val" id="mac">-</div></div>
</div>
<div class="grid2" style="margin-top:6px">
  <div class="info-box"><div class="info-lbl">&#128200; VIX / S&amp;P500</div><div class="info-val" id="finnhubVal">VIX: -- &nbsp; S&P500: --</div></div>
  <div class="info-box" id="finnhubAlertBox" style="display:none"><div class="info-lbl" style="color:#f44747">&#9888; Macro Alert</div><div class="info-val" id="finnhubAlert" style="color:#f44747;font-size:11px">-</div></div>
</div>
<div class="lbl">-- SCAN LOG --</div>
<div class="log-box" id="log"><span style="color:#3d9ea1">Initializing...<span class="cursor"></span></span></div>
<div class="lbl" style="margin-top:14px">-- TODAY'S CANDIDATES --</div>
<div class="stock-tabs" id="stockTabs"></div>
<div id="stockList"><div style="color:#4a4a4a;font-size:11px;padding:12px">No scan results yet.</div></div>
<script>
var sel=null,busy=false,sentOpen=false,curTab=4,lastState={},userChoseTab=false;
var scanningPhase=0,scanStartTime=0,progressInterval=null;
var phaseEstimates={1:45,2:40,3:50,4:30,5:30,0:200};
var phaseActions={1:['Fetching stocks','Sentinel check','AI analyzing','Narrowing'],2:['Re-scoring','AI analyzing','Ranking'],3:['Cross-checking','Rating check','Gemini grounding'],4:['Order book','Selecting TOP3','Verify'],5:['Prices','Momentum','Order book']};
var btnLabels={0:'&#128640;<br>All Ph.',1:'&#128225;<br>Ph.1',2:'&#128300;<br>Ph.2',3:'&#9889;<br>Ph.3',4:'&#127942;<br>Ph.4',5:'&#128200;<br>Ph.5'};
var medals=['&#127941;','&#127942;','&#127943;'];

function startProgressTimer(p){scanningPhase=p;scanStartTime=Date.now();if(progressInterval)clearInterval(progressInterval);progressInterval=setInterval(function(){var e=(Date.now()-scanStartTime)/1000,d=scanningPhase>0?scanningPhase:p,est=phaseEstimates[d]||90,pct=Math.min(100,Math.round(e/est*100));if(window._pcm&&window._pcm[d])pct=100;var b=document.getElementById('statusBadge');if(b&&scanningPhase>0){var a=phaseActions[d]||['Processing'],ai=Math.min(Math.floor(pct/100*a.length),a.length-1);b.innerHTML='<span style="display:inline-block;width:7px;height:7px;border:2px solid #333;border-top-color:#74fafd;border-radius:50%;animation:spin .6s linear infinite;vertical-align:middle;margin-right:4px"></span>Ph.'+d+' '+a[ai]+' '+pct+'%';b.style.color='#74fafd';}},500);}
function stopProgressTimer(){scanningPhase=0;if(progressInterval){clearInterval(progressInterval);progressInterval=null;}}

setInterval(function(){
  var now=new Date();
  var etStr=now.toLocaleString('en-US',{timeZone:'America/New_York',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
  var jstStr=now.toLocaleString('ja-JP',{timeZone:'Asia/Tokyo',hour:'2-digit',minute:'2-digit',hour12:false});
  var etDate=new Date(now.toLocaleString('en-US',{timeZone:'America/New_York'}));
  var days=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  document.getElementById('clk').textContent=days[etDate.getDay()]+' '+etStr+' ET ('+jstStr+' JST)';
  var h=etDate.getHours(),m=etDate.getMinutes(),dow=etDate.getDay();
  var ms=document.getElementById('marketSession');
  if(ms){var s,c;if(dow===0||dow===6){s='Closed (Weekend)';c='#4a4a4a';}else if(h<4){s='Closed';c='#4a4a4a';}else if(h<9||(h===9&&m<30)){s='Pre-Market';c='#ce9178';}else if(h<16){s='MARKET LIVE';c='#f44747';}else if(h<20){s='After Hours';c='#3d9ea1';}else{s='Closed';c='#4a4a4a';}ms.textContent=s;ms.style.color=c;}
},1000);

setInterval(function(){fetchState();},3000);

document.getElementById('sentHdr').addEventListener('click',function(){sentOpen=!sentOpen;document.getElementById('sentBody').classList.toggle('open',sentOpen);document.getElementById('sentArr').innerHTML=sentOpen?'&#9650;':'&#9660;';});
document.querySelectorAll('[data-phase]').forEach(function(btn){btn.addEventListener('click',function(){run(parseInt(this.dataset.phase));});});
document.getElementById('stockTabs').addEventListener('click',function(e){var b=e.target;while(b&&b!==this&&!b.dataset.tab)b=b.parentNode;if(!b||!b.dataset.tab)return;curTab=parseInt(b.dataset.tab);userChoseTab=true;render(lastState);});

async function resetScan(){if(busy)return;if(!confirm('Reset scan data?'))return;try{await fetch('/api/reset',{method:'POST'});sel=null;curTab=1;lastState={};userChoseTab=false;await fetchState();}catch(e){}}
async function fetchState(){try{var r=await fetch('/api/state?t='+Date.now());var d=await r.json();lastState=d;render(d);if(d.scanning&&!busy){busy=true;if(scanningPhase===0)startProgressTimer((d.phase||0)+1);}}catch(e){}}

function render(d){if(!d)return;
  var pb=document.getElementById('phBar');if(pb){var h='';for(var i=1;i<=5;i++){var c='ph';if(d.phase>=i)c+=' done';else if(d.scanning&&d.phase===i-1)c+=' active';h+='<div class="'+c+'"></div>';}pb.innerHTML=h;}
  var me=document.getElementById('mkt');if(me)me.textContent=d.market_condition||'-';
  var ma=document.getElementById('mac');if(ma)ma.textContent=d.macro_summary||'-';
  var fm=d.finnhub_macro||{},fv=document.getElementById('finnhubVal');
  if(fv)fv.innerHTML='VIX: '+(fm.vix||'--')+' &nbsp; S&P500: '+(fm.sp500_change!=null?(fm.sp500_change>=0?'+':'')+fm.sp500_change+'%':'--');
  var fab=document.getElementById('finnhubAlertBox'),fa=document.getElementById('finnhubAlert');
  if(fm.fear_level&&fm.fear_level!=='NORMAL'&&fm.fear_level!=='CALM'){if(fab)fab.style.display='';if(fa)fa.textContent=fm.fear_level+' (VIX: '+(fm.vix_spike_pct||0).toFixed(1)+'%)';}else{if(fab)fab.style.display='none';}
  var sent=d.sentinel||{},ss=document.getElementById('sentStatus');
  if(ss){if(sent.action==='SELL_ALL'){ss.innerHTML='SENTINEL: SELL ALL';ss.style.color='#f44747';}else{ss.innerHTML='SENTINEL: HOLD';ss.style.color='#74fafd';}}
  var sb=document.getElementById('sentBody');if(sb&&sent.reason)sb.innerHTML='<div style="color:#f44747">'+sent.reason+'</div>';
  var le=document.getElementById('log');
  if(le&&d.log&&d.log.length){le.innerHTML=d.log.map(function(l){var c='#888';if(l.indexOf('ERROR')>=0)c='#f44747';else if(l.indexOf('complete')>=0||l.indexOf('HOLD')>=0)c='#4ec94e';else if(l.indexOf('Ph.')>=0)c='#74fafd';return '<div style="color:'+c+'">'+l+'</div>';}).join('');le.scrollTop=le.scrollHeight;}
  var tabs=document.getElementById('stockTabs');
  if(tabs){var hd=[d.top20&&d.top20.length?1:0,d.top10&&d.top10.length?2:0,d.top5&&d.top5.length?3:0,d.top3_final&&d.top3_final.length?4:0,d.post_open_result?5:0].filter(function(x){return x>0;});if(hd.length){tabs.innerHTML=hd.map(function(t){var l=['','Ph.1(20)','Ph.2(10)','Ph.3(5)','TOP3','Ph.5'][t];return '<button data-tab="'+t+'" class="'+(curTab===t?'on':'')+'">'+l+'</button>';}).join('');}if(!userChoseTab&&hd.length)curTab=hd[hd.length-1];}
  var stocks=[];var isFinal=false;
  if(curTab===1)stocks=d.top20||[];else if(curTab===2)stocks=d.top10||[];else if(curTab===3)stocks=d.top5||[];else if(curTab===4){stocks=d.top3_final||[];isFinal=true;}else if(curTab===5){renderPh5(d);return;}
  renderStocks(stocks,isFinal,d.realtime_prices||{});
  if(!d.scanning){var bg=document.getElementById('statusBadge');if(bg&&scanningPhase===0){bg.innerHTML='&#9679; ONLINE';bg.style.color='#4ec94e';}}
}

function renderPh5(d){var el=document.getElementById('stockList');var por=d.post_open_result||{};var evals=por.evaluations||[];var pr=d.realtime_prices||{};var h='';
  if(por.overall){h+='<div class="ph5-result"><div class="ph5-overall">'+por.overall+'</div>';evals.forEach(function(ev){var ic=ev.status==='HOLD'?'OK':'ALERT';var p=pr[ev.code]||{};var pt=p.change_pct!=null?'<span class="price-tag '+(p.change_pct>=0?'price-chg-up':'price-chg-dn')+'">'+(p.change_pct>=0?'+':'')+p.change_pct+'%</span>':'';h+='<div class="ph5-eval"><span class="ev-code">'+ic+' '+ev.code+'</span>'+pt+'<br>'+ev.message+'<span class="ev-advice"> -> '+ev.action_advice+'</span></div>';});h+='</div>';}
  if(d.margin_alert)h+='<div class="margin-alert">'+d.margin_alert+'</div>';
  var obs=d.order_book||{};Object.keys(obs).forEach(function(sym){var ob=obs[sym];h+='<div class="ob-panel"><div class="ob-title">ORDER BOOK: '+sym+'</div>';h+='<div style="color:#4a4a4a">AR:'+(ob.absorption_ratio!=null?ob.absorption_ratio.toFixed(2):'-')+' Vacuum:'+(ob.downside_efficiency!=null?ob.downside_efficiency.toFixed(2):'-')+'</div>';if(ob.bids)ob.bids.slice(0,5).forEach(function(b){h+='<div class="ob-row"><span class="ob-bid">BID $'+b[0]+'</span><span class="ob-vol">x'+b[1]+'</span></div>';});if(ob.asks)ob.asks.slice(0,5).forEach(function(a){h+='<div class="ob-row"><span class="ob-ask">ASK $'+a[0]+'</span><span class="ob-vol">x'+a[1]+'</span></div>';});h+='</div>';});
  el.innerHTML=h||'<div style="color:#4a4a4a;font-size:11px;padding:12px">Ph.5 not yet executed.</div>';}

function renderStocks(stocks,isFinal,prices){prices=prices||{};
  var h=stocks.map(function(s,i){var isSel=sel===s.symbol;var conf=s.confidence||0;var stars='\u2605'.repeat(conf)+'\u2606'.repeat(5-conf);var score=s.score||s.final_score||'-';var chg=s.change_pct!=null?(s.change_pct>=0?'+':'')+Number(s.change_pct).toFixed(2)+'%':'';var bl=isFinal?(i===0?'#74fafd':i===1?'#3d9ea1':'#4a4a4a'):'#3d9ea1';var pf=isFinal&&i<3?medals[i]+' ':'#'+(i+1)+' ';
    var ah='';if(isSel){ah='<div class="action-banner"><div class="action-title">'+s.symbol+' - '+(s.name||'')+'</div><div class="action-row"><button class="action-btn primary" data-action="copy" data-code="'+s.symbol+'">Copy Ticker</button><button class="action-btn secondary" data-action="yahoo" data-code="'+s.symbol+'">Yahoo Finance</button><button class="action-btn secondary" data-action="moomoo" data-code="'+s.symbol+'">moomoo</button><button class="action-btn cancel" data-action="cancel">X</button></div><div class="action-note">Copy ticker and place order on moomoo at 09:30 ET</div></div>';}
    var mh='';if(s.margin_deadline)mh='<div class="margin-alert">Margin 20%: -'+s.margin_drop_pct+'% ($'+s.margin_deadline+')</div>';
    return '<div class="card'+(isSel?' sel':'')+'" data-code="'+s.symbol+'" style="border-left-color:'+bl+'"><div class="card-hd"><span style="color:#4a4a4a;font-size:11px;min-width:24px">'+pf+'</span><span class="c-code">'+s.symbol+'</span><span class="c-name">'+(s.name||'')+'</span>'+(chg?'<span class="c-chg '+(s.change_pct>=0?'price-chg-up':'price-chg-dn')+'">'+chg+'</span>':'')+'</div><div class="meta-row">'+(conf?'<span class="stars">'+stars+'</span>':'')+'<span style="color:#74fafd;font-size:10px">Score:'+score+'</span>'+(s.theme?'<span class="tag">'+s.theme+'</span>':'')+(s.grade?'<span class="tag" style="background:#1e1e2a;color:#ce9178">Grade:'+s.grade+'</span>':'')+'</div><div class="reason"><em>Reason: </em>'+(s.reason||s.buy_reason||'')+'</div>'+(s.sell_trigger?'<div class="stoploss"><em>Stop: </em>'+s.sell_trigger+'</div>':'')+(s.whale_signal?'<div style="font-size:10px;color:#f0a500;margin-top:4px">'+s.whale_signal+'</div>':'')+mh+ah+'</div>';}).join('');
  document.getElementById('stockList').innerHTML=h||'<div style="color:#4a4a4a;font-size:11px;padding:12px">No data.</div>';}

document.addEventListener('click',function(e){var ab=e.target.closest('[data-action]');if(ab){var act=ab.dataset.action;if(act==='copy'){navigator.clipboard.writeText(ab.dataset.code);var m=document.createElement('div');m.style.cssText='position:fixed;top:20px;right:20px;background:#4ec94e;color:#1a1a1a;padding:10px 16px;border-radius:3px;font-family:monospace;font-size:12px;font-weight:700;z-index:9999';m.textContent=ab.dataset.code+' copied';document.body.appendChild(m);setTimeout(function(){m.remove();},2500);}else if(act==='yahoo'){window.open('https://finance.yahoo.com/quote/'+ab.dataset.code,'_blank');}else if(act==='moomoo'){window.open('https://www.moomoo.com/stock/'+ab.dataset.code+'-US','_blank');}else if(act==='cancel'){sel=null;render(lastState);}return;}var card=e.target.closest('[data-code]');if(card){var code=card.dataset.code;sel=sel===code?null:code;render(lastState);}});

async function run(id){if(busy)return;busy=true;var prev=lastState.phase||0;startProgressTimer(id===0?1:id);document.querySelectorAll('[data-phase]').forEach(function(b){b.disabled=true;});var tgt=document.getElementById('b'+id);if(tgt)tgt.innerHTML='<span class="spinner"></span>Run';
  try{await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phase:id})});var to=0;while(to<300){await new Promise(function(r){setTimeout(r,3000);});to+=3;var resp=await fetch('/api/state?t='+Date.now());var d2=await resp.json();lastState=d2;render(d2);var np=d2.phase||0;var rp=scanningPhase;var ll=d2.log?d2.log.slice(-5).join(' '):'';if(ll.indexOf('Ph.5:')>=0)rp=5;else if(ll.indexOf('Ph.4:')>=0)rp=4;else if(ll.indexOf('Ph.3:')>=0)rp=3;else if(ll.indexOf('Ph.2:')>=0)rp=2;var nx=Math.max(np>=5?5:np+1,rp);if((np>0&&np>=scanningPhase)||nx>scanningPhase){var cp=scanningPhase;if(progressInterval){clearInterval(progressInterval);progressInterval=null;}var b2=document.getElementById('statusBadge');if(b2&&cp>0){if(!window._pcm)window._pcm={};window._pcm[cp]=true;b2.innerHTML='<span style="color:#4ec94e">Ph.'+cp+' DONE</span>';}scanningPhase=nx;setTimeout(function(){scanStartTime=Date.now();if(scanningPhase<5)startProgressTimer(scanningPhase);},3000);}var p5d=(id===5)&&(d2.post_open_result!=null&&d2.post_open_result.overall);var done;if(id===0)done=np>=4;else if(id<prev)done=np===id;else done=p5d||(np>prev||np>=id);if(done)break;}}catch(e){}
  stopProgressTimer();busy=false;document.querySelectorAll('[data-phase]').forEach(function(b){var pid=parseInt(b.dataset.phase);b.innerHTML=btnLabels[pid];b.disabled=false;});if(id>0&&id<=5){curTab=id;userChoseTab=true;}await fetchState();}

(async function(){try{var r=await fetch('/api/state?t='+Date.now());if(r.ok){var d=await r.json();lastState=d;render(d);}}catch(e){}})();
</script></body></html>
"""
# ━━━ Flask App & State Management ━━━
claude     = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
STATE_FILE = "/tmp/scan_state.json"
app        = Flask(__name__)

@app.after_request
def add_no_cache(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

def safe_json(text):
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text).strip()
    try:
        from json_repair import repair_json
        return json.loads(repair_json(text))
    except Exception: pass
    try:
        fixed = re.sub(r'(?<=": ")(.*?)(?=")', lambda m: m.group(0).replace('\n', ' '), text, flags=re.DOTALL)
        return json.loads(fixed)
    except Exception: pass
    try: return json.loads(text.replace('\n', ' '))
    except Exception: return {}

def load_state():
    try:
        with open(STATE_FILE, "r") as f: return json.load(f)
    except Exception: return {"phase": 0, "log": []}

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f: json.dump(state, f, ensure_ascii=False, default=str)
    except Exception: pass

def clear_state():
    save_state({"phase": 0, "log": []})

def add_log(msg):
    now = datetime.now(TZ_JST)
    entry = f"[{now.strftime('%H:%M:%S')}] {msg}"
    LOG_BUFFER.append(entry)
    state = load_state()
    logs = state.get("log", [])
    logs.append(entry)
    state["log"] = logs[-50:]
    save_state(state)

def push_notify(title, msg, priority="default"):
    if not SCHEDULED_RUN: return
    try:
        requests.post(f"https://ntfy.sh/{NTFY_CHANNEL}", data=msg.encode("utf-8"),
            headers={"Title": title, "Priority": priority,
                     "Tags": "chart_with_upwards_trend" if "📈" in title else "warning"}, timeout=10)
    except Exception as e:
        add_log(f"[WARN] ntfy failed: {e}")

# ━━━ Finnhub API Functions ━━━
def finnhub_get(endpoint, params=None):
    finnhub_rate_limit()
    params = params or {}
    params["token"] = FINNHUB_API_KEY
    try:
        r = requests.get(f"https://finnhub.io/api/v1/{endpoint}", params=params, timeout=10)
        if r.status_code == 200: return r.json()
    except Exception: pass
    return None

def get_us_symbols():
    global SYMBOL_CACHE, SYMBOL_CACHE_TIME
    now = time.time()
    if SYMBOL_CACHE and now - SYMBOL_CACHE_TIME < 86400: return SYMBOL_CACHE
    data = finnhub_get("stock/symbol", {"exchange": "US"})
    if data:
        symbols = [s for s in data if s.get("type") in ("Common Stock", "EQS")
                   and s.get("symbol") and "." not in s["symbol"] and len(s["symbol"]) <= 5]
        SYMBOL_CACHE = symbols
        SYMBOL_CACHE_TIME = now
        return symbols
    return []

def get_quote(symbol):
    data = finnhub_get("quote", {"symbol": symbol})
    if data and data.get("c"):
        return {"current": data["c"], "open": data["o"], "high": data["h"], "low": data["l"],
                "prev_close": data["pc"],
                "change_pct": round((data["c"] - data["pc"]) / data["pc"] * 100, 2) if data["pc"] else 0,
                "volume": data.get("t", 0)}
    return None

def get_quotes_batch(symbols):
    results = {}
    for sym in symbols:
        q = get_quote(sym)
        if q: results[sym] = q
    return results

def get_premarket_movers():
    add_log("🔍 Scanning pre-market movers...")
    watchlist = [
        "AAPL","MSFT","GOOGL","AMZN","NVDA","META","TSLA","AMD","AVGO","CRM",
        "NFLX","ADBE","INTC","QCOM","MU","MRVL","SMCI","ARM","PLTR","SNOW",
        "COIN","MSTR","SOFI","RIVN","LCID","NIO","BABA","JD","PDD","LI",
        "ORCL","IBM","PANW","CRWD","NET","DDOG","ZS","FTNT",
        "LLY","UNH","JNJ","PFE","MRNA","ABBV",
        "JPM","GS","MS","BAC","WFC","C",
        "BA","RTX","LMT","NOC","GD",
        "XOM","CVX","COP","OXY","SLB",
        "CAT","DE","HON","GE",
        "DIS","CMCSA","V","MA","PYPL","SQ",
        "HD","LOW","TGT","WMT","COST",
        "UBER","LYFT","ABNB","DASH","SHOP",
        "DELL","HPE","ANET","TSM","ASML","LRCX","KLAC","AMAT",
    ]
    movers = []
    for sym in watchlist:
        q = get_quote(sym)
        if q and q["prev_close"] > 0:
            chg = q["change_pct"]
            if abs(chg) >= 1.0:
                movers.append({"symbol": sym, "name": sym, "current": q["current"],
                               "change_pct": chg, "prev_close": q["prev_close"]})
    movers.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
    add_log(f"  Found {len(movers)} movers (>1% change)")
    return movers[:50]

def get_stock_candles(symbol, resolution="D", days=30):
    now = int(time.time())
    data = finnhub_get("stock/candle", {"symbol": symbol, "resolution": resolution,
                                         "from": now - days * 86400, "to": now})
    if data and data.get("s") == "ok":
        return [{"timestamp": data["t"][i], "open": data["o"][i], "high": data["h"][i],
                 "low": data["l"][i], "close": data["c"][i], "volume": data["v"][i]}
                for i in range(len(data.get("c", [])))]
    return []

def get_upgrade_downgrade(symbol):
    data = finnhub_get("stock/upgrade-downgrade", {"symbol": symbol})
    if not data: return []
    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    return [d for d in data if d.get("gradeDate", "") >= cutoff][:10]

def get_company_news(symbol, days=3):
    today = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    data = finnhub_get("company-news", {"symbol": symbol, "from": from_date, "to": today})
    return (data or [])[:10]

def get_insider_transactions(symbol):
    data = finnhub_get("stock/insider-transactions", {"symbol": symbol})
    if data and data.get("data"): return data["data"][:10]
    return []

def get_finnhub_macro():
    result = {"vix": None, "vix_20d_avg": None, "vix_spike_pct": 0,
              "fear_level": "NORMAL", "sp500_change": None, "alerts": []}
    for vix_sym in ["^VIX", "VIX", "VIXY"]:
        try:
            finnhub_rate_limit()
            r = requests.get("https://finnhub.io/api/v1/quote",
                params={"symbol": vix_sym, "token": FINNHUB_API_KEY}, timeout=6)
            if r.status_code == 200:
                d = r.json()
                if d.get("c") and d["c"] > 0:
                    result["vix"] = round(d["c"], 2); break
        except Exception: continue
    if result["vix"]:
        try:
            finnhub_rate_limit()
            now_ts = int(time.time())
            r = requests.get("https://finnhub.io/api/v1/indicator",
                params={"symbol": "^VIX", "resolution": "D", "from": now_ts - 30*86400,
                        "to": now_ts, "indicator": "sma", "timeperiod": 20,
                        "token": FINNHUB_API_KEY}, timeout=8)
            if r.status_code == 200:
                d = r.json()
                sma_vals = [v for v in (d.get("sma") or []) if v]
                if sma_vals:
                    result["vix_20d_avg"] = round(sma_vals[-1], 2)
                    spike = (result["vix"] - result["vix_20d_avg"]) / result["vix_20d_avg"] * 100
                    result["vix_spike_pct"] = round(spike, 1)
                    if spike >= 30:
                        result["fear_level"] = "SPIKE"
                        result["alerts"].append(f"🚨 VIX SPIKE: +{spike:.1f}%")
                    elif spike >= 15:
                        result["fear_level"] = "ELEVATED"
                        result["alerts"].append(f"⚠️ VIX ELEVATED: +{spike:.1f}%")
                    elif spike <= -15:
                        result["fear_level"] = "CALM"
        except Exception: pass
    try:
        finnhub_rate_limit()
        r = requests.get("https://finnhub.io/api/v1/quote",
            params={"symbol": "SPY", "token": FINNHUB_API_KEY}, timeout=6)
        if r.status_code == 200:
            d = r.json()
            if d.get("c") and d.get("pc"):
                chg = round((d["c"] - d["pc"]) / d["pc"] * 100, 2)
                result["sp500_change"] = chg
                if chg <= -2.0:
                    result["alerts"].append(f"🚨 S&P500 Risk-off: {chg}%")
    except Exception: pass
    return result
# ━━━ moomoo OpenAPI Functions ━━━
def moomoo_connect_quote():
    global MOOMOO_QUOTE_CTX
    if not MOOMOO_AVAILABLE: return None
    try:
        if MOOMOO_QUOTE_CTX is None:
            MOOMOO_QUOTE_CTX = OpenQuoteContext(host=MOOMOO_HOST, port=MOOMOO_PORT)
        return MOOMOO_QUOTE_CTX
    except Exception as e:
        add_log(f"[WARN] moomoo quote connect failed: {e}")
        MOOMOO_QUOTE_CTX = None
        return None

def moomoo_connect_trade():
    global MOOMOO_TRADE_CTX
    if not MOOMOO_AVAILABLE: return None
    try:
        if MOOMOO_TRADE_CTX is None:
            MOOMOO_TRADE_CTX = OpenSecTradeContext(filter_trdmarket=None, host=MOOMOO_HOST, port=MOOMOO_PORT, security_firm=None)
        return MOOMOO_TRADE_CTX
    except Exception as e:
        add_log(f"[WARN] moomoo trade connect failed: {e}")
        MOOMOO_TRADE_CTX = None
        return None

def get_order_book(symbol, num=10):
    ctx = moomoo_connect_quote()
    if not ctx: return None
    try:
        ret, data = ctx.get_order_book(f"US.{symbol}", num=num)
        if ret == RET_OK:
            bids = [(row["Bid"], row["BidVol"]) for _, row in data.iterrows() if row.get("Bid")]
            asks = [(row["Ask"], row["AskVol"]) for _, row in data.iterrows() if row.get("Ask")]
            return {"bids": bids, "asks": asks}
    except Exception as e:
        add_log(f"[WARN] Order book failed {symbol}: {e}")
    return None

def calc_absorption_ratio(snapshots):
    if not snapshots or len(snapshots) < 2: return 1.0
    total_bid_r, total_ask_c = 0, 0
    for i in range(1, len(snapshots)):
        prev, curr = snapshots[i-1], snapshots[i]
        pb = sum(b[1] for b in prev.get("bids", []))
        cb = sum(b[1] for b in curr.get("bids", []))
        if cb > pb: total_bid_r += (cb - pb)
        pa = sum(a[1] for a in prev.get("asks", []))
        ca = sum(a[1] for a in curr.get("asks", []))
        if ca < pa: total_ask_c += (pa - ca)
    return round(total_bid_r / total_ask_c, 3) if total_ask_c else 1.0

def calc_downside_efficiency(ob):
    if not ob: return 0.0
    bids = ob.get("bids", [])
    if len(bids) < 2: return 0.0
    prices = [b[0] for b in bids if b[0] > 0]
    if len(prices) < 2: return 0.0
    gaps, total = 0, len(prices) - 1
    avg_spread = (prices[0] - prices[-1]) / total if total > 0 else 0
    for i in range(1, len(prices)):
        if prices[i-1] - prices[i] > avg_spread * 2: gaps += 1
    return round(gaps / max(total, 1), 3)

def calc_whale_threshold_ewma(order_sizes, span=20):
    if not order_sizes or len(order_sizes) < 5: return 1000
    alpha = 2 / (span + 1)
    ewma = order_sizes[0]
    for size in order_sizes[1:]: ewma = alpha * size + (1 - alpha) * ewma
    sorted_s = sorted(order_sizes)
    p95 = sorted_s[min(int(len(sorted_s) * 0.95), len(sorted_s)-1)]
    return int(max(p95, ewma * 2))

def analyze_order_book(symbol):
    ob = get_order_book(symbol)
    if not ob:
        return {"available": False, "absorption_ratio": 1.0, "downside_efficiency": 0.0,
                "whale_threshold": 1000, "bids": [], "asks": [], "whale_detected": False}
    de = calc_downside_efficiency(ob)
    all_sizes = [b[1] for b in ob.get("bids", [])] + [a[1] for a in ob.get("asks", [])]
    whale_th = calc_whale_threshold_ewma(all_sizes)
    whale_bids = [b for b in ob.get("bids", []) if b[1] >= whale_th]
    return {"available": True, "absorption_ratio": 1.0, "downside_efficiency": de,
            "whale_threshold": whale_th, "bids": ob["bids"][:5], "asks": ob["asks"][:5],
            "whale_detected": len(whale_bids) > len([a for a in ob.get("asks", []) if a[1] >= whale_th]),
            "whale_bid_vol": sum(b[1] for b in whale_bids)}

def get_account_info():
    ctx = moomoo_connect_trade()
    if not ctx: return None
    try:
        ret, data = ctx.accinfo_query()
        if ret == RET_OK and not data.empty:
            row = data.iloc[0]
            return {"total_assets": row.get("total_assets", 0), "cash": row.get("cash", 0),
                    "market_val": row.get("market_val", 0)}
    except Exception as e:
        add_log(f"[WARN] Account info failed: {e}")
    return None

def get_positions():
    ctx = moomoo_connect_trade()
    if not ctx: return []
    try:
        ret, data = ctx.position_list_query()
        if ret == RET_OK and not data.empty:
            return [{"symbol": row.get("code", "").replace("US.", ""),
                     "qty": row.get("qty", 0), "cost_price": row.get("cost_price", 0),
                     "market_val": row.get("market_val", 0)}
                    for _, row in data.iterrows()]
    except Exception as e:
        add_log(f"[WARN] Position query failed: {e}")
    return []

def _calc_margin_deadzone(account_info, positions, current_prices):
    if not account_info or not positions: return None
    total_assets = account_info.get("total_assets", 0)
    market_val = account_info.get("market_val", 0)
    cash = account_info.get("cash", 0)
    if market_val <= 0: return None
    borrowed = max(0, market_val - cash)
    if borrowed <= 0:
        return {"margin_pct": 100.0, "allowed_drop_pct": 100.0, "deadlines": {}, "alert_level": "SAFE"}
    equity = total_assets - borrowed
    margin_pct = (equity / market_val) * 100 if market_val > 0 else 100
    allowed_drop_pct = max(0, round(((equity - 0.20 * market_val) / (market_val * 0.80)) * 100, 2))
    deadlines = {}
    for pos in positions:
        sym = pos["symbol"]
        price = current_prices.get(sym, {}).get("current", pos.get("cost_price", 0))
        if price > 0 and allowed_drop_pct < 100:
            deadlines[sym] = {"current_price": price,
                              "deadline_price": round(price * (1 - allowed_drop_pct / 100), 2),
                              "drop_pct": allowed_drop_pct, "qty": pos.get("qty", 0)}
    alert_level = "URGENT" if margin_pct <= 25 else "HIGH" if margin_pct <= 30 else "WARNING" if margin_pct <= 40 else "SAFE"
    return {"margin_pct": round(margin_pct, 2), "allowed_drop_pct": allowed_drop_pct,
            "deadlines": deadlines, "alert_level": alert_level}

# ━━━ News & OSINT ━━━
def get_news():
    articles = []
    if NEWS_API_KEY:
        try:
            r = requests.get("https://newsapi.org/v2/everything",
                params={"q": "stock market OR Wall Street OR Federal Reserve OR earnings",
                        "language": "en", "sortBy": "publishedAt", "pageSize": 20,
                        "apiKey": NEWS_API_KEY}, timeout=10)
            if r.status_code == 200:
                for a in r.json().get("articles", []):
                    articles.append({"title": a.get("title", ""), "source": a.get("source", {}).get("name", "")})
        except Exception: pass
    for feed_url in ["https://rsshub.app/telegram/channel/warmonitor3",
                     "https://rsshub.app/telegram/channel/intelslava"]:
        try:
            r = requests.get(feed_url, timeout=8)
            if r.status_code == 200:
                titles = re.findall(r"<title>(.*?)</title>", r.text)
                for t in titles[1:6]:
                    articles.append({"title": t, "source": "OSINT"})
        except Exception: pass
    return articles

LEAK_KEYWORDS = ["sources say","according to sources","is considering","emergency rate",
    "circuit breaker","breaking:","unexpected","fed pivot","rate cut","tariff","sanctions"]

def detect_leaks(articles):
    leaks = []
    for a in articles:
        tl = a.get("title", "").lower()
        if any(kw in tl for kw in LEAK_KEYWORDS):
            leaks.append(a)
    return leaks

def sentinel_check(news, extra=""):
    if not news: return {"action": "HOLD", "risk": 0, "reason": ""}
    crisis = ["nuclear","invasion","war declared","financial crisis","bank collapse",
              "emergency fed","market crash","circuit breaker triggered","debt default"]
    risk, reasons = 0, []
    for a in news:
        tl = a.get("title", "").lower()
        for kw in crisis:
            if kw in tl: risk += 2; reasons.append(a["title"][:60])
    if risk >= 4:
        return {"action": "SELL_ALL", "risk": min(risk, 5), "reason": " | ".join(reasons[:3])}
    return {"action": "HOLD", "risk": min(risk, 5), "reason": " | ".join(reasons[:3]) if reasons else ""}

def process_whale_ratings(upgrades, quote):
    if not upgrades: return 0, ""
    score_adj, signals = 0, []
    for u in upgrades:
        company = u.get("company", "")
        is_whale = any(f.lower() in company.lower() for f in WHALE_FIRMS)
        if not is_whale: continue
        action = u.get("action", "").lower()
        to_grade = u.get("toGrade", "").lower()
        is_upgrade = action in ("upgrade", "init") and to_grade in ("buy", "overweight", "outperform")
        is_downgrade = action in ("downgrade",) and to_grade in ("sell", "underweight", "underperform")
        if is_upgrade:
            if quote and abs(quote.get("change_pct", 0)) < 0.5:
                score_adj -= 10
                signals.append(f"⚠️ {company}: Buy but low momentum (Distribution?)")
            else:
                score_adj += 10
                signals.append(f"✅ {company}: Upgrade to {to_grade}")
        elif is_downgrade:
            score_adj -= 15
            signals.append(f"🚨 {company}: Downgrade to {to_grade}")
    return score_adj, " | ".join(signals)
# ━━━ Material Grade & Gemini Scoring ━━━
def classify_catalyst_grade(reason_text):
    text_lower = (reason_text or "").lower()
    for grade, keywords in GRADE_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text_lower: return grade
    return "C"

def gemini_score_stocks(stocks, context=""):
    if not google_genai or not GEMINI_API_KEY:
        add_log("[WARN] Gemini not available")
        return {}
    try: client = google_genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        add_log(f"[WARN] Gemini init failed: {e}"); return {}
    results = {}
    for s in stocks:
        symbol = s.get("symbol", "")
        prompt = f"""Evaluate US stock {symbol} ({s.get('name',symbol)}) using real-time web search.
AI Buy Reason: {s.get('reason','')}
{context}
Verify: 1) Is reason accurate NOW? 2) Negative news/SEC issues? 3) Market sentiment? 4) Upcoming events?
Return ONLY JSON: {{"score": 0-100, "red_flag": true/false, "reason": "1-2 sentences"}}
Score: 80+=Strong, 60-79=Moderate, 40-59=Weak, <40=Red flag"""
        try:
            response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt,
                config=genai_types.GenerateContentConfig(
                    tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())], temperature=0.3))
            data = safe_json(response.text or "{}")
            results[symbol] = {"score": data.get("score", 50), "red_flag": data.get("red_flag", False),
                               "reason": data.get("reason", "")}
            add_log(f"  🔮 Gemini: {symbol} → {data.get('score','?')}/100" +
                    (" 🚩RED" if data.get("red_flag") else ""))
        except Exception as e:
            add_log(f"  [WARN] Gemini {symbol}: {e}")
            results[symbol] = {"score": 50, "red_flag": False, "reason": "unavailable"}
    return results

# ━━━ Exit State Machine ━━━
def calc_hold_score(ctx):
    score = 50
    grade = ctx.get("catalyst_grade", "C")
    if grade == "A": score += 25
    elif grade == "B": score += 15
    elif grade == "D": score -= 20
    if ctx.get("vwap_reclaimed"): score += 15
    if ctx.get("recovered_to_positive"): score += 20
    fear = ctx.get("vix_fear_level", "NORMAL")
    if fear == "SPIKE": score -= 30
    elif fear == "ELEVATED": score -= 15
    if ctx.get("whale_detected"): score += 10
    ar = ctx.get("absorption_ratio", 1.0)
    if ar > 1.2: score += 10
    elif ar < 0.5: score -= 10
    return max(0, min(100, score))

def calc_exit_score(ctx):
    score = 0
    if ctx.get("thesis_broken"): return 100
    score += ctx.get("vwap_failed_count", 0) * 15
    fear = ctx.get("vix_fear_level", "NORMAL")
    if fear == "SPIKE": score += 35
    elif fear == "ELEVATED": score += 15
    grade = ctx.get("catalyst_grade", "C")
    pnl = ctx.get("pnl_pct", 0)
    if grade in ("C", "D") and pnl <= -3: score += 20
    if ctx.get("volume_increasing_on_drop"): score += 20
    if ctx.get("downside_efficiency", 0) > 0.3: score += 15
    if ctx.get("absorption_ratio", 1.0) < 0.5: score += 10
    return min(100, score)

def determine_exit_state(ctx):
    hold_sc = calc_hold_score(ctx)
    exit_sc = calc_exit_score(ctx)
    pnl = ctx.get("pnl_pct", 0)
    grade = ctx.get("catalyst_grade", "C")
    elapsed = ctx.get("elapsed_min", 0)
    if ctx.get("thesis_broken") or exit_sc >= 80:
        return EXIT_STATE_THESIS_BROKEN, hold_sc, exit_sc, "EXIT_ALL"
    if pnl >= 10 and ctx.get("momentum_decaying"):
        return EXIT_STATE_PARABOLIC, hold_sc, exit_sc, "TAKE_PROFIT"
    if pnl >= 12:
        return EXIT_STATE_PARABOLIC, hold_sc, exit_sc, "TAKE_PROFIT"
    if exit_sc >= 50 and pnl < 0:
        return EXIT_STATE_DISTRIBUTION, hold_sc, exit_sc, "EXIT_ALL"
    if exit_sc >= 40:
        return EXIT_STATE_DISTRIBUTION, hold_sc, exit_sc, "WARN"
    if pnl >= 2 and hold_sc >= 60:
        return EXIT_STATE_HEALTHY_UPTREND, hold_sc, exit_sc, "HOLD"
    if pnl < -2 and hold_sc >= 50:
        return EXIT_STATE_SHAKEOUT, hold_sc, exit_sc, "HOLD"
    if elapsed >= 30:
        if grade == "D": return EXIT_STATE_THESIS_BROKEN, hold_sc, exit_sc, "EXIT_ALL"
        if grade == "C" and pnl < 1: return EXIT_STATE_DISTRIBUTION, hold_sc, exit_sc, "EXIT_ALL"
    if elapsed < 5: return EXIT_STATE_OPEN_DISCOVERY, hold_sc, exit_sc, "HOLD"
    if pnl >= 0: return EXIT_STATE_HEALTHY_UPTREND, hold_sc, exit_sc, "HOLD"
    return EXIT_STATE_SHAKEOUT, hold_sc, exit_sc, "HOLD"

def evaluate_vwap_reclaim(symbol, open_price, current_price, history):
    if not history: return current_price >= open_price, open_price
    total_pv, total_v = 0, 0
    for h in history:
        p, v = h.get("price", 0), h.get("volume", 1)
        total_pv += p * v; total_v += v
    vwap = total_pv / total_v if total_v > 0 else open_price
    return current_price >= vwap, round(vwap, 2)

def get_realtime_prices(symbols):
    prices = {}
    ctx = moomoo_connect_quote()
    if ctx:
        try:
            moomoo_syms = [f"US.{s}" for s in symbols]
            ret, data = ctx.get_market_snapshot(moomoo_syms)
            if ret == RET_OK:
                for _, row in data.iterrows():
                    sym = row.get("code", "").replace("US.", "")
                    if sym:
                        prices[sym] = {"current": row.get("last_price", 0), "open": row.get("open_price", 0),
                                       "high": row.get("high_price", 0), "low": row.get("low_price", 0),
                                       "volume": row.get("volume", 0),
                                       "change_pct": round(row.get("price_change_rate", 0) or 0, 2)}
                        now_str = datetime.now(TZ_ET).strftime("%H:%M")
                        if sym not in PRICE_HISTORY: PRICE_HISTORY[sym] = []
                        PRICE_HISTORY[sym].append({"time": now_str, "price": row.get("last_price", 0),
                                                    "volume": row.get("volume", 0)})
                        if len(PRICE_HISTORY[sym]) > 200: PRICE_HISTORY[sym] = PRICE_HISTORY[sym][-200:]
                return prices
        except Exception as e:
            add_log(f"[WARN] moomoo snapshot failed: {e}")
    for sym in symbols:
        q = get_quote(sym)
        if q: prices[sym] = q
    return prices
# ━━━ Phase 1: Broad Scan ━━━
def phase1_broad_scan():
    add_log("📡 Ph.1: Broad Scan starting...")
    state = load_state(); state["phase"] = 0; save_state(state)
    movers = get_premarket_movers()
    if not movers:
        add_log("[ERROR] No pre-market movers"); state["phase"] = 1; state["top20"] = []; save_state(state); return
    finnhub = get_finnhub_macro(); state["finnhub_macro"] = finnhub
    for alert in finnhub.get("alerts", []): add_log(f"  {alert}")
    news = get_news(); sentinel = sentinel_check(news)
    state["sentinel"] = sentinel; state["news"] = [{"title": n.get("title","")} for n in news[:15]]
    if sentinel.get("action") == "SELL_ALL":
        add_log("🚨 SENTINEL: SELL_ALL"); push_notify("🚨 SELL ALL", sentinel.get("reason",""), priority="urgent")
        state["phase"] = 1; save_state(state); return
    leaks = detect_leaks(news)
    if leaks: add_log(f"  🔍 {len(leaks)} leak signals")
    movers_text = "\n".join([f"{m['symbol']}: {m.get('change_pct',0):+.2f}% (${m.get('current',0):.2f})" for m in movers[:50]])
    news_text = "\n".join([f"- {n.get('title','')}" for n in news[:10]])
    leak_text = "\n".join([f"⚡ {l.get('title','')}" for l in leaks[:5]])
    macro_text = f"VIX: {finnhub.get('vix','N/A')} ({finnhub.get('fear_level','N/A')}, {finnhub.get('vix_spike_pct',0):+.1f}%)\nS&P500: {finnhub.get('sp500_change','N/A')}%"
    prompt = f"""You are a US stock AI predator. Find stocks that will SURGE today.
PHILOSOPHY: All or Nothing. Whale tracking. Risk visualization.
PRE-MARKET MOVERS:\n{movers_text}\nMACRO:\n{macro_text}\nNEWS:\n{news_text or 'None'}\nLEAKS:\n{leak_text or 'None'}
Select TOP 20 most likely to surge after 09:30 ET open.
Return ONLY JSON array: [{{"symbol":"TICKER","name":"Name","change_pct":X.XX,"reason":"buy reason","confidence":1-5,"theme":"sector","sell_trigger":"stop-loss"}}]"""
    add_log("🤖 Claude analyzing...")
    try:
        res = claude.messages.create(model="claude-opus-4-6", max_tokens=3000, messages=[{"role":"user","content":prompt}])
        top20 = safe_json(res.content[0].text if res.content else "[]")
        if isinstance(top20, dict): top20 = top20.get("stocks", top20.get("top20", []))
        if not isinstance(top20, list): top20 = []
        top20 = top20[:20]
    except Exception as e:
        add_log(f"[ERROR] Claude Ph.1: {e}"); top20 = []
    add_log(f"✅ Ph.1 complete: {len(top20)} candidates")
    for i, s in enumerate(top20[:5]): add_log(f"  #{i+1} {s.get('symbol','')} {s.get('change_pct',0):+.2f}%")
    state["phase"] = 1; state["top20"] = top20
    state["market_condition"] = f"Pre-market: {len(movers)} movers"
    state["macro_summary"] = macro_text; save_state(state)
    push_notify("📡 Ph.1 Complete", f"TOP20 from {len(movers)} movers\nVIX: {finnhub.get('vix','?')}")

# ━━━ Phase 2: Re-Scoring ━━━
def phase2_rescore():
    add_log("🔬 Ph.2: Re-scoring...")
    state = load_state(); top20 = state.get("top20", [])
    if not top20: add_log("[WARN] No TOP20"); state["phase"] = 2; save_state(state); return
    symbols = [s.get("symbol","") for s in top20 if s.get("symbol")]
    fresh = get_quotes_batch(symbols[:20])
    vol_data = {}
    for sym in symbols[:10]:
        candles = get_stock_candles(sym, days=30)
        if candles and len(candles) >= 5:
            closes = [c["close"] for c in candles]
            rets = [(closes[i]-closes[i-1])/closes[i-1] for i in range(1,len(closes))]
            vol_data[sym] = round(statistics.stdev(rets) * (252**0.5) * 100, 1) if len(rets) >= 2 else 0
    refresh_text = "\n".join([f"{sym}: ${q.get('current',0):.2f} ({q.get('change_pct',0):+.2f}%) Vol:{vol_data.get(sym,'N/A')}%" for sym, q in fresh.items()])
    top20_text = "\n".join([f"{s.get('symbol','')}: {s.get('reason','')} (Conf:{s.get('confidence',0)}/5)" for s in top20])
    prompt = f"""Re-score TOP20→TOP10 for US stocks.\nTOP20:\n{top20_text}\nUPDATED QUOTES:\n{refresh_text}\nConsider: momentum change, volatility, priced-in moves.\nReturn ONLY JSON array of TOP 10: [{{"symbol":"TICKER","name":"Name","score":0-100,"change_pct":X.XX,"reason":"updated","confidence":1-5,"theme":"theme","sell_trigger":"stop","volatility":"high/med/low"}}]"""
    add_log("🤖 Claude re-scoring...")
    try:
        res = claude.messages.create(model="claude-opus-4-6", max_tokens=2000, messages=[{"role":"user","content":prompt}])
        top10 = safe_json(res.content[0].text if res.content else "[]")
        if isinstance(top10, dict): top10 = top10.get("stocks", top10.get("top10", []))
        if not isinstance(top10, list): top10 = []
        top10 = top10[:10]
    except Exception as e:
        add_log(f"[ERROR] Claude Ph.2: {e}"); top10 = top20[:10]
    add_log(f"✅ Ph.2 complete: {len(top10)} candidates")
    state["phase"] = 2; state["top10"] = top10; save_state(state)
    push_notify("🔬 Ph.2 Complete", f"TOP10 from {len(top20)}")

# ━━━ Phase 3: Cross-Check ━━━
def phase3_crosscheck():
    add_log("⚡ Ph.3: Cross-check...")
    state = load_state(); top10 = state.get("top10", [])
    if not top10: add_log("[WARN] No TOP10"); state["phase"] = 3; save_state(state); return
    whale_signals = {}
    for s in top10[:10]:
        sym = s.get("symbol", "")
        if not sym: continue
        upgrades = get_upgrade_downgrade(sym); quote = get_quote(sym)
        adj, sig = process_whale_ratings(upgrades, quote)
        if sig: whale_signals[sym] = {"score_adj": adj, "signal": sig}; add_log(f"  🐳 {sym}: {sig}")
    company_news = {}
    for s in top10[:5]:
        sym = s.get("symbol", "")
        if sym:
            cn = get_company_news(sym)
            if cn: company_news[sym] = [n.get("headline", n.get("summary", ""))[:80] for n in cn[:3]]
    add_log("🔮 Gemini grounding...")
    gemini_scores = gemini_score_stocks(top10[:10], context=f"VIX: {state.get('finnhub_macro',{}).get('fear_level','NORMAL')}")
    state["gemini_scores"] = gemini_scores
    whale_text = "\n".join([f"{s}: {w['signal']} ({w['score_adj']:+d})" for s, w in whale_signals.items()]) or "None"
    gemini_text = "\n".join([f"{s}: {g.get('score',0)}/100 {'🚩RED' if g.get('red_flag') else ''} - {g.get('reason','')}" for s, g in gemini_scores.items()]) or "N/A"
    top10_text = "\n".join([f"{s.get('symbol','')}: Score:{s.get('score',0)} - {s.get('reason','')}" for s in top10])
    prompt = f"""Cross-check TOP10→TOP5.\nCRITICAL: "Buy without volume"=TRAP(penalize). "Price target raise+vol>300%"=REAL(boost).\nTOP10:\n{top10_text}\nWHALE RATINGS:\n{whale_text}\nGEMINI:\n{gemini_text}\nRules: red_flag→EXCLUDE, score<40→EXCLUDE, 40-59→warn, Combined=Claude70%+Gemini30%\nReturn ONLY JSON array TOP5: [{{"symbol":"TICKER","name":"Name","score":0-100,"combined_score":0-100,"reason":"reason","confidence":1-5,"theme":"theme","sell_trigger":"stop","grade":"A/B/C/D","whale_signal":"","gemini_score":0-100}}]"""
    add_log("🤖 Claude cross-checking...")
    try:
        res = claude.messages.create(model="claude-opus-4-6", max_tokens=2000, messages=[{"role":"user","content":prompt}])
        top5 = safe_json(res.content[0].text if res.content else "[]")
        if isinstance(top5, dict): top5 = top5.get("stocks", top5.get("top5", []))
        if not isinstance(top5, list): top5 = []
        top5 = top5[:5]
    except Exception as e:
        add_log(f"[ERROR] Claude Ph.3: {e}"); top5 = top10[:5]
    filtered = []
    for s in top5:
        sym = s.get("symbol", ""); gs = gemini_scores.get(sym, {})
        if gs.get("red_flag"): add_log(f"  🚩 {sym} KILLED (red flag)"); continue
        if gs.get("score", 50) < 40: add_log(f"  ❌ {sym} KILLED (score {gs.get('score',0)})"); continue
        filtered.append(s)
    top5 = filtered[:5]
    add_log(f"✅ Ph.3 complete: {len(top5)} after Gemini filter")
    state["phase"] = 3; state["top5"] = top5; state["whale_signals"] = whale_signals; save_state(state)
    push_notify("⚡ Ph.3 Complete", "\n".join([f"{s.get('symbol','')} ({s.get('combined_score','?')})" for s in top5]))

# ━━━ Phase 4: Final TOP3 ━━━
def phase4_final_top3():
    add_log("🏆 Ph.4: Final TOP3...")
    state = load_state(); top5 = state.get("top5", [])
    if not top5: add_log("[WARN] No TOP5"); state["phase"] = 4; save_state(state); return
    ob_results = {}
    for s in top5:
        sym = s.get("symbol", "")
        if sym:
            add_log(f"  📊 Order book: {sym}")
            ob = analyze_order_book(sym); ob_results[sym] = ob
            if ob.get("available"):
                add_log(f"    AR:{ob.get('absorption_ratio','-')} Vacuum:{ob.get('downside_efficiency','-')} {'🐳WHALE' if ob.get('whale_detected') else ''}")
    margin_info = None
    account = get_account_info(); positions = get_positions()
    if account and positions:
        syms = [s.get("symbol","") for s in top5]; cp = get_quotes_batch(syms)
        margin_info = _calc_margin_deadzone(account, positions, cp)
        if margin_info: add_log(f"  💰 Margin:{margin_info['margin_pct']:.1f}% Drop:{margin_info['allowed_drop_pct']:.1f}% {margin_info['alert_level']}")
    scored = []
    for s in top5:
        sym = s.get("symbol", ""); ob = ob_results.get(sym, {})
        combined = s.get("combined_score", s.get("score", 50))
        if ob.get("available"):
            if ob.get("whale_detected"): combined += 10
            if ob.get("downside_efficiency", 0) > 0.3: combined -= 15
        s["final_score"] = min(100, max(0, combined)); s["order_book"] = ob
        if margin_info and margin_info.get("deadlines", {}).get(sym):
            dl = margin_info["deadlines"][sym]; s["margin_deadline"] = dl["deadline_price"]; s["margin_drop_pct"] = dl["drop_pct"]
        scored.append(s)
    scored.sort(key=lambda x: x.get("final_score", 0), reverse=True)
    top3 = scored[:3]
    if not top3:
        add_log("⏭️ All killed. Skip today.")
        push_notify("⏭️ Skip", "No candidates passed.", priority="default")
        state["phase"] = 4; state["top3_final"] = []; save_state(state); return
    medal = ["🥇","🥈","🥉"]
    for i, s in enumerate(top3):
        sym = s.get("symbol",""); grade = s.get("grade", classify_catalyst_grade(s.get("reason",""))); s["grade"] = grade
        margin_str = f"\n⚠️ Margin 20%: -${s.get('margin_drop_pct',0):.1f}% (${s.get('margin_deadline','')})" if s.get("margin_deadline") else ""
        msg = f"{medal[i]} {sym}\nScore:{s.get('final_score',0)} Grade:{grade}\n{s.get('reason','')}\nStop: {s.get('sell_trigger','')}{margin_str}"
        add_log(f"  {medal[i]} {sym} Score:{s.get('final_score',0)} Grade:{grade}")
        push_notify(f"{medal[i]} TOP3 #{i+1}: {sym}", msg, priority="high" if i==0 else "default")
    if margin_info and margin_info.get("alert_level") in ("URGENT","HIGH"):
        push_notify("⚠️ MARGIN ALERT", f"Margin:{margin_info['margin_pct']:.1f}% Drop:{margin_info['allowed_drop_pct']:.1f}%",
            priority="urgent" if margin_info["alert_level"]=="URGENT" else "high")
    state["phase"] = 4; state["top3_final"] = top3
    state["order_book"] = {s.get("symbol",""): ob_results.get(s.get("symbol",""),{}) for s in top3}
    if margin_info: state["margin_alert"] = f"Margin:{margin_info['margin_pct']:.1f}% Drop:{margin_info['allowed_drop_pct']:.1f}% {margin_info['alert_level']}"
    save_state(state); add_log("✅ Ph.4 complete: TOP3 confirmed")
# ━━━ Phase 5: Dynamic Exit Engine ━━━
def phase5_post_open():
    add_log("📈 Ph.5: Dynamic Exit Engine...")
    state = load_state(); top3 = state.get("top3_final", [])
    if not top3: add_log("[WARN] No TOP3"); return
    finnhub = state.get("finnhub_macro", {}); codes = [s.get("symbol","") for s in top3 if s.get("symbol")]
    contexts, ob_history = {}, {}
    for s in top3:
        sym = s.get("symbol","")
        if not sym: continue
        grade = s.get("grade", classify_catalyst_grade(s.get("reason","")))
        contexts[sym] = {"catalyst_grade": grade, "vix_fear_level": finnhub.get("fear_level","NORMAL"),
            "vix_spike_pct": finnhub.get("vix_spike_pct",0), "open_price": 0, "current_price": 0,
            "pnl_pct": 0, "drawdown_pct": 0, "vwap_reclaimed": False, "vwap_failed_count": 0,
            "volume_increasing_on_drop": False, "volume_decreasing_on_drop": False,
            "recovered_to_positive": False, "momentum_decaying": False, "thesis_broken": False,
            "elapsed_min": 0, "prev_volume": 0, "state": EXIT_STATE_OPEN_DISCOVERY,
            "whale_detected": False, "absorption_ratio": 1.0, "downside_efficiency": 0.0}
        ob_history[sym] = []
    state["post_open_result"] = {"evaluations": [], "overall": "⏳ Tracking..."}; save_state(state)
    add_log("📊 Fetching opening prices...")
    prices_open = get_realtime_prices(codes)
    for s in top3:
        sym = s.get("symbol","")
        if sym in prices_open:
            p = prices_open[sym]; contexts[sym]["open_price"] = p.get("open", p.get("current",0))
            contexts[sym]["current_price"] = p.get("current",0); contexts[sym]["pnl_pct"] = p.get("change_pct",0)
            contexts[sym]["prev_volume"] = p.get("volume",0)
            add_log(f"  {'📈' if p.get('change_pct',0)>=0 else '📉'} {sym} {'+' if p.get('change_pct',0)>=0 else ''}{p.get('change_pct',0)}% Grade:{contexts[sym]['catalyst_grade']}")
    news = get_news(); sentinel_now = sentinel_check(news)
    if sentinel_now.get("action") == "SELL_ALL":
        push_notify("🚨 SELL ALL", sentinel_now.get("reason",""), priority="urgent"); add_log("🚨 SELL_ALL!"); return
    decided = {}
    for i in range(3):
        time.sleep(600); elapsed = (i+1)*10; prices_now = get_realtime_prices(codes)
        for s in top3:
            sym = s.get("symbol","")
            if sym in decided or sym not in prices_now: continue
            p = prices_now[sym]; ctx = contexts[sym]
            op = ctx["open_price"] if ctx["open_price"] > 0 else p.get("open",0)
            ctx["current_price"] = p.get("current",0); ctx["elapsed_min"] = elapsed
            ctx["pnl_pct"] = p.get("change_pct",0); ctx["drawdown_pct"] = p.get("change_pct",0)
            hist = PRICE_HISTORY.get(sym, [])
            vwap_ok, vwap_val = evaluate_vwap_reclaim(sym, op, p.get("current",0), hist)
            if not vwap_ok: ctx["vwap_failed_count"] += 1
            ctx["vwap_reclaimed"] = vwap_ok
            vol_now = p.get("volume",0); vol_prev = ctx["prev_volume"]
            if p.get("change_pct",0) < 0:
                ctx["volume_increasing_on_drop"] = vol_now > vol_prev * 1.1
                ctx["volume_decreasing_on_drop"] = vol_now < vol_prev * 0.9
            if p.get("change_pct",0) >= 0: ctx["recovered_to_positive"] = True
            ctx["prev_volume"] = vol_now
            ob = get_order_book(sym)
            if ob:
                ob_history[sym].append(ob); ctx["downside_efficiency"] = calc_downside_efficiency(ob)
                if len(ob_history[sym]) >= 2: ctx["absorption_ratio"] = calc_absorption_ratio(ob_history[sym])
                all_sz = [b[1] for b in ob.get("bids",[])] + [a[1] for a in ob.get("asks",[])]
                ctx["whale_detected"] = any(b[1] >= calc_whale_threshold_ewma(all_sz) for b in ob.get("bids",[]))
            new_state, hold_sc, exit_sc, action = determine_exit_state(ctx); ctx["state"] = new_state
            sign = "+" if p.get("change_pct",0) >= 0 else ""
            st_em = {"S0":"⏳","S1":"🔍","S2":"✅","S3":"⚠️","S4":"🚨","S5":"💰"}.get(new_state,"?")
            add_log(f"  {sym} {elapsed}min {sign}{p.get('change_pct',0)}% | {st_em}{new_state} H:{hold_sc} E:{exit_sc}")
            state["realtime_prices"] = prices_now
            if ob:
                if "order_book" not in state: state["order_book"] = {}
                state["order_book"][sym] = {"bids": ob.get("bids",[])[:5], "asks": ob.get("asks",[])[:5],
                    "absorption_ratio": ctx["absorption_ratio"], "downside_efficiency": ctx["downside_efficiency"],
                    "whale_threshold": calc_whale_threshold_ewma(all_sz) if ob else 0}
            save_state(state)
            if action == "EXIT_ALL":
                reason = "Thesis broken" if ctx.get("thesis_broken") else f"ExitScore:{exit_sc}"
                add_log(f"  🚨 {sym} EXIT → {reason}")
                push_notify(f"🚨 STOP: {sym}", f"{elapsed}min: {sign}{p.get('change_pct',0)}%\n{reason}", priority="urgent")
                decided[sym] = {"action": action, "reason": reason, "pnl": p.get("change_pct",0)}
            elif action == "TAKE_PROFIT":
                add_log(f"  💰 {sym} PROFIT → +{p.get('change_pct',0)}%")
                push_notify(f"💰 PROFIT: {sym}", f"{elapsed}min: +{p.get('change_pct',0)}%", priority="high")
                decided[sym] = {"action": action, "reason": "Parabolic", "pnl": p.get("change_pct",0)}
            elif action == "HOLD" and ctx.get("recovered_to_positive") and i > 0:
                push_notify(f"✅ HOLD: {sym}", f"{elapsed}min: {sign}{p.get('change_pct',0)}% Grade:{ctx['catalyst_grade']}")
    # Final Claude eval
    prices_final = get_realtime_prices(codes)
    top3_text = "\n".join([f"{s.get('symbol','')} Grade:{contexts.get(s.get('symbol',''),{}).get('catalyst_grade','?')} State:{contexts.get(s.get('symbol',''),{}).get('state','?')} "
        + (f"Price:{prices_final[s.get('symbol','')].get('change_pct',0)}%" if s.get('symbol','') in prices_final else "") for s in top3])
    try:
        prompt = f"Final 30min US stock tracking eval.\n[TOP3]\n{top3_text}\nReturn JSON:{{\"evaluations\":[{{\"code\":\"TICKER\",\"status\":\"HOLD/SELL\",\"message\":\"summary\",\"action_advice\":\"advice\"}}],\"overall\":\"assessment\"}}"
        res = claude.messages.create(model="claude-haiku-4-5-20251001", max_tokens=800, messages=[{"role":"user","content":prompt}])
        result = safe_json(res.content[0].text if res.content else "{}")
        msg = "📈 30min Complete\n" + result.get("overall","") + "\n"
        for e in result.get("evaluations",[]):
            msg += f"{'✅' if e.get('status')=='HOLD' else '⚠️'} {e.get('code','')} {e.get('message','')}\n→ {e.get('action_advice','')}\n"
        margin_str = ""
        account = get_account_info(); positions = get_positions()
        if account and positions:
            mi = _calc_margin_deadzone(account, positions, prices_final)
            if mi:
                margin_str = f"\n💰 Margin:{mi['margin_pct']:.1f}% Drop:{mi['allowed_drop_pct']:.1f}%"
                for ds, dl in mi.get("deadlines",{}).items():
                    margin_str += f"\n  ⚠️ {ds}: ${ dl['deadline_price']} (-{dl['drop_pct']:.1f}%)"
                msg += margin_str
                state["margin_alert"] = f"Margin:{mi['margin_pct']:.1f}% Drop:{mi['allowed_drop_pct']:.1f}% {mi['alert_level']}"
        state["phase"] = 5; state["post_open_result"] = result; state["realtime_prices"] = prices_final
        state["exit_contexts"] = {k: {kk: vv for kk, vv in v.items() if isinstance(vv, (str,int,float,bool))} for k, v in contexts.items()}
        save_state(state); push_notify("📈 30min Complete", msg); add_log(f"✅ Ph.5 complete: {result.get('overall','')}")
    except Exception as e:
        add_log(f"[ERROR] Ph.5 final: {e}")

# ━━━ Flask Routes ━━━
@app.route("/")
def index(): return HTML

@app.route("/api/state")
def api_state():
    global BACKGROUND_TASK_RUNNING
    state = load_state(); saved = state.get("log",[]); live = list(LOG_BUFFER)[-50:]
    seen = set(saved); merged = list(saved)
    for l in live:
        if l not in seen: merged.append(l); seen.add(l)
    state["log"] = merged[-50:]; state["server_ready"] = True; state["scanning"] = BACKGROUND_TASK_RUNNING
    state["dst_active"] = is_dst_now(); state["schedule"] = get_jst_schedule()
    return jsonify(state)

@app.route("/api/run", methods=["POST"])
def api_run():
    data = request.get_json(force=True, silent=True) or {}; phase = data.get("phase", 0)
    def run_bg():
        global BACKGROUND_TASK_RUNNING
        BACKGROUND_TASK_RUNNING = True
        try:
            if phase == 0: phase1_broad_scan(); phase2_rescore(); phase3_crosscheck(); phase4_final_top3()
            elif phase == 1: phase1_broad_scan()
            elif phase == 2: phase2_rescore()
            elif phase == 3: phase3_crosscheck()
            elif phase == 4: phase4_final_top3()
            elif phase == 5: phase5_post_open()
        finally: BACKGROUND_TASK_RUNNING = False
    threading.Thread(target=run_bg, daemon=True).start()
    return jsonify({"status": "started", "phase": phase})

@app.route("/api/reset", methods=["POST"])
def api_reset():
    clear_state(); LOG_BUFFER.clear(); PRICE_HISTORY.clear()
    return jsonify({"status": "reset"})

@app.route("/api/logs")
def api_logs(): return jsonify({"logs": list(LOG_BUFFER)[-100:]})

@app.route("/api/chart/<symbol>")
def api_chart(symbol):
    cached = CHART_CACHE.get(symbol)
    if cached and time.time() < cached["expires"]: return jsonify(cached["data"])
    candles = get_stock_candles(symbol, days=30)
    rows = [{"date": datetime.fromtimestamp(c["timestamp"], TZ_ET).strftime("%m/%d"),
             "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"],
             "volume": c.get("volume",0)} for c in candles]
    result = {"code": symbol, "daily": rows}
    if rows: CHART_CACHE[symbol] = {"data": result, "expires": time.time() + 600}
    return jsonify(result)

@app.route("/api/price_history/<symbol>")
def api_price_history(symbol): return jsonify({"code": symbol, "history": PRICE_HISTORY.get(symbol, [])})

@app.route("/api/price_now/<symbol>")
def api_price_now(symbol): return jsonify(get_realtime_prices([symbol]).get(symbol, {}))

@app.route("/api/order_book/<symbol>")
def api_order_book(symbol): return jsonify(analyze_order_book(symbol))

@app.route("/api/margin")
def api_margin():
    account = get_account_info(); positions = get_positions()
    if not account: return jsonify({"error": "moomoo not connected"})
    syms = [p["symbol"] for p in positions]; cp = get_quotes_batch(syms) if syms else {}
    return jsonify(_calc_margin_deadzone(account, positions, cp) or {"error": "No data"})

# ━━━ Scheduler ━━━
def is_us_trading_day():
    return datetime.now(TZ_ET).weekday() < 5

def scheduled_run_all():
    global SCHEDULED_RUN
    if not is_us_trading_day(): add_log("⏭️ Weekend"); return
    SCHEDULED_RUN = True
    try: phase1_broad_scan(); phase2_rescore(); phase3_crosscheck(); phase4_final_top3()
    finally: SCHEDULED_RUN = False

def scheduled_ph5():
    global SCHEDULED_RUN
    if not is_us_trading_day(): return
    SCHEDULED_RUN = True
    try: phase5_post_open()
    finally: SCHEDULED_RUN = False

def run_scheduler():
    add_log("⏰ Scheduler started (DST auto-detect)")
    sched = get_jst_schedule()
    add_log(f"  DST:{'Summer' if is_dst_now() else 'Winter'} Ph.1:{sched['ph1']} Ph.5:{sched['ph5_1']} JST")
    ran_today = set()
    while True:
        now = datetime.now(TZ_JST); today_str = now.strftime("%Y-%m-%d"); hhmm = now.strftime("%H:%M")
        sched = get_jst_schedule()
        if not any(k.startswith(today_str) for k in ran_today): ran_today = set()
        key = f"{today_str}_{hhmm}"
        if hhmm == sched["ph1"] and key not in ran_today:
            ran_today.add(key); add_log(f"🚀 Scheduled Ph.1-4 ({hhmm} JST)")
            threading.Thread(target=scheduled_run_all, daemon=True).start()
        elif hhmm == sched["ph5_1"] and key not in ran_today:
            ran_today.add(key); add_log(f"🚀 Scheduled Ph.5 ({hhmm} JST)")
            threading.Thread(target=scheduled_ph5, daemon=True).start()
        elif hhmm == sched["ph5_2"] and key not in ran_today:
            ran_today.add(key); add_log(f"🚀 Scheduled Ph.5 re-run ({hhmm} JST)")
            threading.Thread(target=scheduled_ph5, daemon=True).start()
        time.sleep(30)

if __name__ == "__main__":
    sched = get_jst_schedule()
    add_log(f"🚀 US Stock Scanner v2.0 ({'Summer DST' if is_dst_now() else 'Winter'})")
    add_log(f"  Ph.1:{sched['ph1']} Ph.5:{sched['ph5_1']} JST")
    if MOOMOO_AVAILABLE: add_log(f"  moomoo: {MOOMOO_HOST}:{MOOMOO_PORT}")
    else: add_log("  ⚠️ moomoo-api not installed")
    threading.Thread(target=run_scheduler, daemon=True).start()
    add_log("🟢 Boot complete — IDLING")
    add_log("💡 Ph.1 to start / Auto: daily per schedule")
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
