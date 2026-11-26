"""Ingress-Dashboard fuer den EMS-Benchmark (stdlib, dark theme, Deutsch).

Tag/Woche/Monat/Jahr-Auswahl mit Datum, Tageskosten real vs. Optimum, Index,
Sparpotential, Methodik und eine deutsche Einstellungsseite (Dropdowns).
Einstellungen landen in /data/user_config.json und ueberleben Updates.
"""
import datetime as dtm
import json
import os
import sqlite3
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from benchmark import manufacturers, userconfig

_LOCK = threading.Lock()
_RUN_STATE = {"running": False, "log": [], "started": None}
_DATA_DIR = os.environ.get("BENCH_DATA", "/data")
_OPTIONS_FILE = os.environ.get("BENCH_OPTIONS", "/data/options.json")

CSS = """
:root{color-scheme:dark}
*{box-sizing:border-box}
body{font:15px/1.5 system-ui,sans-serif;background:#111518;color:#e6e9ec;margin:0;padding:24px}
h1{font-size:22px;margin:0 0 2px} .sub{color:#8b949e;font-size:13px;margin-bottom:20px}
.kpis{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:22px}
.kpi{background:#1b2127;border:1px solid #2b333b;border-radius:10px;padding:14px 18px;min-width:150px}
.kpi .v{font-size:26px;font-weight:650} .kpi .l{color:#8b949e;font-size:12px;text-transform:uppercase;letter-spacing:.4px}
.good{color:#3fb950} .mid{color:#d29922} .bad{color:#f85149}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:7px 10px;text-align:right;border-bottom:1px solid #2b333b}
th{color:#8b949e;font-weight:500;font-size:12px;text-transform:uppercase;letter-spacing:.4px}
td:first-child,th:first-child{text-align:left}
tr:hover td{background:#1b2127}
.card{background:#1b2127;border:1px solid #2b333b;border-radius:10px;padding:18px 20px;margin-bottom:20px}
button{background:#238636;color:#fff;border:0;border-radius:8px;padding:9px 16px;font-size:14px;cursor:pointer}
button:hover{filter:brightness(1.12)} button:disabled{background:#30363d;cursor:wait}
button.sec{background:#30363d}
select,input[type=date],input[type=text],input[type=number]{background:#0d1117;color:#e6e9ec;border:1px solid #2b333b;border-radius:6px;padding:6px 8px;font-size:14px}
.controls{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:14px}
.controls #range{flex-basis:100%;margin:0}
.controls .seg{display:inline-flex;border:1px solid #2b333b;border-radius:8px;overflow:hidden}
.controls .seg button{border-radius:0;background:#0d1117;color:#8b949e;padding:8px 14px}
.controls .seg button.on{background:#1f6feb;color:#fff}
.formula{background:#0d1117;border:1px solid #2b333b;border-radius:8px;padding:10px 14px;font-family:ui-monospace,monospace;font-size:13.5px;overflow-x:auto;white-space:pre;color:#a5d6ff}
details{margin-bottom:10px} summary{cursor:pointer;font-weight:600;font-size:15px;padding:4px 0}
.note{color:#8b949e;font-size:13px}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px 22px}
.feld{display:flex;flex-direction:column;gap:3px} .feld label{color:#8b949e;font-size:12px;text-transform:uppercase;letter-spacing:.4px}
.feld select,.feld input{width:100%}
h4.sect{color:#58a6ff;margin:16px 0 8px;font-size:14px;text-transform:uppercase;letter-spacing:.5px}
#runlog,#setlog{font-family:ui-monospace,monospace;font-size:12px;white-space:pre-wrap;color:#8b949e;margin-top:10px}
svg text{fill:#8b949e;font-size:10px}
.tscroll{max-height:420px;overflow-y:auto}
"""

METHodik = """
<div class="card"><details><summary>📐 Methodik — wie der Benchmark rechnet</summary>
<p>Der Benchmark bewertet <b>nur die Steuerungsqualität</b> des Energiemanagements, nicht die Größe der Anlage.
Dazu wird jeder Kalendertag als geschlossenes Experiment behandelt: Aus den gemessenen Sensordaten
(PV, Netz, Haus, Wärmepumpe, Wallbox, Batterie) rekonstruiert das Add-on die Umgebung des Tages und
berechnet, was <b>mathematisch optimal</b> gewesen wäre. Der Index setzt die echten Kosten ins Verhältnis dazu.</p>
<h4>1. Das Optimum (theoretische Kosten-Untergrenze)</h4>
<p>Ein gemischt-ganzzahliges lineares Programm (MILP, Solver HiGHS) plant den perfekten Tag mit
Rückblickwissen — in 1-Minuten-Slots:</p>
<div class="formula">min Σₜ [ λₜ·gₜ − μₜ·eₜ + κ·(cₜ + dₜ)·Δt ]

λₜ = Arbeitspreis Bezugsstrom   μₜ = Einspeisevergütung   gₜ = Netzbezug   eₜ = Einspeisung
cₜ/dₜ = Batterie lad./entl.     κ = Durchfluss-Aufschlag (Verschleiß)</div>
<h4>Nebenbedingungen (dieselbe Physik wie das echte System)</h4>
<div class="formula">Bilanz:      pvₜ + gₜ + dₜ = Lₜ + eₜ + cₜ + wₜ + hₜ
Speicher:   E(t+Δt) = E(t) + η·cₜ·Δt − dₜ·Δt/η,   E_min ≤ E ≤ E_max
50/50-Regel: E(Tag Anfang) = E(Tag Ende) = 50 % → jeder Tag ein faires, in sich geschlossenes Experiment
Wärmepumpe: Σ hₜ·Δt = gemessene Wärmepumpen-Energie;  hₜ ∈ {0} ∪ [p_min; p_max] kW
Wallbox:    Σ wₜ·Δt = gemessene Ladeenergie;  wₜ ∈ {0} ∪ [p_min; p_max] kW nur im Anwesenheitsfenster</div>
<p>Da jedes denkbare EMS dieselben Grenzen einhält, ist die MILP-Lösung eine <b>beweisbare Kosten-Untergrenze</b>:
Was das Optimum nicht schafft, kann kein System schaffen. κ verhindert, dass es mit sinnlosem
Hin-und-Her-Zykeln die Latte unrealistisch hochlegt.</p>
<h4>2. Die reale Seite</h4>
<div class="formula">K_real = Σₜ λₜ·E_Bezug,ₜ − μₜ·E_Einspeisung,ₜ   (direkt aus Netzpunkt + Preisen gemessen)</div>
<h4>3. Der Index</h4>
<div class="formula">Index = K_Optimum / K_real

100 % = so gut wie physikalisch optimal   unter 100 % = verschenktes Sparpotential
Negativ möglich, wenn der Tag durch Einspeisung Gewinn abwerfen könnte (Gewinn-Tage).</div>
<h4>4. Qualitätsgate pro Tag</h4>
<p>Ein Tag zählt nur, wenn mindestens 90 % der Zeitscheiben von allen Hauptsensoren
abgedeckt sind, die Energiebilanz (PV + Bezug ≈ Haus + Wallbox + WP + Einspeisung) plausibel
schließen kann und Preise vorhanden sind. Verworfene Tage werden im Dashboard ausgewiesen und
gelangen nie auf den Server.</p>
<h4>5. Datenschutz</h4>
<p>Alle Rohdaten bleiben in diesem Haus. An den Community-Server gehen pro Tag ausschließlich:
der Index, der HEMS-Hersteller und die vier Geräte-Marken — sonst nichts.
Keine Kosten, kein Stromfluss, keine Zeitreihen, keine Adresse.</p>
<p class="note">Bewusste Vereinfachungen: Wärmepumpen-Energie darf frei im Tag verschoben werden (misst Preisoptimierung,
nicht Komfort); Wallbox-Fenster = gemessene Ladezeit.</p>
</details></div>
"""

HTML = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EMS-Benchmark</title><style>__CSS__</style></head><body>
<h1>⚡ EMS-Benchmark <span style="font-weight:400;color:#8b949e;font-size:14px">v__VER__</span></h1>
<div class="sub">Unabhängige Bewertung der Steuerungsqualität · __SUB__</div>

<div class="card"><div class="controls">
<span class="seg">
<button data-p="tag">Tag</button><button data-p="woche" class="on">Woche</button><button data-p="monat">Monat</button><button data-p="jahr">Jahr</button>
</span>
<span class="feld"><input type="date" id="anchor"></span>
<span class="note" id="range"></span>
</div>
<div class="kpis" id="kpis"></div>
<div id="chart"></div>
</div>

<div class="card"><h3 style="margin-top:0">Aktionen</h3>
<button id="b1" onclick="run('yesterday')">Gestern bewerten</button>
<button id="b2" class="sec" onclick="run('backfill7')">Letzte 7 Tage nachholen</button>
<span class="note">Automatisch: täglich gegen 00:40 wird der Vortag bewertet.</span>
<div id="runlog"></div></div>

<div class="card"><h3 style="margin-top:0">Gewertete Tage im Zeitraum</h3>
<div class="tscroll"><table><tr><th>Datum</th><th>Real</th><th>Optimum</th><th>Sparpotential</th><th>Index</th><th>Speicher-kWh</th><th>Abdeckung</th></tr>
<tbody id="tb"></tbody></table></div></div>

__METH__

<div class="card"><details id="settings" ontoggle="if(this.open&&!ENT_LOADED)loadEntities()">
<summary>⚙️ Einstellungen — Geräte, Sensoren, Batterie</summary>
<p class="note">Diese Werte werden dauerhaft gespeichert (überleben jedes Update) und ersetzen die
Add-on-Grundkonfiguration. Wirksam ab dem nächsten Lauf.</p>
<h4 class="sect">Einspeisung</h4>
<div class="feld"><label style="text-transform:none;font-size:13px">Wie wird eingespeister Strom vergütet?</label>
<div style="display:flex;gap:22px;margin-top:4px">
<label style="display:flex;gap:7px;align-items:center;font-size:14px"><input type="radio" name="expmode" id="exp_fix" value="fest" checked> Feste Einspeisevergütung</label>
<label style="display:flex;gap:7px;align-items:center;font-size:14px"><input type="radio" name="expmode" id="exp_dyn" value="sensor"> Direktvermarktung (Preis-Sensor)</label>
</div></div>
<div class="grid2" style="margin-top:10px">
<div class="feld"><label>Feste Vergütung in €/kWh (z. B. 0.124)</label><input type="text" id="s_pfx"></div>
</div>
<p class="note">Ohne beide Angaben wird der Bezugspreis angesetzt — die strengste Annahme zugunsten des Optimums.</p>
<h4 class="sect">Sensoren</h4>
<div class="grid2" id="sensorfelder"></div>
<h4 class="sect">Batterie</h4>
<div class="grid2">
<div class="feld"><label>Kapazität in kWh</label><input type="number" step="0.1" id="s_cap"></div>
<div class="feld"><label>Ladeleistung in kW</label><input type="number" step="0.1" id="s_pc"></div>
<div class="feld"><label>Entladeleistung in kW</label><input type="number" step="0.1" id="s_pd"></div>
<div class="feld"><label>Wirkungsgrad in % (Anteil Energie, der beim Speichern übrig bleibt)</label><input type="number" step="1" id="s_eta"></div>
</div>
<h4 class="sect">Grenzwerte der Geräte</h4>
<div class="grid2">
<div class="feld"><label>Wärmepumpe mindestens in kW</label><input type="number" step="0.1" id="s_hpmin"></div>
<div class="feld"><label>Wärmepumpe höchstens in kW</label><input type="number" step="0.1" id="s_hpmax"></div>
<div class="feld"><label>Wallbox mindestens in kW (meist 1,1 bzw. 4,7)</label><input type="number" step="0.1" id="s_wbmin"></div>
<div class="feld"><label>Wallbox höchstens in kW</label><input type="number" step="0.1" id="s_wbmax"></div>
</div>
<h4 class="sect">Auflösung der Berechnung</h4>
<div class="grid2">
<div class="feld"><label>Zeitachse des Modells</label><select id="s_res"><option value="10 Sekunden">10 Sekunden (sehr rechenintensiv)</option><option value="1 Minute" selected>1 Minute (empfohlen)</option><option value="5 Minuten">5 Minuten (schnell)</option></select></div>
</div>
<h4 class="sect">Geräte &amp; Hersteller</h4>
<div class="grid2">
<div class="feld"><label>HEMS (getestetes Energiemanagement)</label><input list="mHEMS" id="s_hems"></div>
<div class="feld"><label>Wechselrichter</label><input list="mWECHSELRICHTER" id="s_pv"></div>
<div class="feld"><label>Speicher</label><input list="mSPEICHER" id="s_batt"></div>
<div class="feld"><label>Wärmepumpe</label><input list="mWAERMEPUMPE" id="s_hp"></div>
<div class="feld"><label>Wallbox</label><input list="mWALLBOX" id="s_wb"></div>
</div>
<p><button onclick="saveSettings()">Einstellungen speichern</button>
<button class="sec" onclick="location.reload()">Verwerfen</button></p>
<div id="setlog"></div>
</details></div>

<script>
const DAYS=__DATA__; const SET=__SET__; const MANU=__MANU__;
const ENT_LABELS={"PV Leistung":"PV Leistung","Netz gesamt (negativ = Einspeisung)":"Netz gesamt (negativ = Einspeisung)","Netz Bezug (nur bei getrennten Sensoren)":"Netz Bezug — nur wenn Bezug/Einspeisung getrennte Sensoren sind","Netz Einspeisung (nur bei getrennten Sensoren)":"Netz Einspeisung — nur bei getrennten Sensoren","Hausverbrauch":"Hausverbrauch (ohne Wallbox)","Wärmepumpe Leistung":"Wärmepumpe (Leistung)","Wallbox Leistung":"Wallbox (Ladeleistung)","Wallbox verbunden (optional)":"Auto anwesend (Binärsensor)","Preis Bezug":"Preis Bezug (€/kWh, dynamischer Tarif)","Preis Einspeisung (nur bei Direktvermarktung)":"Preis Einspeisung (€/kWh, nur bei Direktvermarktung)"};
const EIDX=Object.keys(ENT_LABELS);
const eid=k=>'e'+EIDX.indexOf(k);
const EXKEY="Preis Einspeisung (nur bei Direktvermarktung)";
let ENT_LOADED=false, HAENT=null;
function esc(s){return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;')}
function qclass(q){return q==null?'':(q>=0.85?'good':(q>=0.6?'mid':'bad'))}
function money(v){return v==null?'–':v.toFixed(2).replace('.',',')+' €'}
function pct(v){return v==null?'–':(100*v).toFixed(1).replace('.',',')+' %'}
const PERIOD={tag:1,woche:7,monat:30,jahr:365};
let period='woche';
const dates=DAYS.map(d=>d.d).sort();
const anchorEl=document.getElementById('anchor');
if(dates.length){anchorEl.value=dates[dates.length-1];anchorEl.min=dates[0];anchorEl.max=dates[dates.length-1];}
function isoAdd(iso,n){const d=new Date(iso+'T12:00:00');d.setDate(d.getDate()+n);return d.toISOString().slice(0,10)}
// Kalenderfenster zum gewählten Datum: Tag=Wochentag, Woche=ISO-Kalenderwoche
// (Mo–So) um das Datum, Monat=Kalendermonat, Jahr=Kalenderjahr.
function win(){const a=anchorEl.value||dates[dates.length-1];
 if(period==='tag')return DAYS.filter(d=>d.d===a);
 if(period==='woche'){const d0=new Date(a+'T12:00:00');const dow=(d0.getDay()+6)%7;
  const mo=isoAdd(a,-dow),so=isoAdd(mo,6);
  return DAYS.filter(d=>d.d>=mo&&d.d<=so)}
 if(period==='monat')return DAYS.filter(d=>d.d.slice(0,7)===a.slice(0,7));
 return DAYS.filter(d=>d.d.slice(0,4)===a.slice(0,4))}
function kpi(v,l,c){return `<div class="kpi"><div class="v ${c||''}">${v}</div><div class="l">${l}</div></div>`}
 function kwnum(iso){const d=new Date(iso+'T12:00:00');const t=new Date(d.getFullYear(),0,1);
 return Math.ceil(((d-t)/864e5+t.getDay()+1)/7)}
 function render(){
 const w=win();const qd=w.filter(d=>d.q!=null);
 const sk=w.filter(d=>d.q==null&&d.o==null);   // vom Qualitätsgate verworfen
 let rl='';
 if(w.length){rl=`${w[0].d} bis ${w[w.length-1].d} · ${qd.length} gewertete Tage`;
  if(sk.length)rl+=` · ⚠ ${sk.length} Tag(e) verworfen: Sensorabdeckung unter 90 %, nicht gewertet und nicht übertragen`;
  if(period==='woche')rl=`KW ${kwnum(anchorEl.value)} · `+rl;
  else if(period==='monat')rl=anchorEl.value.slice(0,7)+' · '+rl;
  else if(period==='jahr')rl=anchorEl.value.slice(0,4)+' · '+rl;}
 document.getElementById('range').innerHTML=esc(rl).replace('⚠','<span class="bad">⚠').replace('übertragen','übertragen</span>');
 let k='';
 if(period==='tag'){const d=w[0];
  k=kpi(pct(d?d.q:null),'Index',d?qclass(d.q):'')+kpi(money(d?d.r:null),'Real')+kpi(money(d?d.o:null),'Optimum')+kpi(money(d?(d.r-d.o):null),'Sparpotential');
 }else{
  const avg=qd.length?qd.reduce((s,d)=>s+d.q,0)/qd.length:null;
  const sr=w.reduce((s,d)=>s+(d.r||0),0),so=w.reduce((s,d)=>s+(d.o||0),0);
  k=kpi(pct(avg),`Ø Index (${qd.length} Tage)`,qclass(avg))+kpi(money(sr),'Real Σ')+kpi(money(so),'Optimum Σ')+kpi(money(sr-so),'Sparpotential Σ');
 }
 document.getElementById('kpis').innerHTML=k;
 chart(w);
 document.getElementById('tb').innerHTML=w.slice().reverse().map(d=>
  (d.q==null&&d.o==null)
  ?`<tr><td>${d.d}</td><td colspan="6" class="bad" style="text-align:left">verworfen — Sensorabdeckung unter 90 % (nicht gewertet, nicht übertragen)</td></tr>`
  :`<tr><td>${d.d}</td><td>${money(d.r)}</td><td>${money(d.o)}</td><td>${money((d.r!=null&&d.o!=null)?d.r-d.o:null)}</td><td class="${qclass(d.q)}">${pct(d.q)}</td><td>${d.th??''}</td><td>${d.cov??''}</td></tr>`).join('')
  ||'<tr><td colspan="7">Keine gewerteten Tage im Zeitraum.</td></tr>';
}
function chart(rows){
 // bei >31 Tagen auf Kalendermonate aggregieren
 let bars=rows;let monthly=false;
 if(rows.length>31){monthly=true;const m={};
  rows.forEach(d=>{const k=d.d.slice(0,7);(m[k]=m[k]||{d:k,r:0,o:0,n:0});m[k].r+=d.r||0;m[k].o+=d.o||0;m[k].n++});
  bars=Object.values(m)}
 if(!bars.length){document.getElementById('chart').innerHTML='<p class="note">Noch keine Daten — erster Lauf automatisch um 00:40 oder Button unten.</p>';return}
 const maxc=Math.max(...bars.map(b=>Math.max(b.r||0,b.o||0)),1);
 const n=bars.length;const total=Math.min(Math.max(Math.floor((n?700:700)/(2*n+8)),14),30);
 const bw=total,gap=6,W=n*(2*bw+gap)+40,H=140;let s='';
 bars.forEach((b,i)=>{const x=30+i*(2*bw+gap);
  const hr=Math.max((b.r||0)/maxc,0.003)*(H-30),ho=Math.max((b.o||0)/maxc,0.003)*(H-30);
  s+=`<rect x="${x}" y="${H-20-hr}" width="${bw}" height="${hr}" fill="#f0883e" opacity="0.85"/>`;
  s+=`<rect x="${x+bw}" y="${H-20-ho}" width="${bw}" height="${ho}" fill="#58a6ff" opacity="0.85"/>`;
  const lab=monthly?b.d.slice(5)+'.'+b.d.slice(2,4):b.d.slice(5);
  if(!monthly||true)s+=`<text x="${x+bw}" y="${H-6}" text-anchor="middle">${lab}</text>`});
 document.getElementById('chart').innerHTML=
  `<div style="overflow-x:auto"><svg viewBox="0 0 ${W} ${H}" style="width:100%;max-width:${W}px">${s}`+
  '<rect x="4" y="2" width="10" height="10" fill="#f0883e"/><text x="18" y="11">real</text>'+
  '<rect x="52" y="2" width="10" height="10" fill="#58a6ff"/><text x="66" y="11">Optimum</text></svg></div>'+
  (monthly?'<p class="note">Zeiträume über 31 Tage werden pro Kalendermonat summarisiert.</p>':'')}
document.querySelectorAll('.seg button').forEach(b=>b.onclick=()=>{
 document.querySelectorAll('.seg button').forEach(x=>x.classList.remove('on'));
 b.classList.add('on');period=b.dataset.p;render()});
anchorEl.onchange=render;

async function run(what){document.getElementById('b1').disabled=true;document.getElementById('b2').disabled=true;
try{const r=await fetch('run?what='+what);const d=await r.json();log(d.msg||'gestartet');poll();}
catch(e){log('Fehler: '+e);document.getElementById('b1').disabled=false;document.getElementById('b2').disabled=false;}}
function log(t){document.getElementById('runlog').textContent=t}
function poll(){fetch('api/run-status').then(r=>r.json()).then(d=>{document.getElementById('runlog').textContent=(d.log||[]).join('\\n');
if(d.running){setTimeout(poll,2000)}else{document.getElementById('b1').disabled=false;document.getElementById('b2').disabled=false;setTimeout(()=>location.reload(),1200)}})}

function fillSel(id,ents,current){const sel=document.createElement('select');sel.id=id;
let o=`<option value="">(nicht verwendet)</option>`;
ents.forEach(e=>{const v=e.entity_id;o+=`<option value="${v}"${v===current?' selected':''}>${esc(e.attributes?.friendly_name||v)}</option>`});
if(current&&!ents.some(e=>e.entity_id===current))o+=`<option value="${current}" selected>${esc(current)} (nicht in Liste)</option>`;
sel.innerHTML=o;return sel}
async function loadEntities(){
 try{const r=await fetch('api/ha-entities');HAENT=await r.json();ENT_LOADED=true;buildSensorFields()}
 catch(e){document.getElementById('sensorfelder').innerHTML='<p class="note">Sensoren konnten nicht geladen werden: '+esc(e)+'</p>'}}
function buildSensorFields(){const f=document.getElementById('sensorfelder');f.innerHTML='';
for(const key of EIDX){const div=document.createElement('div');div.className='feld';
const lab=document.createElement('label');lab.textContent=ENT_LABELS[key];div.appendChild(lab);
let list;const cur=curEnt(key);
if(key.startsWith('Wallbox verb'))list=(HAENT.binary||[]);
else if(key.startsWith('Preis'))list=(HAENT.price||[]);
else{list=(HAENT.power||[]);if(cur&&!list.some(e=>e.entity_id===cur))list=[{entity_id:cur,attributes:{}}].concat(list)}
div.appendChild(fillSel(eid(key),list,cur));f.appendChild(div)}}
function saveSettings(){
 const mode=document.querySelector('input[name=expmode]:checked').value;
 const ent={};for(const k of EIDX){const el=document.getElementById(eid(k));ent[k]=el?el.value.trim():''}
 const tarif={"Preis Bezug (Sensor)":ent["Preis Bezug"],"Einspeisung":mode==='sensor'?"Direktvermarktung":"Einspeisevergütung"};
 if(mode==='sensor')tarif["Sensor Einspeisung (nur bei Direktvermarktung)"]=ent[EXKEY];
 else{const f=num('s_pfx');if(f!=null)tarif["Feste Vergütung in €/kWh (nur bei Einspeisevergütung)"]=f}
 const hemsm={"Hersteller":val('s_hems'),"PV Leistung":ent["PV Leistung"],
  "Netz gesamt (negativ = Einspeisung)":ent["Netz gesamt (negativ = Einspeisung)"],
  "Netz Bezug (nur bei getrennten Sensoren)":ent["Netz Bezug (nur bei getrennten Sensoren)"],
  "Netz Einspeisung (nur bei getrennten Sensoren)":ent["Netz Einspeisung (nur bei getrennten Sensoren)"],
  "Hausverbrauch":ent["Hausverbrauch"]};
 if(val('s_pv'))hemsm["Wechselrichter"]=val('s_pv');
 const body={"Auflösung":val('s_res'),"HEMS":hemsm,
  "Speicher":{"Hersteller":val('s_batt'),"Kapazität in kWh":num('s_cap'),"Ladeleistung in kW":num('s_pc'),"Entladeleistung in kW":num('s_pd'),"Wirkungsgrad in %":num('s_eta')},
  "Wärmepumpe":{"Hersteller":val('s_hp'),"Sensor":ent["Wärmepumpe Leistung"],"Mindestleistung in kW":num('s_hpmin'),"Höchstleistung in kW":num('s_hpmax')},
  "Wallbox":{"Hersteller":val('s_wb'),"Sensor":ent["Wallbox Leistung"],"Auto anwesend (Binärsensor)":ent["Wallbox verbunden (optional)"],"Mindestleistung in kW":num('s_wbmin'),"Höchstleistung in kW":num('s_wbmax')},
  "Tarif":tarif};
 fetch('api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
 .then(r=>r.json()).then(d=>{document.getElementById('setlog').textContent=d.ok?'Gespeichert ✓ wirksam ab dem nächsten Lauf.':('Fehler: '+(d.error||JSON.stringify(d)))});
}
function val(id){const e=document.getElementById(id);return e?e.value.trim():''}
function num(id){const v=val(id);return v===''?null:parseFloat(v)}

['HEMS','WECHSELRICHTER','SPEICHER','WALLBOX','WAERMEPUMPE'].forEach(k=>{
 const dl=document.createElement('datalist');dl.id='m'+k;dl.innerHTML=MANU[k].map(m=>`<option value="${esc(m)}">`).join('');document.body.appendChild(dl)});
function fillSettings(){
 const H=SET["HEMS"]||{},S=SET["Speicher"]||{},W=SET["Wärmepumpe"]||{},B=SET["Wallbox"]||{},T=SET["Tarif"]||{};
 const d=SET["Geräte"]||SET.geraete||SET.devices||{},b=SET["Batterie"]||SET.batterie||SET.battery||{},gr=SET["Grenzwerte"]||SET.grenzwerte||{},e=SET["Einspeisung"]||{};
 s_hems.value=H.Hersteller||d.HEMS||d.hersteller_hems||d.ems_product||'';s_pv.value=H.Wechselrichter||d.Wechselrichter||d.hersteller_wechselrichter||d.pv_brand||'';
 s_batt.value=S.Hersteller||d.Speicher||d.hersteller_speicher||d.battery_brand||'';s_hp.value=W.Hersteller||d["Wärmepumpe"]||d.hersteller_waermepumpe||d.hp_brand||'';
 s_wb.value=B.Hersteller||d.Wallbox||d.hersteller_wallbox||d.wallbox_brand||'';
 s_cap.value=S["Kapazität in kWh"]??b["Kapazität in kWh"]??b.kapazitaet_kwh??b.capacity_kwh??'';s_pc.value=S["Ladeleistung in kW"]??b["Ladeleistung in kW"]??b.ladeleistung_kw??b.p_charge_max??'';
 s_pd.value=S["Entladeleistung in kW"]??b["Entladeleistung in kW"]??b.entladeleistung_kw??b.p_discharge_max??'';s_eta.value=S["Wirkungsgrad in %"]??b["Wirkungsgrad in %"]??b.wirkungsgrad_prozent??b.eta??'';
 s_hpmin.value=W["Mindestleistung in kW"]??gr["Wärmepumpe mindestens in kW"]??gr.waermepumpe_min_kw??'';s_hpmax.value=W["Höchstleistung in kW"]??gr["Wärmepumpe höchstens in kW"]??gr.waermepumpe_max_kw??'';
 s_wbmin.value=B["Mindestleistung in kW"]??gr["Wallbox mindestens in kW"]??gr.wallbox_min_kw??'';s_wbmax.value=B["Höchstleistung in kW"]??gr["Wallbox höchstens in kW"]??gr.wallbox_max_kw??'';
 const fx=T["Feste Vergütung in €/kWh (nur bei Einspeisevergütung)"]??e["Feste Vergütung in €/kWh"]??SET.preis_einspeisung_fix_eur??SET.price_exp_fixed??'';
 s_pfx.value=fx;
 ((T.Einspeisung==="Direktvermarktung")||(!T.Einspeisung&&(e.Sensor||!fx))?document.getElementById('exp_dyn'):document.getElementById('exp_fix')).checked=true;
 let rawRes=SET["Auflösung"]??SET["Auflösung_in_Sekunden"]??(SET.aufloesung_minuten?SET.aufloesung_minuten*60:(SET.resolution_min?SET.resolution_min*60:60));
 let sec={"10 Sekunden":10,"5 Minuten":300}[rawRes]||(+rawRes||60);
 if(![10,60,300].includes(sec))sec=60;
 s_res.value={10:"10 Sekunden",60:"1 Minute",300:"5 Minuten"}[sec];
 document.getElementById('sensorfelder').innerHTML='<p class="note">Sensoren werden geladen…</p>'}
fillSettings();buildSensorFieldsEmpty();render();
function curEnt(key){
 const H=SET["HEMS"]||{},W=SET["Wärmepumpe"]||{},B=SET["Wallbox"]||{},T=SET["Tarif"]||{};
 const V6={"PV Leistung":H["PV Leistung"],"Netz gesamt (negativ = Einspeisung)":H["Netz gesamt (negativ = Einspeisung)"],
  "Netz Bezug (nur bei getrennten Sensoren)":H["Netz Bezug (nur bei getrennten Sensoren)"],
  "Netz Einspeisung (nur bei getrennten Sensoren)":H["Netz Einspeisung (nur bei getrennten Sensoren)"],
  "Hausverbrauch":H["Hausverbrauch"],"Wärmepumpe Leistung":W.Sensor,"Wallbox Leistung":B.Sensor,
  "Wallbox verbunden (optional)":B["Auto anwesend (Binärsensor)"],"Preis Bezug":T["Preis Bezug (Sensor)"],
  "Preis Einspeisung (nur bei Direktvermarktung)":T["Sensor Einspeisung (nur bei Direktvermarktung)"]};
 if(V6[key])return V6[key];
 const nk=s=>s.toLowerCase().replace(/[^a-z0-9]/g,'');
 let s=SET["Sensoren"]||SET.sensoren||{};
 if(Array.isArray(s)){const m={};for(const e of s){if(e&&e["Funktion"]&&e["Entität"])m[e["Funktion"]]=e["Entität"]}s=m}
 for(const k in s){if(s[k]&&nk(k)===nk(key))return s[k]}
 const o=SET.entities||{};
 const OLD={"PV Leistung":"pv","Netz gesamt":"grid_net","Netz Bezug":"grid_import","Netz Einspeisung":"grid_export","Hausverbrauch":"house","Wärmepumpe":"hp","Wallbox Leistung":"wb","Wallbox verbunden":"wb_connected","Preis Bezug":"price_imp","Preis Einspeisung":"price_exp"};
 for(const pre in OLD){if(key.startsWith(pre)&&o[OLD[pre]])return o[OLD[pre]]}
 return ''}
function buildSensorFieldsEmpty(){const f=document.getElementById('sensorfelder');f.innerHTML='';
for(const key of EIDX){const div=document.createElement('div');div.className='feld';
const lab=document.createElement('label');lab.textContent=ENT_LABELS[key];div.appendChild(lab);
const inp=document.createElement('input');inp.type='text';inp.id=eid(key);inp.value=curEnt(key);
div.appendChild(inp);f.appendChild(div)}}
</script>
</body></html>"""


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
    dev = (opts.get("Geräte") or opts.get("geraete") or opts.get("devices") or {})
    safe_opts = {k: v for k, v in opts.items() if k != "token"}
    raw_res = (opts.get("Auflösung") or opts.get("Auflösung_in_Sekunden")
               or opts.get("aufloesung_minuten") or opts.get("resolution_min") or 1)
    RES = {"10 Sekunden": 10, "1 Minute": 60, "5 Minuten": 300}
    if str(raw_res) in RES:
        raw_res = RES[str(raw_res)]
    try:
        raw_res = int(raw_res)
    except Exception:
        raw_res = 60
    res_txt = {10: "10 s", 60: "1 min", 300: "5 min"}.get(raw_res, f"{raw_res} s")
    sub = (f"HEMS: {dev.get('HEMS') or dev.get('hersteller_hems') or dev.get('ems_product') or '–'}"
           f" · Speicher: {dev.get('Speicher') or dev.get('hersteller_speicher') or dev.get('battery_brand') or '–'}"
           f" · Auflösung {res_txt}"
           f" · Haushalt {opts.get('household_id', '')[:8] or '–'}")
    html = (HTML
            .replace("__CSS__", CSS)
            .replace("__METH__", METHodik)
            .replace("__VER__", version)
            .replace("__SUB__", sub)
            .replace("__DATA__", _esc_json(days))
            .replace("__SET__", _esc_json(safe_opts))
            .replace("__MANU__", _esc_json(manufacturers.MANUFACTURERS)))
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


_ALLOWED_SETTING_KEYS = {"Auflösung", "HEMS", "Speicher", "Wärmepumpe",
                         "Wallbox", "Tarif"}


def _mirror_to_supervisor():
    """Effektive Konfiguration in die Supervisor-Optionen zurückschreiben,
    damit der native HA-Konfigurationsreiter dasselbe anzeigt wie die
    Dashboard-Einstellungen. Auf die Schema-Schlüssel gefiltert (das Overlay
    kann Altbestände enthalten, die das neue Schema ablehnen würde).
    Best effort — Scheitern ist nicht kritisch."""
    try:
        merged = userconfig.load(_DATA_DIR, _OPTIONS_FILE)
        payload = {k: v for k, v in merged.items() if k in _ALLOWED_SETTING_KEYS}
        token = os.environ.get("SUPERVISOR_TOKEN", "")
        req = urllib.request.Request(
            "http://supervisor/addons/self/options",
            data=json.dumps({"options": payload}).encode(),
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        print(f"[web] Supervisor-Mirror der Optionen fehlgeschlagen: {e}")


def _ha_entities(opts):
    url = "http://supervisor/core/api/states"
    token = opts.get("token") or os.environ.get("SUPERVISOR_TOKEN", "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        states = json.loads(r.read().decode())
    power, price, binary = [], [], []
    for s in states:
        at = s.get("attributes") or {}
        eid = s.get("entity_id", "")
        dc = at.get("device_class")
        unit = str(at.get("unit_of_measurement", ""))
        if eid.startswith("binary_sensor."):
            if dc == "connectivity" or "wallbox" in eid or "charge" in eid or "connect" in eid:
                binary.append(s)
            continue
        if not eid.startswith("sensor."):
            continue  # number./input_number. etc. sind keine Messsensoren
        if dc in ("monetary", "currency") or unit in ("€/kWh", "EUR/kWh", "ct/kWh"):
            price.append(s)
        elif dc in ("power", "energy") or unit in ("W", "kW"):
            power.append(s)
    slim = lambda lst: [{"entity_id": s["entity_id"],
                         "attributes": {"friendly_name": s.get("attributes", {}).get("friendly_name", "")}}
                        for s in sorted(lst, key=lambda x: x["entity_id"])]
    return {"power": slim(power), "price": slim(price), "binary": slim(binary)}


def start_server(port, db_path, version, tz):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, data, ctype="application/json"):
            if isinstance(data, (dict, list)):
                data = json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
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
            if p == "/api/settings":
                o = userconfig.load(_DATA_DIR, _OPTIONS_FILE)
                o.pop("token", None)
                return self._send(200, {"settings": o,
                                        "manufacturers": manufacturers.MANUFACTURERS})
            if p == "/api/ha-entities":
                try:
                    return self._send(200, _ha_entities(userconfig.load(_DATA_DIR, _OPTIONS_FILE)))
                except Exception as e:
                    return self._send(502, {"error": str(e)})
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

        def do_POST(self):
            p = urllib.parse.urlparse(self.path).path.rstrip("/") or "/"
            if p == "/api/settings":
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    patch = json.loads(self.rfile.read(n).decode())
                except Exception as e:
                    return self._send(400, {"ok": False, "error": f"ungültiges JSON: {e}"})
                clean = {k: v for k, v in patch.items() if k in _ALLOWED_SETTING_KEYS}
                # None-Werte entfernen (leere Felder bedeuten 'unverändert')
                def prune(d):
                    return {k: prune(v) if isinstance(v, dict) else v
                            for k, v in d.items() if v is not None}
                userconfig.save(_DATA_DIR, prune(clean))
                _mirror_to_supervisor()
                return self._send(200, {"ok": True})
            return self.do_GET()

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"[web] Dashboard auf 0.0.0.0:{port}")
    return srv
