"""우측통행 방송국 — 24시간 AI 뉴스국 서버 (PNN 구조의 한국판).
재료: 매일신문 유튜브 피드 + imaeil.com 기사 원문 + Open-Meteo 날씨.
편성표대로 서버가 대본을 자동 생성하고(Claude), 음성(Gemini TTS)을 붙이고, 시청 화면이 이어서 튼다.
"""
import os, re, json, html, time, threading, base64, struct
from pathlib import Path
import feedparser, requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE = Path(__file__).parent
DATA = Path(os.environ.get("DATA_DIR", BASE / "data")); DATA.mkdir(parents=True, exist_ok=True)
AUDIO = DATA / "audio"; AUDIO.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE, SHOWS_FILE, USED_FILE, Q_FILE = DATA / "settings.json", DATA / "shows.json", DATA / "used.json", DATA / "questions.json"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
KST_OFF = 9 * 3600
def kst(ts=None): return time.gmtime((ts if ts is not None else time.time()) + KST_OFF)
def kst_str(fmt="%Y-%m-%d %H:%M:%S", ts=None): return time.strftime(fmt, kst(ts))

# ---------- 입력 채널 ----------
SOURCES = json.loads(os.environ.get("SOURCES_JSON") or json.dumps([
    {"id": "maeil", "name": "매일신문", "youtube_channel_id": "UCGrraKQiTF-ml0KqPQ8mrUA", "rss": "https://www.imaeil.com/rss"},
]))
CATS = {  # 기사 분류 키워드 (프로그램별 재료 고르기)
    "경제": ["코스피", "코스닥", "주가", "환율", "금리", "증시", "삼성전자", "반도체", "부동산", "물가", "수출", "실적", "투자"],
    "스포츠": ["삼성 라이온즈", "대구FC", "KBO", "프로야구", "K리그", "축구", "야구", "골프", "손흥민", "배구", "농구", "올림픽"],
    "오피니언": ["[사설]", "[칼럼]", "[기고]", "[매일춘추]", "[야고부]", "[데스크칼럼]"],
}

# ---------- 출연진 (오리지널 캐릭터) ----------
# sprite: [템플릿(m/f/robot), 머리색, 피부색, 상의색, 포인트색]
DEFAULT_CAST = [
    {"id": "jikjin", "name": "한직진", "role": "메인 앵커", "voice": "강경·직설. 결론부터 말하고 에두르지 않는다. 숫자를 들이대고 바로 판정한다.", "catch": "\"이게 뉴스입니다.\"", "tts": ["Charon", "차분하고 낮은 목소리의 한국 뉴스 메인 앵커, 보통 말하는 속도로 또렷하게"], "sprite": ["m", "#2A1F1A", "#F1C9A5", "#1E3A8A", "#D7262D"]},
    {"id": "bora", "name": "서보라", "role": "공동 앵커", "voice": "어이없어하는 리액션 담당. 직진이 센 말을 하면 \"아니 그걸 그렇게…\" 하면서 한 번 더 웃기게 받는다.", "catch": "\"진짜요? 진짜요.\"", "tts": ["Leda", "밝은 목소리의 한국 뉴스 공동 앵커, 보통 속도로 살짝 웃음기 있게"], "sprite": ["f", "#7B4FC9", "#F1C9A5", "#F2B134", "#FFFFFF"]},
    {"id": "fact", "name": "팩트-1", "role": "데스크 검증 로봇", "voice": "감정 없음. 숫자·출처·날짜만 말한다. 누가 단정하면 끼어들어 \"출처?\"라고 묻고, 확인되면 \"확인.\"이라고만 한다.", "catch": "\"출처?\"", "tts": ["Iapetus", "담담한 남성 아나운서, 보통 속도로 또박또박 감정 없이"], "sprite": ["robot", "#3A4150", "#8A94A6", "#3A4150", "#F2B134"]},
    {"id": "jang", "name": "장마감", "role": "경제 특파원", "voice": "장 끝나면 말이 많아지는 사람. 숫자에 흥분하지만 결론은 항상 \"그래서 내 계좌는\".", "catch": "\"마감했습니다.\"", "tts": ["Orus", "활기찬 경제 기자 톤, 보통 속도"], "sprite": ["m", "#111111", "#E8B894", "#2F6B4F", "#F2B134"]},
    {"id": "min", "name": "뛰는민", "role": "현장 기자", "voice": "항상 숨이 찬다. 현장에 있는 척하지만 사실은 기사 읽는 중. 그래도 열정적.", "catch": "\"현장에서 전해드립니다!\"", "tts": ["Puck", "숨 가쁜 현장 기자, 열정적이지만 보통 속도"], "sprite": ["m", "#5A3A1E", "#F1C9A5", "#C0392B", "#FFFFFF"]},
    {"id": "nora", "name": "구름진 박사", "role": "기상 캐스터", "voice": "날씨를 과학으로만 말한다. 비 오면 기쁘다. 숫자는 관측 데이터만.", "catch": "\"하늘은 거짓말을 안 합니다.\"", "tts": ["Aoede", "차분한 여성 기상 캐스터, 또박또박"], "sprite": ["f", "#1F2A44", "#F5D6C0", "#2B7A9E", "#FFFFFF"]},
    {"id": "gitaek", "name": "기택", "role": "IT·인터넷 문화", "voice": "모든 걸 밈으로 설명한다. 옛날 게임과 커뮤니티 용어에 밝다.", "catch": "\"그거 밈인데요.\"", "tts": ["Fenrir", "장난기 있는 남성, 보통 속도"], "sprite": ["m", "#8B5A2B", "#F1C9A5", "#333333", "#7B4FC9"]},
    {"id": "judge", "name": "망치 판사", "role": "직진 법정 판사", "voice": "모든 분쟁을 3분 안에 판결한다. 판결문이 짧고 가혹하다.", "catch": "\"기각.\"", "tts": ["Algieba", "위엄 있는 중년 판사, 보통 속도"], "sprite": ["m", "#DDDDDD", "#E8B894", "#111111", "#F2B134"]},
    {"id": "earl", "name": "얼룩 경위", "role": "법정 경위", "voice": "판사 말을 복창한다. 가끔 판결에 토를 단다.", "catch": "\"정숙!\"", "tts": ["Enceladus", "낮고 무뚝뚝한 남성"], "sprite": ["m", "#2A1F1A", "#C98F6A", "#1E3A8A", "#DDDDDD"]},
    {"id": "park", "name": "박여사", "role": "소주 북클럽 진행", "voice": "오늘의 칼럼을 읽어왔다고 주장한다. 두 잔째부터 솔직해진다.", "catch": "\"한 잔 하고 얘기해요.\"", "tts": ["Kore", "따뜻한 중년 여성, 보통 속도"], "sprite": ["f", "#4A2C2A", "#F5D6C0", "#8E3B46", "#F2B134"]},
    {"id": "jeong", "name": "정여사", "role": "북클럽 패널", "voice": "칼럼을 끝까지 읽은 유일한 사람. 그래서 화가 나 있다.", "catch": "\"그건 2문단에 있어요.\"", "tts": ["Zephyr", "또렷한 여성"], "sprite": ["f", "#111111", "#F1C9A5", "#2F6B4F", "#FFFFFF"]},
    {"id": "oh", "name": "오여사", "role": "북클럽 패널", "voice": "칼럼은 안 읽고 제목만 봤다. 그래도 의견이 제일 세다.", "catch": "\"제목이 다 말해주잖아.\"", "tts": ["Callirrhoe", "쾌활한 여성"], "sprite": ["f", "#C0392B", "#F5D6C0", "#1F2A44", "#F2B134"]},
    {"id": "jisu", "name": "지수", "role": "동전 넣기 진행", "voice": "옛날 오락실 게임을 인생 교훈으로 연결한다.", "catch": "\"코인 하나 더.\"", "tts": ["Sulafat", "밝은 여성, 보통 속도"], "sprite": ["f", "#F2B134", "#F1C9A5", "#111111", "#D7262D"]},
    {"id": "mintae", "name": "민태", "role": "동전 넣기 공동 진행", "voice": "게임은 못하는데 역사는 다 안다.", "catch": "\"그건 1992년 작이고요.\"", "tts": ["Achird", "느긋한 남성"], "sprite": ["m", "#111111", "#E8B894", "#7B4FC9", "#FFFFFF"]},
    {"id": "simya", "name": "강심야", "role": "늦은 직진 진행", "voice": "심야 토크쇼 호스트. 느리고 능글맞다. 게스트를 몰아세운 뒤 커피를 권한다.", "catch": "\"밤이 깊었습니다.\"", "tts": ["Rasalgethi", "느긋하고 낮은 심야 라디오 DJ 톤"], "sprite": ["m", "#3A3A3A", "#E8B894", "#5A2A1E", "#F2B134"]},
    {"id": "sammi", "name": "새미", "role": "아침 체크인", "voice": "아침에만 나온다. 너무 상쾌해서 수상하다.", "catch": "\"좋은 아침입니다, 정말로.\"", "tts": ["Laomedeia", "상쾌한 아침 라디오 톤"], "sprite": ["f", "#8B5A2B", "#F5D6C0", "#2B7A9E", "#F2B134"]},
]
DEFAULT_SETTINGS = {
    "station": {"name": "우측통행", "en": "RIGHT LANE NETWORK", "sub": "실제 헤드라인, 수상한 앵커들.", "monitor": "RL-9000"},
    "cast": DEFAULT_CAST,
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

# ---------- 편성표 (PNN 구조의 한국판, 서울 시간) ----------
# (시작, 끝, prog, length, 이름, 재료 수, 요일(None=매일, 0=월 … 6=일))
SCHEDULE = [
    ("00:00", "04:00", "late",    "shorts", "심야 직진", 2, None),
    ("04:00", "04:30", "rabbit",  "shorts", "토끼굴", 1, None),
    ("04:30", "06:30", "morning", "shorts", "아침 직진", 3, None),
    ("06:30", "06:40", "bell",    "shorts", "개장 종", 2, None),
    ("06:40", "09:00", "morning", "shorts", "아침 직진", 3, None),
    ("09:00", "09:15", "sermon",  "shorts", "일요 훈계", 1, 6),
    ("09:00", "11:00", "daily",   "shorts", "낮 직진", 2, None),
    ("11:00", "11:15", "fact",    "shorts", "팩트 데스크", 2, None),
    ("11:15", "13:30", "daily",   "shorts", "낮 직진", 2, None),
    ("13:30", "13:40", "stonks",  "shorts", "주식 직진", 2, None),
    ("13:40", "14:00", "daily",   "shorts", "낮 직진", 2, None),
    ("14:00", "14:03", "court",   "shorts", "직진 법정", 0, None),
    ("14:03", "15:00", "daily",   "shorts", "낮 직진", 2, None),
    ("15:00", "15:10", "club",    "shorts", "소주 북클럽", 1, None),
    ("15:10", "16:00", "daily",   "shorts", "낮 직진", 2, None),
    ("16:00", "16:10", "sports",  "shorts", "스포츠 직진", 2, None),
    ("16:10", "16:20", "weather", "shorts", "날씨 센터", 0, None),
    ("16:20", "17:00", "daily",   "shorts", "낮 직진", 2, None),
    ("17:00", "20:00", "evening", "five",   "저녁 직진", 4, None),
    ("20:00", "20:10", "coin",    "shorts", "동전 넣기", 0, None),
    ("20:10", "20:40", "sig",     "five",   "웃긴 센 말", 1, 4),
    ("20:10", "21:00", "evening", "five",   "저녁 직진", 4, None),
    ("21:00", "21:30", "talk",    "shorts", "늦은 직진", 2, None),
    ("21:30", "23:00", "lateed",  "shorts", "늦은 판", 3, None),
    ("23:00", "24:00", "after",   "shorts", "심야 뒷풀이", 2, None),
]
PROGRAMS = {
    "심야 직진":  {"time": "00:00–04:00", "prog": "late",    "desc": "느슨한 심야 뉴스. 밤에 올라온 이슈를 농담 섞어 가볍게.", "hosts": ["jang", "bora"]},
    "토끼굴":     {"time": "04:00–04:30", "prog": "rabbit",  "desc": "유튜브에 떠도는 설 하나를 끝까지 파고든다. 뛰는민이 파고 팩트-1이 말린다.", "hosts": ["min", "fact"]},
    "아침 직진":  {"time": "04:30–09:00", "prog": "morning", "desc": "밤사이 매일신문이 다룬 이슈를 출근길 분량으로. 새미의 아침 체크인 포함.", "hosts": ["jikjin", "bora", "sammi"]},
    "개장 종":    {"time": "06:30–06:40", "prog": "bell",    "desc": "장 열리기 전 경제 기사 정리. 장마감이 흥분한다.", "hosts": ["bora", "jang"]},
    "일요 훈계":  {"time": "일 09:00–09:15", "prog": "sermon", "desc": "장마감이 자유시장과 자기책임을 설교한다. 서보라가 아멘 대신 한숨.", "hosts": ["jang", "bora"]},
    "낮 직진":    {"time": "09:00–17:00", "prog": "daily",   "desc": "낮 동안 올라오는 이슈를 받는 대로. 한직진 판정, 서보라 리액션.", "hosts": ["jikjin", "bora"]},
    "팩트 데스크": {"time": "11:00–11:15", "prog": "fact",   "desc": "유튜브에 도는 주장을 팩트-1이 기사와 대조해 확인·미확인·반박으로 판정.", "hosts": ["fact", "jikjin"]},
    "주식 직진":  {"time": "13:30–13:40", "prog": "stonks",  "desc": "오늘 증시·경제 기사. 숫자는 기사에 있는 것만.", "hosts": ["bora", "jang"]},
    "직진 법정":  {"time": "14:00–14:03", "prog": "court",   "desc": "소액 분쟁을 3분 안에 판결하는 법정극. 전부 가상의 사건.", "hosts": ["judge", "earl"]},
    "소주 북클럽": {"time": "15:00–15:10", "prog": "club",   "desc": "오늘의 사설·칼럼 하나를 놓고 여사 셋이 소주 한잔. 읽은 사람은 한 명.", "hosts": ["park", "jeong", "oh"]},
    "스포츠 직진": {"time": "16:00–16:10", "prog": "sports", "desc": "삼성 라이온즈·대구FC·KBO·축구. 한직진이 유일하게 웃는 시간.", "hosts": ["jikjin", "gitaek"]},
    "날씨 센터":  {"time": "16:10–16:20", "prog": "weather", "desc": "대구·서울 실제 관측 데이터(Open-Meteo)로 구름진 박사가 날씨를 읽는다.", "hosts": ["nora"]},
    "저녁 직진":  {"time": "17:00–21:00", "prog": "evening", "desc": "오늘 이슈 4개, 5분 정리. 하루의 메인 뉴스. 시청자 질문도 여기서 읽는다.", "hosts": ["jikjin", "bora", "fact"]},
    "동전 넣기":  {"time": "20:00–20:10", "prog": "coin",    "desc": "옛날 오락실·콘솔 게임 하나를 추억한다. 뉴스 아님.", "hosts": ["jisu", "mintae", "gitaek"]},
    "웃긴 센 말": {"time": "금 20:10–20:40", "prog": "sig",   "desc": "주 1회 간판. 이번 주 최악 하나를 끝까지 웃기게 조진다.", "hosts": ["jikjin", "bora"]},
    "늦은 직진":  {"time": "21:00–21:30", "prog": "talk",    "desc": "강심야의 심야 토크. 한직진을 앉혀놓고 오늘 제일 센 말을 다시 따진다.", "hosts": ["simya", "jikjin"]},
    "늦은 판":    {"time": "21:30–23:00", "prog": "lateed",  "desc": "하루 마감 뉴스. 내일 볼 것까지.", "hosts": ["jikjin", "fact"]},
    "심야 뒷풀이": {"time": "23:00–24:00", "prog": "after",  "desc": "느슨하고 기이한 심야 잡담. 장마감·서보라, 기택은 안락의자.", "hosts": ["jang", "bora", "gitaek"]},
}
PROG = {  # 프로그램별 작법
    "daily":   "일상 뉴스. 담백하고 빠르게. 이슈마다 사실→우리 의견 순. 농담은 가볍게 1~2개.",
    "morning": "아침 뉴스. 밝고 빠르게. 오프닝은 새미(sammi)의 과하게 상쾌한 아침 인사 한 줄, 그다음 한직진·서보라가 이슈 정리.",
    "evening": "저녁 메인 뉴스. 이슈별 블록(사실→주장 구분→의견). 팩트-1이 최소 두 번 끼어든다. 마지막에 시청자 질문이 있으면 읽는다.",
    "late":    "심야 뉴스. 느슨하고 가볍게, 농담 비중 높게. 장마감(jang)과 서보라 진행.",
    "lateed":  "하루 마감 뉴스. 한직진이 오늘을 정리하고 팩트-1이 '내일 볼 것'을 날짜·일정 중심으로 읽는다.",
    "after":   "심야 잡담. 뉴스는 가볍게 한 번만 스치고 나머지는 장마감·서보라·기택의 기이한 농담. 기택(gitaek)은 안락의자에서 한마디씩.",
    "rabbit":  "'토끼굴'. 유튜브에 떠도는 주장 하나를 뛰는민(min)이 흥분해서 파고들고, 팩트-1이 기사와 대조해 계속 말린다. 결론은 반드시 팩트-1의 판정(확인/미확인/반박)으로 끝낸다. 음모론을 사실처럼 말하지 않는다.",
    "bell":    "'개장 종'. 장 열리기 전 경제 기사 정리. 서보라 진행, 장마감(jang)이 숫자에 흥분. 숫자는 기사에 있는 것만. 투자 권유 금지, '투자 판단은 본인 몫'을 한 번 말한다.",
    "stonks":  "'주식 직진'. 오늘 증시·경제 기사. 장마감(jang)이 설명, 서보라가 '그래서 사요 팔아요?'로 받으면 장마감은 절대 답 안 한다. 투자 권유 금지.",
    "sermon":  "'일요 훈계'. 장마감(jang)이 자유시장·자기책임·장기투자를 설교 톤으로 말하고 서보라가 한숨으로 받는다. 특정 종목·코인 권유 금지.",
    "fact":    "'팩트 데스크'. 팩트-1이 주도. 이슈 속 주장을 하나씩 꺼내 출처와 대조하고 '확인/미확인/반박'으로 판정. 한직진은 짧게 반응.",
    "court":   "'직진 법정'. 가상의 소액 분쟁(예: 층간소음, 배달 음식 누락, 중고거래 노쇼)을 망치 판사(judge)가 3분 안에 판결. 얼룩 경위(earl)가 복창·토 달기. 원고·피고 대사는 판사가 요약해서 읽는다. 실존 인물·실제 사건 금지. 뉴스 재료는 쓰지 않는다.",
    "club":    "'소주 북클럽'. 오늘의 사설·칼럼 하나를 놓고 박여사(park) 진행, 정여사(jeong)는 끝까지 읽어서 화남, 오여사(oh)는 제목만 봤는데 의견이 제일 셈. 칼럼의 실제 논지는 정확히 전달하고 평가는 의견으로.",
    "sports":  "'스포츠 직진'. 삼성 라이온즈·대구FC·KBO·축구 기사. 한직진이 유일하게 웃는 시간, 기택(gitaek)이 밈으로 받는다. 점수·기록은 기사에 있는 것만.",
    "weather": "'날씨 센터'. 구름진 박사(nora)가 제공된 관측·예보 데이터만으로 대구·서울 날씨를 읽는다. 데이터에 없는 숫자 금지. 비 오면 기뻐한다.",
    "coin":    "'동전 넣기'. 옛날 오락실·콘솔 게임 하나(예: 테트리스, 보글보글, 스트리트 파이터 2, 슈퍼마리오)를 지수(jisu)·민태(mintae)·기택(gitaek)이 추억한다. 민태는 연도·제작사 같은 사실을 정확히 말하고 틀릴 것 같으면 '아마'를 붙인다. 뉴스 재료는 쓰지 않는다.",
    "talk":    "'늦은 직진' 토크쇼. 강심야(simya)가 진행, 한직진이 게스트. 강심야가 오늘 한직진이 한 센 말을 다시 캐묻고 한직진이 근거를 댄다. 느리고 능글맞게.",
    "sig":     "간판 프로그램 '웃긴 센 말'. 이슈 하나를 골라 끝까지 웃기게 조진다. 농담 비중 높게, 펀치라인은 한직진·서보라 티키타카로. 그래도 사실/주장 구분은 지킨다.",
}
PROG_NAME = {v["prog"]: k for k, v in PROGRAMS.items()}
LEN = {"shorts": "쇼츠 60초: 대사 8~12줄, 한 줄은 25자 안팎. 첫 줄이 훅.", "five": "5분 롱폼: 대사 35~50줄. 오프닝→이슈별 블록→클로징."}
NO_NEWS = {"court", "coin", "weather"}
CAT_OF = {"bell": "경제", "stonks": "경제", "sermon": "경제", "club": "오피니언", "sports": "스포츠"}
ONCE_PER_SLOT = {"rabbit", "bell", "sermon", "fact", "stonks", "court", "club", "sports", "weather", "coin", "sig", "talk"}

# ---------- 저장 ----------
_lock = threading.Lock()
def load_json(p, default):
    try: return json.loads(p.read_text("utf-8"))
    except Exception: return default
def save_json(p, obj):
    with _lock: p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), "utf-8")
settings = {**DEFAULT_SETTINGS, **load_json(SETTINGS_FILE, {})}
have = {c["id"] for c in settings["cast"]}; settings["cast"] += [c for c in DEFAULT_CAST if c["id"] not in have]
for c in settings["cast"]:
    d = next((x for x in DEFAULT_CAST if x["id"] == c["id"]), None)
    if d: c.setdefault("tts", d["tts"]); c.setdefault("sprite", d["sprite"])
settings["station"] = {**DEFAULT_SETTINGS["station"], **settings.get("station", {})}
shows = load_json(SHOWS_FILE, []); used = load_json(USED_FILE, {}); questions = load_json(Q_FILE, [])

app = FastAPI(title="우측통행")

# ---------- 재료 수집 ----------
_cache = {}
def cached(key, ttl, fn):
    now = time.time(); v = _cache.get(key)
    if v and now - v[0] < ttl: return v[1]
    out = fn(); _cache[key] = (now, out); return out
def yt_feed(channel_id, n=15):
    f = feedparser.parse(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")
    return [{"vid": e.get("yt_videoid"), "title": html.unescape(e.get("title", "")), "link": e.get("link"), "published": e.get("published", "")[:16].replace("T", " ")} for e in f.entries[:n]]
def site_rss(url, n=200):
    f = feedparser.parse(url)
    return [{"title": html.unescape(e.get("title", "")), "link": e.get("link"), "published": e.get("published", "")[:16], "summary": html.unescape(re.sub(r"<[^>]+>", " ", e.get("summary", "")))[:300]} for e in f.entries[:n]]
STOP = set("의 이 가 은 는 을 를 에 에서 로 으로 과 와 도 한 및 등 그 저 것 수 더 또 왜 뭐 LIVE 일타뉴스 매일신문 속보 단독".split())
def keywords(title):
    t = re.sub(r"[\[\]\"'“”‘’…·,.?!:()]", " ", title); return {w for w in t.split() if len(w) >= 2 and w not in STOP}
def article_body(link):
    try:
        r = requests.get(link, timeout=12, headers=UA); r.raise_for_status()
        i = r.text.find('itemprop="articleBody"'); seg = r.text[i:i + 12000] if i > 0 else ""
        m = re.search(r'<meta property="og:description" content="([^"]*)"', r.text)
        txt = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", seg))).strip()
        return txt[:2500] if len(txt) > 200 else (html.unescape(m.group(1)) if m else "")
    except Exception: return ""
def transcript(vid):
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        seg = YouTubeTranscriptApi().list(vid).find_transcript(["ko"]).fetch()
        return " ".join(s.text for s in seg)[:6000], "자막"
    except Exception as ex: return "", type(ex).__name__
def all_sources():
    out = []
    for s in SOURCES:
        vids = cached("yt:" + s["id"], 600, lambda: yt_feed(s["youtube_channel_id"])) if s.get("youtube_channel_id") else []
        arts = cached("rss:" + s["id"], 600, lambda: site_rss(s["rss"])) if s.get("rss") else []
        for v in vids:
            kw = keywords(v["title"]); best, score = None, 0
            for a in arts:
                sc = len(kw & keywords(a["title"]))
                if sc > score: score, best = sc, a
            v["article"] = best if score >= 2 else None
        out.append({"id": s["id"], "name": s["name"], "videos": vids, "articles": arts})
    return out
@app.get("/api/sources")
def api_sources():
    src = all_sources()
    return {"sources": [{"id": s["id"], "name": s["name"], "videos": s["videos"], "articles_total": len(s["articles"])} for s in src], "fetched_at": kst_str()}
def weather_data():
    def one(name, lat, lon):
        try:
            r = requests.get("https://api.open-meteo.com/v1/forecast", params={"latitude": lat, "longitude": lon, "current": "temperature_2m,weather_code,wind_speed_10m,relative_humidity_2m,precipitation", "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code", "timezone": "Asia/Seoul", "forecast_days": 3}, timeout=12)
            d = r.json(); return {"city": name, "current": d.get("current"), "daily": d.get("daily")}
        except Exception as ex: return {"city": name, "error": type(ex).__name__}
    return cached("wx", 1800, lambda: [one("대구", 35.87, 128.60), one("서울", 37.57, 126.98)])
@app.get("/api/weather")
def api_weather(): return weather_data()

# ---------- 대본 ----------
class Item(BaseModel):
    title: str
    vid: str | None = None
    link: str | None = None
    article_link: str | None = None
    article_title: str | None = None
class ScriptReq(BaseModel):
    items: list[Item] = []
    prog: str = "daily"
    length: str = "shorts"
    note: str = ""
    program: str | None = None

def build_prompt(req: ScriptReq, material, extra=""):
    st = settings
    hosts = PROGRAMS.get(req.program or PROG_NAME.get(req.prog, ""), {}).get("hosts")
    cast = [c for c in st["cast"] if not hosts or c["id"] in hosts or c["id"] == "fact"]
    cast_txt = "\n".join(f"- {c['name']} (id:{c['id']}, {c['role']}): {c['voice']} 입버릇 {c['catch']}" for c in cast)
    mat = "\n".join(f"### 이슈: {m['title']}\n- 유튜브: {m.get('link') or ''}\n- 자막: {m['transcript'] or '(없음: ' + m['transcript_note'] + ')'}\n- 기사: {m.get('article_title') or '(매칭 기사 없음)'} {m.get('article_link') or ''}\n- 기사 본문: {m['article'] or '(없음)'}" for m in material) or "(이 프로그램은 뉴스 재료를 쓰지 않는다)"
    return f"""너는 가상 뉴스 방송국 "{st['station']['name']}"의 작가다. 아래 스탠스와 톤으로, 이 프로그램의 작법대로 대본을 쓴다. 오늘은 {kst_str('%Y년 %m월 %d일')}이다.

[출연진 — 이 프로그램에 나오는 사람만 쓴다. speaker는 id로]
{cast_txt}

[프로그램] {req.program or PROG_NAME.get(req.prog, '')}: {PROG.get(req.prog, PROG['daily'])}
[길이] {LEN.get(req.length, LEN['shorts'])}

[스탠스 문서]
{st['stance']}

[톤 규칙]
{st['tone']}

[고정 규칙 — 무조건 지킨다]
1. 모든 대사에 kind를 단다: "사실"(기사 본문·자막·제공 데이터로 확인되는 것), "주장"(누군가 제기했지만 확인 안 된 것 — 반드시 "~라는 주장", "~라고 한다"로), "의견"(우리 스탠스의 평가·판정), "농담"(풍자).
2. 실존 인물의 말을 지어내지 않는다. 실존 인물을 흉내 내는 캐릭터를 만들지 않는다. 출연진은 위 캐릭터뿐이다.
3. 특정 지역·성별·출신·종교 집단 자체를 비하하지 않는다. 비판은 정책·행동·발언에 대해서만.
4. 확인 안 된 범죄·비리를 사실처럼 단정하지 않는다. 그런 내용은 kind "주장"으로만, 출처를 적는다. 재판 중인 사건은 판결·구속 등 확정된 사실만 "사실".
5. 욕설은 순화. 광고 제한을 피한다.
6. 재료에 없는 사실·숫자를 지어내지 않는다. 자막·기사·제공 데이터에 있는 것만 쓴다.
7. "사실" 줄에는 source에 출처(기사 링크, "매일신문 유튜브 자막", "Open-Meteo")를 적는다. 근거가 없으면 kind를 "의견"으로 내리거나 뺀다.
8. 유튜브 제목은 제목일 뿐이다. 제목의 따옴표 발언을 그대로 사실로 쓰지 말고, 자막·기사로 확인되면 "사실", 아니면 "주장".
9. 오프닝에서 프로그램명을 한 번 말한다.

[재료]
{mat}
{extra}
[추가 지시] {req.note or '없음'}

[출력] JSON 하나만. 다른 글자 금지:
{{"title":"방송 제목(짧게)","hook":"첫 3초 훅 한 줄","segments":[{{"speaker":"출연진 id","line":"대사","kind":"사실|주장|의견|농담","source":"출처 또는 빈 문자열"}}],"ticker":["티커용 한 줄 헤드라인 3~5개"],"issues_used":["다룬 이슈 제목"]}}"""

def call_claude(prompt, model):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key: raise HTTPException(503, "ANTHROPIC_API_KEY가 서버에 없다.")
    import anthropic
    client = anthropic.Anthropic(api_key=key); last = ""
    for attempt in range(2):
        msg = client.messages.create(model=model, max_tokens=8000, system="너는 JSON만 출력하는 작가다. 설명·코드펜스 없이 JSON 객체 하나만 출력한다.", messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip(); last = text[:200]
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try: return json.loads(m.group(0))
            except Exception: pass
        (DATA / "last_bad.txt").write_text(text, "utf-8")
    raise HTTPException(502, f"모델이 JSON을 안 줬다: {last[:80]}")

def make_show(req: ScriptReq):
    material = []
    for it in req.items[:6]:
        tr, note = transcript(it.vid) if it.vid else ("", "영상 아님")
        art = article_body(it.article_link) if it.article_link else ""
        material.append({"title": it.title, "link": it.link, "transcript": tr, "transcript_note": note, "article": art, "article_title": it.article_title, "article_link": it.article_link})
    extra = ""
    if req.prog == "weather":
        extra = "\n[관측·예보 데이터 (Open-Meteo, 서울 시간)]\n" + json.dumps(weather_data(), ensure_ascii=False)[:3000] + "\n"
    model = os.environ.get("SCRIPT_MODEL", "claude-sonnet-5-5")
    res = call_claude(build_prompt(req, material, extra), model)
    ids = {c["id"] for c in settings["cast"]}; segs = []
    for s in res.get("segments", []):
        segs.append({"speaker": s.get("speaker") if s.get("speaker") in ids else "jikjin", "line": str(s.get("line", "")), "kind": s.get("kind") if s.get("kind") in ("사실", "주장", "의견", "농담") else "의견", "source": str(s.get("source", "")), "on": True})
    if not segs: raise HTTPException(502, "대본이 비었다.")
    res["segments"] = segs
    program = req.program or PROG_NAME.get(req.prog, req.prog)
    show = {"id": str(int(time.time() * 1000)), "createdAt": int(time.time() * 1000), "input": req.model_dump(), "result": res, "program": program, "model": model, "auto": False,
            "material": [{"title": m["title"], "transcript": bool(m["transcript"]), "transcript_note": m["transcript_note"], "article": bool(m["article"])} for m in material],
            "audio": {"ready": False, "note": "생성 중" if os.environ.get("GEMINI_API_KEY") else "GEMINI_API_KEY 없음", "count": 0}}
    shows.insert(0, show); del shows[80:]; save_json(SHOWS_FILE, shows)
    synth_async(show); return show

@app.post("/api/script")
def api_script(req: ScriptReq):
    if not req.items and req.prog not in NO_NEWS: raise HTTPException(400, "이슈를 하나 이상 골라줘.")
    return make_show(req)
@app.get("/api/shows")
def api_shows(): return shows
@app.delete("/api/shows/{sid}")
def api_del(sid: str):
    global shows
    shows = [s for s in shows if s["id"] != sid]; save_json(SHOWS_FILE, shows); return {"ok": True}

# ---------- 음성 (Gemini TTS) ----------
TTS_MODEL = os.environ.get("TTS_MODEL", "gemini-2.5-flash-preview-tts")
tts_state = {"blocked_until": 0, "last_error": ""}
def tts_segment(text, speaker):
    key = os.environ.get("GEMINI_API_KEY")
    if not key: return None, "GEMINI_API_KEY 없음"
    if time.time() < tts_state["blocked_until"]: return None, "할당 초과(대기)"
    c = next((x for x in settings["cast"] if x["id"] == speaker), settings["cast"][0]); voice, style = c.get("tts", ["Charon", "뉴스 앵커"])
    body = {"contents": [{"parts": [{"text": f"{style}. 다음 문장만 읽는다:\n\n{text}"}]}], "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}
    for attempt in range(3):
        r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{TTS_MODEL}:generateContent", params={"key": key}, json=body, timeout=90)
        if r.status_code == 429:
            m = re.search(r'"retryDelay":\s*"(\d+)s"', r.text); delay = int(m.group(1)) if m else 30
            if delay > 120: tts_state["blocked_until"] = time.time() + delay; tts_state["last_error"] = "일일 할당 초과"; return None, "일일 할당 초과"
            time.sleep(delay + 2); continue
        if r.status_code != 200: tts_state["last_error"] = r.text[:120]; return None, f"HTTP {r.status_code}"
        try:
            part = r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
            pcm = base64.b64decode(part["data"]); mime = part.get("mimeType", "audio/L16;rate=24000")
            rate = int(re.search(r"rate=(\d+)", mime).group(1)) if "rate=" in mime else 24000
            hdr = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) + b"data" + struct.pack("<I", len(pcm))
            return hdr + pcm, "ok"
        except Exception as ex: return None, f"파싱 실패 {type(ex).__name__}"
    return None, "429 반복"
def synth_show(show):
    sid = show["id"]; d = AUDIO / sid; d.mkdir(exist_ok=True); errs = []
    for i, seg in enumerate(show["result"]["segments"]):
        f = d / f"{i}.wav"
        if f.exists(): continue
        wav, note = tts_segment(seg["line"], seg["speaker"])
        if wav: f.write_bytes(wav)
        else:
            errs.append(note)
            if "없음" in note or "할당" in note: break
    show["audio"] = {"ready": not errs, "note": errs[0] if errs else "ok", "count": len(list(d.glob("*.wav")))}; save_json(SHOWS_FILE, shows)
def synth_async(show): threading.Thread(target=synth_show, args=(show,), daemon=True).start()
def synth_backlog():
    if not os.environ.get("GEMINI_API_KEY") or time.time() < tts_state["blocked_until"]: return
    for s in shows[:10]:
        if not s.get("audio", {}).get("ready"): synth_show(s)
@app.get("/api/audio/{sid}/{i}.wav")
def api_audio(sid: str, i: int):
    f = AUDIO / sid / f"{i}.wav"
    if not f.exists(): raise HTTPException(404, "no audio")
    return Response(f.read_bytes(), media_type="audio/wav", headers={"Cache-Control": "public, max-age=86400"})
@app.post("/api/shows/{sid}/synth")
def api_synth(sid: str, force: bool = False):
    sh = next((x for x in shows if x["id"] == sid), None)
    if not sh: raise HTTPException(404, "없음")
    if force:
        for f in (AUDIO / sid).glob("*.wav"): f.unlink()
        sh["audio"] = {"ready": False, "note": "다시 생성 중", "count": 0}
    synth_async(sh); return {"ok": True}

# ---------- 편성·오토파일럿 ----------
def _m(t): h, m = t.split(":"); return int(h) * 60 + int(m)
def kst_now(): t = kst(); return t.tm_hour * 60 + t.tm_min, t.tm_wday
def slot_at(minute, wday):
    for a, b, prog, length, name, n, wd in SCHEDULE:
        if wd is not None and wd != wday: continue
        if _m(a) <= minute < _m(b): return {"prog": prog, "length": length, "name": name, "n": n, "start": a, "end": b}
    return {"prog": "daily", "length": "shorts", "name": "낮 직진", "n": 2, "start": "00:00", "end": "24:00"}
def slot_for(): m, wd = kst_now(); return slot_at(m, wd)
def next_slot():
    m, wd = kst_now(); cur = slot_at(m, wd); nm = _m(cur["end"])
    return slot_at(nm % 1440, wd if nm < 1440 else (wd + 1) % 7)
def seconds_to_next_boundary():
    m, wd = kst_now(); sec = kst().tm_sec
    starts = sorted({_m(a) for a, *_ in SCHEDULE} | {1440}); nxt = next((x for x in starts if x > m), 1440)
    return max(60, (nxt - m) * 60 - sec + 5)
AUTO_MIN = int(os.environ.get("AUTO_INTERVAL_MIN", "60"))
auto_state = {"last_run": None, "last_result": "아직 안 돌았음", "running": False, "last_slot": None}

def pick_items(slot):
    n, prog = slot["n"], slot["prog"]
    if n == 0: return []
    cutoff = time.time() - 36 * 3600
    for k in [k for k, v in used.items() if v < cutoff]: used.pop(k, None)
    src = all_sources(); items = []
    cat = CAT_OF.get(prog)
    if cat:
        for s in src:
            for a in s["articles"]:
                if a["link"] in used: continue
                if any(w in a["title"] for w in CATS[cat]):
                    items.append(Item(title=a["title"], link=a["link"], article_link=a["link"], article_title=a["title"]))
                    if len(items) >= n: break
        return items
    for s in src:
        for v in s["videos"]:
            if v["vid"] in used: continue
            items.append(Item(title=v["title"], vid=v["vid"], link=v["link"], article_link=(v.get("article") or {}).get("link"), article_title=(v.get("article") or {}).get("title")))
            if len(items) >= n: break
    if prog == "rabbit" and not items:
        for s in src:
            for v in s["videos"][:3]: items.append(Item(title=v["title"], vid=v["vid"], link=v["link"], article_link=(v.get("article") or {}).get("link"), article_title=(v.get("article") or {}).get("title")))
            break
    return items

def autopilot_once(force_slot=None):
    if auto_state["running"]: return
    auto_state["running"] = True
    try:
        if not os.environ.get("ANTHROPIC_API_KEY"): auto_state["last_result"] = "API 키 없음"; return
        slot = force_slot or slot_for()
        key = f"{kst_str('%Y-%m-%d')} {slot['start']} {slot['name']}"
        if not force_slot and slot["prog"] in ONCE_PER_SLOT and any(s.get("slot_key") == key for s in shows[:40]):
            auto_state["last_result"] = f"{slot['name']} 이미 만듦"; return
        items = pick_items(slot)
        if slot["n"] > 0 and not items: auto_state["last_result"] = f"{slot['name']}: 새 재료 없음"; return
        pend = [q for q in questions if not q.get("read")][:2] if slot["prog"] in ("evening", "late", "talk", "after") else []
        qtxt = ("시청자 질문 코너를 마지막에 넣는다(보낸 이름을 부르고 2~3줄로 답한다): " + " / ".join(f"[{q.get('name') or '익명'}] {q['text']}" for q in pend)) if pend else ""
        show = make_show(ScriptReq(items=items, prog=slot["prog"], length=slot["length"], note=qtxt, program=slot["name"])); show["auto"] = True; show["slot_key"] = key
        for it in items: used[it.vid or it.link] = time.time()
        save_json(USED_FILE, used)
        for q in pend: q["read"] = show["id"]
        if pend: save_json(Q_FILE, questions)
        save_json(SHOWS_FILE, shows)
        auto_state["last_result"] = f"{slot['name']} 생성: {show['result'].get('title', '')}"
    except Exception as ex:
        auto_state["last_result"] = f"실패: {type(ex).__name__} {str(ex)[:120]}"
    finally:
        auto_state["last_run"] = kst_str(); auto_state["running"] = False
def autopilot_loop():
    time.sleep(20)
    while True:
        try: autopilot_once(); synth_backlog()
        except Exception: pass
        time.sleep(min(AUTO_MIN * 60, seconds_to_next_boundary()))
if os.environ.get("AUTOPILOT", "1") == "1": threading.Thread(target=autopilot_loop, daemon=True).start()

@app.get("/api/now")
def api_now():
    return {"kst": kst_str("%H:%M"), "date": kst_str("%Y-%m-%d"), "wday": kst().tm_wday, "on_now": slot_for(), "up_next": next_slot(),
            "schedule": [{"start": a, "end": b, "name": nm, "prog": p, "wday": wd} for a, b, p, l, nm, n, wd in SCHEDULE],
            "programs": PROGRAMS, "cast": settings["cast"], "station": settings["station"],
            "autopilot": {**auto_state, "interval_min": AUTO_MIN}, "tts": {"key": bool(os.environ.get("GEMINI_API_KEY")), "blocked": time.time() < tts_state["blocked_until"], "last_error": tts_state["last_error"]},
            "questions_pending": len([q for q in questions if not q.get("read")]), "shows": shows[:40]}
@app.post("/api/autopilot/run")
def api_auto_run(prog: str | None = None):
    slot = None
    if prog:
        name = PROG_NAME.get(prog, prog); p = PROGRAMS.get(name)
        if not p: raise HTTPException(400, "그런 프로그램 없음")
        n = next((x[5] for x in SCHEDULE if x[4] == name), 2); length = next((x[3] for x in SCHEDULE if x[4] == name), "shorts")
        slot = {"prog": p["prog"], "length": length, "name": name, "n": n, "start": "", "end": ""}
    threading.Thread(target=autopilot_once, args=(slot,), daemon=True).start(); return {"ok": True}

class AskReq(BaseModel):
    q: str
    name: str = ""
    kind: str = "question"
    on_air: bool = True
@app.post("/api/ask")
def api_ask(req: AskReq):
    q = req.q.strip()[:300]
    if not q: raise HTTPException(400, "질문이 비었다.")
    st = settings; desk = [c for c in st["cast"] if c["id"] in ("jikjin", "bora", "fact")]
    cast_txt = "\n".join(f"- {c['name']} (id:{c['id']}, {c['role']}): {c['voice']}" for c in desk)
    recent = "\n".join(f"- {s['result'].get('title','')}: " + " / ".join(x["line"] for x in s["result"]["segments"][:4]) for s in shows[:3])
    prompt = f"""너는 가상 뉴스 방송국 "{st['station']['name']}"의 작가다. 시청자가 데스크에 보낸 {'편지' if req.kind=='letter' else '질문'}에 앵커들이 짧게 답한다.
[출연진]\n{cast_txt}\n[스탠스]\n{st['stance']}\n[톤]\n{st['tone']}
[고정 규칙] 사실을 지어내지 않는다. 모르면 팩트-1이 "확인된 바 없음"이라고 한다. 실존 인물 비하·확인 안 된 범죄 단정·집단 비하 금지. 욕설 순화.
[최근 방송]\n{recent or '(없음)'}
[보낸 사람] {req.name.strip()[:20] or '익명'}
[내용] {q}
[출력] JSON 하나만: {{"segments":[{{"speaker":"jikjin|bora|fact","line":"대사","kind":"사실|주장|의견|농담","source":""}}]}} 대사 2~4줄."""
    res = call_claude(prompt, os.environ.get("ASK_MODEL", "claude-haiku-5-5"))
    if req.on_air:
        questions.insert(0, {"id": str(int(time.time() * 1000)), "text": q, "name": req.name.strip()[:20], "kind": req.kind, "at": int(time.time() * 1000), "read": None}); del questions[200:]; save_json(Q_FILE, questions)
    return {"q": q, "segments": res.get("segments", []), "queued": req.on_air}

@app.get("/api/today")
def api_today():
    day = kst_str("%Y-%m-%d"); todays = [s for s in shows if kst_str("%Y-%m-%d", s["createdAt"] / 1000) == day]
    return {"date": day, "station": settings["station"]["name"], "shows": [{"time": kst_str("%H:%M", s["createdAt"] / 1000), "program": s.get("program"), "title": s["result"].get("title"), "issues": s["result"].get("issues_used", []), "sources": sorted({g["source"] for g in s["result"]["segments"] if g.get("source", "").startswith("http")})} for s in todays]}

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

# ---------- 페이지 ----------
@app.get("/")
def index(): return FileResponse(BASE / "static" / "index.html")
@app.get("/studio")
def studio(): return FileResponse(BASE / "static" / "studio.html")
@app.get("/replays")
def replays(): return FileResponse(BASE / "static" / "replays.html")
@app.get("/about")
def about(): return FileResponse(BASE / "static" / "about.html")
@app.get("/guide")
def guide(): return RedirectResponse("/#guide")
@app.get("/shows")
def shows_page(): return RedirectResponse("/#programs")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
