# STOCK SCANNER v1.1 - 日本株暴騰スキャナー (Safari fix)
import os, time, schedule, requests, anthropic, json, zipfile, io, threading, re
try:
    from google import genai as google_genai
    from google.genai import types as genai_types
except Exception:
    google_genai = None
    genai_types  = None
import pytz
from datetime import datetime, timedelta
from flask import Flask, jsonify, request

JQUANTS_API_KEY   = os.environ.get("JQUANTS_API_KEY", "")
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY", "")
FINNHUB_API_KEY   = os.environ.get("FINNHUB_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
NEWS_API_KEY      = os.environ.get("NEWS_API_KEY", "")
X_BEARER_TOKEN    = os.environ.get("X_API_BEARER_TOKEN", "")
EDINET_API_KEY    = os.environ.get("EDINET_API_KEY", "")
NTFY_CHANNEL      = "mitsugu-stock-scanner"
PORT              = int(os.environ.get("PORT", 8080))


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
.logo{color:#74fafd;font-size:17px;font-weight:700;letter-spacing:3px;transition:opacity .12s}.logo:active{opacity:.45}
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
.stock-tabs{display:flex;gap:4px;margin-bottom:8px;flex-wrap:nowrap;overflow-x:auto;-webkit-overflow-scrolling:touch;touch-action:pan-x}
.tab-btn{padding:5px 10px;background:#242424;border:1px solid #333;border-radius:3px;cursor:pointer;color:#4a4a4a;font-size:10px;font-family:"JetBrains Mono",monospace;transition:all .15s;touch-action:manipulation;-webkit-tap-highlight-color:transparent}
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
  <div><div class="logo" id="logoBtn" onclick="location.reload()" style="cursor:pointer;user-select:none;-webkit-tap-highlight-color:transparent;transition:opacity .1s">STOCK SCANNER</div><div class="sub">日本株暴騰スキャナー v1.1</div></div>
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
  <button class="ph-btn" id="bReset" onclick="resetScan()" style="border-color:#666;color:#888">&#8635;<br>リセット</button>
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
<div class="grid2" style="margin-top:6px">
  <div class="info-box" id="finnhubBox" style="display:none">
    <div class="info-lbl">&#128200; VIX / S&amp;P500</div>
    <div class="info-val" id="finnhubVal">-</div>
  </div>
  <div class="info-box" id="finnhubAlertBox" style="display:none">
    <div class="info-lbl" style="color:#f44747">&#9888; Macro Alert</div>
    <div class="info-val" id="finnhubAlert" style="color:#f44747;font-size:11px">-</div>
  </div>
</div>
<div class="lbl">-- SCAN LOG --</div>
<div class="log-box" id="log"><span style="color:#3d9ea1">起動中...<span class="cursor"></span></span></div>
<div class="lbl" style="margin-top:14px">-- 本日の候補銘柄 --</div>
<div class="stock-tabs" id="stockTabs"></div>
<div id="stockList"><div style="color:#4a4a4a;font-size:11px;padding:12px">スキャン結果がありません。</div></div>
<script>
var sel=null,busy=false,sentOpen=false,curTab=4,lastState={},userChoseTab=false;
var scanningPhase=0; // 実行中のフェーズ番号（0=待機中）
var serverBootedOnce=false; // 一度でも起動完了したらtrue（リセット後も維持）
var scanStartTime=0;
var progressInterval=null;

// スキャン進捗の概算時間（秒）
var phaseEstimates={1:90,2:60,3:60,4:45,5:30,0:300};

// フェーズ名（バッジ表示用）
var phaseNames={1:'Broad Scan',2:'Re-Score',3:'Cross-Check',4:'TOP3 Final',5:'Post-Open',0:'Scanning'};
// フェーズのアクション名（英語）
var phaseActions={
  1:['Fetching stocks','Sentinel check','AI analyzing','Narrowing down'],
  2:['Re-scoring','AI analyzing','Ranking'],
  3:['Cross-checking','Philosophy score','Gemini checking','Finalizing'],
  4:['Selecting TOP3','Gemini verify'],
  5:['Fetching prices','Analyzing momentum'],
  0:['Scanning']
};

function startProgressTimer(phaseId){
  scanningPhase=phaseId;
  scanStartTime=Date.now();
  if(progressInterval)clearInterval(progressInterval);
  progressInterval=setInterval(function(){
    var elapsed=(Date.now()-scanStartTime)/1000;
    // scanningPhaseはrun()のポーリングで更新される
    var displayPhase=scanningPhase>0?scanningPhase:phaseId;
    var estimate=phaseEstimates[displayPhase]||90;
    var pct=Math.min(95,Math.round(elapsed/estimate*100));
    var badge=document.getElementById('statusBadge');
    if(badge&&scanningPhase>0){
      var spinner='<span style="display:inline-block;width:7px;height:7px;border:2px solid #333;border-top-color:#74fafd;border-radius:50%;animation:spin .6s linear infinite;vertical-align:middle;margin-right:4px"></span>';
      // アクション名をpct進捗に応じて切り替え
      var actions=phaseActions[displayPhase]||['処理中'];
      var actionIdx=Math.min(Math.floor(pct/100*actions.length), actions.length-1);
      var actionLabel=actions[actionIdx];
      var label='Ph.'+displayPhase+' '+actionLabel;
      badge.innerHTML=spinner+label+' '+pct+'%';
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
  var days=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  var months=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  var dow=jst.getUTCDay();
  var dateStr=days[dow]+', '+months[jst.getUTCMonth()]+' '+jst.getUTCDate();
  document.getElementById('clk').textContent=dateStr+'  '+t;
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

// 8秒ごとに自動でstate取得（チャート描画中の重複を緩和）
setInterval(function(){
  if(!busy)fetchState();
},8000);

// ロゴのタップフィードバック（モバイル対応）
(function(){
  var logo=document.getElementById('logoBtn');
  if(!logo)return;
  logo.addEventListener('touchstart',function(){logo.style.opacity='0.4';},{passive:true});
  logo.addEventListener('touchend',function(){setTimeout(function(){logo.style.opacity='';},150);},{passive:true});
})();

document.getElementById('sentHdr').addEventListener('click',function(){
  sentOpen=!sentOpen;
  document.getElementById('sentBody').classList.toggle('open',sentOpen);
  document.getElementById('sentArr').innerHTML=sentOpen?'&#9650;':'&#9660;';
});

document.querySelectorAll('[data-phase]').forEach(function(btn){
  btn.addEventListener('click',function(){run(parseInt(this.dataset.phase));});
});

// タブクリック: 親要素に1回だけ登録（Event Delegation）→ Safari/iOS対応
document.getElementById('stockTabs').addEventListener('click',function(e){
  var btn=e.target;
  while(btn&&btn!==this&&!btn.dataset.tab) btn=btn.parentNode;
  if(!btn||!btn.dataset.tab) return;
  stopPh5Interval();
  curTab=parseInt(btn.dataset.tab);
  userChoseTab=true;
  render(lastState);
});

async function resetScan(){
  if(busy)return;
  if(!confirm('スキャンデータをリセットしPh.1からやり直しますか？'))return;
  try{
    await fetch('/api/reset',{method:'POST'});
    sel=null;curTab=1;lastState={};userChoseTab=false;destroyCharts();
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
    var cls=p.id<=cp?'done':(scanningPhase===p.id?'scanning':scanningPhase>0&&p.id<scanningPhase?'done':'pending');
    // フェーズバーは名前のみ（%は右上バッジに統一）
    // ph-dotのCSSアニメーションのみ使用（scanDot不要）
    return '<div class="ph '+cls+'"><div class="ph-time"><span class="ph-dot"></span>'+p.time+'</div><div class="ph-name">'+p.label+'</div><div class="ph-cnt">'+p.count+'</div></div>';
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

  // Finnhubマクロ表示（相対的急騰ベース）
  var fh = d.finnhub_macro;
  var finnhubBox = document.getElementById('finnhubBox');
  var finnhubAlertBox = document.getElementById('finnhubAlertBox');
  if(fh && fh.vix !== null){
    finnhubBox.style.display = '';
    var vixStr = fh.vix !== null ? fh.vix : 'N/A';
    var avgStr = fh.vix_20d_avg !== null ? '20dAvg:'+fh.vix_20d_avg : '';
    var spikeVal = fh.vix_spike_pct;
    var spikeStr = spikeVal !== null ? (spikeVal>=0?'+':'')+spikeVal+'% vs avg' : '';
    var sp5Val = fh.sp500_change !== null ? (fh.sp500_change>0?'+':'')+fh.sp500_change+'%' : 'N/A';
    var fearLevel = fh.fear_level || 'NORMAL';
    // 色と絵文字
    var fearColor = fearLevel==='SPIKE'?'#f44747':fearLevel==='ELEVATED'?'#ce9178':fearLevel==='CALM'?'#4ec94e':'#4ec94e';
    var fearIcon  = fearLevel==='SPIKE'?'🚨':fearLevel==='ELEVATED'?'⚠️':fearLevel==='CALM'?'😌':'✅';
    document.getElementById('finnhubVal').innerHTML =
      'VIX: <span style="font-weight:700">'+vixStr+'</span>'+
      (spikeStr?' <span style="color:'+fearColor+'">'+fearIcon+spikeStr+'</span>':'')+
      (avgStr?' <span style="color:#4a4a4a;font-size:10px">'+avgStr+'</span>':'')+
      ' &nbsp; S&amp;P500: <span style="color:'+(fh.sp500_change<=-1?'#f44747':fh.sp500_change>=0?'#4ec94e':'#c8c8c8')+'">'+sp5Val+'</span>';
    if(fh.alerts && fh.alerts.length > 0){
      finnhubAlertBox.style.display = '';
      document.getElementById('finnhubAlert').textContent = fh.alerts.join(' | ');
    } else {
      finnhubAlertBox.style.display = 'none';
    }
  } else {
    finnhubBox.style.display = 'none';
    finnhubAlertBox.style.display = 'none';
  }

  // ステータスバッジ更新（スキャン中でない時）
  var badge=document.getElementById('statusBadge');
  if(badge&&scanningPhase===0){
    if(d.server_ready) serverBootedOnce=true;
    if(!serverBootedOnce&&!d.server_ready&&d.boot_pct!==undefined&&d.boot_pct<100){
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

  // タブ自動前進（手動選択していない場合のみ）
  var autoTab=cp>=5&&tabs.find(function(t){return t.id===5;})?5:
              cp>=4&&tabs.find(function(t){return t.id===4;})?4:
              cp>=3&&tabs.find(function(t){return t.id===3;})?3:
              cp>=2&&tabs.find(function(t){return t.id===2;})?2:
              cp>=1&&tabs.find(function(t){return t.id===1;})?1:curTab;
  if(!tabs.find(function(t){return t.id===curTab;})){
    curTab=autoTab; userChoseTab=false;
  } else if(!userChoseTab && autoTab>curTab){
    curTab=autoTab;
  }

  document.getElementById('stockTabs').innerHTML=tabs.map(function(t){
    var extra=t.isPh5?' style="border-color:#f0a500;color:#f0a500"':'';
    return '<button class="tab-btn'+(t.id===curTab?' active':'')+'" data-tab="'+t.id+'"'+extra+'>'+t.label+'</button>';
  }).join('');

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
  Object.keys(chartInstances).forEach(function(k){
    try{chartInstances[k].destroy();}catch(e){}
  });
  chartInstances={};
}

var ph5PriceInterval=null;
function stopPh5Interval(){
  if(ph5PriceInterval){clearInterval(ph5PriceInterval);ph5PriceInterval=null;}
}

function renderPh5Tab(d){
  // スキップ: データ変化なし かつ stockListにPh.5のHTMLが既に表示されている場合のみ
  var ph5Key=JSON.stringify((d.post_open_result||{}).evaluations||[]);
  var firstCode=(d.top3_final||[{}])[0].code;
  var alreadyRendered=!!firstCode&&!!document.getElementById('chart_'+firstCode)
                      &&!!document.getElementById('rsi_wrap_'+firstCode);
  if(renderPh5Tab._lastKey===ph5Key && alreadyRendered){
    return;
  }
  renderPh5Tab._lastKey=ph5Key;
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
      // 時間足切り替えボタン（onclickなし、data属性でJS処理）
      +'<div style="padding:4px 12px 4px;display:flex;gap:5px;align-items:center;border-top:1px solid #2a2a2a">'
      +'<span style="font-size:9px;color:#4a4a4a;margin-right:2px">足:</span>'
      +'<button class="chart-type-btn" id="btn_daily_'+code+'" data-code="'+code+'" data-type="daily" style="font-family:monospace;font-size:9px;padding:2px 8px;background:#1e3a3a;border:1px solid #74fafd;color:#74fafd;border-radius:2px;cursor:pointer">1日足</button>'
      +'<button class="chart-type-btn" id="btn_5min_'+code+'" data-code="'+code+'" data-type="5min" style="font-family:monospace;font-size:9px;padding:2px 8px;background:#2a2a2a;border:1px solid #333;color:#4a4a4a;border-radius:2px;cursor:pointer">5分足</button>'
      +'</div>'
      // インジケーターボタン（日足）
      +'<div id="ind_daily_'+code+'" style="padding:3px 12px 4px;display:flex;gap:5px;flex-wrap:wrap">'
      +'<span style="font-size:9px;color:#4a4a4a;margin-right:2px">表示:</span>'
      +'<button class="ind-btn" id="ind_bb_'+code+'" data-code="'+code+'" data-ftype="daily" data-ind="bb" style="font-family:monospace;font-size:9px;padding:2px 7px;background:#1a2a3a;border:1px solid #74fafd;color:#74fafd;border-radius:2px;cursor:pointer">BB</button>'
      +'<button class="ind-btn" id="ind_ma_'+code+'" data-code="'+code+'" data-ftype="daily" data-ind="ma" style="font-family:monospace;font-size:9px;padding:2px 7px;background:#1a2a3a;border:1px solid #74fafd;color:#74fafd;border-radius:2px;cursor:pointer">MA</button>'
      +'<button class="ind-btn" id="ind_rsi_'+code+'" data-code="'+code+'" data-ftype="daily" data-ind="rsi" style="font-family:monospace;font-size:9px;padding:2px 7px;background:#1a2a3a;border:1px solid #74fafd;color:#74fafd;border-radius:2px;cursor:pointer">RSI</button>'
      +'</div>'
      // インジケーターボタン（5分足）
      +'<div id="ind_5min_'+code+'" style="padding:3px 12px 4px;display:none;flex-wrap:wrap;gap:5px">'
      +'<span style="font-size:9px;color:#4a4a4a;margin-right:2px">表示:</span>'
      +'<button class="ind-btn" id="ind5_bb_'+code+'" data-code="'+code+'" data-ftype="5min" data-ind="bb" style="font-family:monospace;font-size:9px;padding:2px 7px;background:#1a2a3a;border:1px solid #74fafd;color:#74fafd;border-radius:2px;cursor:pointer">BB</button>'
      +'<button class="ind-btn" id="ind5_ma_'+code+'" data-code="'+code+'" data-ftype="5min" data-ind="ma" style="font-family:monospace;font-size:9px;padding:2px 7px;background:#1a2a3a;border:1px solid #74fafd;color:#74fafd;border-radius:2px;cursor:pointer">MA</button>'
      +'<button class="ind-btn" id="ind5_rsi_'+code+'" data-code="'+code+'" data-ftype="5min" data-ind="rsi" style="font-family:monospace;font-size:9px;padding:2px 7px;background:#1a2a3a;border:1px solid #74fafd;color:#74fafd;border-radius:2px;cursor:pointer">RSI</button>'
      +'</div>'
      // メインチャートCanvas
      +'<div style="padding:0 12px 4px;position:relative;height:170px">'
      +'<canvas id="chart_'+code+'"></canvas>'
      +'</div>'
      // RSICanvas（トグルで表示/非表示）
      +'<div id="rsi_wrap_'+code+'" style="padding:0 12px 10px;position:relative;height:100px">'
      +'<div style="font-size:9px;color:#888;margin-bottom:2px">RSI(14)</div>'
      +'<canvas id="rsi_'+code+'" style="display:block;width:100%;height:78px"></canvas>'
      +'</div>'
      +'</div>';
  });

  if(!top3.length) html='<div style="color:#4a4a4a;font-size:11px;padding:12px">Ph.4完了後にPh.5を実行してください</div>';
  document.getElementById('stockList').innerHTML=html;

  // 足切り替えボタンのイベント登録
  document.querySelectorAll('.chart-type-btn').forEach(function(btn){
    btn.addEventListener('click',function(){
      switchChartType(this.dataset.code, this.dataset.type);
    });
  });
  // インジケータトグルボタンのイベント登録
  document.querySelectorAll('.ind-btn').forEach(function(btn){
    btn.addEventListener('click',function(){
      toggleIndicator(this.dataset.code, this.dataset.ftype, this.dataset.ind);
    });
  });

  // 各銘柄を100ms間隔でずらしてロード（UI詰まり防止）
  top3.forEach(function(s,i){
    initChartState(s.code);
    setTimeout(function(){ loadChart(s.code,'daily'); }, i*150);
  });

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
// インジケータ表示状態 {code: {daily:{bb,ma,rsi}, 5min:{bb,ma,rsi}}}
var indState={};
// 現在の足種 {code: 'daily'|'5min'}
var chartType={};

function initChartState(code){
  if(!indState[code]) indState[code]={'daily':{bb:true,ma:true,rsi:true},'5min':{bb:true,ma:true,rsi:true}};
  if(!chartType[code]) chartType[code]='daily';
}

function switchChartType(code,type){
  chartType[code]=type;
  // 足切り替えボタンのスタイル更新
  var on='background:#1e3a3a;border:1px solid #74fafd;color:#74fafd';
  var off='background:#2a2a2a;border:1px solid #333;color:#4a4a4a';
  var bd=document.getElementById('btn_daily_'+code);
  var b5=document.getElementById('btn_5min_'+code);
  if(bd)bd.style.cssText='font-family:monospace;font-size:9px;padding:2px 8px;border-radius:2px;cursor:pointer;'+(type==='daily'?on:off);
  if(b5)b5.style.cssText='font-family:monospace;font-size:9px;padding:2px 8px;border-radius:2px;cursor:pointer;'+(type==='5min'?on:off);
  // インジケータボタン表示切り替え
  var id=document.getElementById('ind_daily_'+code);
  var i5=document.getElementById('ind_5min_'+code);
  if(id)id.style.display=type==='daily'?'flex':'none';
  if(i5)i5.style.display=type==='5min'?'flex':'none';
  loadChart(code,type);
}

function toggleIndicator(code,type,ind){
  initChartState(code);
  indState[code][type][ind]=!indState[code][type][ind];
  var on=indState[code][type][ind];
  var prefix=type==='daily'?'ind_':'ind5_';
  var btn=document.getElementById(prefix+ind+'_'+code);
  if(btn){
    btn.style.background=on?'#1a2a3a':'#2a2a2a';
    btn.style.borderColor=on?'#74fafd':'#333';
    btn.style.color=on?'#74fafd':'#4a4a4a';
  }
  // RSIラッパー表示/非表示
  if(ind==='rsi'){
    var rw=document.getElementById('rsi_wrap_'+code);
    if(rw)rw.style.display=on?'block':'none';
  }
  loadChart(code,type);
}

function calcMA(arr,n){
  return arr.map(function(_,i){
    if(i<n-1)return null;
    var s=arr.slice(i-n+1,i+1).reduce(function(a,b){return a+b;},0);
    return Math.round(s/n*10)/10;
  });
}
function calcBB(arr,n){
  var mid=calcMA(arr,n);
  var upper=arr.map(function(_,i){
    if(i<n-1)return null;
    var sl=arr.slice(i-n+1,i+1),m=mid[i];
    var sd=Math.sqrt(sl.reduce(function(s,v){return s+(v-m)*(v-m);},0)/n);
    return Math.round((m+2*sd)*10)/10;
  });
  var lower=arr.map(function(_,i){
    if(i<n-1)return null;
    var sl=arr.slice(i-n+1,i+1),m=mid[i];
    var sd=Math.sqrt(sl.reduce(function(s,v){return s+(v-m)*(v-m);},0)/n);
    return Math.round((m-2*sd)*10)/10;
  });
  return {mid:mid,upper:upper,lower:lower};
}
function calcRSI(arr,n){
  return arr.map(function(_,i){
    if(i<n)return null;
    var gains=0,losses=0;
    for(var j=i-n+1;j<=i;j++){var d=arr[j]-arr[j-1];if(d>0)gains+=d;else losses-=d;}
    if(losses===0)return 100;
    return Math.round((100-100/(1+gains/losses))*10)/10;
  });
}

function buildMainChart(ctx,labels,closes,indCfg){
  // Safari向け: canvasのdisplayをblockに強制
  ctx.style.display='block';
  var datasets=[];
  if(indCfg.bb){
    var bband=calcBB(closes,Math.min(20,closes.length));
    datasets.push({label:'BB上限',data:bband.upper,borderColor:'rgba(116,250,253,0.35)',borderWidth:1,pointRadius:0,fill:'+1',backgroundColor:'rgba(116,250,253,0.05)'});
    datasets.push({label:'BB中心',data:bband.mid,borderColor:'rgba(116,250,253,0.55)',borderWidth:1,borderDash:[3,3],pointRadius:0,fill:false});
    datasets.push({label:'BB下限',data:bband.lower,borderColor:'rgba(116,250,253,0.35)',borderWidth:1,pointRadius:0,fill:false});
  }
  datasets.push({label:'価格',data:closes,borderColor:'#c8c8c8',borderWidth:2,pointRadius:0,fill:false});
  if(indCfg.ma){
    var n5=Math.min(5,closes.length),n25=Math.min(25,closes.length);
    datasets.push({label:'MA'+n5,data:calcMA(closes,n5),borderColor:'#f0a500',borderWidth:1.5,pointRadius:0,fill:false});
    datasets.push({label:'MA'+n25,data:calcMA(closes,n25),borderColor:'#4ec94e',borderWidth:1.5,pointRadius:0,fill:false});
  }
  if(chartInstances['main_'+ctx.id])try{chartInstances['main_'+ctx.id].destroy();}catch(e){}
  chartInstances['main_'+ctx.id]=new Chart(ctx,{
    type:'line',data:{labels:labels,datasets:datasets},
    options:{responsive:true,maintainAspectRatio:false,animation:false,
      plugins:{legend:{display:false}},
      scales:{
        x:{ticks:{color:'#4a4a4a',font:{size:9},maxTicksLimit:8},grid:{color:'#2a2a2a'}},
        y:{position:'right',ticks:{color:'#4a4a4a',font:{size:9}},grid:{color:'#2a2a2a'}}
      }
    }
  });
}

function buildRSIChart(rctx,labels,closes){
  var rsiData=calcRSI(closes,Math.min(14,closes.length-1));
  if(chartInstances['rsi_'+rctx.id])try{chartInstances['rsi_'+rctx.id].destroy();}catch(e){}
  // Safari向け: canvasのdisplayをblockに強制
  rctx.style.display='block';
  // 70/30ラインをデータセットとして追加（grid.colorコールバック不要）
  var l70=labels.map(function(){return 70;});
  var l30=labels.map(function(){return 30;});
  chartInstances['rsi_'+rctx.id]=new Chart(rctx,{
    type:'line',
    data:{labels:labels,datasets:[
      {label:'RSI',data:rsiData,borderColor:'#f0a500',borderWidth:2,pointRadius:0,fill:false,tension:0,order:1},
      {label:'70',data:l70,borderColor:'rgba(244,71,71,0.5)',borderWidth:1,borderDash:[4,4],pointRadius:0,fill:false,tension:0,order:2},
      {label:'30',data:l30,borderColor:'rgba(116,250,253,0.4)',borderWidth:1,borderDash:[4,4],pointRadius:0,fill:false,tension:0,order:3}
    ]},
    options:{responsive:true,maintainAspectRatio:false,animation:false,
      plugins:{legend:{display:false}},
      scales:{
        x:{display:false},
        y:{position:'right',min:0,max:100,
          ticks:{color:'#888',font:{size:9},stepSize:50},
          grid:{color:'#1e1e1e'}
        }
      }
    }
  });
}

function loadChart(code,type){
  initChartState(code);
  var indCfg=indState[code][type]||{bb:true,ma:true,rsi:true};
  var ctx=document.getElementById('chart_'+code);
  var rctx=document.getElementById('rsi_'+code);
  if(!ctx||!rctx)return;

  if(type==='daily'){
    fetch('/api/chart/'+code).then(function(r){return r.json();}).then(function(data){
      var rows=data.daily||[];
      if(!rows.length){ctx.getContext('2d').fillStyle='#4a4a4a';ctx.getContext('2d').fillText('データなし',10,80);return;}
      var labels=rows.map(function(r){return r.date;});
      var closes=rows.map(function(r){return r.close;});
      buildMainChart(ctx,labels,closes,indCfg);
      if(indCfg.rsi)buildRSIChart(rctx,labels,closes);
    }).catch(function(e){console.error('chart err',e);});
  } else {
    // 5分足: PRICE_HISTORYから取得
    fetch('/api/price_history/'+code).then(function(r){return r.json();}).then(function(data){
      var hist=data.history||[];
      // 既存チャートを破棄
      if(chartInstances['main_'+ctx.id])try{chartInstances['main_'+ctx.id].destroy();}catch(e){}
      if(chartInstances['rsi_'+rctx.id])try{chartInstances['rsi_'+rctx.id].destroy();}catch(e){}
      if(hist.length<2){
        // データなしメッセージ
        ctx.width=ctx.width; // clear
        var c2d=ctx.getContext('2d');
        c2d.fillStyle='#4a4a4a'; c2d.font='11px monospace';
        c2d.fillText('5分足データなし（当日09:05以降に自動蓄積）',10,90);
        c2d.fillText('現在 '+hist.length+'件',10,110);
        return;
      }
      var labels=hist.map(function(h){return h.time;});
      var closes=hist.map(function(h){return h.price;});
      buildMainChart(ctx,labels,closes,indCfg);
      if(indCfg.rsi)buildRSIChart(rctx,labels,closes);
    }).catch(function(e){console.error('5min chart err',e);});
  }
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
      // np=完了フェーズ番号 → 次の実行中フェーズ = np+1
      if(np>0&&np>=scanningPhase){
        scanningPhase=np<5?np+1:5; // Ph.5は5のまま
        scanStartTime=Date.now();
      }
      var ph5done=(id===5)&&(d2.post_open_result!=null||np>=5);
      var done;
      if(id===0){
        done=np>=4;
      } else if(id<prevPhase){
        // 過去フェーズ再実行: phaseがidに達したら完了
        // prevPhase>idなので「np===id」が確認できたらOK
        // ただしバックエンドが完了前にprevPhaseのままのこともあるので
        // phaseがidになったか、または一度下がってからidに達したかを確認
        done=np===id;
      } else {
        done=ph5done||(np>prevPhase||np>=id);
      }
      if(done)break;
    }
  }catch(e){}
  stopProgressTimer();busy=false;
  document.querySelectorAll('[data-phase]').forEach(function(b){
    var pid=parseInt(b.dataset.phase);
    b.innerHTML=btnLabels[pid];b.disabled=false;
  });
  // 完了後: そのフェーズの結果タブに切り替え（All Ph.は除く）
  if(id>0&&id<=5){
    var targetTab=id;
    // Ph.5タブは存在する時のみ
    curTab=targetTab;
    userChoseTab=true;
    // Ph.5以外ならPh.5タブのキャッシュをクリアして再描画を促す
    if(id!==5) renderPh5Tab._lastKey=null;
  }
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
claude     = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
STATE_FILE = "/tmp/scan_state.json"
NASHI="なし"
BAR_FULL="█"
BAR_LIGHT="░"
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

GEMINI_MODEL = "gemini-2.5-flash"

def gemini_score_top5(top5, state):
    """Ph.3.5: Geminiがリアルタイム情報でTOP5を評価してスコア付加"""
    if not GEMINI_API_KEY or not google_genai:
        add_log("[Gemini] スキップ（APIキー未設定）")
        return {s.get("code",""): {"gemini_score": 50, "red_flag": None, "news_sentiment": "NEUTRAL", "one_line": "スキップ"} for s in top5}
    try:
        client = google_genai.Client(api_key=GEMINI_API_KEY)
        stocks_lines = []
        for s in top5:
            line = "- " + s.get("code","") + " " + s.get("name","")
            line += " Claude評価:" + str(s.get("final_score",s.get("score",0))) + "/100"
            line += " 根拠:" + s.get("buy_reason","")[:60]
            stocks_lines.append(line)
        stocks_text = chr(10).join(stocks_lines)

        news_raw = state.get("news", [])
        leak_lines = ["  [LEAK] " + n.get("title","") for n in news_raw if isinstance(n,dict) and n.get("is_leak")]
        leak_text = chr(10).join(leak_lines[:5]) if leak_lines else "なし"

        # Finnhubマクロデータをテキスト化（相対的急騰ベース）
        finnhub = state.get("finnhub_macro", {})
        finnhub_parts = []
        if finnhub.get("vix"):
            vix_info = "VIX:" + str(finnhub["vix"])
            if finnhub.get("vix_20d_avg"):
                vix_info += "(20dAvg:" + str(finnhub["vix_20d_avg"]) + ")"
            if finnhub.get("vix_spike_pct") is not None:
                sp = finnhub["vix_spike_pct"]
                vix_info += " spike:" + ("+" if sp >= 0 else "") + str(sp) + "%"
            vix_info += " [" + finnhub.get("fear_level","?") + "]"
            finnhub_parts.append(vix_info)
        if finnhub.get("sp500_change") is not None:
            finnhub_parts.append("S&P500:" + str(finnhub["sp500_change"]) + "%")
        for alert in finnhub.get("alerts", []):
            finnhub_parts.append(alert)
        finnhub_text = " | ".join(finnhub_parts) if finnhub_parts else "Finnhub N/A"

        prompt = (
            "あなたは日本株の冷徹なリスク管理番兵です。" + chr(10) + chr(10) +
            "【Claudeが選んだTOP5銘柄（あなたが評価する対象）】" + chr(10) + stocks_text + chr(10) + chr(10) +
            "【OSINTリーク検知ニュース】" + chr(10) + leak_text + chr(10) + chr(10) +
            "【Finnhubリアルタイム数値データ】" + chr(10) + finnhub_text + chr(10) + chr(10) +
            "【マクロ状況】" + state.get("market_condition","") + " / " + state.get("macro_summary","") + chr(10) + chr(10) +
            "【任務】Google検索で各銘柄の最新情報（日本語・英語・中国語）を調べてください。" + chr(10) +
            "各銘柄に0-100のgemini_scoreを付けてください（高いほど買い推奨）。" + chr(10) +
            "VIX spikeが+30%以上またはS&P500が-2%以下の場合は全銘柄のgemini_scoreを最大60に制限してください。" + chr(10) +
            "red_flagは【重大なネガティブ材料がある場合のみ】記載。情報がない・不明・懸念程度ならnullにしてください。" + chr(10) +
            "red_flagの例：決算大幅悪化・不正会計・上場廃止リスク・主力製品の販売停止・重大訴訟。" + chr(10) +
            "単なる赤字経営・株価下落・情報不足はred_flagではありません。" + chr(10) + chr(10) +
            '【出力形式（JSONのみ・余分なテキスト不要）】' + chr(10) +
            '{"stocks":[{"code":"銘柄コード","gemini_score":75,"red_flag":null,"news_sentiment":"POSITIVE","one_line":"一言20字以内"}],' +
            '"macro_alert":"マクロリスク一言","overall_verdict":"BUY"}'
        )

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
                temperature=1.0,
            )
        )
        # Google Search Grounding使用時はpartsからテキストのみ抽出
        raw_text = ""
        try:
            for part in response.candidates[0].content.parts:
                if hasattr(part, 'text') and part.text:
                    raw_text += part.text
        except Exception:
            raw_text = response.text or ""
        result = safe_json(raw_text)
        if not result:
            add_log("[Gemini] JSONパース失敗 raw:" + raw_text[:100])
            return {}
        # code→評価のマップに変換（コードの前4桁でもマッチ）
        score_map = {}
        for item in result.get("stocks", []):
            raw_code = str(item.get("code","")).strip()
            # 先頭4桁のみ抽出（Geminiが"6740 ジャパンディスプレイ"等を返す場合に対応）
            code_key = raw_code[:4] if len(raw_code) >= 4 else raw_code
            score_map[code_key] = item
        add_log("Gemini score_map keys: " + str(list(score_map.keys()))[:80])
        verdict = result.get("overall_verdict","?")
        macro_a = result.get("macro_alert","")
        add_log("Gemini Ph.3.5: " + verdict + " | マクロ: " + macro_a[:40])
        # red_flagがある銘柄をログ
        for code, info in score_map.items():
            if info.get("red_flag"):
                add_log("⚠️ Gemini警告 " + code + ": " + str(info["red_flag"])[:60])
        return score_map
    except Exception as e:
        add_log("[Gemini ERROR] " + str(e)[:100])
        return {}

def gemini_double_check(top3, state):
    """Ph.4後: Geminiの最終確認（後方互換のため残す）"""
    return None


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

# OSINTリークキーワード定義（f-string外で定義してバックスラッシュ問題回避）

def get_finnhub_macro():
    """Finnhub APIでVIX・S&P500取得 + 20日平均との比較で相対的急騰を検知"""
    if not FINNHUB_API_KEY:
        return {"vix": None, "vix_20d_avg": None, "vix_spike_pct": None,
                "sp500_change": None, "fear_level": "unknown", "alerts": []}

    result = {"vix": None, "vix_20d_avg": None, "vix_spike_pct": None,
              "sp500_change": None, "fear_level": "NORMAL", "alerts": []}
    try:
        # VIX当日値
        r = requests.get("https://finnhub.io/api/v1/quote",
            params={"symbol": "^VIX", "token": FINNHUB_API_KEY}, timeout=6)
        if r.status_code == 200:
            vix = r.json().get("c", 0)
            result["vix"] = round(vix, 2)
    except: pass

    try:
        # VIX過去30日の履歴データを取得して20日平均を計算
        import time as _time
        now_ts = int(_time.time())
        from30d_ts = now_ts - 30 * 86400
        rh = requests.get("https://finnhub.io/api/v1/indicator",
            params={"symbol": "^VIX", "resolution": "D",
                    "from": from30d_ts, "to": now_ts,
                    "indicator": "sma", "timeperiod": 20,
                    "token": FINNHUB_API_KEY}, timeout=8)
        if rh.status_code == 200:
            sma_vals = rh.json().get("sma", [])
            # 最新の有効な値を20日平均として使用
            valid = [v for v in sma_vals if v is not None and v > 0]
            if valid:
                avg20 = round(valid[-1], 2)
                result["vix_20d_avg"] = avg20
                if result["vix"] and avg20 > 0:
                    spike_pct = round((result["vix"] - avg20) / avg20 * 100, 1)
                    result["vix_spike_pct"] = spike_pct
                    # 相対的急騰で判断（絶対値ではない）
                    if spike_pct >= 30:
                        result["fear_level"] = "SPIKE"
                        result["alerts"].append(
                            "VIX SPIKE +" + str(spike_pct) + "% vs 20d avg(" +
                            str(avg20) + ") - Extreme volatility surge")
                    elif spike_pct >= 15:
                        result["fear_level"] = "ELEVATED"
                        result["alerts"].append(
                            "VIX ELEVATED +" + str(spike_pct) + "% vs 20d avg(" +
                            str(avg20) + ") - Caution")
                    elif spike_pct <= -15:
                        result["fear_level"] = "CALM"
                    else:
                        result["fear_level"] = "NORMAL"
    except: pass

    try:
        # S&P500前日比
        r2 = requests.get("https://finnhub.io/api/v1/quote",
            params={"symbol": "SPY", "token": FINNHUB_API_KEY}, timeout=6)
        if r2.status_code == 200:
            d2 = r2.json()
            prev = d2.get("pc", 1)
            curr = d2.get("c", prev)
            change_pct = round((curr - prev) / prev * 100, 2) if prev else 0
            result["sp500_change"] = change_pct
            if change_pct <= -2.0:
                result["alerts"].append("S&P500 " + str(change_pct) + "% - Risk-off signal")
    except: pass

    if result["alerts"]:
        add_log("Finnhub: " + " | ".join(result["alerts"]))
    else:
        vix_str = str(result["vix"]) if result["vix"] else "N/A"
        avg_str = str(result["vix_20d_avg"]) if result["vix_20d_avg"] else "N/A"
        spike_str = ("+" if (result["vix_spike_pct"] or 0) >= 0 else "") + str(result["vix_spike_pct"]) + "%" if result["vix_spike_pct"] is not None else "N/A"
        add_log("Finnhub: VIX=" + vix_str + " 20dAvg=" + avg_str + " spike=" + spike_str + " " + result["fear_level"])
    return result


LEAK_KW_JA = [
    "関係者によると", "方針を固めた", "見送りへ", "検討に入った",
    "調整に入った", "方向で調整", "協議に入った", "緊急利上げ"
]
LEAK_KW_EN = [
    "sources say", "according to sources", "is considering",
    "emergency rate", "circuit breaker", "breaking:", "unexpected"
]

def get_news():
    """NewsAPI(日英) + NHK/Reuters RSS + Telegramチャンネル + リーク検知"""
    articles = []
    # NewsAPI 英語
    if NEWS_API_KEY:
        try:
            r = requests.get("https://newsapi.org/v2/everything",
                params={"q": "japan stock OR nikkei OR BOJ OR yen",
                        "language": "en", "sortBy": "publishedAt",
                        "pageSize": 20, "apiKey": NEWS_API_KEY}, timeout=8)
            if r.status_code == 200:
                articles += r.json().get("articles", [])
        except Exception: pass
        # NewsAPI 日本語
        try:
            r2 = requests.get("https://newsapi.org/v2/everything",
                params={"q": "日本株 OR 日経 OR 東証 OR 日銀",
                        "language": "jp", "sortBy": "publishedAt",
                        "pageSize": 20, "apiKey": NEWS_API_KEY}, timeout=8)
            if r2.status_code == 200:
                articles += r2.json().get("articles", [])
        except Exception: pass
    # RSSフィード（無料）
    rss_list = [
        ("NHK経済",    "https://www.nhk.or.jp/rss/news/cat4.xml"),
        ("Reuters JP", "https://feeds.reuters.com/reuters/JPBusinessNews"),
    ]
    for name, url in rss_list:
        try:
            r = requests.get(url, timeout=6,
                headers={"User-Agent": "StockScanner/1.1"})
            if r.status_code == 200:
                import re as _re2
                titles = _re2.findall(
                    r"<title>(?:<![CDATA[)?(.*?)(?:]]>)?</title>", r.text)
                for title in titles[1:16]:
                    title = title.strip()
                    if title and len(title) > 4:
                        articles.append({"title": title,
                            "source": {"name": name}, "url": url})
        except Exception: pass
    # Telegram OSINTチャンネル（RSSHub経由）
    tg_list = [
        ("TG:WarMonitor",    "https://rsshub.app/telegram/channel/warmonitor3"),
        ("TG:IntelSlava",    "https://rsshub.app/telegram/channel/intelslava"),
    ]
    for name, url in tg_list:
        try:
            r = requests.get(url, timeout=6,
                headers={"User-Agent": "StockScanner/1.1"})
            if r.status_code == 200:
                import re as _re3
                titles = _re3.findall(
                    r"<title>(?:<![CDATA[)?(.*?)(?:]]>)?</title>", r.text)
                for title in titles[1:6]:
                    title = title.strip()
                    if title and len(title) > 4:
                        articles.append({"title": title,
                            "source": {"name": name}, "url": url})
        except Exception: pass
    # リークキーワード検知
    for art in articles:
        title = art.get("title", "")
        art["is_leak"] = any(kw in title for kw in LEAK_KW_JA + LEAK_KW_EN)
    n_leak = sum(1 for a in articles if a.get("is_leak"))
    if n_leak > 0:
        add_log("OSINT: " + str(n_leak) + "件のリーク疑いニュース検知")
    return articles

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
                    # 業績カタリスト関連キーワード（数値ベース）
                    for kw in ["売上高","上方修正","営業利益","受注残高","純利益","業績予想","増収","最高益"]:
                        idx = text.find(kw)
                        if idx > 0: return text[max(0,idx-100):idx+3000]
                    return text[:5000]
    except: pass
    return None

def score_catalyst(code, company_name, text):
    """EDINET改修: 思想スコアから業績カタリスト抽出に変更"""
    if not text: return 50, "EDINET data unavailable", ""
    try:
        prompt = (
            "Extract earnings catalysts from this Japanese financial document." + chr(10) +
            "Company: " + company_name + " (" + code + ")" + chr(10) +
            "Text: " + text[:2000] + chr(10) + chr(10) +
            "Look for ONLY numeric evidence of strong fundamentals:" + chr(10) +
            "- Upward earnings revision (上方修正) with specific % or amount" + chr(10) +
            "- Revenue growth > 20% YoY" + chr(10) +
            "- Record high sales/profit" + chr(10) +
            "- Major new contract or product launch with numbers" + chr(10) + chr(10) +
            "Score 0-100 based on strength of numeric catalysts ONLY. " +
            "No philosophy. No qualitative statements. Numbers only." + chr(10) +
            'JSON only: {"score":75,"catalyst":"specific numeric evidence","quote":"exact number found"}'
        )
        res = claude.messages.create(model="claude-haiku-4-5-20251001", max_tokens=300,
            messages=[{"role":"user","content": prompt}])
        t = res.content[0].text if res.content else "{}"
        d = safe_json(t)
        return d.get("score",50), d.get("catalyst",""), d.get("quote","")
    except: return 50, "parse failed", ""

# 後方互換性のためエイリアスを残す
def score_philosophy(code, company_name, text):
    return score_catalyst(code, company_name, text)


def get_short_sell_ratio(code):
    """空売り残高比率を取得（JPX公開データ）"""
    try:
        # JPXの空売り残高データ（公開CSVを利用）
        url = "https://www.jpx.co.jp/markets/statistics-equities/short-selling/nlsgeu000000423u-att/2024shortbalance.csv"
        res = requests.get(url, timeout=8, headers={"User-Agent": "StockScanner/1.1"})
        if res.status_code == 200:
            import csv, io as _io
            reader = csv.reader(_io.StringIO(res.text))
            for row in reader:
                if len(row) > 3 and str(code) in row[0]:
                    try:
                        ratio = float(row[3].replace(",","").replace("%",""))
                        return ratio
                    except: pass
    except: pass
    return None

def check_short_sell_kill(code, name):
    """空売り残高が異常に高い銘柄をキル（機関の罠）"""
    ratio = get_short_sell_ratio(code)
    if ratio is None:
        return False, 0
    # 空売り比率15%超 = 機関の強い売り圧力 → キル
    KILL_SHORT_RATIO = 15.0
    if ratio >= KILL_SHORT_RATIO:
        add_log("SHORT KILL " + code + " " + name + ": " + str(ratio) + "% short ratio")
        return True, ratio
    return False, ratio

def sentinel_check(news, twitter):
    # リーク検知ニュースを先頭に
    leaks   = [n for n in news if n.get("is_leak")]
    normals = [n for n in news if not n.get("is_leak")]
    sorted_news = leaks + normals
    news_text = chr(10).join(
        "[LEAK] " + n.get("title","") if n.get("is_leak") else n.get("title","")
        for n in sorted_news[:20])
    twitter_text = "\n".join([f"- {t.get('text','')[:100]}" for t in twitter[:10]])
    try:
        res = claude.messages.create(model="claude-haiku-4-5-20251001", max_tokens=300,
            messages=[{"role":"user","content":
                f"\u5168\u6c7a\u6e08\u30bb\u30f3\u30c1\u30cd\u30eb\u3002\n"
                f"\u30cb\u30e5\u30fc\u30b9:{news_text or NASHI}\nX:{twitter_text or NASHI}\n"
                f"\u5730\u653f\u5b66\u6025\u5909\u30fb\u91d1\u878d\u5371\u6a5f\u30fb\u65e5\u9280\u7dca\u6025\u306e\u307fSELL_ALL\u3002\n"
                f"JSON\u306e\u307f:{{\"action\":\"HOLD\",\"reason\":\"\u7406\u7531\",\"risk_level\":1}}"}])
        t = res.content[0].text if res.content else "{}"
        return safe_json(t)
    except: return {"action":"HOLD","reason":"\u5224\u5b9a\u5931\u6557","risk_level":1}

def push_notify(title, msg, priority="default"):
    if not SCHEDULED_RUN:
        return  # 手動実行時は通知しない
    try:
        requests.post(f"https://ntfy.sh/{NTFY_CHANNEL}",
            data=msg.encode("utf-8"), headers={"Title":title,"Priority":priority})
    except: pass

LOG_BUFFER = []
SCHEDULED_RUN = False   # スケジューラー経由の実行かどうか
PRICE_HISTORY = {}  # {code: [{time, price}, ...]} 5分足用メモリ蓄積
CHART_CACHE = {}    # {code: {data, expires}} チャートデータキャッシュ（10分）


def calc_hold_score(ctx):
    """ホールドスコアを計算（高いほどホールド推奨）"""
    score = 50  # ベース

    # 材料グレードボーナス
    grade_bonus = {"A": 25, "B": 15, "C": 0, "D": -20}
    score += grade_bonus.get(ctx.get("catalyst_grade","C"), 0)

    # 政治テーマボーナス
    score += ctx.get("political_score", 0)

    # VWAP奪回
    if ctx.get("vwap_reclaimed"):
        score += 15
    else:
        score -= 10

    # 出来高方向（下落中に出来高増加=吸収の可能性）
    if ctx.get("volume_increasing_on_drop"):
        score += 10

    # プラ転
    if ctx.get("recovered_to_positive"):
        score += 20

    # マクロリスク
    fear = ctx.get("vix_fear_level", "NORMAL")
    macro_penalty = {"SPIKE": -30, "ELEVATED": -15, "NORMAL": 0, "CALM": 5}
    score += macro_penalty.get(fear, 0)

    # イベントリスク
    score -= ctx.get("event_risk", 0) * 5

    return min(max(score, 0), 100)

def calc_exit_score(ctx):
    """退出スコアを計算（高いほど即時退出）"""
    score = 0

    # 材料崩壊
    if ctx.get("thesis_broken"):
        return 100

    # VWAP奪回失敗回数
    failed = ctx.get("vwap_failed_count", 0)
    score += min(failed * 15, 45)

    # 出来高減少しながら下落（材料剥落）
    if ctx.get("volume_decreasing_on_drop"):
        score += 20

    # マクロショック
    fear = ctx.get("vix_fear_level", "NORMAL")
    if fear == "SPIKE":
        score += 35
    elif fear == "ELEVATED":
        score += 15

    # C/D級材料 + 下落
    if ctx.get("catalyst_grade") in ["C", "D"] and ctx.get("drawdown_pct", 0) < -3:
        score += 20

    # 材料グレードD
    if ctx.get("catalyst_grade") == "D":
        score += 15

    return min(score, 100)

def determine_exit_state(ctx):
    """状態遷移を決定してアクションを返す"""
    hold_score = calc_hold_score(ctx)
    exit_score = calc_exit_score(ctx)
    pnl        = ctx.get("pnl_pct", 0)
    elapsed    = ctx.get("elapsed_min", 0)
    grade      = ctx.get("catalyst_grade", "C")

    # 絶対カット条件
    if ctx.get("thesis_broken") or exit_score >= 90:
        return EXIT_STATE_THESIS_BROKEN, hold_score, exit_score, "EXIT_ALL"

    # 利確条件（パラボラ）
    if pnl >= 10 and ctx.get("momentum_decaying"):
        return EXIT_STATE_PARABOLIC_TAKEPROFIT, hold_score, exit_score, "TAKE_PROFIT"
    if pnl >= 12:  # +12%は無条件利確
        return EXIT_STATE_PARABOLIC_TAKEPROFIT, hold_score, exit_score, "TAKE_PROFIT"

    # 時間切れ（Rule C3: 10:00までに高値更新できない）
    if elapsed >= 60 and pnl <= 0 and grade in ["C", "D"]:
        return EXIT_STATE_THESIS_BROKEN, hold_score, exit_score, "TIME_EXIT"

    # ふるい落としと判定（ホールド）
    if hold_score >= 65 and exit_score < 40:
        return EXIT_STATE_SHAKEOUT_CANDIDATE if pnl < 0 else EXIT_STATE_HEALTHY_UPTREND,                hold_score, exit_score, "HOLD"

    # 健全な上昇
    if hold_score >= 60 and pnl >= 0:
        return EXIT_STATE_HEALTHY_UPTREND, hold_score, exit_score, "HOLD"

    # 配り警戒
    if exit_score >= 60:
        return EXIT_STATE_DISTRIBUTION_WARN, hold_score, exit_score, "WARN"

    return EXIT_STATE_OPEN_DISCOVERY, hold_score, exit_score, "WAIT"

  # {code: [{time, price}, ...]} 5分足用メモリ蓄積
CHART_CACHE = {}    # {code: {data, expires}} チャートデータキャッシュ（10分）

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
    # 空売り残高チェック（機関の罠を除外）
    short_killed = []
    filtered_candidates = []
    for c in candidates:
        is_kill, ratio = check_short_sell_kill(c.get("code",""), c.get("name",""))
        if is_kill:
            short_killed.append(c.get("code",""))
        else:
            filtered_candidates.append(c)
    if short_killed:
        add_log("Short-sell kill: " + ", ".join(short_killed))
    candidates = filtered_candidates
    add_log(f"\u6025\u9a30\u5019\u88dc: {len(candidates)}\u9298\u67c4")
    news     = get_news()
    twitter  = get_twitter_buzz()
    # Finnhubマクロデータ取得
    finnhub_macro = get_finnhub_macro()
    sentinel = sentinel_check(news, twitter)
    risk     = sentinel.get("risk_level",1)
    add_log(f"\u30bb\u30f3\u30c1\u30cd\u30eb: {sentinel.get('action')} {BAR_FULL*risk+BAR_LIGHT*(5-risk)} ({risk}/5)")
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
                f"\u3010\u30cb\u30e5\u30fc\u30b9\u3011{news_text or NASHI}\n"
                f"\u3010X\u3011{twitter_text or NASHI}\n"
                f"\u5fc5\u305aJSON\u306e\u307f\u3067\u56de\u7b54\u3002\u30b3\u30fc\u30c9\u30d6\u30ed\u30c3\u30af\u4e0d\u8981\u3002"
                f"market_condition\u306f\u5f53\u65e5\u306e\u5730\u5408\u3044\u3092\u7c21\u6f54\u306b20\u5b57\u4ee5\u5185\u3067\u8868\u73fe\u3002macro_summary\u306f\u30de\u30af\u30ed\u72b6\u6cc1\u3092\u7c21\u6f54\u306b20\u5b57\u4ee5\u5185\u3067\u3002\n"
                f"{{\"top20\":[{{\"code\":\"4890\",\"name\":\"\u5764\u7530\u30e9\u30dc\",\"score\":92,\"reason\":\"\u7406\u7531\",\"theme\":\"\u30d0\u30a4\u30aa\"}}],"
                f"\"market_condition\":\"\u65e5\u7d4c\u5e73\u5747\u5c0f\u5e45\u9ad8\u3001\u534a\u5c0e\u4f53\u5f37\",\"macro_summary\":\"\u7c73\u56fd\u91d1\u5229\u843d\u3061\u8d85\u3048\u6c17\u5406\u6c17\"}}"}])
        t      = res.content[0].text if res.content else "{}"
        result = safe_json(t)
        top20  = result.get("top20",[])
        add_log(f"\u2705 Ph.1\u5b8c\u4e86 \u2014 {len(top20)}\u9298\u67c4\u3092\u9078\u51fa")
        save_state({"phase":1,"top20":top20,
            "market_condition":result.get("market_condition",""),"finnhub_macro":finnhub_macro,
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
                f"\u5730\u5408\u3044:{state.get('market_condition','')}\n\u30cb\u30e5\u30fc\u30b9:{news_text or NASHI}\n"
                f"\u5fc5\u305aJSON\u306e\u307f:{{\"top10\":[{{\"code\":\"\u30b3\u30fc\u30c9\",\"name\":\"\u540d\u524d\","
                f"\"score\":90,\"reason\":\"\u7406\u7531\",\"risk\":\"\u30ea\u30b9\u30af\",\"confidence\":4}}],"
                f"\"eliminated\":\"\u9664\u5916\u7406\u7531\"}}"}])
        t      = res.content[0].text if res.content else "{}"
        result = safe_json(t)
        top10  = result.get("top10",[])
        add_log(f"\u2705 Ph.2\u5b8c\u4e86 \u2014 {len(top10)}\u9298\u67c4")
        state.update({"phase":2,"top10":top10,"market_condition":state.get("market_condition",""),"macro_summary":state.get("macro_summary",""),"log":LOG_BUFFER[-20:]}); save_state(state)
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
        add_log(f"\U0001f4ca [{c}] {n} Catalyst score...")
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
        # Ph.3.5: GeminiがリアルタイムでTOP5を評価
        add_log("\U0001f916 Ph.3.5: Gemini\u30ea\u30a2\u30eb\u30bf\u30a4\u30e0\u8a55\u4fa1\u4e2d...")
        gemini_scores = gemini_score_top5(top5, state)
        # Geminiスコアを各銘柄に付加 + キルスイッチ判定
        KILL_THRESHOLD = 40   # 40点未満はキルスイッチ
        WARN_THRESHOLD = 60   # 60点未満は警告付き
        killed = []
        for s in top5:
            code = s.get("code","")
            code_key = code[:4] if len(code) >= 4 else code
            gs = gemini_scores.get(code_key, gemini_scores.get(code, {}))
            s["gemini_score"]     = gs.get("gemini_score", 50)
            s["gemini_red_flag"]  = gs.get("red_flag", None)
            s["gemini_sentiment"] = gs.get("news_sentiment", "NEUTRAL")
            s["gemini_one_line"]  = gs.get("one_line", "")
            # キルスイッチ判定
            if s["gemini_red_flag"]:
                # red_flagがある場合は無条件でキル
                s["combined_score"] = 0
                s["kill_switch"] = True
                s["warn_flag"]   = False
                killed.append(code + "(red_flag:" + str(s["gemini_red_flag"])[:30] + ")")
                add_log("KILL " + code + ": red_flag検知 → TOP3除外")
            elif s["gemini_score"] < KILL_THRESHOLD:
                # Geminiスコアが40未満はキル
                s["combined_score"] = 0
                s["kill_switch"] = True
                s["warn_flag"]   = False
                killed.append(code + "(gemini:" + str(s["gemini_score"]) + ")")
                add_log("KILL " + code + ": Gemini" + str(s["gemini_score"]) + "点 → TOP3除外")
            elif s["gemini_score"] < WARN_THRESHOLD:
                # 40〜59点は警告付き採用
                claude_sc = s.get("final_score", s.get("score", 50))
                s["combined_score"] = round(claude_sc * 0.7 + s["gemini_score"] * 0.3, 1)
                s["kill_switch"] = False
                s["warn_flag"]   = True
                add_log("WARN " + code + ": Gemini" + str(s["gemini_score"]) + "点 ⚠️警告付き採用")
            else:
                # 60点以上は正常採用
                claude_sc = s.get("final_score", s.get("score", 50))
                s["combined_score"] = round(claude_sc * 0.7 + s["gemini_score"] * 0.3, 1)
                s["kill_switch"] = False
                s["warn_flag"]   = False
        if killed:
            add_log("キルスイッチ発動: " + ", ".join(killed))
        # 総合スコアで再ソート（キルされた銘柄は0点なので自動的に末尾）
        top5.sort(key=lambda x: x.get("combined_score",0), reverse=True)
        alive = [s for s in top5 if not s.get("kill_switch")]
        add_log("✅ Ph.3.5完了 — 生存:" + str(len(alive)) + "銘柄 / キル:" + str(len(killed)) + "銘柄")
        state.update({"phase":3,"top5":top5,"philosophy":philosophy_results,
            "crosscheck_summary":result.get("crosscheck_summary",""),
            "gemini_scores":gemini_scores,
            "market_condition":state.get("market_condition",""),"macro_summary":state.get("macro_summary",""),
            "log":LOG_BUFFER[-20:]}); save_state(state)
        push_notify("\u26a1 Ph.3\u5b8c\u4e86",
            f"10\u2192{len(top5)}\u9298\u67c4\n{result.get('crosscheck_summary','')}")
    except Exception as e: add_log(f"[ERROR] Ph.3: {e}")

POLITICAL_THEMES = {
    "高市": ["防衛", "半導体", "AI", "エネルギー"],
    "防衛費増額": ["防衛", "重工", "電子"],
    "GX": ["再エネ", "蓄電", "水素", "電力"],
    "半導体補助金": ["半導体", "電子部品", "製造装置"],
    "AI投資": ["AI", "データセンター", "光ファイバー", "電力"],
    "インバウンド": ["観光", "旅行", "ホテル", "百貨店"],
}

def get_political_theme_score(stock):
    """現在の政治・世論テーマとの関連度スコア（0-20点）"""
    name   = stock.get("name", "")
    reason = stock.get("buy_reason", "")
    text   = name + " " + reason
    score  = 0
    matched_themes = []
    for theme, sectors in POLITICAL_THEMES.items():
        if any(sector in text for sector in sectors):
            score += 5
            matched_themes.append(theme)
    return min(score, 20), matched_themes


def classify_catalyst_grade(stock):
    """材料グレードA/B/C/Dを判定（出口戦略に使用）"""
    buy_reason = stock.get("buy_reason", "")
    name       = stock.get("name", "")
    score      = stock.get("catalyst_score", stock.get("final_score", 50))

    # A級：純粋な業績系カタリスト
    a_keywords = ["上方修正", "増配", "自社株買い", "最高益", "増収増益",
                  "受注", "通期上振れ", "決算", "業績", "売上高", "営業利益"]
    # B級：業績+テーマ混合
    b_keywords = ["AI", "半導体", "防衛", "再エネ", "DX", "データセンター",
                  "GX", "電力", "インフラ", "成長", "拡大"]
    # C級：テーマ・思惑中心
    c_keywords = ["期待", "テーマ", "注目", "話題", "思惑", "材料",
                  "短期", "仕掛け", "急騰"]
    # D級：仕手・煽り系
    d_keywords = ["低位", "株価低迷", "出来高急増", "仕手", "煽り"]

    text = buy_reason + " " + name
    a_count = sum(1 for kw in a_keywords if kw in text)
    b_count = sum(1 for kw in b_keywords if kw in text)
    c_count = sum(1 for kw in c_keywords if kw in text)
    d_count = sum(1 for kw in d_keywords if kw in text)

    if d_count >= 1:
        return "D"
    if a_count >= 2:
        return "A"
    if a_count >= 1 and b_count >= 1:
        return "B"
    if a_count >= 1:
        return "B"
    if b_count >= 1:
        return "B"
    if c_count >= 1:
        return "C"
    if score >= 85:
        return "B"
    return "C"

# 政治・世論スコア（Google Trends代替 - キーワードでGemini検索）
POLITICAL_THEMES = {
    "高市": ["防衛", "半導体", "AI", "エネルギー"],
    "防衛費増額": ["防衛", "重工", "電子"],
    "GX": ["再エネ", "蓄電", "水素", "電力"],
    "半導体補助金": ["半導体", "電子部品", "製造装置"],
    "AI投資": ["AI", "データセンター", "光ファイバー", "電力"],
    "インバウンド": ["観光", "旅行", "ホテル", "百貨店"],
}

def get_political_theme_score(stock):
    """現在の政治・世論テーマとの関連度スコア（0-20点）"""
    name   = stock.get("name", "")
    reason = stock.get("buy_reason", "")
    text   = name + " " + reason
    score  = 0
    matched_themes = []
    for theme, sectors in POLITICAL_THEMES.items():
        if any(sector in text for sector in sectors):
            score += 5
            matched_themes.append(theme)
    return min(score, 20), matched_themes


def calculate_vwap(price_history):
    """VWAP（出来高加重平均価格）を計算"""
    if not price_history:
        return 0
    total_pv = sum(p.get("price", 0) * p.get("volume", 1) for p in price_history)
    total_v  = sum(p.get("volume", 1) for p in price_history)
    return round(total_pv / total_v, 1) if total_v > 0 else 0

def evaluate_vwap_reclaim(code, open_price, current_price, price_history):
    """VWAPを奪回できているか判定"""
    if not price_history or open_price <= 0:
        return False, 0
    vwap = calculate_vwap(price_history)
    if vwap <= 0:
        vwap = open_price  # VWAPが計算できない場合は寄り値で代替
    return current_price >= vwap, vwap

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
    # 価格履歴を蓄積（5分足グラフ用）
    jst2 = pytz.timezone("Asia/Tokyo")
    ts_str = datetime.now(jst2).strftime("%H:%M")
    for code, p in prices.items():
        if code not in PRICE_HISTORY:
            PRICE_HISTORY[code] = []
        PRICE_HISTORY[code].append({"time": ts_str, "price": p.get("current", 0)})
        if len(PRICE_HISTORY[code]) > 200:
            PRICE_HISTORY[code] = PRICE_HISTORY[code][-200:]
    return prices


def phase4_final_top3():
    add_log("\U0001f3c6 Ph.4:\u6700\u7d42TOP3\u6c7a\u5b9a")
    state = load_state()
    if not state or state.get("aborted") or state.get("phase",0) < 3:
        add_log("\u26a0\ufe0f Ph.3\u30c7\u30fc\u30bf\u306a\u3057"); return
    top5       = state.get("top5",[])
    philosophy = state.get("philosophy",{})
    sentinel   = state.get("sentinel",{})
    risk       = sentinel.get("risk_level",1)
    # キルスイッチ済み銘柄を除外してTOP3確定
    alive = [s for s in top5 if not s.get("kill_switch", False)]
    killed = [s for s in top5 if s.get("kill_switch", False)]
    if killed:
        add_log("除外銘柄: " + ", ".join([s.get("code","") + "(kill)" for s in killed]))
    # combined_scoreで再ソート
    alive_sorted = sorted(alive, key=lambda x: x.get("combined_score", x.get("final_score",0)), reverse=True)
    top3 = alive_sorted[:3]
    if not top3:
        add_log("⚠️ 全銘柄キルスイッチ発動 — 本日は見送り推奨")
        push_notify("⚠️ 本日見送り推奨",
            "Geminiが全銘柄にキルスイッチを発動しました。\n安全のコストを払い、本日はエントリーを見送ることを推奨します。",
            priority="high")
        state.update({"phase":4,"top3_final":[],"gemini_check":None,"log":LOG_BUFFER[-20:]}); save_state(state)
        return
    # ⚠️警告付き銘柄をログ
    for s in top3:
        if s.get("warn_flag"):
            add_log("⚠️ WARN " + s.get("code","") + ": Gemini" + str(s.get("gemini_score","")) + "点 警告付き採用")
    medals     = ["\U0001f947","\U0001f948","\U0001f949"]
    for i, s in enumerate(top3, 1):
        code  = s.get("code","")
        stars = "\u2605"*s.get("confidence",3)+"\u2606"*(5-s.get("confidence",3))
        phil  = philosophy.get(code,{})
        warn_mark = "⚠️ " if s.get("warn_flag") else ""
        gemini_info = "Gemini:" + str(s.get("gemini_score","-")) + "/100"
        if s.get("gemini_one_line"):
            gemini_info += " " + s.get("gemini_one_line","")
        msg   = (f"{medals[i-1]} {warn_mark}第{i}候補\n《{code}》{s['name']}\n"
                 f"確信度:{stars}\n目標:{s.get('target','')}\n"
                 f"根拠:{s['buy_reason']}\n損切り:{s.get('sell_trigger','')}\n"
                 f"思想:{phil.get('score','-')}/100 | {gemini_info}")
        push_notify(f"🏆 TOP3#{i} {warn_mark}《{code}》{s['name']}", msg,
            priority="high" if i==1 else "default")
        time.sleep(1)
    summary = "\U0001f3c6 \u672c\u65e5\u306eTOP3\u78ba\u5b9a\n"
    summary += "".join([f"{medals[i]}\u300a{s['code']}\u300b{s['name']} {s.get('target','')}\n"
                        for i,s in enumerate(top3)])
    summary += (f"\n\u5730\u5408\u3044:{state.get('market_condition','')}\n"
                f"\u30ea\u30b9\u30af:{BAR_FULL*risk+BAR_LIGHT*(5-risk)}({risk}/5)\n\n"
                f"\U0001f446 1\u9298\u67c4\u3092\u9078\u3093\u3067\u5bc4\u308a\u4ed8\u304d(9:00)\u3067\u8cb7\u3044\uff01")
    push_notify("\U0001f3c6 \u672c\u65e5\u306eTOP3", summary, priority="high")
    # Gemini逆張りチェック
    add_log("Gemini逆張りチェック中...")
    gemini_result = gemini_double_check(top3, state)
    if gemini_result and SCHEDULED_RUN:
        verdict = gemini_result.get("verdict", "?")
        agree   = gemini_result.get("claude_vs_gemini", "?")
        risk_sc = gemini_result.get("risk_score", "-")
        macro_a = gemini_result.get("macro_alert", "")
        if agree == "DISAGREE":
            reason = gemini_result.get("disagree_reason", "")[:100]
            push_notify("Gemini警告",
                "判定:" + verdict + " リスク:" + str(risk_sc) + "/100\n"
                + "ClaudeとDISAGREE\n" + reason + "\n" + macro_a, priority="high")
        else:
            push_notify("Gemini確認",
                "判定:" + verdict + " AGREE リスク:" + str(risk_sc) + "/100\n" + macro_a)
    add_log("\u2705 Ph.4\u5b8c\u4e86 \u2014 TOP3\u78ba\u5b9a" + ("\uff08\u901a\u77e5\u9001\u4fe1\u6e08\u307f\uff09" if SCHEDULED_RUN else ""))
    state.update({"phase":4,"top3_final":top3,"gemini_check":gemini_result,"catalyst_grades":{s["code"]:classify_catalyst_grade(s) for s in top3},"log":LOG_BUFFER[-20:]}); save_state(state)
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
    # 価格履歴を蓄積（5分足グラフ用）
    jst2 = pytz.timezone("Asia/Tokyo")
    ts_str = datetime.now(jst2).strftime("%H:%M")
    for code, p in prices.items():
        if code not in PRICE_HISTORY:
            PRICE_HISTORY[code] = []
        PRICE_HISTORY[code].append({"time": ts_str, "price": p.get("current", 0)})
        if len(PRICE_HISTORY[code]) > 200:
            PRICE_HISTORY[code] = PRICE_HISTORY[code][-200:]
    return prices

def phase5_post_open():
    """ダイナミック・エグジット：状態遷移型出口戦略エンジン"""
    add_log("📈 Ph.5: Dynamic Exit Engine 起動（30分追跡）")
    state = load_state()
    if not state or state.get("aborted") or state.get("phase",0) < 4:
        add_log("⚠️ Ph.4データなし"); return
    top3   = state.get("top3_final",[])
    codes  = [s.get("code","") for s in top3]
    grades = state.get("catalyst_grades", {})
    finnhub= state.get("finnhub_macro", {})

    # 各銘柄の初期コンテキスト構築
    contexts = {}
    for s in top3:
        code = s.get("code","")
        grade = grades.get(code, classify_catalyst_grade(s))
        pol_score, pol_themes = get_political_theme_score(s)
        contexts[code] = {
            "catalyst_grade":     grade,
            "political_score":    pol_score,
            "political_themes":   pol_themes,
            "vix_fear_level":     finnhub.get("fear_level", "NORMAL"),
            "vix_spike_pct":      finnhub.get("vix_spike_pct", 0),
            "open_price":         0,
            "current_price":      0,
            "pnl_pct":            0,
            "drawdown_pct":       0,
            "vwap_reclaimed":     False,
            "vwap_failed_count":  0,
            "volume_increasing_on_drop": False,
            "volume_decreasing_on_drop": False,
            "recovered_to_positive":     False,
            "momentum_decaying":  False,
            "thesis_broken":      False,
            "event_risk":         0,
            "elapsed_min":        0,
            "prev_volume":        0,
            "state":              EXIT_STATE_OPEN_DISCOVERY,
        }

    # 寄り付き価格取得
    add_log("📊 株価取得中...")
    prices_open = get_realtime_prices(codes)
    for s in top3:
        code = s.get("code","")
        if code in prices_open:
            p = prices_open[code]
            contexts[code]["open_price"]    = p["open"]
            contexts[code]["current_price"] = p["current"]
            contexts[code]["pnl_pct"]       = p["change_pct"]
            contexts[code]["prev_volume"]   = p["volume"]
            sign = "+" if p["change_pct"] >= 0 else ""
            arrow = "📈" if p["change_pct"] >= 0 else "📉"
            grade = contexts[code]["catalyst_grade"]
            pol_themes = contexts[code]["political_themes"]
            theme_str = "(" + "/".join(pol_themes[:2]) + ")" if pol_themes else ""
            add_log(f"  {arrow} 《{code}》{sign}{p['change_pct']}% | Grade:{grade}{theme_str}")

    # センチネルチェック
    news = get_news(); twitter = get_twitter_buzz()
    sentinel_now = sentinel_check(news, twitter)
    if sentinel_now.get("action") == "SELL_ALL":
        push_notify("🚨 緊急全決済",
            "センチネル発動！\n" + sentinel_now.get('reason','') + "\n今すぐ全て売れ！",
            priority="urgent")
        add_log("🚨 SELL_ALL発動！"); return

    # 追跡ループ（10分×3回 = 30分）
    decided = {}
    for i in range(3):
        time.sleep(600)  # 10分待機
        elapsed = (i + 1) * 10
        prices_now = get_realtime_prices(codes)

        for s in top3:
            code = s.get("code","")
            if code in decided:
                continue
            if code not in prices_now:
                continue

            p   = prices_now[code]
            ctx = contexts[code]
            op  = ctx["open_price"] if ctx["open_price"] > 0 else p["open"]
            ctx["current_price"] = p["current"]
            ctx["elapsed_min"]   = elapsed
            ctx["pnl_pct"]       = p["change_pct"]
            ctx["drawdown_pct"]  = p["change_pct"]

            # VWAP奪回チェック
            hist = PRICE_HISTORY.get(code, [])
            vwap_ok, vwap_val = evaluate_vwap_reclaim(code, op, p["current"], hist)
            if not vwap_ok:
                ctx["vwap_failed_count"] += 1
            ctx["vwap_reclaimed"] = vwap_ok

            # 出来高方向チェック
            vol_now  = p["volume"]
            vol_prev = ctx["prev_volume"]
            if p["change_pct"] < 0:
                ctx["volume_increasing_on_drop"] = vol_now > vol_prev * 1.1
                ctx["volume_decreasing_on_drop"] = vol_now < vol_prev * 0.9
            if p["change_pct"] >= 0:
                ctx["recovered_to_positive"] = True

            ctx["prev_volume"] = vol_now

            # 状態遷移判定
            new_state, hold_sc, exit_sc, action = determine_exit_state(ctx)
            ctx["state"] = new_state

            sign = "+" if p["change_pct"] >= 0 else ""
            arrow = "📈" if p["change_pct"] >= 0 else "📉"
            state_emoji = {"S0":"⏳","S1":"🔍","S2":"✅","S3":"⚠️","S4":"🚨","S5":"💰"}.get(new_state,"?")
            add_log(f"  {arrow}《{code}》{elapsed}分後 {sign}{p['change_pct']}% "
                    f"| {state_emoji}{new_state} Hold:{hold_sc} Exit:{exit_sc}")

            # アクション実行
            if action == "EXIT_ALL":
                reason = "材料崩壊" if ctx.get("thesis_broken") else \
                         "VIX SPIKE" if ctx.get("vix_fear_level") == "SPIKE" else \
                         "時間切れ(Grade:" + ctx['catalyst_grade'] + ")" if action == "TIME_EXIT" else \
                         "ExitScore:" + str(exit_sc)
                add_log("  🚨《" + code + "》EXIT_ALL → " + reason)
                push_notify("🚨 損切り推奨《" + code + "》",
                    str(elapsed) + "分後: " + sign + str(p['change_pct']) + "%\n" +
                    "判定: " + reason + "\n即時損切りを推奨",
                    priority="urgent")
                decided[code] = {"action": action, "reason": reason, "pnl": p["change_pct"]}

            elif action == "TAKE_PROFIT":
                add_log("  💰《" + code + "》TAKE_PROFIT → +" + str(p['change_pct']) + "%")
                push_notify("💰 利確推奨《" + code + "》",
                    str(elapsed) + "分後: +" + str(p['change_pct']) + "%\n" +
                    "モメンタム減速検知。利確を推奨。",
                    priority="high")
                decided[code] = {"action": action, "reason": "Parabolic", "pnl": p["change_pct"]}

            elif action == "HOLD":
                if ctx.get("recovered_to_positive") and i > 0:
                    add_log("  ✅《" + code + "》HOLD確定 → Grade:" + ctx['catalyst_grade'] + " プラ転")
                    push_notify("✅ ホールド《" + code + "》",
                        str(elapsed) + "分後: " + sign + str(p['change_pct']) + "%\n" +
                        "Grade:" + ctx['catalyst_grade'] + " VWAP奪回:" + str(vwap_ok) + "\n" +
                        "ふるい落とし可能性。ホールド継続推奨。")

    # 最終Claude評価
    prices_final = get_realtime_prices(codes)
    top3_text = chr(10).join([
        "《" + s['code'] + "》" + s['name'] + " 目標:" + s.get('target','') +
        " Grade:" + contexts.get(s['code'],{}).get('catalyst_grade','?') +
        " State:" + contexts.get(s['code'],{}).get('state','?') +
        (" 現在:" + str(prices_final[s['code']]['change_pct']) + "%" if s['code'] in prices_final else "")
        for s in top3])
    news_text = chr(10).join(["- " + n.get('title','') for n in news[:8]])

    try:
        prompt = ("寄り付き後30分間の状態遷移追跡結果を踏まえた最終評価。\n" +
                  "【TOP3+状態】\n" + top3_text + "\n【ニュース】\n" + (news_text or NASHI) + "\n" +
                  'JSONのみ:{"evaluations":[{"code":"コード","status":"HOLD",' +
                  '"message":"30分追跡を踏まえたコメント",' +
                  '"action_advice":"最終アドバイス"}],"overall":"総評"}')
        res = claude.messages.create(model="claude-haiku-4-5-20251001", max_tokens=800,
            messages=[{"role":"user","content": prompt}])
        t      = res.content[0].text if res.content else "{}"
        result = safe_json(t)
        evals  = result.get("evaluations",[])
        msg    = "📈 30分追跡完了\n" + result.get("overall","") + "\n\n"
        for e in evals:
            icon = "✅" if e.get("status") == "HOLD" else "⚠️"
            msg += icon + "《" + e.get('code','') + "》" + e.get('message','') + "\n→ " + e.get('action_advice','') + "\n"
        state["phase"] = 5
        state["post_open_result"]  = result
        state["realtime_prices"]   = prices_final
        state["exit_contexts"]     = {k: {kk: vv for kk, vv in v.items()
                                          if isinstance(vv, (str,int,float,bool))}
                                      for k, v in contexts.items()}
        save_state(state)
        push_notify("📈 初動確認完了", msg)
        add_log(f"✅ Ph.5完了: {result.get('overall','')}")
    except Exception as e:
        add_log(f"[ERROR] Ph.5: {e}")



@app.route("/api/state")


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
    """日足チャートデータ（過去30日）キャッシュ付き"""
    import time as _time
    # キャッシュチェック（10分有効）
    cached = CHART_CACHE.get(code)
    if cached and _time.time() < cached["expires"]:
        return jsonify(cached["data"])

    jst = pytz.timezone("Asia/Tokyo")
    today = datetime.now(jst)
    rows = []
    # JQuants: /v2/equities/prices/daily?code=XXXX0 で一括取得を試みる
    # まず過去45日分の日付リストを作成し、営業日を特定
    # bars/daily エンドポイントで1件ずつ取得
    checked = 0
    for i in range(1, 60):
        if checked >= 30: break
        d = today - timedelta(days=i)
        if d.weekday() >= 5: continue
        date_str = d.strftime("%Y%m%d")
        try:
            res = requests.get("https://api.jquants.com/v2/equities/bars/daily",
                headers=jquants_headers(),
                params={"code": code + "0", "date": date_str}, timeout=8)
            if res.status_code == 200:
                data = res.json().get("data", [])
                if data:
                    q = data[0]
                    cl = q.get("C") or q.get("Close") or q.get("ClosePrice") or 0
                    op = q.get("O") or q.get("Open")  or q.get("OpenPrice")  or cl
                    hi = q.get("H") or q.get("High")  or q.get("HighPrice")  or cl
                    lo = q.get("L") or q.get("Low")   or q.get("LowPrice")   or cl
                    vo = q.get("Vo") or q.get("Volume") or 0
                    if cl > 0:
                        rows.append({"date": d.strftime("%m/%d"),
                            "open": op, "high": hi, "low": lo,
                            "close": cl, "volume": int(vo)})
                        checked += 1
        except: pass
    rows.reverse()
    result = {"code": code, "daily": rows}
    # キャッシュ保存（データがある時のみ・10分）
    if rows:
        CHART_CACHE[code] = {"data": result, "expires": _time.time() + 600}
    return jsonify(result)

@app.route("/api/price_history/<code>")
def api_price_history(code):
    """5分足用価格履歴を返す"""
    return jsonify({"code": code, "history": PRICE_HISTORY.get(code, [])})

@app.route("/api/price_now/<code>")
def api_price_now(code):
    """現在価格のみ高速取得"""
    prices = get_realtime_prices([code])
    return jsonify(prices.get(code, {}))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Scheduler
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def is_japan_weekday():
    """日本時間で月〜金かどうかチェック"""
    jst = pytz.timezone("Asia/Tokyo")
    now_jst = datetime.now(jst)
    return now_jst.weekday() < 5  # 0=月 〜 4=金

def scheduled_run_all():
    global SCHEDULED_RUN
    if not is_japan_weekday():
        add_log("⏭️ 本日は土日のためスキャンをスキップ")
        return
    SCHEDULED_RUN = True
    try:
        phase1_broad_scan()
        phase2_rescore()
        phase3_crosscheck()
        phase4_final_top3()
    finally:
        SCHEDULED_RUN = False

def scheduled_ph5():
    global SCHEDULED_RUN
    if not is_japan_weekday():
        return
    SCHEDULED_RUN = True
    try:
        phase5_post_open()
    finally:
        SCHEDULED_RUN = False

def run_scheduler():
    schedule.every().day.at("08:00").do(scheduled_run_all)
    schedule.every().day.at("09:05").do(scheduled_ph5)
    schedule.every().day.at("09:30").do(scheduled_ph5)
    schedule.every().day.at("10:00").do(scheduled_ph5)

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
