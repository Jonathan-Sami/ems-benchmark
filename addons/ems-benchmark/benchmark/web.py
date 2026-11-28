"""Lokales Dashboard des EMS-Benchmark-Add-ons (Port 8091, auch via Ingress).

Zeigt die bewerteten Tage: KPIs, Säulendiagramm, Tabelle und Aktions-Buttons.
Konfiguration passiert ausschliesslich im nativen HA-Konfigurationsreiter —
dieses Dashboard ist reine Anzeige. Helles Design im Stil der Community-
Website (Ink #0F172A, Akzent #10B981), mit Animationen.
"""
import datetime as dtm
import json
import os
import sqlite3
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from benchmark import userconfig

_LOCK = threading.Lock()
_RUN_STATE = {"running": False, "log": [], "started": None}
_DATA_DIR = os.environ.get("BENCH_DATA", "/data")
_OPTIONS_FILE = os.environ.get("BENCH_OPTIONS", "/data/options.json")
SITE_URL = os.environ.get("BENCH_API_URL", "http://192.168.2.213:8090")

LOGO = ('<svg viewBox="0 0 512 512" fill="none" stroke-linecap="round" '
        'stroke-linejoin="round"><path d="M84 208 L256 72 L428 208" stroke="currentColor" '
        'stroke-width="30"/><rect x="132" y="336" width="66" height="96" rx="16" '
        'fill="currentColor"/><rect x="223" y="288" width="66" height="144" rx="16" '
        'fill="currentColor"/><rect x="314" y="240" width="66" height="192" rx="16" '
        'fill="#10B981"/></svg>')

CSS = """
:root{--ink:#0f172a;--mut:#64748b;--line:#e2e8f0;--bg:#f4f6f9;--card:#ffffff;
--green:#10b981;--orange:#f97316;--red:#ef4444;--amber:#f59e0b;
--seg:#eef1f6;--seg-on:#ffffff;--hover:#f8fafc;--kpi1:#ffffff;--kpi2:#fbfcfe;
--btn-bg:var(--ink);--btn-fg:#fff;--input-bg:#fff;--grid:#eef1f6;--tip-bg:#0f172a;--tip-fg:#fff;
--shadow:0 1px 2px rgba(15,23,42,.05),0 8px 24px -12px rgba(15,23,42,.12)}
@media (prefers-color-scheme: dark){:root:not(.light){--ink:#e6e9ec;--mut:#9aa7b4;--line:#39424e;--bg:#0d1117;--card:#161b22;
--seg:#1c2128;--seg-on:#2d333b;--hover:#1c2128;--kpi1:#191f27;--kpi2:#12171e;
--btn-bg:#e6e9ec;--btn-fg:#0f172a;--input-bg:#0d1117;--grid:#21262d;--tip-bg:#2d333b;--tip-fg:#e6e9ec;
--shadow:0 1px 2px rgba(0,0,0,.4),0 10px 26px -12px rgba(0,0,0,.6)}
:root:not(.light) .idx.good{background:#0d3323;color:#4ade80}
:root:not(.light) .idx.mid{background:#3a2b0d;color:#fbbf24}
:root:not(.light) .idx.bad{background:#3a1214;color:#f87171}}
html.dark{--ink:#e6e9ec;--mut:#9aa7b4;--line:#39424e;--bg:#0d1117;--card:#161b22;
--seg:#1c2128;--seg-on:#2d333b;--hover:#1c2128;--kpi1:#191f27;--kpi2:#12171e;
--btn-bg:#e6e9ec;--btn-fg:#0f172a;--input-bg:#0d1117;--grid:#21262d;--tip-bg:#2d333b;--tip-fg:#e6e9ec;
--shadow:0 1px 2px rgba(0,0,0,.4),0 10px 26px -12px rgba(0,0,0,.6)}
html.dark .idx.good{background:#0d3323;color:#4ade80}
html.dark .idx.mid{background:#3a2b0d;color:#fbbf24}
html.dark .idx.bad{background:#3a1214;color:#f87171}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
background-image:radial-gradient(1200px 500px at 85% -160px,rgba(16,185,129,.10),transparent 60%),
radial-gradient(900px 420px at -10% -140px,rgba(249,115,22,.08),transparent 60%)}
.wrap{max-width:980px;margin:0 auto;padding:26px 20px 60px}
header{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:22px}
.brand{display:flex;align-items:center;gap:10px;font-weight:800;font-size:19px;letter-spacing:-.3px}
.brand svg{width:30px;height:30px}
.verchip{font-size:11.5px;font-weight:700;color:var(--mut);background:var(--card);border:1px solid var(--line);
border-radius:99px;padding:2px 10px;margin-left:2px;vertical-align:2px}
.sitebtn{display:inline-flex;align-items:center;gap:9px;background:var(--btn-bg);color:var(--btn-fg);text-decoration:none;
font-weight:700;font-size:14.5px;padding:11px 20px;border-radius:99px;
transition:transform .25s cubic-bezier(.16,1,.3,1),box-shadow .25s;box-shadow:0 10px 26px -12px rgba(15,23,42,.5)}
.sitebtn:hover{transform:translateY(-3px);box-shadow:0 18px 34px -14px rgba(15,23,42,.55)}
.sitebtn svg{width:16px;height:16px;transition:transform .25s}
.sitebtn:hover svg{transform:translate(3px,-3px)}
.themebtn{width:42px;height:42px;border-radius:50%;border:1px solid var(--line);background:var(--card);
color:var(--ink);cursor:pointer;display:inline-flex;align-items:center;justify-content:center;
transition:transform .25s cubic-bezier(.16,1,.3,1),box-shadow .25s;box-shadow:var(--shadow);flex:none}
.themebtn:hover{transform:translateY(-3px)}
.themebtn svg{width:19px;height:19px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round}
.headbtns{display:flex;align-items:center;gap:12px}
.sub{color:var(--mut);font-size:13px;margin:-14px 0 22px}
.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:20px 22px;
box-shadow:var(--shadow);margin-bottom:18px;animation:rise .5s cubic-bezier(.16,1,.3,1) both}
.card:nth-of-type(2){animation-delay:.06s}.card:nth-of-type(3){animation-delay:.12s}.card:nth-of-type(4){animation-delay:.18s}
@keyframes rise{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
.controls{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:16px}
.seg{display:inline-flex;background:var(--seg);border-radius:99px;padding:3px}
.seg button{border:0;background:transparent;font:inherit;font-weight:600;font-size:13.5px;color:var(--mut);
padding:7px 16px;border-radius:99px;cursor:pointer;transition:all .2s}
.seg button.on{background:var(--seg-on);color:var(--ink);box-shadow:0 1px 6px rgba(15,23,42,.14)}
.seg button:not(.on):hover{color:var(--ink)}
input[type=date]{font:inherit;font-size:13.5px;border:1px solid var(--line);border-radius:10px;padding:7px 10px;background:var(--input-bg);color:var(--ink)}
.note{color:var(--mut);font-size:12.5px}
.bad{color:var(--red)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:18px}
.kpi{background:linear-gradient(180deg,var(--kpi1),var(--kpi2));border:1px solid var(--line);border-radius:14px;
padding:14px 16px;transition:transform .2s,box-shadow .2s}
.kpi:hover{transform:translateY(-2px);box-shadow:var(--shadow)}
.kpi .v{font-size:24px;font-weight:800;letter-spacing:-.5px;font-variant-numeric:tabular-nums}
.kpi .l{font-size:12px;color:var(--mut);margin-top:2px}
.v.good{color:var(--green)}.v.mid{color:var(--amber)}.v.bad{color:var(--red)}
h3{margin:0 0 12px;font-size:15.5px}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th{text-align:left;color:var(--mut);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.4px;
padding:8px 10px;border-bottom:1px solid var(--line)}
td{padding:9px 10px;border-bottom:1px solid var(--grid);font-variant-numeric:tabular-nums}
tbody tr{transition:background .15s}
tbody tr:hover{background:var(--hover)}
tbody tr:last-child td{border-bottom:0}
.tscroll{overflow-x:auto}
.idx{display:inline-block;min-width:56px;text-align:center;font-weight:700;border-radius:99px;padding:2px 9px;font-size:12.5px}
.idx.good{background:#d1fae5;color:#065f46}.idx.mid{background:#fef3c7;color:#92400e}.idx.bad{background:#fee2e2;color:#991b1b}
button.act{border:0;font:inherit;font-weight:700;font-size:14px;border-radius:12px;padding:11px 20px;cursor:pointer;
transition:transform .2s,box-shadow .2s,opacity .2s}
button.act.primary{background:var(--green);color:#fff;box-shadow:0 8px 20px -8px rgba(16,185,129,.55)}
button.act.primary:hover{transform:translateY(-2px);box-shadow:0 14px 26px -10px rgba(16,185,129,.6)}
button.act.sec{background:var(--seg);color:var(--ink)}
button.act.sec:hover{transform:translateY(-2px);background:var(--line)}
button.act:disabled{opacity:.45;cursor:wait;transform:none}
#runlog{margin-top:12px;font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;color:#cbd5e1;background:#0f172a;
border-radius:12px;padding:12px 14px;white-space:pre-wrap;display:none}
#runlog.show{display:block;animation:rise .3s both}
.spin{display:inline-block;width:14px;height:14px;border:2px solid rgba(255,255,255,.4);border-top-color:#fff;
border-radius:50%;animation:sp .7s linear infinite;vertical-align:-2px;margin-right:7px}
@keyframes sp{to{transform:rotate(360deg)}}
.legend{display:flex;gap:18px;font-size:12.5px;color:var(--mut);margin:4px 2px 8px}
.legend i{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.bar{transform-box:fill-box;transform-origin:bottom;animation:grow .7s cubic-bezier(.16,1,.3,1) both}
@keyframes grow{from{transform:scaleY(0)}to{transform:scaleY(1)}}
.bar:hover{opacity:1!important;filter:brightness(1.12)}
svg text{font-size:10.5px;fill:var(--mut)}
.gridline{stroke:var(--grid);stroke-width:1}
.tip{position:fixed;z-index:9;background:var(--tip-bg);color:var(--tip-fg);font-size:12px;line-height:1.45;border-radius:9px;
padding:7px 11px;pointer-events:none;opacity:0;transition:opacity .15s;white-space:nowrap;box-shadow:0 8px 24px -8px rgba(15,23,42,.5)}
.foot{color:var(--mut);font-size:12.5px;text-align:center;margin-top:26px}
@media(max-width:640px){.kpis{grid-template-columns:1fr 1fr}}
"""

HTML = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EMS-Benchmark</title><style>__CSS__</style></head><body><div class="wrap">
<header>
 <div class="brand">__LOGO__EMS-Benchmark<span class="verchip">v__VER__</span></div>
 <span class="headbtns">
 <a class="sitebtn" href="__SITE__" target="_blank" rel="noopener">Alle Benchmarks sehen
  <svg viewBox="0 0 16 16" fill="currentColor"><path d="M3.75 2h3.5a.75.75 0 0 1 0 1.5H5.06l6.97 6.97a.75.75 0 1 1-1.06 1.06L4 4.56v2.19a.75.75 0 0 1-1.5 0v-4.5a.75.75 0 0 1 .75-.75Z"/><path d="M2 12.25A.75.75 0 0 1 2.75 11.5h10.5a.75.75 0 0 1 0 1.5H2.75a.75.75 0 0 1-.75-.75Z"/></svg></a>
 <button class="themebtn" id="tbtn" title="Hell/Dunkel" onclick="window.__themeToggle&&__themeToggle()"></button></span>
</header>
<div class="sub">__SUB__</div>

<div class="card">
 <div class="controls">
  <span class="seg">
   <button data-p="tag">Tag</button><button data-p="woche" class="on">Woche</button><button data-p="monat">Monat</button><button data-p="jahr">Jahr</button>
  </span>
  <input type="date" id="anchor">
  <span class="note" id="range"></span>
 </div>
 <div class="kpis" id="kpis"></div>
 <div class="legend"><span><i style="background:var(--orange)"></i>Real</span><span><i style="background:var(--green)"></i>Optimum (theoretisch)</span></div>
 <div id="chart"></div>
</div>

<div class="card">
 <h3>Aktionen</h3>
 <button class="act primary" id="b1" onclick="run('yesterday')">Gestern bewerten</button>
 <button class="act sec" id="b2" onclick="run('backfill7')">Letzte 7 Tage nachholen</button>
 <span class="note">Automatisch: täglich gegen 00:05 wird der Vortag bewertet.</span>
 <div id="runlog"></div>
</div>

<div class="card">
 <h3>Gewertete Tage im Zeitraum</h3>
 <div class="tscroll"><table><tr><th>Datum</th><th>Real</th><th>Optimum</th><th>Sparpotential</th><th>Index</th><th>Speicher-kWh</th><th>Abdeckung</th></tr>
 <tbody id="tb"></tbody></table></div>
</div>

<div class="foot">Konfiguration im nativen Konfigurationsreiter von Home Assistant ·
 Kennzahl: Index = Optimum ÷ Real (100 % = bereits kostenoptimal)</div>
</div>
<div class="tip" id="tip"></div>
<script>
const MOON='<svg viewBox="0 0 24 24"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/></svg>';
const SUN='<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4.4"/><path d="M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5M4.9 4.9 6.7 6.7M17.3 17.3l1.8 1.8M19.1 4.9l-1.8 1.8M6.7 17.3l-1.8 1.8"/></svg>';
(function(){const p=new URLSearchParams(location.search);let saved=null;
 try{saved=localStorage.getItem('emsbench-theme')}catch(e){}
 let d=null;
 if(saved==='dark'||saved==='light')d=saved==='dark';
 else if(p.get('dark')!==null)d=p.get('dark')==='1';
 else if(p.get('theme'))d=/dark/i.test(p.get('theme'));
 const mq=matchMedia('(prefers-color-scheme: dark)');
 function isDark(){return d===null?mq.matches:d}
 function paint(){document.documentElement.classList.toggle('dark',isDark());
  const b=document.getElementById('tbtn');if(b){b.innerHTML=isDark()?SUN:MOON;
   b.title=d===null?'Hell/Dunkel ( folgt dem System — klicken zum Festlegen )':(isDark()?'Zu Hell wechseln':'Zu Dunkel wechseln')}}
 window.__themeToggle=function(){try{localStorage.setItem('emsbench-theme',isDark()?'light':'dark')}catch(e){}
  d=isDark()?false:true;paint()};
 mq.addEventListener('change',()=>{if(d===null)paint()});
 addEventListener('DOMContentLoaded',paint);paint()})();
const DAYS=__DATA__;
function esc(s){return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;')}
function qclass(q){return q==null?'':(q>=0.85?'good':(q>=0.6?'mid':'bad'))}
function money(v){return v==null?'–':v.toFixed(2).replace('.',',')+' €'}
function pct(v){return v==null?'–':(100*v).toFixed(1).replace('.',',')+' %'}
let period='woche';
const dates=DAYS.map(d=>d.d).sort();
const anchorEl=document.getElementById('anchor');
if(dates.length){anchorEl.value=dates[dates.length-1];anchorEl.min=dates[0];anchorEl.max=dates[dates.length-1];}
function isoAdd(iso,n){const d=new Date(iso+'T12:00:00');d.setDate(d.getDate()+n);return d.toISOString().slice(0,10)}
function win(){const a=anchorEl.value||dates[dates.length-1];
 if(period==='tag')return DAYS.filter(d=>d.d===a);
 if(period==='woche'){const d0=new Date(a+'T12:00:00');const dow=(d0.getDay()+6)%7;
  const mo=isoAdd(a,-dow),so=isoAdd(mo,6);
  return DAYS.filter(d=>d.d>=mo&&d.d<=so)}
 if(period==='monat')return DAYS.filter(d=>d.d.slice(0,7)===a.slice(0,7));
 return DAYS.filter(d=>d.d.slice(0,4)===a.slice(0,4))}
function kwnum(iso){const d=new Date(iso+'T12:00:00');const t=new Date(d.getFullYear(),0,1);
 return Math.ceil(((d-t)/864e5+t.getDay()+1)/7)}
function kpi(v,l,c){return `<div class="kpi"><div class="v ${c||''}">${v}</div><div class="l">${l}</div></div>`}
// KPI-Zahlen animiert hochzaehlen lassen
function countUp(){document.querySelectorAll('.kpi .v').forEach(el=>{
 const m=el.textContent.match(/^([\\d.,]+)(.*)$/);if(!m)return;
 const target=parseFloat(m[1].replace(',','.'));if(isNaN(target))return;
 const suf=m[2],dec=(m[1].split(',')[1]||'').length,t0=performance.now(),D=650;
 (function step(t){const p=Math.min((t-t0)/D,1),e=1-Math.pow(1-p,3),val=(target*e).toFixed(dec).replace('.',',');
  el.textContent=val+suf;if(p<1)requestAnimationFrame(step)})(t0)})}
function render(){
 const w=win();const qd=w.filter(d=>d.q!=null);
 const sk=w.filter(d=>d.q==null&&d.o==null);
 let rl='';
 if(w.length){rl=`${w[0].d} bis ${w[w.length-1].d} · ${qd.length} gewertete Tage`;
  if(sk.length)rl+=` · <span class="bad">⚠ ${sk.length} Tag(e) verworfen: Sensorabdeckung unter 90 %, nicht gewertet und nicht übertragen</span>`;
  if(period==='woche')rl=`KW ${kwnum(anchorEl.value)} · `+rl;
  else if(period==='monat')rl=anchorEl.value.slice(0,7)+' · '+rl;
  else if(period==='jahr')rl=anchorEl.value.slice(0,4)+' · '+rl;}
 document.getElementById('range').innerHTML=rl;
 let k='';
 if(period==='tag'){const d=w[0];
  k=kpi(pct(d?d.q:null),'Index',d?qclass(d.q):'')+kpi(money(d?d.r:null),'Real')+kpi(money(d?d.o:null),'Optimum')+kpi(money(d?(d.r-d.o):null),'Sparpotential');
 }else{
  const avg=qd.length?qd.reduce((s,d)=>s+d.q,0)/qd.length:null;
  const sr=w.reduce((s,d)=>s+(d.r||0),0),so=w.reduce((s,d)=>s+(d.o||0),0);
  k=kpi(pct(avg),`Ø Index (${qd.length} Tage)`,qclass(avg))+kpi(money(sr),'Real Σ')+kpi(money(so),'Optimum Σ')+kpi(money(sr-so),'Sparpotential Σ');
 }
 document.getElementById('kpis').innerHTML=k;countUp();
 chart(w);
 document.getElementById('tb').innerHTML=w.slice().reverse().map(d=>
  (d.q==null&&d.o==null)
  ?`<tr><td>${d.d}</td><td colspan="6" class="bad" style="text-align:left">verworfen — Sensorabdeckung unter 90 % (nicht gewertet, nicht übertragen)</td></tr>`
  :`<tr><td>${d.d}</td><td>${money(d.r)}</td><td>${money(d.o)}</td><td>${money((d.r!=null&&d.o!=null)?d.r-d.o:null)}</td><td><span class="idx ${qclass(d.q)}">${pct(d.q)}</span></td><td>${d.th==null?'–':d.th.toFixed(2).replace('.',',')}</td><td>${typeof d.cov==='number'?(100*d.cov).toFixed(0).replace('.',',')+' %':(d.cov??'')}</td></tr>`).join('')
  ||'<tr><td colspan="7">Keine gewerteten Tage im Zeitraum.</td></tr>';
}
const tip=document.getElementById('tip');
function showTip(ev,html){tip.innerHTML=html;tip.style.opacity=1;
 const r=tip.getBoundingClientRect();
 tip.style.left=Math.min(ev.clientX+14,innerWidth-r.width-8)+'px';
 tip.style.top=Math.max(ev.clientY-r.height-10,8)+'px'}
function hideTip(){tip.style.opacity=0}
function chart(rows){
 let bars=rows;let monthly=false;
 if(rows.length>31){monthly=true;const m={};
  rows.forEach(d=>{const k=d.d.slice(0,7);(m[k]=m[k]||{d:k,r:0,o:0,n:0,q:0,nq:0});m[k].r+=d.r||0;m[k].o+=d.o||0;m[k].n++;if(d.q!=null){m[k].q+=d.q;m[k].nq++}});
  bars=Object.values(m).map(b=>({...b,q:b.nq?b.q/b.nq:null}))}
 if(!bars.length){document.getElementById('chart').innerHTML='<p class="note">Noch keine Daten — erster Lauf automatisch um 00:05 oder mit „Gestern bewerten“.</p>';return}
 const maxc=Math.max(...bars.map(b=>Math.max(b.r||0,b.o||0)),1);
 const n=bars.length;const bw=Math.min(Math.max(Math.floor(700/(2*n+8)),14),30);
 const gap=6,W=n*(2*bw+gap)+46,H=190,PB=26;let s='';
 for(let g=1;g<=3;g++){const y=PB+(H-PB-24)*g/4;s+=`<line class="gridline" x1="36" x2="${W-6}" y1="${y}" y2="${y}"/>`}
 s+=`<text x="30" y="${PB+6}" text-anchor="end">${maxc.toFixed(1)} €</text>`;
 s+=`<text x="30" y="${H-20}" text-anchor="end">0</text>`;
 bars.forEach((b,i)=>{const x=40+i*(2*bw+gap);
  const hr=Math.max((b.r||0)/maxc,0.004)*(H-PB-26),ho=Math.max((b.o||0)/maxc,0.004)*(H-PB-26);
  const tt=`${b.d}<br>real: <b>${money(b.r)}</b><br>Optimum: <b>${money(b.o)}</b>`+(b.q!=null?`<br>Index: <b>${pct(b.q)}</b>`:'');
  s+=`<rect class="bar" style="animation-delay:${i*40}ms" x="${x}" y="${H-PB-hr}" width="${bw}" height="${hr}" rx="4" fill="var(--orange)" opacity=".88" data-tip="${esc(tt)}"/>`;
  s+=`<rect class="bar" style="animation-delay:${i*40+20}ms" x="${x+bw}" y="${H-PB-ho}" width="${bw}" height="${ho}" rx="4" fill="var(--green)" opacity=".88" data-tip="${esc(tt)}"/>`;
  const lab=monthly?b.d.slice(5)+'.'+b.d.slice(2,4):b.d.slice(5);
  s+=`<text x="${x+bw}" y="${H-8}" text-anchor="middle">${lab}</text>`});
 document.getElementById('chart').innerHTML=
  `<div style="overflow-x:auto"><svg viewBox="0 0 ${W} ${H}" style="width:100%;max-width:${W}px">${s}</svg></div>`+
  (monthly?'<p class="note">Zeiträume über 31 Tage werden pro Kalendermonat summarisiert.</p>':'');
 document.querySelectorAll('.bar').forEach(r=>{
  r.addEventListener('mousemove',e=>showTip(e,r.dataset.tip));
  r.addEventListener('mouseleave',hideTip)})}
document.querySelectorAll('.seg button').forEach(b=>b.onclick=()=>{
 document.querySelectorAll('.seg button').forEach(x=>x.classList.remove('on'));
 b.classList.add('on');period=b.dataset.p;render()});
anchorEl.onchange=render;
async function run(what){document.getElementById('b1').disabled=true;document.getElementById('b2').disabled=true;
try{const r=await fetch('run?what='+what);const d=await r.json();log(d.msg||'gestartet');poll();}
catch(e){log('Fehler: '+e);document.getElementById('b1').disabled=false;document.getElementById('b2').disabled=false;}}
function log(t){const el=document.getElementById('runlog');el.classList.add('show');el.textContent=t}
function poll(){fetch('api/run-status').then(r=>r.json()).then(d=>{const el=document.getElementById('runlog');
 el.classList.add('show');el.textContent=(d.log||[]).join('\\n');
 if(d.running){document.getElementById('b1').innerHTML='<span class="spin"></span>Läuft…';setTimeout(poll,2000)}
 else{document.getElementById('b1').textContent='Gestern bewerten';document.getElementById('b1').disabled=false;document.getElementById('b2').disabled=false;setTimeout(()=>location.reload(),1200)}})}
render();
</script></body></html>"""


def _esc_json(obj):
    return json.dumps(obj, ensure_ascii=True).replace("</", "<\\/")


def _days(db_path):
    con = sqlite3.connect(db_path)
    rows = con.execute("SELECT date,real_cost,oracle_cost,quotient,"
                       "battery_throughput_kwh,quality_flags FROM days ORDER BY date").fetchall()
    con.close()
    out = []
    for d, r, o, q, th, fl in rows:
        try:
            cov = json.loads(fl or "{}").get("coverage", "")
        except Exception:
            cov = ""
        out.append({"d": d, "r": r, "o": o, "q": q, "th": th, "cov": cov})
    return out


def render(db_path, version):
    opts = userconfig.load(_DATA_DIR, _OPTIONS_FILE)
    days = _days(db_path)
    # v0.7-Menüformat bevorzugen, flache Altbestände weiterhin verstehen
    hems = opts.get("HEMS") or {}
    sp = opts.get("Speicher") or {}
    dev = (opts.get("Geräte") or opts.get("geraete") or opts.get("devices") or {})
    hems_name = (hems.get("Hersteller") or dev.get("HEMS")
                 or dev.get("hersteller_hems") or dev.get("ems_product") or "–")
    batt_name = (sp.get("Hersteller") or dev.get("Speicher")
                 or dev.get("hersteller_speicher") or dev.get("battery_brand") or "–")
    raw_res = (opts.get("Auflösung") or opts.get("Auflösung_in_Sekunden")
               or opts.get("aufloesung_minuten") or opts.get("resolution_min") or 60)
    RES = {"10 Sekunden": 10, "10 Sekunden (rechenintensiv)": 10,
           "1 Minute": 60, "5 Minuten": 300}
    raw_res = RES.get(str(raw_res), raw_res)
    try:
        raw_res = int(raw_res)
    except Exception:
        raw_res = 60
    res_txt = {10: "10 s", 60: "1 min", 300: "5 min"}.get(raw_res, f"{raw_res} s")
    hhid = ""
    try:
        with open(os.path.join(_DATA_DIR, "household_id")) as f:
            hhid = f.read().strip()
    except Exception:
        hhid = str(opts.get("household_id", ""))
    sub = (f"HEMS: {hems_name} · Speicher: {batt_name} · Auflösung {res_txt}"
           f" · Haushalt {hhid[:8] or '–'}")
    html = (HTML
            .replace("__CSS__", CSS)
            .replace("__LOGO__", LOGO)
            .replace("__SITE__", SITE_URL)
            .replace("__VER__", version)
            .replace("__SUB__", sub)
            .replace("__DATA__", _esc_json(days)))
    return html.encode("utf-8")


def _run_job(dates):
    with _LOCK:
        _RUN_STATE.update(running=True, log=[], started=dtm.datetime.now().isoformat())
    for d in dates:
        try:
            p = subprocess.run(
                [sys.executable, os.path.join(os.path.dirname(__file__), "daemon.py"), d],
                capture_output=True, text=True, timeout=900)
            out = (p.stdout + p.stderr).strip()
            with _LOCK:
                _RUN_STATE["log"].append(f"[{d}] {out or ('ok' if p.returncode == 0 else 'fehler')}")
        except Exception as e:
            with _LOCK:
                _RUN_STATE["log"].append(f"[{d}] EXCEPTION {e}")
    with _LOCK:
        _RUN_STATE["running"] = False


def start_server(port, db_path, version, tz):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, data, ctype="application/json"):
            if isinstance(data, (dict, list)):
                data = json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            p = urllib.parse.urlparse(self.path).path.rstrip("/") or "/"
            if p in ("/", "/index.html"):
                return self._send(200, render(db_path, version), "text/html; charset=utf-8")
            if p == "/api/run-status":
                with _LOCK:
                    return self._send(200, {"running": _RUN_STATE["running"],
                                            "log": _RUN_STATE["log"][-10:]})
            if p == "/api/days":
                return self._send(200, {"days": _days(db_path)})
            if p == "/run":
                if _RUN_STATE["running"]:
                    return self._send(409, {"msg": "Lauf bereits aktiv"})
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                what = (q.get("what") or ["yesterday"])[0]
                today = dtm.datetime.now(tz).date()
                if what == "backfill7":
                    dates = [(today - dtm.timedelta(days=i)).isoformat() for i in range(7, 0, -1)]
                else:
                    dates = [(today - dtm.timedelta(days=1)).isoformat()]
                threading.Thread(target=_run_job, args=(dates,), daemon=True).start()
                return self._send(200, {"msg": f"Starte {len(dates)} Tag(e)…"})
            return self._send(404, {"error": "not found"})

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"[web] Dashboard auf 0.0.0.0:{port}")
    return srv
