// 우측통행 공용: 픽셀 캐릭터, 스튜디오 무대, 재생기
window.RL = (() => {
const $ = s => document.querySelector(s);
const el = (t, a = {}, ...kids) => { const e = document.createElement(t); for (const [k,v] of Object.entries(a)) { if (k==='class') e.className=v; else if (k==='text') e.textContent=v; else e.setAttribute(k,v); } for (const k of kids) if (k!=null) e.append(k); return e; };
const KST = { timeZone:'Asia/Seoul' };
const fmtWhen = ms => new Date(ms).toLocaleString('ko-KR',{...KST,month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'});

// 템플릿: H 머리, S 피부, T 상의, A 포인트, M 입, E 눈, . 투명
const TPL = {
  m: ['....HHHHHH....','...HHHHHHHH...','...HHSSSSSS...','...HSEESSEE...','...HSSSSSSSS..','....SSSSSS....','....SSMMSS....','.....SSSS.....','...TTTAATTT...','..TTTTAATTTT..','..TTTTAATTTT..','..TTTTTTTTTT..','..TTTTTTTTTT..','..TTTTTTTTTT..'],
  f: ['....HHHHHH....','...HHHHHHHH...','..HHSSSSSSH...','..HSEESSEEHH..','..HSSSSSSSSH..','..HHSSSSSSH...','...HSSMMSS....','....SSSS......','...TTTTTTTT...','..TTAAAAAATT..','..TTAAAAAATT..','..TTTTTTTTTT..','..TTTTTTTTTT..','..TTTTTTTTTT..'],
  robot: ['......A.......','......S.......','..HHHHHHHHHH..','..HSSSSSSSSH..','..HSEMMMMESH..','..HSEEEEEESH..','..HSSSSSSSSH..','..HHHHHHHHHH..','....HHHHHH....','..HHHHHHHHHH..','..HAHHHHHHAH..','..HHHHHHHHHH..','..HH......HH..','..HH......HH..']
};
let cast = [];
const setCast = c => { cast = c || []; };
const castOf = id => cast.find(c => c.id===id) || cast[0] || {id:'jikjin',name:'앵커',sprite:['m','#2A1F1A','#F1C9A5','#1E3A8A','#D7262D']};
function drawSprite(ctx, c, x, y, sc, open) {
  const [tpl, H, S, T, A] = c.sprite || ['m','#2A1F1A','#F1C9A5','#1E3A8A','#D7262D'];
  const rows = TPL[tpl] || TPL.m; const robot = tpl==='robot';
  rows.forEach((row,j) => [...row].forEach((ch,i) => {
    let col = null;
    if (ch==='H') col = H; else if (ch==='S') col = S; else if (ch==='T') col = T; else if (ch==='A') col = A;
    else if (ch==='E') col = robot ? '#0F7A4F' : '#222';
    else if (ch==='M') col = robot ? (open ? A : '#0F7A4F') : (open ? '#111' : '#C98F6A');
    if (!col) return; ctx.fillStyle = col; ctx.fillRect(x+i*sc, y+j*sc, sc, (open && ch==='M' && !robot) ? sc*1.6 : sc);
  }));
}
function portrait(id, size=14){ const c = el('canvas',{width:'14',height:'14'}); const x = c.getContext('2d'); x.fillStyle='#0B2238'; x.fillRect(0,0,14,14); drawSprite(x, castOf(id), 0, 0, 1, false); c.style.width = c.style.height = size+'px'; c.style.imageRendering='pixelated'; return c; }

// 무대: 현재 출연진 최대 3명 배치
let stageCanvas = null, onStage = ['jikjin','bora','fact'], speaking = null;
function mountStage(canvas){ stageCanvas = canvas; (function loop(t){ draw(t||0); requestAnimationFrame(loop); })(); }
function setOnStage(ids){ onStage = (ids && ids.length ? ids : ['jikjin','bora','fact']).slice(0,3); }
function setSpeaking(id){ speaking = id; }
function draw(t){
  if (!stageCanvas) return; const c = stageCanvas.getContext('2d');
  c.fillStyle='#0B2238'; c.fillRect(0,0,320,180); c.fillStyle='#132E4C'; c.fillRect(20,18,280,70);
  for (let i=0;i<40;i++){ c.fillStyle=(i*7+Math.floor(t/700))%5===0?'#F2B134':'#2B5278'; c.fillRect(24+(i*7)%272, 30+((i*13)%50), 2, 2); }
  c.fillStyle='#D7262D'; c.fillRect(132,22,56,16); c.fillStyle='#fff'; c.font='8px "Press Start 2P", monospace'; c.fillText('RL NEWS',137,34);
  c.fillStyle='#5A2A1E'; c.fillRect(0,128,320,52); c.fillStyle='#7A3A28'; c.fillRect(0,128,320,4); c.fillStyle='#F2B134'; c.fillRect(140,146,40,6);
  const open = speaking && Math.floor(t/120)%2===0;
  const xs = onStage.length===1 ? [125] : onStage.length===2 ? [80,170] : [50,125,200];
  onStage.forEach((id,i) => { const ch = castOf(id); const sc = ch.sprite && ch.sprite[0]==='robot' ? 4.2 : 5; drawSprite(c, ch, xs[i], ch.sprite && ch.sprite[0]==='robot' ? 68 : 62, sc, speaking===id && open); });
}

// 재생기: 서버 음성(wav) → 없으면 브라우저 TTS → 없으면 시간만
const player = new Audio(); let useTts = () => true;
function playFile(url){ return new Promise(res => { let d=false; const fin=()=>{ if(!d){d=true; player.onended=null; player.onerror=null; res(true);} };
  player.src = url; player.onended = fin; player.onerror = () => { if(!d){d=true; res(false);} }; player.play().catch(() => { if(!d){d=true; res(false);} }); }); }
const pickVoice = () => { try { return speechSynthesis.getVoices().find(v => /ko/i.test(v.lang)) || null; } catch { return null; } };
function say(text, c, url){ return new Promise(async res => {
  if (!useTts()) { setTimeout(res, Math.max(1200, text.length*110)); return; }
  if (url) { const ok = await playFile(url); if (ok) { res(); return; } }
  if (!('speechSynthesis' in window)) { setTimeout(res, Math.max(1200, text.length*110)); return; }
  try { const u = new SpeechSynthesisUtterance(text); const v = pickVoice(); if (v) u.voice = v; u.lang='ko-KR'; u.pitch=c.pitch||1; u.rate=c.rate||1; let d=false; const fin=()=>{ if(!d){d=true;res();} }; u.onend=fin; u.onerror=fin; speechSynthesis.speak(u); setTimeout(fin, Math.max(3000, text.length*220)); }
  catch { setTimeout(res, Math.max(1200, text.length*110)); } }); }
function stopAll(){ try{speechSynthesis.cancel();}catch{} try{player.pause(); player.currentTime=0;}catch{} }
const sleep = ms => new Promise(r => setTimeout(r, ms));
return { $, el, KST, fmtWhen, setCast, castOf, portrait, mountStage, setOnStage, setSpeaking, say, stopAll, sleep, setUseTts: f => { useTts = f; }, get cast(){ return cast; } };
})();
