"""우측통행 방송국 — 조정실 서버.
유튜브(매일신문) 피드 + 매일신문 기사 원문을 재료로, 스탠스 문서 기준 앵커 대본을 만든다.
"""
import os, re, json, html, time, threading
from pathlib import Path
import base64, struct, feedparser, requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE = Path(__file__).parent
DATA = Path(os.environ.get("DATA_DIR", BASE / "data")); DATA.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = DATA / "settings.json"
SHOWS_FILE = DATA / "shows.json"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"}

# 입력 채널(재료). 추가는 여기에 한 줄.
SOURCES = json.loads(os.environ.get("SOURCES_JSON") or json.dumps([
    {"id": "maeil", "name": "매일신문", "youtube_channel_id": "UCGrraKQiTF-ml0KqPQ8mrUA", "rss": "https://www.imaeil.com/rss"},
]))

DEFAULT_SETTINGS = {
    "station": {"name": "우측통행", "sub": "RIGHT LANE NETWORK · 실제 헤드라인, 수상한 앵커들 · 가칭"},
    "cast": [
        {"id": "jikjin", "name": "한직진", "role": "메인 앵커", "voice": "강경·직설. 결론부터 말하고 에두르지 않는다. 숫자를 들이대고 바로 판정한다.", "catch": "\"이게 뉴스입니다.\"", "pitch": 0.8, "rate": 1.05},
        {"id": "bora", "name": "서보라", "role": "서브 앵커", "voice": "어이없어하는 리액션 담당. 직진이 센 말을 하면 \"아니 그걸 그렇게…\" 하면서 한 번 더 웃기게 받는다.", "catch": "\"진짜요? 진짜요.\"", "pitch": 1.25, "rate": 1.08},
        {"id": "fact", "name": "팩트-1", "role": "데스크 검증 로봇", "voice": "감정 없음. 숫자·출처·날짜만 말한다. 누가 단정하면 끼어들어 \"출처?\"라고 묻고, 확인되면 \"확인.\"이라고만 한다.", "catch": "\"출처?\"", "pitch": 0.6, "rate": 0.95},
    ],
    "stance": """[채널 정체성]
- 우리는 우파 시각의 젊은 뉴스 방송국이다. 노년층 정치 유튜브의 내용은 가져오되 포장은 완전히 새로 한다.
- 시청자: 20~40대. 틀어놓고 보는 사람과 쇼츠로 처음 만나는 사람 둘 다.

[기본 입장]
- 시장경제·개인의 자유·법치·튼튼한 안보와 한미동맹을 기본값으로 본다.
- 세금 인상, 규제 확대, 포퓰리즘 현금 살포, 반기업 정책은 비판 대상이다.
- 집권 세력이 어디든 권력 남용·내로남불·말 바꾸기는 그대로 때린다. 우리 편이라고 봐주지 않는다(그래야 신뢰가 쌓인다).
- 상대 진영의 "웃기지도 않은데 웃긴 척하는 콘텐츠"가 어떻게 먹혔는지 안다. 우리는 진짜 웃겨야 한다.

[다루는 것]
- 국회·정부 정책, 예산, 세금, 안보·외교, 노동·규제, 언론·사법 이슈, 우파 유튜브에서 그날 가장 많이 다룬 이슈.

[다루지 않는 것]
- 개인 사생활, 확인 안 된 폭로, 음모론. 다루더라도 "이런 주장이 돈다"까지만.""",
    "tone": """- 한직진은 세게, 서보라는 웃기게, 팩트-1은 차갑게. 셋의 대비가 포맷이다.
- 센 말은 의견으로 말한다: "이건 미친 정책이다"(O) / "누가 돈을 빼돌렸다"(X, 확인 전).
- 욕설은 순화한다. 광고 제한 피하기. "개판"까지는 되고 그 이상은 안 된다.
- 일상 뉴스는 담백하게, 간판 프로그램에서만 펀치라인을 쓴다.
- 쇼츠는 첫 문장이 훅이어야 한다. 3초 안에 "뭐?" 소리가 나와야 한다.""",
}

_lock = threading.Lock()
def load_json(p, default):
    try: return json.loads(p.read_text("utf-8"))
    except Exception: return default
def save_json(p, obj):
    with _lock: p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), "utf-8")

settings = {**DEFAULT_SETTINGS, **load_json(SETTINGS_FILE, {})}
shows = load_json(SHOWS_FILE, [])

app = FastAPI(title="우측통행 조정실")

# ---------- 재료 수집 ----------
_cache = {}
def cached(key, ttl, fn):
    now = time.time(); v = _cache.get(key)
    if v and now - v[0] < ttl: return v[1]
    out = fn(); _cache[key] = (now, out); return out

def yt_feed(channel_id, n=15):
    f = feedparser.parse(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")
    out = []
    for e in f.entries[:n]:
        out.append({"vid": e.get("yt_videoid"), "title": html.unescape(e.get("title", "")), "link": e.get("link"), "published": e.get("published", "")[:16].replace("T", " ")})
    return out

def site_rss(url, n=120):
    f = feedparser.parse(url)
    return [{"title": html.unescape(e.get("title", "")), "link": e.get("link"), "published": e.get("published", "")[:16], "summary": html.unescape(re.sub(r"<[^>]+>", " ", e.get("summary", "")))[:300]} for e in f.entries[:n]]

STOP = set("의 이 가 은 는 을 를 에 에서 로 으로 과 와 도 한 및 등 그 저 것 수 더 또 왜 뭐 LIVE 일타뉴스 매일신문 속보 단독".split())
def keywords(title):
    t = re.sub(r"[\[\]\"'“”‘’…·,.?!:()]", " ", title)
    return {w for w in t.split() if len(w) >= 2 and w not in STOP}

def article_body(link):
    try:
        r = requests.get(link, timeout=12, headers=UA); r.raise_for_status()
        i = r.text.find('itemprop="articleBody"')
        seg = r.text[i:i + 12000] if i > 0 else ""
        m = re.search(r'<meta property="og:description" content="([^"]*)"', r.text)
        txt = html.unescape(re.sub(r"<[^>]+>", " ", seg)); txt = re.sub(r"\s+", " ", txt).strip()
        return (txt[:2500] if len(txt) > 200 else (html.unescape(m.group(1)) if m else ""))
    except Exception:
        return ""

def transcript(vid):
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi(); tl = api.list(vid); t = tl.find_transcript(["ko"]); seg = t.fetch()
        txt = " ".join(s.text for s in seg)
        return txt[:6000], "자막"
    except Exception as ex:
        return "", type(ex).__name__

@app.get("/api/sources")
def api_sources():
    out = []
    for s in SOURCES:
        vids = cached("yt:" + s["id"], 600, lambda: yt_feed(s["youtube_channel_id"])) if s.get("youtube_channel_id") else []
        arts = cached("rss:" + s["id"], 600, lambda: site_rss(s["rss"])) if s.get("rss") else []
        for v in vids:
            kw = keywords(v["title"]); best = None; score = 0
            for a in arts:
                sc = len(kw & keywords(a["title"]))
                if sc > score: score, best = sc, a
            v["article"] = best if score >= 2 else None
        out.append({"id": s["id"], "name": s["name"], "videos": vids, "articles_total": len(arts)})
    return {"sources": out, "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S")}

# ---------- 대본 ----------
class Item(BaseModel):
    title: str
    vid: str | None = None
    link: str | None = None
    article_link: str | None = None
    article_title: str | None = None

class ScriptReq(BaseModel):
    items: list[Item]
    prog: str = "daily"
    length: str = "shorts"
    note: str = ""

PROG = {
    "daily": "일상 뉴스. 담백하고 빠르게. 이슈마다 사실→우리 의견 순. 농담은 가볍게 1~2개.",
    "sig": "간판 프로그램 \"웃긴 센 말\". 이슈 하나를 골라 끝까지 웃기게 조진다. 농담 비중 높게, 펀치라인은 한직진·서보라 티키타카로. 그래도 사실/주장 구분은 지킨다.",
    "fact": "팩트 데스크. 팩트-1이 주도. 이슈 속 주장을 하나씩 꺼내 출처와 대조하고 \"확인/미확인/반박\"으로 판정. 직진·보라는 짧게 반응.",
}
PROG_NAME = {"daily": "일상 뉴스", "sig": "웃긴 센 말", "fact": "팩트 데스크"}
LEN = {"shorts": "쇼츠 60초: 대사 8~12줄, 한 줄은 25자 안팎. 첫 줄이 훅.", "five": "5분 롱폼: 대사 35~50줄. 오프닝→이슈별 블록→클로징."}

def build_prompt(req: ScriptReq, material: list[dict]):
    st = settings
    cast_txt = "\n".join(f"- {c['name']} (id:{c['id']}, {c['role']}): {c['voice']} 입버릇 {c['catch']}" for c in st["cast"])
    mat = []
    for m in material:
        mat.append(f"### 이슈: {m['title']}\n- 유튜브: {m.get('link') or ''}\n- 자막: {m['transcript'] or '(없음: ' + m['transcript_note'] + ')'}\n- 기사: {m.get('article_title') or '(매칭 기사 없음)'} {m.get('article_link') or ''}\n- 기사 본문: {m['article'] or '(없음)'}")
    return f"""너는 가상 뉴스 방송국 "{st['station']['name']}"의 작가다. 아래 스탠스와 톤으로, 재료를 가지고 앵커 대본을 쓴다.

[출연진]
{cast_txt}

[프로그램] {PROG.get(req.prog, PROG['daily'])}
[길이] {LEN.get(req.length, LEN['shorts'])}

[스탠스 문서]
{st['stance']}

[톤 규칙]
{st['tone']}

[고정 규칙 — 무조건 지킨다]
1. 모든 대사에 kind를 단다: "사실"(기사 본문이나 자막으로 확인되는 것), "주장"(누군가가 제기했지만 확인 안 된 것 — 반드시 "~라는 주장", "~라고 한다"로 쓴다), "의견"(우리 스탠스의 평가·판정), "농담"(풍자).
2. 실존 인물의 말을 지어내지 않는다. 실존 인물을 흉내 내는 캐릭터를 만들지 않는다. 출연진은 위 캐릭터뿐이다.
3. 특정 지역·성별·출신·종교 집단 자체를 비하하지 않는다. 비판은 정책·행동·발언에 대해서만.
4. 확인 안 된 범죄·비리를 사실처럼 단정하지 않는다. 그런 내용은 kind "주장"으로만, 출처를 적는다. 재판 중인 사건은 판결·구속 등 확정된 사실만 "사실".
5. 욕설은 순화. 광고 제한을 피한다.
6. 재료에 없는 사실·숫자를 지어내지 않는다. 자막·기사에 있는 것만 쓴다.
7. "사실" 줄에는 source에 출처(기사 링크 또는 "매일신문 유튜브 자막")를 적는다. 재료에 근거가 없으면 kind를 "의견"으로 내리거나 빼라.
8. 유튜브 제목은 제목일 뿐이다. 제목의 따옴표 발언을 그대로 사실로 쓰지 말고, 자막·기사로 확인되면 "사실", 아니면 "주장".

[재료]
{chr(10).join(mat)}

[추가 지시] {req.note or '없음'}

[출력] JSON 하나만. 다른 글자 금지:
{{"title":"방송 제목(짧게)","hook":"첫 3초 훅 한 줄","segments":[{{"speaker":"jikjin|bora|fact","line":"대사","kind":"사실|주장|의견|농담","source":"출처 또는 빈 문자열"}}],"ticker":["티커용 한 줄 헤드라인 3~5개"],"issues_used":["다룬 이슈 제목"]}}"""

def call_claude(prompt, model):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key: raise HTTPException(503, "ANTHROPIC_API_KEY가 서버에 없다. Railway 변수에 넣어줘.")
    import anthropic
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(model=model, max_tokens=4000, messages=[{"role": "user", "content": prompt}])
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    m = re.search(r"\{.*\}", text, re.S)
    if not m: raise HTTPException(502, "모델이 JSON을 안 줬다. 다시 눌러줘.")
    return json.loads(m.group(0))

@app.post("/api/script")
def api_script(req: ScriptReq):
    if not req.items: raise HTTPException(400, "이슈를 하나 이상 골라줘.")
    material = []
    for it in req.items[:6]:
        tr, note = transcript(it.vid) if it.vid else ("", "영상 아님")
        art = article_body(it.article_link) if it.article_link else ""
        material.append({"title": it.title, "link": it.link, "transcript": tr, "transcript_note": note, "article": art, "article_title": it.article_title, "article_link": it.article_link})
    model = os.environ.get("SCRIPT_MODEL", "claude-sonnet-5-5")
    res = call_claude(build_prompt(req, material), model)
    segs = []
    ids = {c["id"] for c in settings["cast"]}
    for s in res.get("segments", []):
        segs.append({"speaker": s.get("speaker") if s.get("speaker") in ids else settings["cast"][0]["id"], "line": str(s.get("line", "")), "kind": s.get("kind") if s.get("kind") in ("사실", "주장", "의견", "농담") else "의견", "source": str(s.get("source", "")), "on": True})
    if not segs: raise HTTPException(502, "대본이 비었다. 다시 눌러줘.")
    res["segments"] = segs
    show = {"id": str(int(time.time() * 1000)), "createdAt": int(time.time() * 1000), "input": req.model_dump(), "result": res,
            "material": [{"title": m["title"], "transcript": bool(m["transcript"]), "transcript_note": m["transcript_note"], "article": bool(m["article"])} for m in material], "model": model, "program": PROG_NAME.get(req.prog, req.prog), "auto": False}
    show["audio"] = {"ready": False, "note": "생성 중" if os.environ.get("GEMINI_API_KEY") else "GEMINI_API_KEY 없음", "count": 0}
    shows.insert(0, show); del shows[60:]; save_json(SHOWS_FILE, shows)
    synth_async(show)
    return show

# ---------- 음성 (Gemini TTS = NotebookLM 계열) ----------
AUDIO = DATA / "audio"; AUDIO.mkdir(parents=True, exist_ok=True)
TTS_MODEL = os.environ.get("TTS_MODEL", "gemini-2.5-flash-preview-tts")
VOICES = {"jikjin": ("Charon", "단호하고 낮은 뉴스 앵커 톤, 빠르고 또렷하게"), "bora": ("Leda", "밝고 장난기 있는 공동 앵커, 어이없어하는 뉘앙스를 살려서"), "fact": ("Iapetus", "감정 없는 기계적 톤, 또박또박 천천히")}
def tts_segment(text, speaker):
    key = os.environ.get("GEMINI_API_KEY")
    if not key: return None, "GEMINI_API_KEY 없음"
    voice, style = VOICES.get(speaker, VOICES["jikjin"])
    body = {"contents": [{"parts": [{"text": f"{style}. 다음 대사를 한국어로 읽어라:\n{text}"}]}],
            "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}
    for attempt in range(4):
        r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{TTS_MODEL}:generateContent", params={"key": key}, json=body, timeout=90)
        if r.status_code == 429: time.sleep(12 * (attempt + 1)); continue
        if r.status_code != 200: return None, f"HTTP {r.status_code} {r.text[:120]}"
        try:
            part = r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
            pcm = base64.b64decode(part["data"]); mime = part.get("mimeType", "audio/L16;rate=24000")
            rate = int(re.search(r"rate=(\d+)", mime).group(1)) if "rate=" in mime else 24000
            hdr = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) + b"data" + struct.pack("<I", len(pcm))
            return hdr + pcm, "ok"
        except Exception as ex:
            return None, f"파싱 실패 {type(ex).__name__}"
    return None, "429 반복"

def synth_show(show):
    sid = show["id"]; d = AUDIO / sid; d.mkdir(exist_ok=True); errs = []
    for i, seg in enumerate(show["result"]["segments"]):
        f = d / f"{i}.wav"
        if f.exists(): continue
        wav, note = tts_segment(seg["line"], seg["speaker"])
        if wav: f.write_bytes(wav)
        else: errs.append(note); 
        if note != "ok" and "GEMINI_API_KEY" in note: break
    show["audio"] = {"ready": not errs, "note": (errs[0] if errs else "ok"), "count": len(list(d.glob("*.wav")))}
    save_json(SHOWS_FILE, shows)

def synth_async(show): threading.Thread(target=synth_show, args=(show,), daemon=True).start()

@app.get("/api/audio/{sid}/{i}.wav")
def api_audio(sid: str, i: int):
    f = AUDIO / sid / f"{i}.wav"
    if not f.exists(): raise HTTPException(404, "no audio")
    return Response(f.read_bytes(), media_type="audio/wav", headers={"Cache-Control": "public, max-age=86400"})

@app.post("/api/shows/{sid}/synth")
def api_synth(sid: str):
    sh = next((x for x in shows if x["id"] == sid), None)
    if not sh: raise HTTPException(404, "없음")
    synth_async(sh); return {"ok": True}

# ---------- 편성 ----------
SCHEDULE = [  # (시작시, 끝시, prog, length, name, 이슈 수)
    (6, 11, "daily", "shorts", "아침 직진", 3),
    (11, 17, "daily", "shorts", "점심 속보", 2),
    (17, 23, "daily", "five", "저녁 뉴스", 4),
    (23, 30, "fact", "shorts", "팩트 데스크", 2),
]
def kst_hour():
    return (time.gmtime().tm_hour + 9) % 24
def slot_for(hour=None):
    h = kst_hour() if hour is None else hour
    hh = h if h >= 6 else h + 24
    for a, b, prog, length, name, n in SCHEDULE:
        if a <= hh < b: return {"prog": prog, "length": length, "name": name, "n": n, "start": a % 24, "end": b % 24}
    return {"prog": "daily", "length": "shorts", "name": "일상 뉴스", "n": 2, "start": 0, "end": 24}
def next_slot():
    cur = slot_for(); return slot_for(cur["end"] % 24)

USED_FILE = DATA / "used.json"
used = load_json(USED_FILE, {})
AUTO_MIN = int(os.environ.get("AUTO_INTERVAL_MIN", "60"))
auto_state = {"last_run": None, "last_result": "아직 안 돌았음", "running": False}

def pick_items(n):
    src = api_sources()["sources"]
    cutoff = time.time() - 36 * 3600
    for k in [k for k, v in used.items() if v < cutoff]: used.pop(k, None)
    items = []
    for s in src:
        for v in s["videos"]:
            if v["vid"] in used: continue
            items.append(Item(title=v["title"], vid=v["vid"], link=v["link"], article_link=(v.get("article") or {}).get("link"), article_title=(v.get("article") or {}).get("title")))
            if len(items) >= n: break
    return items

def autopilot_once():
    if auto_state["running"]: return
    auto_state["running"] = True
    try:
        if not os.environ.get("ANTHROPIC_API_KEY"): auto_state["last_result"] = "API 키 없음"; return
        slot = slot_for(); items = pick_items(slot["n"])
        if not items: auto_state["last_result"] = "새 영상 없음"; return
        req = ScriptReq(items=items, prog=slot["prog"], length=slot["length"], note=f"프로그램명 {slot['name']}. 오프닝에 프로그램명을 한 번 말한다.")
        show = api_script(req); show["program"] = slot["name"]; show["auto"] = True
        save_json(SHOWS_FILE, shows)
        for it in items: used[it.vid] = time.time()
        save_json(USED_FILE, used)
        auto_state["last_result"] = f"{slot['name']} 생성: {show['result'].get('title','')}"
    except Exception as ex:
        auto_state["last_result"] = f"실패: {type(ex).__name__} {str(ex)[:120]}"
    finally:
        auto_state["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + 9 * 3600)); auto_state["running"] = False

def autopilot_loop():
    time.sleep(20)
    while True:
        try: autopilot_once()
        except Exception: pass
        time.sleep(AUTO_MIN * 60)
if os.environ.get("AUTOPILOT", "1") == "1":
    threading.Thread(target=autopilot_loop, daemon=True).start()

@app.get("/api/now")
def api_now():
    slot = slot_for(); nxt = next_slot()
    return {"kst": time.strftime("%H:%M", time.gmtime(time.time() + 9 * 3600)), "on_now": slot, "up_next": nxt,
            "schedule": [{"start": a % 24, "end": b % 24, "name": nm, "prog": p} for a, b, p, l, nm, n in SCHEDULE],
            "autopilot": {**auto_state, "interval_min": AUTO_MIN}, "shows": shows[:30], "station": settings["station"]}

@app.post("/api/autopilot/run")
def api_auto_run():
    threading.Thread(target=autopilot_once, daemon=True).start(); return {"ok": True}

class AskReq(BaseModel):
    q: str
@app.post("/api/ask")
def api_ask(req: AskReq):
    q = req.q.strip()[:300]
    if not q: raise HTTPException(400, "질문이 비었다.")
    st = settings
    cast_txt = "\n".join(f"- {c['name']} (id:{c['id']}, {c['role']}): {c['voice']}" for c in st["cast"])
    recent = "\n".join(f"- {s['result'].get('title','')}: " + " / ".join(x["line"] for x in s["result"]["segments"][:4]) for s in shows[:3])
    prompt = f"""너는 가상 뉴스 방송국 "{st['station']['name']}"의 작가다. 시청자가 데스크에 보낸 질문에 앵커들이 짧게 답한다.
[출연진]\n{cast_txt}\n[스탠스]\n{st['stance']}\n[톤]\n{st['tone']}
[고정 규칙] 사실을 지어내지 않는다. 모르면 팩트-1이 "확인된 바 없음"이라고 한다. 실존 인물 비하·확인 안 된 범죄 단정·집단 비하 금지. 욕설 순화.
[최근 방송]\n{recent or '(없음)'}
[시청자 질문] {q}
[출력] JSON 하나만: {{"segments":[{{"speaker":"jikjin|bora|fact","line":"대사","kind":"사실|주장|의견|농담","source":""}}]}} 대사 2~4줄."""
    res = call_claude(prompt, os.environ.get("ASK_MODEL", "claude-haiku-5-5"))
    return {"q": q, "segments": res.get("segments", [])}

@app.get("/api/shows")
def api_shows(): return shows

@app.delete("/api/shows/{sid}")
def api_del(sid: str):
    global shows
    shows = [s for s in shows if s["id"] != sid]; save_json(SHOWS_FILE, shows); return {"ok": True}

@app.get("/api/settings")
def api_settings(): return settings

class SettingsReq(BaseModel):
    station: dict | None = None
    cast: list | None = None
    stance: str | None = None
    tone: str | None = None

@app.put("/api/settings")
def api_settings_put(req: SettingsReq):
    for k, v in req.model_dump().items():
        if v is not None: settings[k] = v
    save_json(SETTINGS_FILE, settings); return settings

@app.get("/health")
def health(): return {"ok": True, "key": bool(os.environ.get("ANTHROPIC_API_KEY")), "tts": bool(os.environ.get("GEMINI_API_KEY"))}

@app.get("/")
def index(): return FileResponse(BASE / "static" / "index.html")
@app.get("/studio")
def studio(): return FileResponse(BASE / "static" / "studio.html")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
