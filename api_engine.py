import os
import json
import sqlite3
import re
import math
import time
import requests
import difflib
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

from grading_postmortem import (
    build_postmortem,
    normalize_official_stats,
    official_stats_text,
    postmortem_json,
    postmortem_text,
)

load_dotenv()

API_KEY = os.getenv("API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

if not API_KEY or not GITHUB_TOKEN:
    print("🚨 [보안 경고] .env 파일에서 키를 찾을 수 없습니다! 서버 세팅을 확인하세요.")

GITHUB_REPO = "chleowhd77-ops/-"
API_HOST = "v3.football.api-sports.io"
headers = {'x-apisports-key': API_KEY}
DEFAULT_LOGO = "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d3/Soccerball.svg/120px-Soccerball.svg.png"
STRICT_REFEREES = ["Taylor", "Hernandez", "Lahoz", "Orsato", "Oliver", "Dean", "Turpin", "Makkelie"]
ANALYSIS_VERSION = "V7.3.9-unified-final-learning"
# 프로그램 배포 버전과 예측 모델 버전을 분리한다. 화면/수집/집계 오류를
# 고쳤다는 이유만으로 과거 예측이 다른 모델 기록처럼 분리되면 안 된다.
SYSTEM_VERSION = "R7.3.9.3-canonical-fixture-ui-status"

# API-Football의 하루 한도를 분석 작업이 전부 소모하지 않게 보호한다.
# 기본값은 7,500회 요금제에서 라이브/채점용 600회를 남기는 구성이다.
API_DAILY_TOTAL_LIMIT = max(100, int(os.getenv("API_DAILY_TOTAL_LIMIT", "7500")))
API_LIVE_RESERVE = max(50, int(os.getenv("API_LIVE_RESERVE", "600")))
API_ANALYSIS_SOFT_LIMIT = max(50, API_DAILY_TOTAL_LIMIT - API_LIVE_RESERVE)
API_WORLD_DAILY_LIMIT = max(10, int(os.getenv("API_WORLD_DAILY_LIMIT", "1500")))
API_WORLD_MIN_REMAINING = max(100, int(os.getenv("API_WORLD_MIN_REMAINING", "1000")))
API_MIN_REQUEST_INTERVAL = max(0.0, float(os.getenv("API_MIN_REQUEST_INTERVAL", "0.22")))
API_RATE_LIMIT_RETRIES = max(0, int(os.getenv("API_RATE_LIMIT_RETRIES", "2")))
_API_PROVIDER_REMAINING = None
_API_PROVIDER_DAY = None
_API_QUOTA_NOTICE_SHOWN = False
_API_LAST_REQUEST_AT = 0.0
_API_PURPOSE_OVERRIDE = None

SQLITE_BUSY_TIMEOUT_MS = max(
    5000, min(60000, int(os.getenv("SQLITE_BUSY_TIMEOUT_MS", "30000")))
)
SQLITE_BUSY_RETRY_DELAYS = (0.2, 0.5, 1.0)


def _sqlite_connect(path="ai_predictions.db", timeout=None):
    """Open the shared database with one consistent multi-worker wait policy."""
    timeout_seconds = (
        float(timeout)
        if timeout is not None
        else SQLITE_BUSY_TIMEOUT_MS / 1000.0
    )
    conn = sqlite3.connect(path, timeout=max(1.0, timeout_seconds))
    conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    return conn


def _is_sqlite_busy(error):
    message = str(error or "").casefold()
    return "database is locked" in message or "database is busy" in message


class ApiQuotaUnavailable(RuntimeError):
    """일일 API 한도 보호 장치가 요청을 중단했음을 뜻한다."""


class ApiRateLimited(RuntimeError):
    """공급사의 분당/순간 요청 제한이 잠시 적용됐음을 뜻한다."""


def _response_error_text(response):
    try:
        return json.dumps(response.json().get("errors", {}), ensure_ascii=False).lower()
    except Exception:
        return ""


def _header_int(response, name):
    try:
        value = response.headers.get(name)
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _is_daily_quota_response(response, error_text=""):
    """하루 한도 소진과 일시적인 분당 제한을 구분한다.

    API-Sports는 일일 잔여량과 분당 잔여량을 서로 다른 헤더로 보낸다.
    일반적인 ``rate limit`` 문구만으로 하루 전체를 잠그면 결제 갱신 뒤에도
    최근 전적ㆍ부상자ㆍLIVE 조회가 모두 중단될 수 있다.
    """
    daily_remaining = _header_int(response, "x-ratelimit-requests-remaining")
    if daily_remaining is not None:
        return daily_remaining <= 0
    daily_markers = (
        "daily quota",
        "daily request",
        "requests per day",
        "request limit for the day",
        "quota for the day",
    )
    return any(marker in error_text for marker in daily_markers)


def _pace_api_request():
    global _API_LAST_REQUEST_AT
    if API_MIN_REQUEST_INTERVAL > 0:
        wait_seconds = API_MIN_REQUEST_INTERVAL - (time.monotonic() - _API_LAST_REQUEST_AT)
        if wait_seconds > 0:
            time.sleep(wait_seconds)
    _API_LAST_REQUEST_AT = time.monotonic()


def _api_provider_day_key():
    """API-Sports의 일일 사용량 초기화 기준(매일 00:00 UTC)을 따른다.

    기존 버전은 한국 날짜를 사용해 공급사 초기화 시각과 최대 9시간 어긋났다.
    ``utc:`` 접두사는 과거 KST 기준으로 저장된 소진 기록과 새 기록을 분리해,
    업데이트 직후에도 오래된 0회 잔여 기록이 로봇을 막지 않게 한다.
    """
    return f"utc:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"


def _api_usage_today():
    day_key = _api_provider_day_key()
    try:
        conn = _sqlite_connect()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_usage_daily (
                usage_day TEXT PRIMARY KEY,
                calls INTEGER NOT NULL DEFAULT 0,
                provider_remaining INTEGER,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute(
            "SELECT calls, provider_remaining FROM api_usage_daily WHERE usage_day = ?",
            (day_key,),
        )
        row = cursor.fetchone()
        conn.commit()
        conn.close()
        return day_key, int(row[0] or 0) if row else 0, (row[1] if row else None)
    except Exception:
        return day_key, 0, None


def _api_purpose_usage_today(day_key, purpose):
    try:
        conn = _sqlite_connect()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_usage_purpose_daily (
                usage_day TEXT NOT NULL,
                purpose TEXT NOT NULL,
                calls INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (usage_day, purpose)
            )
        """)
        cursor.execute(
            "SELECT calls FROM api_usage_purpose_daily WHERE usage_day = ? AND purpose = ?",
            (day_key, str(purpose or "analysis")),
        )
        row = cursor.fetchone()
        conn.commit()
        conn.close()
        return int(row[0] or 0) if row else 0
    except Exception:
        return 0


def _record_api_usage(day_key, provider_remaining=None, purpose="analysis"):
    try:
        conn = _sqlite_connect()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_usage_daily (
                usage_day TEXT PRIMARY KEY,
                calls INTEGER NOT NULL DEFAULT 0,
                provider_remaining INTEGER,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            INSERT INTO api_usage_daily (usage_day, calls, provider_remaining, updated_at)
            VALUES (?, 1, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(usage_day) DO UPDATE SET
                calls = api_usage_daily.calls + 1,
                provider_remaining = COALESCE(excluded.provider_remaining, api_usage_daily.provider_remaining),
                updated_at = CURRENT_TIMESTAMP
        """, (day_key, provider_remaining))
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_usage_purpose_daily (
                usage_day TEXT NOT NULL,
                purpose TEXT NOT NULL,
                calls INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (usage_day, purpose)
            )
        """)
        cursor.execute("""
            INSERT INTO api_usage_purpose_daily (usage_day, purpose, calls, updated_at)
            VALUES (?, ?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(usage_day, purpose) DO UPDATE SET
                calls = api_usage_purpose_daily.calls + 1,
                updated_at = CURRENT_TIMESTAMP
        """, (day_key, str(purpose or "analysis")))
        conn.commit()
        conn.close()
    except Exception:
        pass


def _mark_api_quota_exhausted(day_key):
    """재시작 뒤에도 같은 날 소진된 API를 반복 호출하지 않게 기록한다."""
    try:
        conn = _sqlite_connect()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_usage_daily (
                usage_day TEXT PRIMARY KEY,
                calls INTEGER NOT NULL DEFAULT 0,
                provider_remaining INTEGER,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            INSERT INTO api_usage_daily (usage_day, calls, provider_remaining, updated_at)
            VALUES (?, 0, 0, CURRENT_TIMESTAMP)
            ON CONFLICT(usage_day) DO UPDATE SET
                provider_remaining = 0,
                updated_at = CURRENT_TIMESTAMP
        """, (day_key,))
        conn.commit()
        conn.close()
    except Exception:
        pass


def _show_api_quota_notice(message):
    global _API_QUOTA_NOTICE_SHOWN
    if not _API_QUOTA_NOTICE_SHOWN:
        print(f"⚠️ API 한도 보호 모드: {message} (기존 캐시/베트맨 데이터로 계속 동작)")
        _API_QUOTA_NOTICE_SHOWN = True


@contextmanager
def api_purpose_context(purpose):
    """Count nested helper requests under one isolated job purpose.

    Most data helpers are shared by PROTO and WORLD.  The world worker runs in
    its own process, so a short-lived process-local override lets those helpers
    keep their stable signatures while every real network request is charged
    to the WORLD safety budget.
    """
    global _API_PURPOSE_OVERRIDE
    previous = _API_PURPOSE_OVERRIDE
    _API_PURPOSE_OVERRIDE = str(purpose or "analysis").strip().lower()
    try:
        yield
    finally:
        _API_PURPOSE_OVERRIDE = previous


def api_get(path, params=None, timeout=7, purpose=None):
    """API-Football 호출을 한곳에서 집계하고 분석/라이브 예산을 분리한다."""
    global _API_PROVIDER_REMAINING, _API_PROVIDER_DAY, _API_QUOTA_NOTICE_SHOWN
    current_day = _api_provider_day_key()
    if _API_PROVIDER_DAY != current_day:
        _API_PROVIDER_DAY = current_day
        _API_PROVIDER_REMAINING = None
        _API_QUOTA_NOTICE_SHOWN = False

    # 한 프로세스 안에서 이미 소진을 확인했다면 SQLite 조회조차 반복하지 않는다.
    if _API_PROVIDER_REMAINING is not None and _API_PROVIDER_REMAINING <= 0:
        _show_api_quota_notice("공급사 일일 사용량 소진")
        raise ApiQuotaUnavailable("API daily quota exhausted")

    day_key, local_calls, stored_remaining = _api_usage_today()
    purpose = str(purpose or _API_PURPOSE_OVERRIDE or "analysis").strip().lower()
    world_calls = _api_purpose_usage_today(day_key, "world") if purpose == "world" else 0
    _API_PROVIDER_DAY = day_key
    if _API_PROVIDER_REMAINING is None and stored_remaining is not None:
        # 이전 실행이 0을 저장했더라도 결제 갱신ㆍ플랜 변경이 있었을 수 있다.
        # 새 프로세스에서는 첫 요청 한 번으로 공급사 상태를 다시 확인하고,
        # 실제로 일일 잔여량이 0일 때만 그 실행 동안 차단한다.
        if int(stored_remaining) > 0:
            _API_PROVIDER_REMAINING = int(stored_remaining)
        else:
            print("[API] 저장된 소진 기록 재확인: 공급사 상태를 한 번 조회합니다.")

    allowed_calls = API_DAILY_TOTAL_LIMIT - 50 if purpose in {"live", "scoring"} else API_ANALYSIS_SOFT_LIMIT
    if purpose == "world":
        if world_calls >= API_WORLD_DAILY_LIMIT:
            _show_api_quota_notice(
                f"세계경기 전용 예산 도달 {world_calls}/{API_WORLD_DAILY_LIMIT}"
            )
            raise ApiQuotaUnavailable("World-football API safety budget reached")
        if (
            _API_PROVIDER_REMAINING is not None
            and int(_API_PROVIDER_REMAINING) <= API_WORLD_MIN_REMAINING
        ):
            _show_api_quota_notice(
                f"세계경기 중단·기존 LIVE 보호 {int(_API_PROVIDER_REMAINING)}회 남음"
            )
            raise ApiQuotaUnavailable("World-football reserve protected")
    if _API_PROVIDER_REMAINING is not None and _API_PROVIDER_REMAINING <= 0:
        _show_api_quota_notice("공급사 일일 사용량 소진")
        raise ApiQuotaUnavailable("API daily quota exhausted")
    if local_calls >= allowed_calls:
        _show_api_quota_notice(f"{purpose} 예산 도달 {local_calls}/{allowed_calls}")
        raise ApiQuotaUnavailable("Local API safety budget reached")

    response = None
    for attempt in range(API_RATE_LIMIT_RETRIES + 1):
        _pace_api_request()
        response = requests.get(
            f"https://{API_HOST}/{str(path).lstrip('/')}",
            headers=headers,
            params=params,
            timeout=timeout,
        )
        error_text = _response_error_text(response)
        daily_remaining = _header_int(response, "x-ratelimit-requests-remaining")
        if daily_remaining is not None:
            _API_PROVIDER_REMAINING = daily_remaining
        _record_api_usage(day_key, _API_PROVIDER_REMAINING, purpose=purpose)

        if _is_daily_quota_response(response, error_text):
            _API_PROVIDER_REMAINING = 0
            _mark_api_quota_exhausted(day_key)
            _show_api_quota_notice("공급사 일일 사용량 소진")
            return response

        transient_rate_limit = response.status_code == 429 or "rate limit" in error_text
        if not transient_rate_limit:
            return response
        if attempt >= API_RATE_LIMIT_RETRIES:
            raise ApiRateLimited("API temporary rate limit; retry on next collection cycle")

        try:
            retry_after = float(response.headers.get("Retry-After", "0") or 0)
        except (TypeError, ValueError):
            retry_after = 0
        delay = retry_after if retry_after > 0 else 10 * (attempt + 1)
        delay = max(1.0, min(60.0, delay))
        print(f"⏳ API 분당 제한 감지: {delay:g}초 후 재시도 ({attempt + 1}/{API_RATE_LIMIT_RETRIES})")
        time.sleep(delay)

    return response


def get_api_usage_status():
    """수집기와 관리자 화면에 노출할 안전한 API 사용 현황."""
    day_key, local_calls, stored_remaining = _api_usage_today()
    remaining = stored_remaining
    if _API_PROVIDER_DAY == day_key and _API_PROVIDER_REMAINING is not None:
        remaining = _API_PROVIDER_REMAINING
    analysis_calls = _api_purpose_usage_today(day_key, "analysis")
    live_calls = _api_purpose_usage_today(day_key, "live")
    scoring_calls = _api_purpose_usage_today(day_key, "scoring")
    world_calls = _api_purpose_usage_today(day_key, "world")
    return {
        "usage_day": day_key.removeprefix("utc:"),
        "reset_timezone": "UTC",
        "local_calls": int(local_calls or 0),
        "provider_remaining": remaining,
        "analysis_soft_limit": API_ANALYSIS_SOFT_LIMIT,
        "daily_limit": API_DAILY_TOTAL_LIMIT,
        "live_reserve": API_LIVE_RESERVE,
        "world_calls": int(world_calls or 0),
        "analysis_calls": int(analysis_calls or 0),
        "live_calls": int(live_calls or 0),
        "scoring_calls": int(scoring_calls or 0),
        "world_daily_limit": API_WORLD_DAILY_LIMIT,
        "world_min_remaining": API_WORLD_MIN_REMAINING,
        "quota_exhausted": remaining is not None and int(remaining) <= 0,
    }

# 득점/도움 순위만으로는 골키퍼ㆍ수비수ㆍ갑작스러운 로테이션을 놓칠 수 있다.
# 팀을 고정하지 않고 선수 이름만 관리한 뒤, 실제 부상 명단/소속 스쿼드에
# 등장할 때만 사용한다. 이 목록은 config.py에서도 쉽게 추가할 수 있다.
PROTECTED_STAR_PLAYERS = [
    {"name": "Son Heung-min", "aliases": ["Heung Min Son", "Heung-min Son", "손흥민"], "impact": 1.25},
    {"name": "Erling Haaland", "aliases": ["E. Haaland"], "impact": 1.25},
    {"name": "Kylian Mbappe", "aliases": ["Kylian Mbappé", "K. Mbappe"], "impact": 1.25},
    {"name": "Mohamed Salah", "aliases": ["M. Salah"], "impact": 1.20},
    {"name": "Harry Kane", "aliases": ["H. Kane"], "impact": 1.20},
    {"name": "Vinicius Junior", "aliases": ["Vinícius Júnior", "Vini Jr"], "impact": 1.15},
    {"name": "Jude Bellingham", "aliases": ["J. Bellingham"], "impact": 1.15},
    {"name": "Kevin De Bruyne", "aliases": ["K. De Bruyne"], "impact": 1.15},
    {"name": "Rodri", "aliases": ["Rodrigo Hernandez", "Rodrigo Hernández"], "impact": 1.15},
    {"name": "Bukayo Saka", "aliases": ["B. Saka"], "impact": 1.10},
    {"name": "Bruno Fernandes", "aliases": ["B. Fernandes"], "impact": 1.10},
    {"name": "Lautaro Martinez", "aliases": ["Lautaro Martínez", "L. Martinez"], "impact": 1.10},
    {"name": "Robert Lewandowski", "aliases": ["R. Lewandowski"], "impact": 1.10},
    {"name": "Victor Osimhen", "aliases": ["V. Osimhen"], "impact": 1.10},
    {"name": "Cole Palmer", "aliases": ["C. Palmer"], "impact": 1.10},
]


def _normalize_player_name(value):
    return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").casefold())


def find_protected_star(player_name):
    target = _normalize_player_name(player_name)
    if not target:
        return None
    for star in PROTECTED_STAR_PLAYERS:
        names = [star.get("name", "")] + list(star.get("aliases", []))
        for name in names:
            candidate = _normalize_player_name(name)
            if candidate and (target == candidate or (len(target) >= 6 and (target in candidate or candidate in target))):
                return star
    return None

# API-Football이 실제 엠블럼 대신 '이미지 없음' 그림을 주는 국내 구단은
# K리그 공식 엠블럼을 우선 사용한다. 팀 ID는 그대로 유지하므로 전적 조회에는
# 영향을 주지 않고 화면의 로고만 정확하게 교체된다.
OFFICIAL_TEAM_LOGOS = {
    "김포": "https://www.kleague.com/assets/images/emblem/emblem_K36%403x.png",
    "김포FC": "https://www.kleague.com/assets/images/emblem/emblem_K36%403x.png",
    "Gimpo": "https://www.kleague.com/assets/images/emblem/emblem_K36%403x.png",
    "Gimpo FC": "https://www.kleague.com/assets/images/emblem/emblem_K36%403x.png",
    "충북청주": "https://www.kleague.com/assets/images/emblem/emblem_K37%403x.png",
    "충북청주FC": "https://www.kleague.com/assets/images/emblem/emblem_K37%403x.png",
    "충북청주 프로축구단": "https://www.kleague.com/assets/images/emblem/emblem_K37%403x.png",
    "Chungbuk Cheongju": "https://www.kleague.com/assets/images/emblem/emblem_K37%403x.png",
    "화성": "https://www.kleague.com/assets/images/emblem/emblem_K39%403x.png",
    "화성FC": "https://www.kleague.com/assets/images/emblem/emblem_K39%403x.png",
    "Hwaseong": "https://www.kleague.com/assets/images/emblem/emblem_K39%403x.png",
    "Hwaseong FC": "https://www.kleague.com/assets/images/emblem/emblem_K39%403x.png",
}

OFFICIAL_TEAM_LOGOS_BY_ID = {
    10453: "https://www.kleague.com/assets/images/emblem/emblem_K36%403x.png",
    10452: "https://www.kleague.com/assets/images/emblem/emblem_K37%403x.png",
}

TEAM_NAME_MAP = {
    "광주FC": "Gwangju FC", "포항스틸": "Pohang Steelers", "포항 스틸러스": "Pohang Steelers", "제주SKFC": "Jeju United", "제주 SKFC": "Jeju United", 
    "FC안양": "FC Anyang", "FC 안양": "FC Anyang", "FC서울": "FC Seoul", "대전하나": "Daejeon Citizen", "대전 하나시티즌": "Daejeon Citizen", 
    "충북청주": "Chungbuk Cheongju", "충북청주 프로축구단": "Chungbuk Cheongju", "전남드래": "Jeonnam Dragons", "전남 드래곤즈": "Jeonnam Dragons",
    "김해FC": "Gimhae", "김해FC 2008": "Gimhae", "경남FC": "Gyeongnam FC", "수원삼성": "Suwon Samsung", "수원 삼성블루윙즈": "Suwon Samsung", "수원FC": "Suwon FC",
    "부산아이": "Busan I Park", "부산 아이파크": "Busan I Park", "화성FC": "Hwaseong", "인천유나": "Incheon United", "인천 유나이티드": "Incheon United",
    "김천상무": "Gimcheon Sangmu", "김천상무 프로축구단": "Gimcheon Sangmu", "부천FC": "Bucheon FC 1995", "부천FC 1995": "Bucheon FC 1995", 
    "전북현대": "Jeonbuk Motors", "전북 현대모터스": "Jeonbuk Motors", "울산HDFC": "Ulsan Hyundai", "울산 HDFC": "Ulsan Hyundai", "강원FC": "Gangwon FC",
    "서울이랜드": "Seoul E-Land", "서울 이랜드": "Seoul E-Land", "안산그리": "Ansan Greeners", "안산 그리너스": "Ansan Greeners", "대구FC": "Daegu FC", 
    "충남아산": "Chungnam Asan", "충남아산 프로축구단": "Chungnam Asan", "김포FC": "Gimpo FC", "천안시티": "Cheonan City", "천안 시티FC": "Cheonan City", 
    "파주프런": "Paju Citizen", "파주 프런티어": "Paju Citizen", "성남FC": "Seongnam FC", "용인FC": "Yongin",
    "맨체스C": "Manchester City", "맨체스터 시티": "Manchester City", "리버풀": "Liverpool", "뉴캐슬U": "Newcastle", "뉴캐슬 유나이티드": "Newcastle",
    "본머스": "Bournemouth", "AFC본머스": "Bournemouth", "브라이턴": "Brighton", "브라이턴&호브 앨비언": "Brighton", "A빌라": "Aston Villa", "애스턴 빌라": "Aston Villa",
    "노팅엄F": "Nottingham Forest", "노팅엄 포리스트": "Nottingham Forest", "리즈U": "Leeds", "리즈 유나이티드": "Leeds", "에버턴": "Everton",
    "크리스털": "Crystal Palace", "크리스털 팰리스": "Crystal Palace", "입스위치": "Ipswich", "입스위치 타운": "Ipswich", "선덜랜드": "Sunderland",
    "브렌트퍼": "Brentford", "브렌트퍼드": "Brentford", "토트넘": "Tottenham", "토트넘 홋스퍼": "Tottenham", "아스널": "Arsenal",
    "맨유": "Manchester United", "맨체스U": "Manchester United", "맨체스터 유나이티드": "Manchester United", "웨스트햄 유나이티드": "West Ham", "웨스트브로미치 앨비언": "West Brom", "번리": "Burnley",
    "코번트리": "Coventry", "코번트리 시티": "Coventry", "버밍엄 시티": "Birmingham", "브리스틀 시티": "Bristol City", "링컨 시티": "Lincoln", "포츠머스": "Portsmouth",
    "밀월": "Millwall", "노리치 시티": "Norwich City", "헐시티": "Hull City", "헐 시티": "Hull City", "블랙번 로버스": "Blackburn", "미들즈브러": "Middlesbrough",
    "더비 카운티": "Derby", "카디프 시티": "Cardiff City", "프레스턴 노스엔드": "Preston", "울버햄튼 원더러스": "Wolves", "울버햄튼": "Wolves",
    "퀸즈파크 레인저스": "QPR", "볼턴 원더러스": "Bolton", "사우샘프턴": "Southampton", "스토크 시티": "Stoke City", "스완지 시티": "Swansea",
    "셰필드 유나이티드": "Sheffield Utd", "찰턴 애슬레틱": "Charlton", "렉섬": "Wrexham", "왓포드": "Watford", "풀럼": "Fulham", "첼시": "Chelsea",
    "프로시노": "Frosinone", "프로시노네": "Frosinone", "유벤투스": "Juventus", "베네치아": "Venezia", "US레체": "Lecce",
    "아탈란타": "Atalanta", "아탈란타BC": "Atalanta", "사수올로": "Sassuolo", "US사수올로": "Sassuolo", "토리노": "Torino", "AC밀란": "AC Milan",
    "제노아": "Genoa", "나폴리": "Napoli", "SSC나폴리": "Napoli", "파르마": "Parma", "칼리아리": "Cagliari", "인테르나치오날레 밀라노": "Inter",
    "인테르": "Inter", "AC몬차": "Monza", "우디네세": "Udinese", "코모1907": "Como", "볼로냐": "Bologna", "라치오": "Lazio", "SS라치오": "Lazio", "AS로마": "Roma", "피오렌티": "Fiorentina", "ACF피오렌티나": "Fiorentina",
    "레알 마드리드": "Real Madrid", "바르셀로나": "Barcelona", "아틀레티코 마드리드": "Atletico Madrid", "비야레알": "Villarreal",
    "레알 베티스": "Real Betis", "레알 소시에다드": "Real Sociedad", "발렌시아": "Valencia", "RC셀타데비고": "Celta Vigo", 
    "RCD에스파뇰": "Espanyol", "헤타페": "Getafe", "라싱 산탄데르": "Racing Santander", "엘체": "Elche", "오사수나": "Osasuna", "레반테": "Levante", "말라가": "Malaga", "데포르티보 아코루냐": "Deportivo La Coruna",
    "파리 생제르맹": "Paris Saint Germain", "AS모나코": "Monaco", "올랭피크드 마르세유": "Marseille", "올랭피크 리옹": "Lyon",
    "RC스트라스부르": "Strasbourg", "RC랑스": "Lens", "AJ오세르": "Auxerre", "르망FC": "Le Mans", "스타드 브레스투아29": "Brest",
    "OGC니스": "Nice", "로리앙": "Lorient", "툴루즈": "Toulouse", "트루아AC": "Troyes", "파리FC": "Paris FC", "스타드 렌": "Rennes",
    "르아브르AC": "Le Havre", "앙제SCO": "Angers", "릴OSC": "Lille",
    "도르트문트": "Borussia Dortmund", "함부르크": "Hamburger SV", "바이에른 뮌헨": "Bayern Munich",
    "RB라이프치히": "RB Leipzig", "묀헨글라트바흐": "Borussia Monchengladbach", "FSV마인츠05": "FSV Mainz 05",
    "파더보른07": "SC Paderborn 07", "프랑크푸르트": "Eintracht Frankfurt",
    "포르튀나 시타르트": "Fortuna Sittard", "AZ알크마르": "AZ Alkmaar", "스파르타 로테르담": "Sparta Rotterdam", "위트레흐트": "Utrecht",
    "엑셀시오르 로테르담": "Excelsior", "엑셀시오르": "Excelsior",
    "SC헤이렌베인": "Heerenveen", "PEC즈볼러": "PEC Zwolle", "고어헤드 이글스": "Go Ahead Eagles", "ADO덴하흐": "ADO Den Haag",
    "PSV에인트호번": "PSV Eindhoven", "흐로닝언": "Groningen", "SC캄뷔르": "Cambuur", "페예노르트": "Feyenoord",
    "가시와 레이솔": "Kashiwa Reysol", "V바렌 나가사키": "V-Varen Nagasaki", "FC도쿄": "FC Tokyo", "제프 유나이티드": "JEF United Chiba",
    "가시마 앤틀러스": "Kashima Antlers", "아비스파 후쿠오카": "Avispa Fukuoka", "파지아노 오카야마": "Fagiano Okayama", "도쿄 베르디": "Tokyo Verdy",
    "나고야 그램퍼스": "Nagoya Grampus", "감바 오사카": "Gamba Osaka", "교토 상가FC": "Kyoto Sanga", "미토 홀리호크": "Mito Hollyhock",
    "세레소 오사카": "Cerezo Osaka", "시미즈 에스펄스": "Shimizu S-Pulse", "산프레체 히로시마": "Sanfrecce Hiroshima", "가와사키 프론탈레": "Kawasaki Frontale",
    "요코하마 F마리노스": "Yokohama F. Marinos", "비셀 고베": "Vissel Kobe", "FC마치다 젤비아": "Machida Zelvia", "우라와 레드": "Urawa Red Diamonds",
    "콘사도레 삿포로": "Consadole Sapporo", "RB오미야 아르디자": "Omiya Ardija", "오미야 아르디자": "Omiya Ardija",
    "반라우레 하치노헤FC": "Vanraure Hachinohe", "반라우레 하치노헤": "Vanraure Hachinohe", "베갈타 센다이": "Vegalta Sendai", 
    "블라우블리츠 아키타": "Blaublitz Akita", "반포레 고후": "Ventforet", "카탈레 도야마": "Kataller", 
    "FC이마바리": "Imabari", "이마바리": "Imabari", "몬테디오 야마가타": "Montedio Yamagata", "요코하마FC": "Yokohama FC", 
    "알비렉스 니가타": "Albirex Niigata", "후지에다 MYFC": "Fujieda", "주빌로 이와타": "Jubilo Iwata", 
    "도쿠시마 보르티스": "Tokushima Vortis", "사간 도스": "Sagan Tosu", "도치기 시티FC": "Tochigi", "도치기": "Tochigi",
    "오이타 트리니타": "Oita Trinita", "이와키FC": "Iwaki", "이와키": "Iwaki", "테게바자로 미야자키": "Tegevajaro", 
    "쇼난 벨마레": "Shonan Bellmare",
    "태국": "Thailand", "베트남": "Vietnam",
    "샬럿FC": "Charlotte", "DC유나이티드": "DC United", "FC신시내티": "FC Cincinnati", "시애틀 사운더스FC": "Seattle Sounders",
    "인터 마이애미CF": "Inter Miami", "토론토FC": "Toronto FC", "CF몽레알": "Montreal Impact", "LA 갤럭시": "LA Galaxy",
    "뉴욕 레드불스": "New York Red Bulls", "시카고 파이어FC": "Chicago Fire", "올랜도 시티SC": "Orlando City", "레알 솔트레이크": "Real Salt Lake",
    "오스틴FC": "Austin", "필라델피아 유니언": "Philadelphia Union", "내슈빌SC": "Nashville SC", "콜럼버스 크루": "Columbus Crew",
    "세인트루이스 시티SC": "St. Louis City", "휴스턴 다이너모FC": "Houston Dynamo", "밴쿠버 화이트캡스FC": "Vancouver Whitecaps", "FC댈러스": "FC Dallas",
    "LAFC": "Los Angeles FC", "포틀랜드 팀버스": "Portland Timbers", "샌디에이고FC": "San Diego", "콜로라도 래피즈": "Colorado Rapids",
    "새너제이 어스퀘이크스": "San Jose Earthquakes", "미네소타 유나이티드FC": "Minnesota United", "뉴잉글랜드 레벌루션": "New England Revolution",
    "뉴욕 시티FC": "New York City FC", "애틀랜타 유나이티드FC": "Atlanta United", "스포팅 캔자스시티": "Sporting Kansas City",
    "AEK아테": "AEK Athens", "L소피아": "Lokomotiv Sofia", "비킹FK": "Viking FK", "D자그레": "Dinamo Zagreb",
    "NK첼레": "NK Celje", "슬로반브": "Slovan Bratislava", "리옹": "Lyon", "페네르SK": "Fenerbahce",
    "이베리아": "Iberia 1999", "야기엘로": "Jagiellonia Bialystok", "오모니아": "Omonia Nicosia", "신트트라": "Sint-Truiden",
    "플젠": "Viktoria Plzen", "츠르베나": "Crvena Zvezda", "릴레스트": "Lillestrom", "에그나티": "Egnatia",
    "잘츠부르": "Red Bull Salzburg", "미엘뷔": "Mjallby", "카우노잘": "Kauno Zalgiris", "베식타시": "Besiktas",
    "FC툰": "FC Thun", "L포즈난": "Lech Poznan", "C소피아": "CSKA Sofia", "OFI크레": "OFI Crete",
    "페렌츠바": "Ferencvaros", "트라브존": "Trabzonspor", "안더레흐": "Anderlecht", "카이라트": "Kairat Almaty",
    "LASK": "LASK Linz", "셀틱": "Celtic"
}

MANUAL_TEAM_MAP = {
    "아라라트 아르메니아": "Ararat-Armenia", "우니베르시타테아 크라이오바": "Universitatea Craiova",
    "이베리아1999 트빌리시": "Iberia 1999", "야기엘로니아 비아위스토크": "Jagiellonia",
    "카우노 잘기리스": "Kauno Zalgiris", "레흐 포즈난": "Lech Poznan",
    "SK슬로반 브라티슬라바": "Slovan Bratislava", "레프스키 소피아": "Levski Sofia",
    "GNK디나모 자그레브": "Dinamo Zagreb", "신트 트라위던VV": "Sint-Truiden",
    "RSC안더레흐트": "Anderlecht", "비킹FK": "Viking", "OFI크레타": "OFI Crete",
    "페렌츠바로시TC": "Ferencvarosi", "트라브존스포르": "Trabzonspor",
    "카이라트 알마티": "Kairat", "미엘뷔AIF": "Mjallby", "KF에그나티아": "Egnatia",
    "AC오모니아": "Omonia", "빅토리아 플젠": "Viktoria Plzen",
    "FK츠르베나 즈베즈다": "Crvena Zvezda", "릴레스트룀SK": "Lillestrom",
    "NK첼레": "Celje", "AEK아테네": "AEK Athens", "CSKA소피아": "CSKA Sofia",
    "AGF오르후스": "Aarhus", "SL벤피카": "Benfica", "FC툰": "Thun",
    "RC셀타데비고": "Celta Vigo", "오사수나": "Osasuna", "바르셀로나": "Barcelona",
    "아틀레틱 빌바오": "Athletic Club", "레알 마드리드": "Real Madrid",
    "아틀레티코 마드리드": "Atletico Madrid", "세비야FC": "Sevilla",
    "발렌시아CF": "Valencia", "레알 소시에다드": "Real Sociedad",
    "레알 베티스": "Real Betis", "비야레알CF": "Villarreal",
    "클루브 아메리카": "Club America", "콜럼버스 크루": "Columbus Crew",
    "인터 마이애미": "Inter Miami", "맨체스터 시티": "Manchester City",
    "맨체스터 유나이티드": "Manchester United", "토트넘 홋스퍼": "Tottenham",
    "노팅엄 포레스트": "Nottingham Forest", "울버햄튼": "Wolverhampton",
    "인터 밀란": "Inter", "AC밀란": "AC Milan", "유벤투스": "Juventus",
    "파리 생제르맹": "Paris Saint Germain", "울산 HD": "Ulsan Hyundai",
    "전북 현대": "Jeonbuk Motors", "FC서울": "FC Seoul", "포항 스틸러스": "Pohang Steelers"
}

# 기획자님이 주셨던 100% 원본 딕셔너리에 + 캡처로 주신 팀만 안전하게 추가!
DIRECT_TEAM_INFO = {
    "제주 SKFC": {"id": 2977, "logo": "https://media.api-sports.io/football/teams/2977.png"},
    "제주": {"id": 2977, "logo": "https://media.api-sports.io/football/teams/2977.png"},
    "제주 유나이티드": {"id": 2977, "logo": "https://media.api-sports.io/football/teams/2977.png"},
    "울산 HDFC": {"id": 2975, "logo": "https://media.api-sports.io/football/teams/2975.png"},
    "울산HD": {"id": 2975, "logo": "https://media.api-sports.io/football/teams/2975.png"},
    "김천상무": {"id": 2978, "logo": "https://media.api-sports.io/football/teams/2978.png"},
    "김천상무 프로축구단": {"id": 2978, "logo": "https://media.api-sports.io/football/teams/2978.png"},
    "강원FC": {"id": 2972, "logo": "https://media.api-sports.io/football/teams/2972.png"},
    "포항스틸": {"id": 2974, "logo": "https://media.api-sports.io/football/teams/2974.png"},
    "포항 스틸러스": {"id": 2974, "logo": "https://media.api-sports.io/football/teams/2974.png"},
    "FC서울": {"id": 2766, "logo": "https://media.api-sports.io/football/teams/2766.png"},
    "FC 서울": {"id": 2766, "logo": "https://media.api-sports.io/football/teams/2766.png"},
    "서울FC": {"id": 2766, "logo": "https://media.api-sports.io/football/teams/2766.png"},
    "수원FC": {"id": 2980, "logo": "https://media.api-sports.io/football/teams/2980.png"},
    "광주FC": {"id": 2983, "logo": "https://media.api-sports.io/football/teams/2983.png"},
    "인천유나": {"id": 2973, "logo": "https://media.api-sports.io/football/teams/2973.png"},
    "인천 유나이티드": {"id": 2973, "logo": "https://media.api-sports.io/football/teams/2973.png"},
    "전북현대": {"id": 2971, "logo": "https://media.api-sports.io/football/teams/2971.png"},
    "전북 현대모터스": {"id": 2971, "logo": "https://media.api-sports.io/football/teams/2971.png"},
    "대전하나": {"id": 2985, "logo": "https://media.api-sports.io/football/teams/2985.png"},
    "대전 하나시티즌": {"id": 2985, "logo": "https://media.api-sports.io/football/teams/2985.png"},
    "FC안양": {"id": 2986, "logo": "https://media.api-sports.io/football/teams/2986.png"},
    "FC 안양": {"id": 2986, "logo": "https://media.api-sports.io/football/teams/2986.png"},
    "전남드래": {"id": 2988, "logo": "https://media.api-sports.io/football/teams/2988.png"},
    "전남 드래곤즈": {"id": 2988, "logo": "https://media.api-sports.io/football/teams/2988.png"},
    "서울이랜드": {"id": 2987, "logo": "https://media.api-sports.io/football/teams/2987.png"},
    "서울 이랜드": {"id": 2987, "logo": "https://media.api-sports.io/football/teams/2987.png"},
    "수원삼성": {"id": 2976, "logo": "https://media.api-sports.io/football/teams/2976.png"},
    "수원 삼성블루윙즈": {"id": 2976, "logo": "https://media.api-sports.io/football/teams/2976.png"},
    "부산아이": {"id": 2990, "logo": "https://media.api-sports.io/football/teams/2990.png"},
    "부산 아이파크": {"id": 2990, "logo": "https://media.api-sports.io/football/teams/2990.png"},
    "부천FC": {"id": 2984, "logo": "https://media.api-sports.io/football/teams/2984.png"},
    "부천FC 1995": {"id": 2984, "logo": "https://media.api-sports.io/football/teams/2984.png"},
    "김포FC": {"id": 10453, "logo": "https://media.api-sports.io/football/teams/10453.png"},
    "충남아산": {"id": 3155, "logo": "https://media.api-sports.io/football/teams/3155.png"},
    "충남아산 프로축구단": {"id": 3155, "logo": "https://media.api-sports.io/football/teams/3155.png"},
    "충북청주": {"id": 10452, "logo": "https://media.api-sports.io/football/teams/10452.png"},
    "충북청주 프로축구단": {"id": 10452, "logo": "https://media.api-sports.io/football/teams/10452.png"},
    "안산그리": {"id": 2989, "logo": "https://media.api-sports.io/football/teams/2989.png"},
    "안산 그리너스": {"id": 2989, "logo": "https://media.api-sports.io/football/teams/2989.png"},
    "경남FC": {"id": 2981, "logo": "https://media.api-sports.io/football/teams/2981.png"},
    "천안시티": {"id": 3410, "logo": "https://media.api-sports.io/football/teams/3410.png"},
    "천안 시티FC": {"id": 3410, "logo": "https://media.api-sports.io/football/teams/3410.png"},
    "베트남": {"id": 24, "logo": "https://media.api-sports.io/football/teams/24.png"},
    "태국": {"id": 25, "logo": "https://media.api-sports.io/football/teams/25.png"},
    "프레스턴 라이온스": {"id": 15001, "logo": DEFAULT_LOGO},
    "사우스 멜버른": {"id": 6542, "logo": "https://media.api-sports.io/football/teams/6542.png"},
    "인디펜디엔테 델바예": {"id": 1133, "logo": "https://media.api-sports.io/football/teams/1133.png"},
    "데포르테스 톨리마": {"id": 1184, "logo": "https://media.api-sports.io/football/teams/1184.png"},
    "발렌시아": {"id": 532, "logo": "https://media.api-sports.io/football/teams/532.png"},
    "레알 베티스": {"id": 543, "logo": "https://media.api-sports.io/football/teams/543.png"},
    "버밍엄 시티": {"id": 33, "logo": "https://media.api-sports.io/football/teams/33.png"},
    "브렌트퍼드": {"id": 55, "logo": "https://media.api-sports.io/football/teams/55.png"},
    "노팅엄 포리스트": {"id": 65, "logo": "https://media.api-sports.io/football/teams/65.png"},
    "리즈 유나이티드": {"id": 63, "logo": "https://media.api-sports.io/football/teams/63.png"},
    "LASK": {"id": 649, "logo": "https://media.api-sports.io/football/teams/649.png"},
    "셀틱": {"id": 247, "logo": "https://media.api-sports.io/football/teams/247.png"},
    "FK보되 글림트": {"id": 353, "logo": "https://media.api-sports.io/football/teams/353.png"},
    "NEC네이메헌": {"id": 417, "logo": "https://media.api-sports.io/football/teams/417.png"},
    "블랙번 로버스": {"id": 43, "logo": "https://media.api-sports.io/football/teams/43.png"},
    "셰필드 유나이티드": {"id": 62, "logo": "https://media.api-sports.io/football/teams/62.png"},
    "사우샘프턴": {"id": 41, "logo": "https://media.api-sports.io/football/teams/41.png"},
    "웨스트햄 유나이티드": {"id": 48, "logo": "https://media.api-sports.io/football/teams/48.png"},
    "스토크 시티": {"id": 68, "logo": "https://media.api-sports.io/football/teams/68.png"},
    "헐 시티": {"id": 66, "logo": "https://media.api-sports.io/football/teams/66.png"},
    "CF몬테레이": {"id": 2284, "logo": "https://media.api-sports.io/football/teams/2284.png"},
    "클루브 레온": {"id": 2288, "logo": "https://media.api-sports.io/football/teams/2288.png"},
    "마카비 하이파": {"id": 4440, "logo": "https://media.api-sports.io/football/teams/4440.png"},
    "시카고 파이어FC": {"id": 254, "logo": "https://media.api-sports.io/football/teams/254.png"},
    "레알 솔트레이크": {"id": 257, "logo": "https://media.api-sports.io/football/teams/257.png"},
    "사바FK": {"id": 20456, "logo": "https://media.api-sports.io/football/teams/20456.png"},
    "하포엘 베르셰바": {"id": 4443, "logo": "https://media.api-sports.io/football/teams/4443.png"},
    "야기엘로": {"id": 336, "logo": "https://media.api-sports.io/football/teams/336.png"},
    "신트트라": {"id": 738, "logo": "https://media.api-sports.io/football/teams/738.png"},
    "퀸즐랜드 라이온스": {"id": 6516, "logo": "https://media.api-sports.io/football/teams/6516.png"},
    "아라라트 아르메니아": {"id": 5934, "logo": "https://tmssl.akamaized.net/images/wappen/head/53453.png"},
    "신트 트라위던vv": {"id": 738, "logo": "https://media.api-sports.io/football/teams/738.png"},
    "잘츠부르크": {"id": 571, "logo": "https://media.api-sports.io/football/teams/571.png"},
    "OFI크레타": {"id": 249, "logo": "https://media.api-sports.io/football/teams/249.png"},
    "데포르티보 톨루카": {"id": 2282, "logo": "https://media.api-sports.io/football/teams/2282.png"},
    "신트 트라위던VV": {"id": 738, "logo": "https://media.api-sports.io/football/teams/738.png"},
    "FC툰": {"id": 1012, "logo": "https://media.api-sports.io/football/teams/1012.png"},
    "FC 툰": {"id": 1012, "logo": "https://media.api-sports.io/football/teams/1012.png"},
    "미들즈브러": {"id": 61, "logo": "https://media.api-sports.io/football/teams/61.png"},
    "웨스트브로미치 앨비언": {"id": 60, "logo": "https://media.api-sports.io/football/teams/60.png"},
    "포츠머스": {"id": 67, "logo": "https://media.api-sports.io/football/teams/67.png"},
    "카디프 시티": {"id": 64, "logo": "https://media.api-sports.io/football/teams/64.png"},
    "우니온 베를린": {"id": 182, "logo": "https://media.api-sports.io/football/teams/182.png"},
    "장크트 파울리": {"id": 186, "logo": "https://media.api-sports.io/football/teams/186.png"},
    "쾰른": {"id": 192, "logo": "https://media.api-sports.io/football/teams/192.png"},
    "TSG1899 호펜하임": {"id": 167, "logo": "https://media.api-sports.io/football/teams/167.png"},
    "엘버스베르크": {"id": 3514, "logo": "https://media.api-sports.io/football/teams/3514.png"},
    "바이어04 레버쿠젠": {"id": 168, "logo": "https://media.api-sports.io/football/teams/168.png"}
}

# API-Football의 고정 팀 ID가 검증된 구단은 검색 API가 느리거나 한도에
# 걸려도 엠블럼과 경기 연결이 끊기지 않도록 직접 연결한다.
DIRECT_TEAM_INFO.update({
    # 세리에A 팀은 검색 결과의 동명이인/유소년 팀 오연결을 막기 위해
    # API-Football의 검증된 1군 팀 ID를 직접 사용합니다.
    "AS로마": {"id": 100, "logo": "https://media.api-sports.io/football/teams/100.png"},
    "AS 로마": {"id": 100, "logo": "https://media.api-sports.io/football/teams/100.png"},
    "로마": {"id": 100, "logo": "https://media.api-sports.io/football/teams/100.png"},
    "AS Roma": {"id": 100, "logo": "https://media.api-sports.io/football/teams/100.png"},
    "US레체": {"id": 867, "logo": "https://media.api-sports.io/football/teams/867.png"},
    "US 레체": {"id": 867, "logo": "https://media.api-sports.io/football/teams/867.png"},
    "레체": {"id": 867, "logo": "https://media.api-sports.io/football/teams/867.png"},
    "Lecce": {"id": 867, "logo": "https://media.api-sports.io/football/teams/867.png"},
    "RB라이프치히": {"id": 173, "logo": "https://media.api-sports.io/football/teams/173.png"},
    "묀헨글라트바흐": {"id": 163, "logo": "https://media.api-sports.io/football/teams/163.png"},
    "FSV마인츠05": {"id": 164, "logo": "https://media.api-sports.io/football/teams/164.png"},
    "파더보른07": {"id": 185, "logo": "https://media.api-sports.io/football/teams/185.png"},
    "프랑크푸르트": {"id": 169, "logo": "https://media.api-sports.io/football/teams/169.png"},
    "함부르크": {"id": 175, "logo": "https://media.api-sports.io/football/teams/175.png"},
    # 베트맨의 짧은 표기와 프로토의 전체 표기를 같은 1군 팀으로 고정한다.
    "산프히로": {"id": 282, "logo": "https://media.api-sports.io/football/teams/282.png"},
    "산프레체 히로시마": {"id": 282, "logo": "https://media.api-sports.io/football/teams/282.png"},
    "Sanfrecce Hiroshima": {"id": 282, "logo": "https://media.api-sports.io/football/teams/282.png"},
})

def init_cache_db():
    try:
        conn = _sqlite_connect()
        # WAL lets the isolated LIVE/score/master/world workers read while one
        # short write is committing. Existing data and table contents are kept.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS db_meta (version INTEGER)")
        cursor.execute("SELECT version FROM db_meta")
        row = cursor.fetchone()
        if not row or row[0] < 3:
            cursor.execute("DROP TABLE IF EXISTS predictions")
            cursor.execute("DELETE FROM db_meta")
            cursor.execute("INSERT INTO db_meta (version) VALUES (3)")
            conn.commit()
        cursor.execute("CREATE TABLE IF NOT EXISTS api_cache (cache_key TEXT PRIMARY KEY, cache_value TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, match_id TEXT UNIQUE, league TEXT, home_team TEXT, away_team TEXT,
                prob_pick TEXT, prob_pick_prob REAL,
                ev_pick TEXT, ev_pick_prob REAL,
                odd_h REAL, odd_d REAL, odd_a REAL,
                actual_score TEXT DEFAULT '-:-', actual_result TEXT DEFAULT 'PENDING', 
                is_correct_prob INTEGER DEFAULT 0, is_correct_ev INTEGER DEFAULT 0,
                ai_note TEXT DEFAULT '', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
                is_toto14 INTEGER DEFAULT 0, api_fixture_id INTEGER DEFAULT 0, match_time TEXT DEFAULT '',
                analysis_version TEXT DEFAULT '', postmortem_json TEXT DEFAULT '{}'
            )
        """)
        prediction_columns = {
            str(column[1]) for column in cursor.execute("PRAGMA table_info(predictions)")
        }
        if "analysis_version" not in prediction_columns:
            cursor.execute(
                "ALTER TABLE predictions ADD COLUMN analysis_version TEXT DEFAULT ''"
            )
        if "postmortem_json" not in prediction_columns:
            cursor.execute(
                "ALTER TABLE predictions ADD COLUMN postmortem_json TEXT DEFAULT '{}'"
            )
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS prediction_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id TEXT NOT NULL,
                analysis_version TEXT NOT NULL,
                stage TEXT NOT NULL,
                confidence REAL DEFAULT 0,
                prob_pick TEXT,
                prob_pick_prob REAL,
                ev_pick TEXT,
                ev_pick_prob REAL,
                odd_h REAL,
                odd_d REAL,
                odd_a REAL,
                api_fixture_id INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_prediction_snapshots_match ON prediction_snapshots(match_id, created_at)")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS prediction_analysis (
                match_id TEXT PRIMARY KEY,
                analysis_version TEXT DEFAULT '',
                stage TEXT DEFAULT '',
                odds_source TEXT DEFAULT '',
                selected_market TEXT,
                selected_pick TEXT,
                model_probability REAL,
                fair_probability REAL,
                edge REAL,
                confidence REAL,
                error_margin REAL,
                probability_low REAL,
                probability_high REAL,
                evidence_json TEXT DEFAULT '[]',
                markets_json TEXT DEFAULT '[]',
                categories_json TEXT DEFAULT '{}',
                decision_json TEXT DEFAULT '{}',
                report_text TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS prediction_analysis_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id TEXT NOT NULL,
                analysis_version TEXT NOT NULL,
                stage TEXT NOT NULL,
                odds_source TEXT DEFAULT '',
                confidence REAL DEFAULT 0,
                selected_market TEXT,
                selected_pick TEXT,
                candidate_count INTEGER DEFAULT 0,
                evidence_json TEXT DEFAULT '[]',
                candidates_json TEXT DEFAULT '[]',
                categories_json TEXT DEFAULT '{}',
                decision_json TEXT DEFAULT '{}',
                report_text TEXT DEFAULT '',
                fingerprint TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(match_id, analysis_version, stage, fingerprint)
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_analysis_snapshots_match "
            "ON prediction_analysis_snapshots(match_id, id)"
        )
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS prediction_candidate_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id TEXT NOT NULL,
                analysis_snapshot_id INTEGER NOT NULL,
                analysis_version TEXT NOT NULL,
                stage TEXT NOT NULL,
                market_key TEXT NOT NULL,
                raw_pick TEXT NOT NULL,
                model_probability REAL DEFAULT 0,
                fair_probability REAL,
                odd REAL DEFAULT 0,
                selection_rank INTEGER,
                market_rank INTEGER,
                selected_as TEXT DEFAULT '',
                is_correct INTEGER NOT NULL,
                actual_score TEXT NOT NULL,
                graded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(analysis_snapshot_id, market_key, raw_pick)
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_candidate_results_match "
            "ON prediction_candidate_results(match_id, analysis_version)"
        )
        conn.commit()
        conn.close()
    except Exception as e: print(f"❌ [DB 에러] 초기화 실패: {e}")

def get_db_cache(key, ttl_hours):
    for attempt in range(len(SQLITE_BUSY_RETRY_DELAYS) + 1):
        conn = None
        try:
            conn = _sqlite_connect()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT cache_value, updated_at FROM api_cache WHERE cache_key = ?",
                (key,),
            )
            row = cursor.fetchone()
            if row:
                val, updated_at = row
                updated_time = datetime.strptime(
                    updated_at, "%Y-%m-%d %H:%M:%S"
                ).replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - updated_time < timedelta(hours=ttl_hours):
                    return json.loads(val)
            return None
        except sqlite3.OperationalError as error:
            if _is_sqlite_busy(error) and attempt < len(SQLITE_BUSY_RETRY_DELAYS):
                time.sleep(SQLITE_BUSY_RETRY_DELAYS[attempt])
                continue
            print(f"⚠️ [관제 봇 떡밥] DB 캐시 읽기 실패 ({key}): {error}")
            return None
        except Exception as error:
            print(f"⚠️ [관제 봇 떡밥] DB 캐시 읽기 실패 ({key}): {error}")
            return None
        finally:
            if conn is not None:
                conn.close()
    return None


def set_db_cache(key, value):
    encoded = json.dumps(value, ensure_ascii=False)
    for attempt in range(len(SQLITE_BUSY_RETRY_DELAYS) + 1):
        conn = None
        try:
            conn = _sqlite_connect()
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "INSERT OR REPLACE INTO api_cache "
                "(cache_key, cache_value, updated_at) VALUES (?, ?, ?)",
                (key, encoded, now_str),
            )
            conn.commit()
            return True
        except sqlite3.OperationalError as error:
            if conn is not None:
                conn.rollback()
            if _is_sqlite_busy(error) and attempt < len(SQLITE_BUSY_RETRY_DELAYS):
                time.sleep(SQLITE_BUSY_RETRY_DELAYS[attempt])
                continue
            print(f"⚠️ [관제 봇 떡밥] DB 캐시 쓰기 실패 ({key}): {error}")
            return False
        except Exception as error:
            if conn is not None:
                conn.rollback()
            print(f"⚠️ [관제 봇 떡밥] DB 캐시 쓰기 실패 ({key}): {error}")
            return False
        finally:
            if conn is not None:
                conn.close()
    return False

# ==============================================================
# 🔥 [V4 엔진 업그레이드] 탐지 함수 및 메커니즘 영역
# ==============================================================

SMART_MAPPING_FILE = "smart_mapping.json"
TEAM_INFO_MEMORY_CACHE = {}
TEAM_INFO_FAILURE_RETRY_AT = {}

# 베트맨/국내 표기와 API-FOOTBALL 영문 팀명을 연결한다.
# 여기서 찾은 동일 팀 ID를 팀 마크와 최근 전적 조회에 함께 사용한다.
BUILTIN_TEAM_ALIASES = {
    # 베트맨 화면의 짧은 팀명도 같은 공식 API 팀으로 연결합니다.
    "포츠머스": "Portsmouth",
    "더비카운": "Derby",
    "더비카운티": "Derby",
    "프레스턴": "Preston",
    "프레스턴 노스엔드": "Preston",
    "브리스C": "Bristol City",
    "셰필드U": "Sheffield Utd",
    "볼턴W": "Bolton",
    "스완지C": "Swansea",
    "웨스트햄": "West Ham",
    "사우샘프": "Southampton",
    "버밍엄C": "Birmingham",
    "스토크C": "Stoke City",
    "노리치C": "Norwich City",
    "도쿄베르": "Tokyo Verdy",
    "비셀고베": "Vissel Kobe",
    "마치다Z": "Machida Zelvia",
    "가와사키": "Kawasaki Frontale",
    "시미즈S": "Shimizu S-Pulse",
    "산프히로": "Sanfrecce Hiroshima",
    "후쿠오카": "Avispa Fukuoka",
    "C오사카": "Cerezo Osaka",
    "오사카C": "Cerezo Osaka",
    "가시와R": "Kashiwa Reysol",
    "삿포로": "Consadole Sapporo",
    "나고야G": "Nagoya Grampus",
    "우라와R": "Urawa Red Diamonds",
    "G오사카": "Gamba Osaka",
    "오사카G": "Gamba Osaka",
    "데포르티보 라코루냐": "Deportivo La Coruna",
    "시미즈 에스펄스": "Shimizu S-Pulse",
    "시미즈S펄스": "Shimizu S-Pulse",
    "V바렌 나가사키": "V-Varen Nagasaki",
    "V-바렌 나가사키": "V-Varen Nagasaki",
    "V바렌나가사키": "V-Varen Nagasaki",
    "V바렌나": "V-Varen Nagasaki",
    "요코하마 F 마리노스": "Yokohama F. Marinos",
    "요코하마F마리노스": "Yokohama F. Marinos",
    "빌럼II": "Willem II",
    "빌럼2": "Willem II",
    "SC헤이렌베인": "Heerenveen",
    "헤이렌베인": "Heerenveen",
    "SC프라이부르크": "SC Freiburg",
    "프라이부르크": "SC Freiburg",
    "베르더브레멘": "Werder Bremen",
    "베르더 브레멘": "Werder Bremen",
    "텔스타": "Telstar",
    "AFC아약스": "Ajax",
    "아약스": "Ajax",
    "아우크스부르크": "FC Augsburg",
    "샬케04": "FC Schalke 04",
    "샬케 04": "FC Schalke 04",
    "SC캄뷔르": "Cambuur",
    "캄뷔르": "Cambuur",
    "트벤테": "Twente",
    "PSV에인트호번": "PSV Eindhoven",
    "PSV 에인트호번": "PSV Eindhoven",
    "위트레흐트": "Utrecht",
    "페예노르트": "Feyenoord",
    "묀헨글라트바흐": "Borussia Monchengladbach",
    "RB라이프치히": "RB Leipzig",
    "레버쿠젠": "Bayer Leverkusen",
    "도르트문트": "Borussia Dortmund",
    "브라이턴": "Brighton",
    "브라이튼": "Brighton",
    "헐시티": "Hull City",
    "헐 시티": "Hull City",
    "코벤트리": "Coventry City",
    "코번트리": "Coventry City",
    "맨체스터U": "Manchester United",
    "맨유": "Manchester United",
    "리버풀": "Liverpool",
    "칼리아리": "Cagliari",
    "인터밀란": "Inter",
    "AS로마": "Roma",
    "AS 로마": "Roma",
    "로마": "Roma",
    "US레체": "Lecce",
    "US 레체": "Lecce",
    "레체": "Lecce",
    "SSC나폴리": "Napoli",
    "나폴리": "Napoli",
    "코모1907": "Como",
    "광주FC": "Gwangju FC",
    "FC서울": "FC Seoul",
}


def _builtin_team_alias_key(value):
    return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").lower())


BUILTIN_TEAM_ALIAS_INDEX = {
    _builtin_team_alias_key(local_name): api_name
    for local_name, api_name in BUILTIN_TEAM_ALIASES.items()
}


def _lookup_builtin_team_alias(team_name):
    return BUILTIN_TEAM_ALIAS_INDEX.get(_builtin_team_alias_key(team_name))

def _sanitize_team_search(value):
    """API-Football 검색 규칙(영문/숫자/공백만 허용)에 맞춘다."""
    return re.sub(r'\s+', ' ', re.sub(r'[^A-Za-z0-9 ]+', ' ', str(value or ''))).strip()

def _team_search_candidates(translated_name, saved_name=None):
    """정확한 이름부터 안전한 별칭까지 중복 없이 검색 후보를 만든다."""
    candidates = []

    def add(value):
        value = _sanitize_team_search(value)
        if len(value) >= 3 and value.casefold() not in {item.casefold() for item in candidates}:
            candidates.append(value)

    add(saved_name)
    add(translated_name)

    sanitized = _sanitize_team_search(saved_name or translated_name)
    # API에서 자주 쓰지 않는 창단연도와 구단 접두/접미어를 제거한 후보.
    without_year = re.sub(r'\b(?:19|20)\d{2}\b', ' ', sanitized)
    without_club_words = re.sub(
        r'\b(?:FC|AFC|CF|SC|AC|AS|RC|RSC|SK|FK|SV|GNK|KF|AGF|CSKA|TC|AIF|NK|UD|CD)\b',
        ' ',
        without_year,
        flags=re.IGNORECASE,
    )
    add(without_year)
    add(without_club_words)

    # 공식 API가 별칭을 빼고 도시명으로만 등록한 대표 사례들.
    add(re.sub(r'\b(?:Red Diamonds|Antlers)\b$', ' ', without_club_words, flags=re.IGNORECASE))

    # 전체 구단명 검색이 비어 있을 때 핵심 단어로 한 번 더 찾는다.
    # 예: Shimizu S-Pulse -> Pulse/Shimizu,
    #     V-Varen Nagasaki -> Nagasaki/Varen,
    #     Yokohama F. Marinos -> Marinos/Yokohama
    # 너무 짧은 약자는 오검색 위험이 커서 제외하고 호출 수 또한 최대 2회로 제한한다.
    key_tokens = [token for token in without_club_words.split() if len(token) >= 5]
    if key_tokens:
        add(key_tokens[-1])
        add(key_tokens[0])
    return candidates

def _normalize_team_alias(value):
    return re.sub(r'[^0-9A-Za-z가-힣]+', '', str(value or '')).casefold()

def _resolve_team_logo(team_name, team_id=0, api_logo=None):
    """공식 예외 로고를 우선하고 나머지는 API 로고를 그대로 쓴다."""
    try:
        direct_logo = OFFICIAL_TEAM_LOGOS_BY_ID.get(int(team_id or 0))
        if direct_logo:
            return direct_logo
    except (TypeError, ValueError):
        pass

    normalized_name = _normalize_team_alias(team_name)
    for mapped_name, official_logo in OFFICIAL_TEAM_LOGOS.items():
        if normalized_name == _normalize_team_alias(mapped_name):
            return official_logo
    return api_logo or DEFAULT_LOGO

def _team_id_from_resolved_logo(team_name):
    """이미 확인된 팀 로고와 같은 API 팀 ID를 돌려줍니다."""
    resolved_logo = _resolve_team_logo(team_name, 0, DEFAULT_LOGO)
    if not resolved_logo or resolved_logo == DEFAULT_LOGO:
        return 0

    clean_logo = str(resolved_logo).split("?", 1)[0]

    for known_id, known_logo in OFFICIAL_TEAM_LOGOS_BY_ID.items():
        if str(known_logo).split("?", 1)[0] == clean_logo:
            try:
                return int(known_id)
            except (TypeError, ValueError):
                pass

    match = re.search(r"/teams/(\d+)\.(?:png|webp|svg)$", clean_logo, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return 0


def _resolve_translated_team_name(team_name):
    """베트맨의 띄어쓰기/축약 차이를 기존 한영 사전에 안전하게 연결한다."""
    builtin_alias = _lookup_builtin_team_alias(team_name)
    if builtin_alias:
        return builtin_alias
    if team_name in MANUAL_TEAM_MAP:
        return MANUAL_TEAM_MAP[team_name]
    if team_name in TEAM_NAME_MAP:
        return TEAM_NAME_MAP[team_name]

    normalized = _normalize_team_alias(team_name)
    if len(normalized) < 3:
        return team_name

    alias_candidates = []
    combined_mapping = {**TEAM_NAME_MAP, **MANUAL_TEAM_MAP}
    for korean_name, english_name in combined_mapping.items():
        normalized_key = _normalize_team_alias(korean_name)
        if normalized == normalized_key:
            return english_name
        # '코번트리' ↔ '코번트리 시티', '라치오' ↔ 'SS라치오'처럼
        # 한쪽이 다른 쪽에 완전히 포함될 때만 허용해 엉뚱한 팀 연결을 막는다.
        if normalized in normalized_key or normalized_key in normalized:
            alias_candidates.append((abs(len(normalized_key) - len(normalized)), english_name))

    if alias_candidates:
        alias_candidates.sort(key=lambda item: item[0])
        best_distance = alias_candidates[0][0]
        best_names = {name for distance, name in alias_candidates if distance == best_distance}
        if len(best_names) == 1:
            return next(iter(best_names))
    return team_name


def _verified_team_cache_key(team_name):
    return f"team_info_v7_verified_{_normalize_team_alias(team_name)}"


def _load_verified_team_info(team_name):
    """경기표의 홈·원정 한 쌍으로 검증된 팀 정보만 불러온다."""
    cached = get_db_cache(_verified_team_cache_key(team_name), 24 * 365)
    if not isinstance(cached, dict) or not int(cached.get("id") or 0):
        return None
    cached = dict(cached)
    cached["logo"] = _resolve_team_logo(
        team_name, cached.get("id"), cached.get("logo")
    )
    return cached


def _remember_verified_team(team_name, api_team):
    """실제 경기표에서 확인한 ID·로고를 모든 수집 단계의 기준으로 저장한다."""
    result = dict(api_team or {})
    team_id = int(result.get("id") or 0)
    if not team_id:
        return None
    result["logo"] = _resolve_team_logo(
        team_name,
        team_id,
        result.get("logo") or f"https://media.api-sports.io/football/teams/{team_id}.png",
    )
    result["verified_pair"] = True
    identity_key = _normalize_team_alias(team_name)
    TEAM_INFO_MEMORY_CACHE[team_name] = result
    if identity_key:
        TEAM_INFO_MEMORY_CACHE[identity_key] = result
    set_db_cache(_verified_team_cache_key(team_name), result)
    return result


def _latin_team_key(value):
    """악센트·구두점·FC 표기 차이를 제거한 영문 팀 비교 키."""
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").casefold()
    return re.sub(r"[^0-9a-z]+", "", ascii_value)


def known_team_id(team_name):
    """베트맨 이름으로 이미 검증된 API 팀 ID를 반환한다."""
    # 같은 프로세스에서 이미 찾은 팀과 DB에 저장된 정상 팀을 모두 같은
    # 대표 ID로 사용한다. 로고ㆍ최근 전적ㆍ라이브ㆍ채점이 서로 다른 팀을
    # 가리키지 않도록 하는 단일 팀 신원 기준이다.
    target = _normalize_team_alias(team_name)
    remembered = TEAM_INFO_MEMORY_CACHE.get(team_name) or TEAM_INFO_MEMORY_CACHE.get(target)
    if isinstance(remembered, dict) and int(remembered.get("id") or 0):
        return int(remembered["id"])

    verified = _load_verified_team_info(team_name)
    if verified:
        return int(verified["id"])

    direct = DIRECT_TEAM_INFO.get(team_name)
    if direct:
        return int(direct.get("id") or 0)
    for mapped_name, mapped in DIRECT_TEAM_INFO.items():
        if target and target == _normalize_team_alias(mapped_name):
            return int(mapped.get("id") or 0)
    return 0


def team_matches_api(local_name, api_name, api_team_id=0):
    """베트맨 팀과 API 팀이 같은 팀인지 보수적으로 판정한다.

    검증된 팀 ID를 최우선으로 사용하고, ID가 아직 없는 팀에 한해서만
    영문 변환 이름의 정확 일치·포함·높은 유사도를 허용한다.
    """
    expected_id = known_team_id(local_name)
    try:
        candidate_id = int(api_team_id or 0)
    except (TypeError, ValueError):
        candidate_id = 0
    if expected_id and candidate_id:
        return expected_id == candidate_id

    translated = _resolve_translated_team_name(local_name)
    local_keys = {
        _latin_team_key(local_name),
        _latin_team_key(translated),
    }
    local_keys.discard("")
    api_key = _latin_team_key(api_name)
    if not api_key or not local_keys:
        return False

    for local_key in local_keys:
        if local_key == api_key:
            return True
        if min(len(local_key), len(api_key)) >= 6 and (
            local_key in api_key or api_key in local_key
        ):
            return True
        if difflib.SequenceMatcher(None, local_key, api_key).ratio() >= 0.78:
            return True
    return False

def load_smart_mapping():
    if os.path.exists(SMART_MAPPING_FILE):
        try:
            with open(SMART_MAPPING_FILE, "r", encoding="utf-8") as f: return json.load(f)
        except: pass
    return {}

def save_smart_mapping(mapping):
    try:
        with open(SMART_MAPPING_FILE, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=4)
    except: pass

def fetch_team_info_api(team_name):
    identity_key = _normalize_team_alias(team_name)
    remembered = TEAM_INFO_MEMORY_CACHE.get(team_name) or TEAM_INFO_MEMORY_CACHE.get(identity_key)
    if remembered:
        return remembered

    def remember(result):
        TEAM_INFO_MEMORY_CACHE[team_name] = result
        if identity_key:
            TEAM_INFO_MEMORY_CACHE[identity_key] = result
        return result

    verified = _load_verified_team_info(team_name)
    if verified:
        return remember(verified)

    if team_name in DIRECT_TEAM_INFO:
        direct = DIRECT_TEAM_INFO[team_name]
        result = {
            "id": direct["id"],
            "name": team_name,
            "logo": _resolve_team_logo(team_name, direct["id"], direct.get("logo")),
        }
        return remember(result)

    canonical_id = known_team_id(team_name)
    if canonical_id:
        result = {
            "id": canonical_id,
            "name": _resolve_translated_team_name(team_name) or team_name,
            "logo": _resolve_team_logo(
                team_name,
                canonical_id,
                f"https://media.api-sports.io/football/teams/{canonical_id}.png",
            ),
        }
        set_db_cache(f"team_info_v7_search_{team_name}", result)
        return remember(result)

    # 수동/공식 로고가 이미 확인된 팀은 그 로고의 API 팀 ID를 그대로 사용합니다.
    # 이 ID가 최근 전적, 경기 매칭, 라이브 스코어 조회에 공통으로 전달됩니다.
    resolved_logo = _resolve_team_logo(team_name, 0, DEFAULT_LOGO)
    logo_team_id = _team_id_from_resolved_logo(team_name)
    if logo_team_id:
        result = {
            "id": logo_team_id,
            "name": _resolve_translated_team_name(team_name) or team_name,
            "logo": resolved_logo,
        }
        set_db_cache(f"team_info_v7_search_{team_name}", result)
        return remember(result)

    fallback_res = {"id": 0, "name": team_name, "logo": _resolve_team_logo(team_name, 0, DEFAULT_LOGO)}
    retry_at = TEAM_INFO_FAILURE_RETRY_AT.get(team_name)
    if retry_at and datetime.now(timezone.utc) < retry_at:
        return fallback_res
    # 이전 버전은 검색 실패(id=0)까지 1년 캐시해 복구를 막았다. 버전을
    # 올리고 실제 팀을 찾은 결과만 장기 캐시한다.
    cache_key = f"team_info_v7_search_{team_name}"
    cached_data = get_db_cache(cache_key, 8760)
    if cached_data and cached_data.get("id"):
        cached_data["logo"] = _resolve_team_logo(
            team_name, cached_data.get("id"), cached_data.get("logo")
        )
        set_db_cache(cache_key, cached_data)
        return remember(cached_data)

    translated_name = _resolve_translated_team_name(team_name)
    smart_mapping = load_smart_mapping()
    search_name = smart_mapping.get(translated_name, translated_name)
    candidates = _team_search_candidates(translated_name, search_name)

    if not candidates:
        print(f"⚠️ API 팀 검색용 영문 이름이 없음: {team_name}")
        remember(fallback_res)
        return fallback_res

    try:
        comparison_name = _sanitize_team_search(translated_name).casefold()
        last_error = None
        had_api_error = False
        for candidate in candidates:
            res = api_get(
                "/teams",
                params={"search": candidate},
                timeout=8,
            )
            if res.status_code != 200:
                last_error = f"HTTP {res.status_code}"
                had_api_error = True
                # 제한 초과나 인증 오류일 때 후보를 연달아 호출하지 않는다.
                if res.status_code in (401, 403, 429):
                    break
                continue

            payload = res.json()
            if payload.get("errors"):
                last_error = str(payload.get("errors"))
                had_api_error = True
                continue

            data = payload.get("response", [])
            if not data:
                continue

            def similarity(entry):
                api_name = _sanitize_team_search(entry.get("team", {}).get("name", "")).casefold()
                return max(
                    difflib.SequenceMatcher(None, comparison_name, api_name).ratio(),
                    difflib.SequenceMatcher(None, candidate.casefold(), api_name).ratio(),
                )

            best_entry = max(data, key=similarity)
            # 핵심 단어 검색 결과가 전혀 다른 팀이면 저장하지 않는다.
            # 잘못 저장된 팀 ID는 로고·최근 전적·채점까지 모두 오염시키기 때문이다.
            if similarity(best_entry) < 0.45:
                continue
            result = best_entry.get('team', {})
            if not result.get("id"):
                continue

            result["logo"] = _resolve_team_logo(
                team_name, result.get("id"), result.get("logo")
            )

            if candidate.casefold() != candidates[0].casefold():
                print(
                    f"✅ 팀 자동 대체 검색 성공: {team_name} -> "
                    f"{result.get('name', team_name)} (핵심 검색어: {candidate})"
                )
            smart_mapping[translated_name] = candidate
            save_smart_mapping(smart_mapping)
            set_db_cache(cache_key, result)
            remember(result)
            TEAM_INFO_FAILURE_RETRY_AT.pop(team_name, None)
            return result

        if last_error:
            print(f"⚠️ 팀 검색 API 오류({team_name}): {last_error}")
        else:
            print(f"⚠️ API에서 팀을 찾지 못함: {team_name} (검색 후보: {', '.join(candidates)})")
        # API 시간 초과/서버 오류는 실패로 고정하지 않고 다음 20분 주기에 재시도한다.
        if had_api_error:
            TEAM_INFO_FAILURE_RETRY_AT[team_name] = datetime.now(timezone.utc) + timedelta(minutes=2)
        else:
            remember(fallback_res)
        return fallback_res

    except Exception as e:
        print(f"⚠️ 팀 검색 통신 오류({team_name}): {e}")
        TEAM_INFO_FAILURE_RETRY_AT[team_name] = datetime.now(timezone.utc) + timedelta(minutes=2)
        return fallback_res

def parse_match_time(match_time_str):
    now = datetime.now(timezone(timedelta(hours=9)))
    if not match_time_str or match_time_str in ["시간 미정", "마감/진행중"]:
        return now - timedelta(hours=3)
    try:
        m_match = re.search(
            r'(?:(\d{2,4})\.)?(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})',
            str(match_time_str),
        )
        if m_match:
            year_text, mo, d, h, m = m_match.groups()
            mo, d, h, m = map(int, (mo, d, h, m))
            if year_text:
                year = int(year_text)
                if year < 100:
                    year += 2000
                return datetime(
                    year, mo, d, h, m,
                    tzinfo=timezone(timedelta(hours=9)),
                )
            candidates = [
                datetime(year, mo, d, h, m, tzinfo=timezone(timedelta(hours=9)))
                for year in (now.year - 1, now.year, now.year + 1)
            ]
            return min(candidates, key=lambda value: abs((value - now).total_seconds()))
    except Exception as e:
        print(f"⚠️ [관제 봇 떡밥] 경기 시간 파싱 실패: {e}")
    return now - timedelta(hours=3)


def _team_name_match_score(local_name, api_name):
    """고정 ID를 배제하고 이름만으로 동일 팀 신뢰도를 계산한다."""
    translated = _resolve_translated_team_name(local_name)
    api_key = _latin_team_key(api_name)
    if not api_key:
        return 0.0

    scores = []
    for candidate in (translated, local_name):
        local_key = _latin_team_key(candidate)
        if not local_key:
            continue
        if local_key == api_key:
            scores.append(1.0)
            continue
        if min(len(local_key), len(api_key)) >= 5 and (
            local_key in api_key or api_key in local_key
        ):
            scores.append(0.96)
            continue
        scores.append(difflib.SequenceMatcher(None, local_key, api_key).ratio())
    return max(scores, default=0.0)


def _fetch_date_fixtures_api(date_str, ttl_h=2, purpose="analysis"):
    """한 날짜 경기표를 한 번만 받아 팀 신원·경기 ID가 함께 사용한다."""
    cache_key = f"fixtures_by_date_v2_{date_str}"
    cached = get_db_cache(cache_key, min(max(float(ttl_h or 0), 0.2), 2))
    if cached is not None:
        return cached
    try:
        response = api_get(
            "/fixtures",
            params={"date": date_str, "timezone": "Asia/Seoul"},
            timeout=12,
            purpose=purpose,
        )
        if response.status_code != 200:
            print(f"⚠️ 실제 경기표 조회 실패({date_str}): HTTP {response.status_code}")
            return None
        payload = response.json()
        if payload.get("errors"):
            print(f"⚠️ 실제 경기표 API 오류({date_str}): {payload.get('errors')}")
            return None
        fixtures = payload.get("response", [])
        set_db_cache(cache_key, fixtures)
        return fixtures
    except Exception as error:
        print(f"⚠️ 실제 경기표 조회 오류({date_str}): {error}")
        return None


def _fixture_team_payload(fixture_data, side):
    team = dict(fixture_data.get("teams", {}).get(side, {}) or {})
    team.pop("winner", None)
    team_id = int(team.get("id") or 0)
    if team_id and not team.get("logo"):
        team["logo"] = f"https://media.api-sports.io/football/teams/{team_id}.png"
    return team


def resolve_match_team_pair(home_name, away_name, match_time_str, ttl_h=2):
    """실제 날짜별 경기표에서 홈·원정 두 팀을 동시에 확정한다.

    한 팀씩 검색하면 동명이인이나 오래된 잘못된 캐시가 상대 팀까지 오염시킬
    수 있다. 이 함수는 같은 경기의 양쪽 이름과 킥오프 시간을 함께 대조하고,
    서로 다른 두 팀에 같은 ID가 배정되는 결과를 절대 반환하지 않는다.
    """
    home_name = str(home_name or "").strip()
    away_name = str(away_name or "").strip()
    different_teams = _normalize_team_alias(home_name) != _normalize_team_alias(away_name)

    if match_time_str not in (None, "", "시간 미정", "마감/진행중"):
        match_dt = parse_match_time(match_time_str)
        date_str = match_dt.strftime("%Y-%m-%d")
        fixtures = _fetch_date_fixtures_api(date_str, ttl_h)
        if fixtures is not None:
            candidates = []
            for fixture_data in fixtures:
                home_api = _fixture_team_payload(fixture_data, "home")
                away_api = _fixture_team_payload(fixture_data, "away")
                home_id = int(home_api.get("id") or 0)
                away_id = int(away_api.get("id") or 0)
                if not home_id or not away_id or (different_teams and home_id == away_id):
                    continue
                home_score = _team_name_match_score(home_name, home_api.get("name"))
                away_score = _team_name_match_score(away_name, away_api.get("name"))
                timestamp = int(fixture_data.get("fixture", {}).get("timestamp") or 0)
                time_delta = (
                    abs(timestamp - int(match_dt.timestamp())) / 3600.0
                    if timestamp
                    else 99.0
                )
                time_bonus = max(0.0, 0.18 - min(time_delta, 12.0) * 0.015)
                candidates.append(
                    (home_score + away_score + time_bonus, home_score, away_score,
                     time_delta, fixture_data, home_api, away_api)
                )

            candidates.sort(key=lambda item: item[0], reverse=True)
            selected = None
            if candidates:
                best = candidates[0]
                if min(best[1], best[2]) >= 0.72 and best[1] + best[2] >= 1.52:
                    selected = best

            # 베트맨의 새 축약명이 사전에 아직 없더라도, 한쪽 팀이 정확하고
            # 같은 시각 후보가 하나뿐이면 실제 상대 팀을 경기표에서 역확정한다.
            if selected is None:
                partner_candidates = [
                    item for item in candidates
                    if max(item[1], item[2]) >= 0.90 and item[3] <= 2.5
                ]
                unique_pairs = {
                    (int(item[5].get("id") or 0), int(item[6].get("id") or 0))
                    for item in partner_candidates
                }
                if len(unique_pairs) == 1 and partner_candidates:
                    selected = max(partner_candidates, key=lambda item: item[0])

            if selected is not None:
                _, home_score, away_score, _, fixture_data, home_api, away_api = selected
                verified_home = _remember_verified_team(home_name, home_api)
                verified_away = _remember_verified_team(away_name, away_api)
                if (
                    verified_home
                    and verified_away
                    and int(verified_home["id"]) != int(verified_away["id"])
                ):
                    print(
                        f"[팀검증 성공] 실제 경기표 팀 확정: {home_name}({verified_home['id']}) vs "
                        f"{away_name}({verified_away['id']}) "
                        f"[이름 점수 {home_score:.2f}/{away_score:.2f}]"
                    )
                    return verified_home, verified_away, fixture_data

            print(
                f"[팀검증 재시도] 실제 경기표에서 팀 쌍을 확정하지 못함: "
                f"{home_name} vs {away_name} ({date_str})"
            )

    # 날짜 정보가 없거나 공급사 경기표가 잠시 실패하면 기존 개별 검색을
    # 사용하되, 서로 다른 팀이 같은 ID가 되는 순간 결과를 폐기한다.
    home_info = fetch_team_info_api(home_name)
    away_info = fetch_team_info_api(away_name)
    home_id = int(home_info.get("id") or 0)
    away_id = int(away_info.get("id") or 0)
    if different_teams and home_id and home_id == away_id:
        print(
            f"[팀검증 차단] 중복 팀 ID: {home_name}와 {away_name}가 모두 {home_id}번으로 "
            "연결되어 해당 결과를 사용하지 않습니다."
        )
        return (
            {"id": 0, "name": home_name, "logo": None, "identity_error": "duplicate_id"},
            {"id": 0, "name": away_name, "logo": None, "identity_error": "duplicate_id"},
            None,
        )
    return home_info, away_info, None

def fetch_weather_api(city_name, ttl_h):
    if not city_name: return "Clear"
    clean_city = city_name.split(',')[0].strip()
    cache_key = f"weather_{clean_city}"
    cached_data = get_db_cache(cache_key, ttl_h)
    if cached_data: return cached_data
    try:
        res = requests.get(f"https://wttr.in/{clean_city}?format=j1", timeout=4)
        data = res.json()
        condition = data['current_condition'][0]['weatherDesc'][0]['value'].lower()
        if 'rain' in condition or 'shower' in condition or 'drizzle' in condition: result = "Rain"
        elif 'snow' in condition or 'blizzard' in condition: result = "Snow"
        else: result = "Clear"
        set_db_cache(cache_key, result)
        return result
    except: return "Clear"

def _extract_match_winner_odds(odds_data):
    """Return median 1X2 odds across bookmakers with complete prices."""
    samples = {"odd_h": [], "odd_d": [], "odd_a": []}
    complete_bookmakers = 0
    for odds_row in odds_data or []:
        for bookmaker in odds_row.get("bookmakers", []) or []:
            bookmaker_values = {}
            for bet in bookmaker.get("bets", []) or []:
                if str(bet.get("name", "")).strip().casefold() not in {
                    "match winner", "1x2"
                }:
                    continue
                for value in bet.get("values", []) or []:
                    label = str(value.get("value", "")).strip().casefold()
                    key = {"home": "odd_h", "draw": "odd_d", "away": "odd_a"}.get(label)
                    try:
                        odd = float(value.get("odd") or 0)
                    except (TypeError, ValueError):
                        continue
                    if key and odd > 1.0:
                        bookmaker_values[key] = odd
            if all(key in bookmaker_values for key in samples):
                complete_bookmakers += 1
                for key in samples:
                    samples[key].append(bookmaker_values[key])

    if not complete_bookmakers:
        return None

    result = {"bookmaker_count": complete_bookmakers}
    for key, values in samples.items():
        ordered = sorted(values)
        middle = len(ordered) // 2
        median = (
            ordered[middle]
            if len(ordered) % 2
            else (ordered[middle - 1] + ordered[middle]) / 2.0
        )
        result[key] = round(median, 3)
    return result


def fetch_overseas_odds_and_fixture_api(
    home_id, away_id, ttl_h, match_time_str="시간 미정", include_odds=False
):
    """두 팀과 경기 날짜가 모두 일치하는 API fixture만 반환한다.

    예전 코드는 홈 팀의 다음/이전 경기로 대체해 다른 경기 점수가 LIVE로
    붙을 수 있었다. 정확히 일치하지 않으면 fixture_id를 만들지 않는다.
    """
    if not home_id or not away_id or match_time_str in ["시간 미정", "마감/진행중"]:
        return None

    m_dt = parse_match_time(match_time_str)
    date_str = m_dt.strftime('%Y-%m-%d')
    odds_requested = bool(include_odds or os.getenv("ENABLE_OVERSEAS_ODDS", "0") == "1")
    cache_key = f"odds_fixture_v11_{home_id}_{away_id}_{date_str}_{int(odds_requested)}"
    cached_data = get_db_cache(cache_key, ttl_h)
    if cached_data: return cached_data
    try:
        # 팀 신원을 확정할 때 받은 같은 날짜 경기표를 그대로 재사용한다.
        date_fixtures = _fetch_date_fixtures_api(date_str, ttl_h)
        if date_fixtures is None:
            return None

        target_ids = {int(home_id), int(away_id)}
        exact_matches = []
        for fixture_data in date_fixtures:
            teams = fixture_data.get("teams", {})
            candidate_ids = {
                teams.get("home", {}).get("id"),
                teams.get("away", {}).get("id"),
            }
            if candidate_ids == target_ids:
                exact_matches.append(fixture_data)

        if exact_matches:
            match_data = min(
                exact_matches,
                key=lambda item: abs(
                    int(item.get("fixture", {}).get("timestamp", 0)) - int(m_dt.timestamp())
                ),
            )
            fix_id = match_data["fixture"]["id"]
            referee_name = match_data["fixture"].get("referee")
            city_name = match_data["fixture"].get("venue", {}).get("city")
            res_val = {
                "fixture_id": fix_id,
                "odd_h": None,
                "odd_d": None,
                "odd_a": None,
                "referee": referee_name,
                "city": city_name,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }

            # 베트맨 배당이 없을 때만 호출자가 해외배당을 명시적으로 요청한다.
            # 여러 북메이커 중 한 곳을 임의 선택하지 않고 완전한 1X2 세트의
            # 중앙값을 사용해 한 업체의 튀는 배당 영향을 줄인다.
            if odds_requested:
                try:
                    odds_res = api_get("/odds", params={"fixture": fix_id}, timeout=5)
                    odds_payload = odds_res.json() if odds_res.status_code == 200 else {}
                    odds_data = odds_payload.get("response", []) if not odds_payload.get("errors") else []
                    extracted = _extract_match_winner_odds(odds_data)
                    if extracted:
                        res_val.update(extracted)
                        res_val["odds_source"] = "overseas_median"
                except Exception as odds_error:
                    print(f"⚠️ 해외배당 조회 실패({fix_id}): {odds_error}")
            set_db_cache(cache_key, res_val)
            return res_val
        print(f"⚠️ 두 팀이 정확히 일치하는 경기 ID 없음: {home_id} vs {away_id} ({date_str})")
    except Exception as e:
        print(f"⚠️ 경기 ID/해외배당 조회 오류({home_id}-{away_id}): {e}")
    return None

def fetch_fixture_details_api(home_id, away_id, ttl_h):
    default_res = {"match_time": None, "last_h2h_date": "-", "h_wins": 0, "draws": 0, "a_wins": 0, "total": 0}
    if not home_id or not away_id: return default_res
    cache_key = f"app_h2h_{home_id}_{away_id}"
    cached_data = get_db_cache(cache_key, ttl_h)
    if cached_data: return cached_data
    try:
        response = api_get("/fixtures/headtohead", params={"h2h": f"{home_id}-{away_id}"}, timeout=5)
        matches = response.json().get("response", [])
        h_wins, draws, a_wins = 0, 0, 0
        for m in matches[:10]:
            if m.get("teams", {}).get("home", {}).get("winner"):
                if m.get("teams", {}).get("home", {}).get("id") == home_id: h_wins += 1
                else: a_wins += 1
            elif m.get("teams", {}).get("away", {}).get("winner"):
                if m.get("teams", {}).get("home", {}).get("id") == home_id: a_wins += 1
                else: h_wins += 1
            else: draws += 1
        res_val = {"match_time": None, "last_h2h_date": "-", "h_wins": h_wins, "draws": draws, "a_wins": a_wins, "total": len(matches[:10])}
        set_db_cache(cache_key, res_val)
        return res_val
    except: return default_res

def _validate_recent_fixtures(matches, team_id):
    """종료되고 해당 팀이 실제 참가한 경기만 중복 없이 시간순으로 정리한다."""
    validated = {}
    for match in matches or []:
        fixture = match.get("fixture", {})
        teams = match.get("teams", {})
        home_id = teams.get("home", {}).get("id")
        away_id = teams.get("away", {}).get("id")
        status = fixture.get("status", {}).get("short")
        fixture_id = fixture.get("id")
        timestamp = int(fixture.get("timestamp") or 0)
        goals = match.get("goals", {})

        if status not in {"FT", "AET", "PEN"}:
            continue
        if team_id not in {home_id, away_id} or not fixture_id or timestamp <= 0:
            continue
        if goals.get("home") is None or goals.get("away") is None:
            continue
        validated[int(fixture_id)] = match

    return sorted(
        validated.values(),
        key=lambda match: int(match.get("fixture", {}).get("timestamp") or 0),
    )[-40:]


def fetch_team_recent_fixtures_api(team_id, ttl_h):
    """최근 경기 원본을 팀당 한 번만 받아 전적/휴식/장기지표가 함께 쓴다."""
    if not team_id:
        return []
    cache_key = f"recent_fixtures_v2_{team_id}"
    cached_data = get_db_cache(cache_key, ttl_h)
    if cached_data is not None:
        return cached_data
    # 일시적인 API 장애 때 웹의 전적이 사라지지 않도록 마지막 정상본을 보존한다.
    stale_data = get_db_cache(cache_key, 24 * 365 * 5)
    try:
        response = api_get(
            "/fixtures",
            params={"team": team_id, "last": 40, "timezone": "Asia/Seoul"},
            timeout=12,
        )
        if response.status_code != 200:
            print(f"⚠️ 최근 경기 조회 실패({team_id}): HTTP {response.status_code}")
            return stale_data or []
        payload = response.json()
        if payload.get("errors"):
            print(f"⚠️ 최근 경기 API 오류({team_id}): {payload.get('errors')}")
            return stale_data or []
        data = _validate_recent_fixtures(payload.get("response", []), team_id)
        if data:
            set_db_cache(cache_key, data)
            return data
        print(f"⚠️ 완료된 최근 경기가 없음({team_id}) - 마지막 정상 기록을 사용합니다.")
        return stale_data or []
    except Exception as e:
        print(f"⚠️ 최근 경기 조회 오류({team_id}): {e}")
        return stale_data or []


def fetch_team_form_api(team_id, ttl_h):
    if not team_id: return ""
    cache_key = f"form_v4_{team_id}"
    cached_data = get_db_cache(cache_key, ttl_h)
    if cached_data is not None: return cached_data
    try:
        data = fetch_team_recent_fixtures_api(team_id, ttl_h)[-5:]
        form_list = []
        for m in data:
            home_id = m["teams"]["home"]["id"]
            home_win = m["teams"]["home"]["winner"]
            away_win = m["teams"]["away"]["winner"]
            if home_id == team_id:
                if home_win is True: form_list.append("승")
                elif home_win is False and away_win is True: form_list.append("패")
                else: form_list.append("무")
            else:
                if away_win is True: form_list.append("승")
                elif away_win is False and home_win is True: form_list.append("패")
                else: form_list.append("무")
        res = "-".join(form_list) if form_list else ""
        # 빈 문자열은 장기 저장하지 않아 다음 수집 주기에 다시 시도한다.
        if res:
            set_db_cache(cache_key, res)
        return res
    except Exception as e:
        print(f"⚠️ 최근 전적 조회 오류({team_id}): {e}")
        return ""

def fetch_team_long_term_stats_api(team_id, ttl_h):
    default_res = {"home_wins": 0, "home_total": 0, "home_gf": 0, "home_ga": 0, "away_wins": 0, "away_total": 0, "away_gf": 0, "away_ga": 0}
    if not team_id: return default_res
    cache_key = f"stats_v4_{team_id}"
    cached_data = get_db_cache(cache_key, ttl_h)
    if cached_data: return cached_data
    try:
        data = fetch_team_recent_fixtures_api(team_id, ttl_h)
        for m in data:
            home_id = m["teams"]["home"]["id"]
            away_id = m["teams"]["away"]["id"]
            winner_home = m["teams"]["home"]["winner"]
            winner_away = m["teams"]["away"]["winner"]
            goals_h = m.get("goals", {}).get("home")
            goals_a = m.get("goals", {}).get("away")
            gh = int(goals_h) if goals_h is not None else 0
            ga = int(goals_a) if goals_a is not None else 0
            if home_id == team_id:
                default_res["home_total"] += 1
                default_res["home_gf"] += gh
                default_res["home_ga"] += ga
                if winner_home is True: default_res["home_wins"] += 1
            elif away_id == team_id:
                default_res["away_total"] += 1
                default_res["away_gf"] += ga
                default_res["away_ga"] += gh
                if winner_away is True: default_res["away_wins"] += 1
        set_db_cache(cache_key, default_res)
        return default_res
    except: return default_res

def fetch_team_standing_api(team_id, ttl_h):
    default_res = {
        "rank": 99, "points": 0, "played": 0, "league_id": None,
        "season": None, "total_teams": 0, "team_goals": 0,
        "description": "", "relegation_start_rank": None,
        "safety_rank": None, "safety_points": None,
        "relegation_cut_points": None, "points_to_safety": None,
        "points_above_zone": None, "relegation_zone_source": "none",
    }
    if not team_id: return default_res
    cache_key = f"standing_v5_survival_{team_id}"
    cached_data = get_db_cache(cache_key, ttl_h)
    if cached_data: return cached_data
    try:
        year = datetime.now().year
        res = api_get("/standings", params={"team": team_id, "season": year}, timeout=5)
        data = res.json().get("response", [])
        if not data:
            res = api_get("/standings", params={"team": team_id, "season": year-1}, timeout=5)
            data = res.json().get("response", [])
        if data:
            for league_data in data:
                league_id = league_data.get("league", {}).get("id")
                season = league_data.get("league", {}).get("season")
                standings_list = league_data.get("league", {}).get("standings", [])
                for group in standings_list:
                    group = [row for row in (group or []) if isinstance(row, dict)]
                    rank_rows = {}
                    relegation_ranks = []
                    for table_row in group:
                        try:
                            table_rank = int(table_row.get("rank") or 0)
                            table_points = int(table_row.get("points") or 0)
                        except (TypeError, ValueError):
                            continue
                        if table_rank <= 0:
                            continue
                        rank_rows[table_rank] = table_points
                        description = str(table_row.get("description") or "").casefold()
                        if any(keyword in description for keyword in (
                            "relegat", "descenso", "rebaixamento", "abstieg",
                            "retrocessione", "降格", "강등",
                        )):
                            relegation_ranks.append(table_rank)
                    for s in group:
                        if int((s.get("team", {}) or {}).get("id") or 0) == int(team_id):
                            played = s.get("all", {}).get("played", 0)
                            team_goals = s.get("all", {}).get("goals", {}).get("for", 0)
                            rank = int(s.get("rank") or 99)
                            points = int(s.get("points") or 0)
                            total_teams = len(rank_rows)
                            if relegation_ranks:
                                relegation_start_rank = min(relegation_ranks)
                                zone_source = "official_description"
                            elif total_teams >= 18:
                                relegation_start_rank = total_teams - 2
                                zone_source = "league_size_fallback"
                            elif total_teams >= 8:
                                relegation_start_rank = total_teams - 1
                                zone_source = "league_size_fallback"
                            else:
                                relegation_start_rank = None
                                zone_source = "none"
                            safety_rank = (
                                relegation_start_rank - 1
                                if relegation_start_rank and relegation_start_rank > 1
                                else None
                            )
                            safety_points = rank_rows.get(safety_rank)
                            relegation_cut_points = rank_rows.get(relegation_start_rank)
                            points_to_safety = (
                                max(0, int(safety_points) - points)
                                if safety_points is not None and rank >= relegation_start_rank
                                else None
                            )
                            points_above_zone = (
                                max(0, points - int(relegation_cut_points))
                                if relegation_cut_points is not None and rank < relegation_start_rank
                                else None
                            )
                            res_val = {
                                "rank": rank, "points": points, "played": played,
                                "league_id": league_id, "season": season,
                                "total_teams": total_teams, "team_goals": team_goals,
                                "description": str(s.get("description") or ""),
                                "relegation_start_rank": relegation_start_rank,
                                "safety_rank": safety_rank,
                                "safety_points": safety_points,
                                "relegation_cut_points": relegation_cut_points,
                                "points_to_safety": points_to_safety,
                                "points_above_zone": points_above_zone,
                                "relegation_zone_source": zone_source,
                            }
                            set_db_cache(cache_key, res_val)
                            return res_val
    except: pass
    set_db_cache(cache_key, default_res)
    return default_res

def fetch_league_key_players(league_id, season):
    if not league_id or not season: return {}
    cache_key = f"keyplayers_v4_{league_id}_{season}"
    cached_data = get_db_cache(cache_key, 168)
    if cached_data: return cached_data
    
    key_players = {}
    try:
        res_s = api_get("/players/topscorers", params={"league": league_id, "season": season}, timeout=5)
        data_s = res_s.json().get("response", [])
        for p in data_s:
            name = p.get("player", {}).get("name", "")
            stats = (p.get("statistics") or [{}])[0]
            goals = stats.get("goals", {}).get("total", 0) or 0
            team_id = stats.get("team", {}).get("id")
            if name and goals:
                key_players[name] = {"goals": goals, "assists": 0, "team_id": team_id}
            
        res_a = api_get("/players/topassists", params={"league": league_id, "season": season}, timeout=5)
        data_a = res_a.json().get("response", [])
        for p in data_a:
            name = p.get("player", {}).get("name", "")
            stats = (p.get("statistics") or [{}])[0]
            assists = stats.get("goals", {}).get("assists", 0) or 0
            team_id = stats.get("team", {}).get("id")
            if not name:
                continue
            if name not in key_players:
                key_players[name] = {"goals": 0, "assists": assists, "team_id": team_id}
            else:
                key_players[name]["assists"] = assists
                key_players[name]["team_id"] = key_players[name].get("team_id") or team_id
            
        set_db_cache(cache_key, key_players)
        return key_players
    except: return {}

def fetch_team_injuries_api(team_id, league_id, season, ttl_h, fixture_id=0):
    default_res = {"count": 0, "ace_missing": False, "ace_names": [], "missing_goals": 0, "available": False, "source": "none"}
    if not team_id: return default_res
    cache_key = f"inj_v5_{team_id}_{league_id}_{season}_{int(fixture_id or 0)}"
    cached_data = get_db_cache(cache_key, ttl_h)
    if cached_data is not None: return cached_data
    # 당일 API가 소진돼도 직전 정상 부상자 자료를 버리지 않는다.
    stale_data = get_db_cache(cache_key, max(72, ttl_h))

    def stale_or_default():
        if isinstance(stale_data, dict):
            preserved = dict(stale_data)
            preserved["available"] = True
            preserved["source"] = "stale_cache"
            return preserved
        return default_res

    try:
        if fixture_id:
            params = {"fixture": int(fixture_id)}
            source = "target_fixture"
        elif league_id and season:
            params = {"team": int(team_id), "league": int(league_id), "season": int(season)}
            source = "team_season"
        else:
            return default_res

        inj_res = api_get("/injuries", params=params, timeout=8)
        payload = inj_res.json() if inj_res.status_code == 200 else {}
        if inj_res.status_code != 200 or payload.get("errors"):
            return stale_or_default()
        inj_data = payload.get("response", [])
        injured_names = sorted({
            x.get("player", {}).get("name", "").strip()
            for x in inj_data
            if x.get("team", {}).get("id") == team_id and x.get("player", {}).get("name")
        })
        count = len(injured_names)
        ace_names = []
        missing_goals_total = 0
        key_players = fetch_league_key_players(league_id, season) if league_id and season else {}

        for name in injured_names:
            normalized = _normalize_player_name(name)
            matched = None
            for kp_name, kp_stats in key_players.items():
                if kp_stats.get("team_id") not in (None, team_id):
                    continue
                candidate = _normalize_player_name(kp_name)
                if normalized == candidate or (len(normalized) >= 6 and (normalized in candidate or candidate in normalized)):
                    matched = kp_stats
                    break
            protected = find_protected_star(name)
            if matched or protected:
                ace_names.append(name)
                if matched:
                    missing_goals_total += int(matched.get("goals", 0) or 0)

        res_val = {
            "count": count,
            "ace_missing": bool(ace_names),
            "ace_names": sorted(set(ace_names)),
            "missing_goals": missing_goals_total,
            "available": True,
            "source": source,
        }
        set_db_cache(cache_key, res_val)
        return res_val
    except Exception as error:
        print(f"⚠️ 부상자 조회 오류({team_id}): {error}")
        return stale_or_default()

def fetch_new_manager_status(team_id, ttl_h):
    default_res = {"is_new_manager": False, "days_since_hired": 999}
    if not team_id: return default_res
    cache_key = f"coach_v4_{team_id}"
    cached_data = get_db_cache(cache_key, ttl_h)
    if cached_data: return cached_data
    try:
        res = api_get("/coachs", params={"team": team_id}, timeout=5)
        data = res.json().get("response", [])
        if data:
            for coach in data:
                career = coach.get("career", [])
                for stint in career:
                    if stint.get("team", {}).get("id") == team_id and stint.get("end") is None:
                        start_date = stint.get("start")
                        if start_date:
                            start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                            days_since = (datetime.now(timezone.utc) - start_dt).days
                            is_new = days_since <= 21 
                            res_val = {"is_new_manager": is_new, "days_since_hired": days_since}
                            set_db_cache(cache_key, res_val)
                            return res_val
        return default_res
    except: return default_res

KNOWN_DERBIES = [
    {"Manchester United", "Manchester City"}, {"Tottenham", "Arsenal"}, 
    {"Real Madrid", "Barcelona"}, {"Atletico Madrid", "Real Madrid"},
    {"AC Milan", "Inter"}, {"Lazio", "Roma"}, {"Juventus", "Torino"},
    {"Liverpool", "Everton"}, {"FC Seoul", "Suwon Samsung"}, 
    {"Ulsan Hyundai", "Pohang Steelers"}, {"Bayern Munich", "Borussia Dortmund"}, 
    {"Paris Saint Germain", "Marseille"}
]

def check_derby_match(home_name, away_name):
    h_name = MANUAL_TEAM_MAP.get(home_name, TEAM_NAME_MAP.get(home_name, home_name))
    a_name = MANUAL_TEAM_MAP.get(away_name, TEAM_NAME_MAP.get(away_name, away_name))
    for derby in KNOWN_DERBIES:
        if h_name in derby and a_name in derby:
            return True
    return False

def fetch_lineups_api(fixture_id, ttl_h):
    default_res = {"home": [], "away": [], "confirmed": False}
    if not fixture_id: return default_res
    cache_key = f"lineups_v1_{fixture_id}"
    cached_data = get_db_cache(cache_key, ttl_h)
    if cached_data: return cached_data
    try:
        res = api_get("/fixtures/lineups", params={"fixture": fixture_id}, timeout=5)
        data = res.json().get("response", [])
        res_val = {"home": [], "away": [], "confirmed": False}
        if data and len(data) == 2:
            for t in data:
                t_id = t["team"]["id"]
                starters = [x["player"]["name"] for x in t.get("startXI", [])]
                res_val[str(t_id)] = starters
            res_val["confirmed"] = all(len(res_val.get(str(t.get("team", {}).get("id")), [])) >= 11 for t in data)
        # 발표 전 빈 명단은 저장하지 않아 다음 5분 주기에 다시 확인한다.
        if res_val["confirmed"]:
            set_db_cache(cache_key, res_val)
        return res_val
    except: pass
    return default_res

def fetch_team_last_match_date_api(team_id, ttl_h):
    default_res = {"date": None, "is_extreme_fatigue": False}
    if not team_id: return default_res
    cache_key = f"last_match_v5_{team_id}"
    cached_data = get_db_cache(cache_key, ttl_h)
    if cached_data: return cached_data
    try:
        data = fetch_team_recent_fixtures_api(team_id, ttl_h)[-1:]
        if data:
            match = data[0]
            date_val = match["fixture"]["date"]
            league_name = match["league"]["name"]
            is_away = match["teams"]["away"]["id"] == team_id
            continental_kws = ["Champions League", "Europa", "Conference", "Libertadores", "Sudamericana", "AFC", "World Cup"]
            is_continental = any(kw.lower() in league_name.lower() for kw in continental_kws)
            res_val = {"date": date_val, "is_extreme_fatigue": is_away and is_continental}
            set_db_cache(cache_key, res_val)
            return res_val
    except: pass
    return default_res

def fetch_team_next_fixture_api(team_id, ttl_h):
    default_res = {"days_until_next": 99, "is_important": False, "league_name": ""}
    if not team_id: return default_res
    cache_key = f"next_fix_v5_{team_id}"
    cached_data = get_db_cache(cache_key, ttl_h)
    if cached_data: return cached_data
    try:
        current_season = datetime.now().year
        res = api_get("/fixtures", params={"team": team_id, "season": current_season, "next": 3}, timeout=5)
        data = res.json().get("response", [])
        for next_fix in data:
            next_date_str = next_fix["fixture"]["date"]
            league_name = next_fix["league"]["name"]
            next_dt = datetime.fromisoformat(next_date_str.replace('Z', '+00:00'))
            now = datetime.now(timezone(timedelta(hours=9)))
            diff_hours = (next_dt - now).total_seconds() / 3600.0
            if diff_hours > 40:
                days_until = max(1, int(diff_hours / 24))
                important_keywords = ["Champions League", "Europa", "Cup", "Copa", "Sudamericana", "Libertadores", "AFC", "FA Cup"]
                is_important = any(kw.lower() in league_name.lower() for kw in important_keywords)
                res_val = {"days_until_next": days_until, "is_important": is_important, "league_name": league_name}
                set_db_cache(cache_key, res_val)
                return res_val
    except: pass
    return default_res

def fetch_recent_team_stats_api(team_id, ttl_h):
    default_res = {
        "possession": 50, "shots_on_goal": 4.0, "corners": 4.5,
        "yellow_cards": 1.5, "sample_size": 0, "xg": None,
        "xg_sample_size": 0,
    }
    if not team_id: return default_res
    cache_key = f"recent_stats_v2_{team_id}"
    cached_data = get_db_cache(cache_key, ttl_h)
    if cached_data: return cached_data
    try:
        fixtures = fetch_team_recent_fixtures_api(team_id, ttl_h)[-2:]
        total_possession, total_sog, total_corn, total_yc = 0, 0, 0, 0
        total_xg, xg_matches = 0.0, 0
        valid_matches = 0
        for f in fixtures:
            fix_id = f["fixture"]["id"]
            stat_res = api_get("/fixtures/statistics", params={"fixture": fix_id}, timeout=5)
            stats_data = stat_res.json().get("response", [])
            for team_stat in stats_data:
                if team_stat["team"]["id"] == team_id:
                    pos_val, sog_val, corn_val, yc_val = 50, 4.0, 4.5, 1.5
                    xg_val = None
                    for s in team_stat["statistics"]:
                        if s["type"] == "Ball Possession" and s["value"]: pos_val = int(str(s["value"]).replace('%', ''))
                        if s["type"] == "Shots on Goal" and s["value"]: sog_val = float(s["value"])
                        if s["type"] == "Corner Kicks" and s["value"]: corn_val = float(s["value"])
                        if s["type"] == "Yellow Cards" and s["value"]: yc_val = float(s["value"])
                        if str(s.get("type", "")).casefold() in {"expected goals", "expected_goals", "xg"} and s.get("value") not in (None, ""):
                            try:
                                xg_val = float(s["value"])
                            except (TypeError, ValueError):
                                xg_val = None
                    total_possession += pos_val
                    total_sog += sog_val
                    total_corn += corn_val
                    total_yc += yc_val
                    if xg_val is not None:
                        total_xg += xg_val
                        xg_matches += 1
                    valid_matches += 1
                    break
        if valid_matches > 0:
            res_val = {
                "possession": round(total_possession / valid_matches, 1),
                "shots_on_goal": round(total_sog / valid_matches, 1),
                "corners": round(total_corn / valid_matches, 1),
                "yellow_cards": round(total_yc / valid_matches, 1),
                "sample_size": valid_matches,
                "xg": round(total_xg / xg_matches, 2) if xg_matches else None,
                "xg_sample_size": xg_matches,
            }
        else: res_val = default_res
        set_db_cache(cache_key, res_val)
        return res_val
    except: pass
    return default_res


def fetch_team_recent_form_metrics(team_id, ttl_h):
    """추가 API 호출 없이 최근 5경기의 득실ㆍ승점을 작은 보정값으로 만든다."""
    default_res = {"matches": 0, "ppg": 1.33, "gf_pg": 1.2, "ga_pg": 1.2, "strength": 0.0}
    if not team_id:
        return default_res
    try:
        fixtures = fetch_team_recent_fixtures_api(team_id, ttl_h)[-5:]
        points = goals_for = goals_against = 0
        for match in fixtures:
            is_home = match.get("teams", {}).get("home", {}).get("id") == team_id
            gh = int(match.get("goals", {}).get("home") or 0)
            ga = int(match.get("goals", {}).get("away") or 0)
            own, opp = (gh, ga) if is_home else (ga, gh)
            goals_for += own
            goals_against += opp
            points += 3 if own > opp else (1 if own == opp else 0)
        count = len(fixtures)
        if not count:
            return default_res
        ppg = points / count
        gf_pg = goals_for / count
        ga_pg = goals_against / count
        strength = max(-0.12, min(0.12, ((ppg - 1.33) * 0.05) + ((gf_pg - ga_pg) * 0.035)))
        return {"matches": count, "ppg": round(ppg, 2), "gf_pg": round(gf_pg, 2), "ga_pg": round(ga_pg, 2), "strength": round(strength, 4)}
    except Exception:
        return default_res

def calculate_rest_days(last_date_iso, match_time_str):
    if not last_date_iso or not match_time_str or match_time_str in ["시간 미정", "마감/진행중"]: return 99
    try:
        last_dt = datetime.fromisoformat(last_date_iso.replace('Z', '+00:00'))
        m_dt = parse_match_time(match_time_str)
        diff = m_dt - last_dt
        return max(0, diff.days)
    except: pass
    return 99

def get_league_averages(league_name):
    name = league_name.lower() if league_name else ""
    if "프리미어" in name or "epl" in name: return 1.60, 1.35
    if "분데스리가" in name: return 1.65, 1.45
    if "에레디비시" in name: return 1.75, 1.45
    if "라리가" in name or "스페인" in name: return 1.45, 1.20
    if "세리에" in name or "이탈리아" in name: return 1.40, 1.15
    if "k1" in name or "k리그1" in name: return 1.35, 1.20 
    if "k2" in name or "k리그2" in name: return 1.30, 1.15
    if "j1" in name or "j리그" in name: return 1.40, 1.25 
    if "메이저리그" in name or "mls" in name: return 1.60, 1.30
    if "챔피언스" in name or "유로파" in name: return 1.55, 1.25
    return 1.50, 1.20

def calculate_poisson_probs(exp_h, exp_a, handi_val=1.0, uo_base=2.5):
    rho = -0.15 
    h_probs = [(math.exp(-exp_h) * (exp_h**i)) / math.factorial(i) for i in range(8)]
    a_probs = [(math.exp(-exp_a) * (exp_a**j)) / math.factorial(j) for j in range(8)]
    matrix = [[0.0 for _ in range(8)] for _ in range(8)]
    total_prob = 0.0
    for h in range(8):
        for a in range(8):
            p = h_probs[h] * a_probs[a]
            if h == 0 and a == 0: p *= max(0, 1 - (exp_h * exp_a * rho))
            elif h == 1 and a == 0: p *= max(0, 1 + (exp_h * rho))
            elif h == 0 and a == 1: p *= max(0, 1 + (exp_a * rho))
            elif h == 1 and a == 1: p *= max(0, 1 - rho)
            matrix[h][a] = p
            total_prob += p
    for h in range(8):
        for a in range(8):
            matrix[h][a] /= total_prob
            
    h_win, draw, a_win, prob_u, prob_o = 0.0, 0.0, 0.0, 0.0, 0.0
    prob_handi_h, prob_handi_d, prob_handi_a = 0.0, 0.0, 0.0
    for h in range(8):
        for a in range(8):
            p = matrix[h][a]
            if h > a: h_win += p
            elif h == a: draw += p
            else: a_win += p
            if (h + a) < uo_base: prob_u += p
            else: prob_o += p
            if (h + handi_val) > a: prob_handi_h += p
            elif (h + handi_val) == a: prob_handi_d += p
            elif (h + handi_val) < a: prob_handi_a += p
            
    return h_win, draw, a_win, prob_u, prob_o, prob_handi_h, prob_handi_d, prob_handi_a


def normalize_probabilities(values):
    cleaned = [max(0.0, float(value or 0.0)) for value in values]
    total = sum(cleaned)
    if total <= 0:
        return [1.0 / len(cleaned)] * len(cleaned)
    return [value / total for value in cleaned]


def _temperature_scale(values, temperature):
    normalized = normalize_probabilities(values)
    powered = [max(value, 1e-9) ** (1.0 / max(1.0, temperature)) for value in normalized]
    return normalize_probabilities(powered)


def _cap_probabilities(values, cap):
    values = normalize_probabilities(values)
    for _ in range(len(values) + 2):
        highest = max(range(len(values)), key=values.__getitem__)
        if values[highest] <= cap + 1e-12:
            break
        excess = values[highest] - cap
        values[highest] = cap
        other_total = sum(values[index] for index in range(len(values)) if index != highest)
        for index in range(len(values)):
            if index != highest:
                values[index] += excess * (values[index] / other_total if other_total else 1.0 / (len(values) - 1))
    return normalize_probabilities(values)


def calibrate_three_way_probabilities(model_probs, odds, confidence):
    """모델 과신을 낮추고 실제 배당의 마진 제거 확률을 일부 혼합한다."""
    confidence = max(0.35, min(0.95, float(confidence or 0.35)))
    temperature = 1.55 - (0.45 * confidence)
    model = _temperature_scale(model_probs, temperature)
    valid_odds = len(odds) == 3 and all(float(odd or 0) > 1.0 for odd in odds)
    if valid_odds:
        market = normalize_probabilities([1.0 / float(odd) for odd in odds])
        market_weight = 0.45 - (0.18 * confidence)
        model = [(1.0 - market_weight) * m + market_weight * q for m, q in zip(model, market)]
    return _cap_probabilities(model, 0.80)


def calibrate_two_way_probabilities(model_probs, odds, confidence):
    confidence = max(0.35, min(0.95, float(confidence or 0.35)))
    model = _temperature_scale(model_probs, 1.45 - (0.35 * confidence))
    valid_odds = len(odds) == 2 and all(float(odd or 0) > 1.0 for odd in odds)
    if valid_odds:
        market = normalize_probabilities([1.0 / float(odd) for odd in odds])
        market_weight = 0.42 - (0.15 * confidence)
        model = [(1.0 - market_weight) * m + market_weight * q for m, q in zip(model, market)]
    return _cap_probabilities(model, 0.82)

def generate_match_story(best_prob_pick, best_ev_pick, math_exp_h, math_exp_a, prob_h, prob_d, prob_a, h2h_h, h2h_a, home, away, odd_h, odd_a, h_form, a_form, h_long, a_long, h_inj_data, a_inj_data, h_rest, a_rest, h_next, a_next, h_rank, a_rank, h_market, a_market, h_stats, a_stats, referee, weather, h_extreme, a_extreme, h_lineup_msg, a_lineup_msg):
    story_parts = []
    story_parts.append(f"📈 [딕슨-콜스 모델] 양 팀의 공격/수비 지수를 환산한 결과, 예상 정규시간 득점은 {home} <b style='color:#00F2FE;'>{math_exp_h:.1f}골</b>, {away} <b style='color:#EF4444;'>{math_exp_a:.1f}골</b>로 산출되었습니다.")
    if referee and any(sr.lower() in referee.lower() for sr in STRICT_REFEREES): 
        story_parts.append(f"🟨 [카드 캡처 주의] 악명 높은 엄격한 성향의 주심({referee})이 배정되었습니다. 예기치 않은 변수에 주의하세요.")
    if weather in ["Rain", "Snow"]:
        w_ko = "폭우" if weather == "Rain" else "폭설"
        story_parts.append(f"⛈️ [기상 악화 특보] 경기장 현지에 {w_ko}가 예보되어 늪축구 양상(언더)이 예상됩니다.")
    if h_extreme: story_parts.append(f"✈️ {home}은(는) 대륙간 장거리 원정으로 극심한 피로 누적이 우려됩니다.")
    if a_extreme: story_parts.append(f"✈️ 원정팀 {away}은(는) 대륙간 장거리 원정 직후라 체력 방전이 뚜렷합니다.")
     
    if h_next["is_important"] and h_next["days_until_next"] <= 4: story_parts.append(f"⚠️ [조기 경보] {home}은(는) {h_next['days_until_next']}일 뒤 챔스/컵 일정이 겹쳐 있어, 정상 전력 가동이 불투명합니다. (배팅 시 로테이션 주의!)")
    if a_next["is_important"] and a_next["days_until_next"] <= 4: story_parts.append(f"⚠️ [조기 경보] {away} 측은 {a_next['days_until_next']}일 뒤 중요 일정 탓에 선발 명단 변동 확률이 매우 높습니다.")
     
    if h_market > 0: story_parts.append(f"💸 [마켓 알럿] 글로벌 도박사들의 자금이 {home} 쪽으로 집중되며 정배 흐름이 강해지고 있습니다.")
    elif a_market > 0: story_parts.append(f"💸 [마켓 알럿] 해외 시장에서 {away} 승리(역배)에 스마트머니가 쏠리며 가치가 급상승 중입니다!")
     
    if h_lineup_msg: story_parts.append(h_lineup_msg)
    elif h_inj_data['ace_missing']: story_parts.append(f"🚨 [선제 타격] {home} 핵심 에이스({', '.join(h_inj_data['ace_names'])}) 결장 의심! 전력 누수가 치명적입니다.")
    if a_lineup_msg: story_parts.append(a_lineup_msg)
    elif a_inj_data['ace_missing']: story_parts.append(f"🚨 [선제 타격] 원정팀 {away} 핵심 자원({', '.join(a_inj_data['ace_names'])}) 결장 의심으로 공격 전 창의성이 떨어질 전망입니다.")
     
    if best_prob_pick == best_ev_pick:
        story_parts.append(f"🤖 현재 확보된 데이터에서 확률과 배당 가치가 함께 우세한 후보는 **[{best_prob_pick}]**입니다. 경기 직전 선발 변동에 따라 최종 판단이 달라질 수 있습니다.")
    else:
        story_parts.append(f"🤖 현재 모델의 **상대적으로 안정적인 후보는 [{best_prob_pick}]**이며, 배당 대비 확률 차이가 있는 **가치 후보는 [{best_ev_pick}]**입니다. 두 항목 모두 적중을 보장하지 않습니다.")
         
    return " ".join(story_parts)

def evaluate_single_pick(pick_str, h_team, a_team, goals_h, goals_a):
    pick_str = str(pick_str or "").upper()
    h_team = str(h_team or "").upper()
    a_team = str(a_team or "").upper()
    picks = [p.strip() for p in pick_str.split(",")]
    
    for pick in picks:
        if "핸디" in pick or "적용 후" in pick:
            # 핸디캡은 팀명이 들어 있는지만 보면 안 된다. 예를 들어
            # "[+1.0] 파주 핸디패"는 홈팀 이름을 포함하지만, +1 적용 뒤
            # 홈팀이 앞서면 명백한 미적중이다. 문구에 선언된 승/무/패와
            # 실제 조정 점수를 직접 비교한다.
            m_handi = re.search(r'\[\s*([+-]?\d+(?:\.\d+)?)\s*\]', pick)
            if not m_handi:
                m_handi = re.search(
                    r'([+-]?\d+(?:\.\d+)?)\s*(?:적용\s*후|HANDICAP)', pick
                )
            if m_handi:
                h_base = float(m_handi.group(1))
                adjusted_home = float(goals_h) + h_base
                actual = (
                    "승" if adjusted_home > float(goals_a)
                    else "패" if adjusted_home < float(goals_a)
                    else "무"
                )
                expected_match = re.search(
                    r'(?:핸디|적용\s*후)\s*(승|무|패)', pick
                )
                if expected_match and expected_match.group(1) == actual:
                    return 1
            continue

        if ("무승부" in pick or pick == "무" or "DRAW" in pick):
            if goals_h == goals_a: return 1
        if "승" in pick or "WIN" in pick:
            if a_team and a_team in pick:
                if goals_h < goals_a: return 1
            elif goals_h > goals_a and (not h_team or h_team in pick or pick == "승"):
                return 1
        if pick == "패" and goals_h < goals_a:
            return 1
                
        if "언더" in pick or "오버" in pick:
            m_uo = re.search(r'(\d+(?:\.\d+)?)', pick)
            if m_uo:
                uo_base = float(m_uo.group(1))
                if "언더" in pick and (goals_h + goals_a) < uo_base: return 1
                if "오버" in pick and (goals_h + goals_a) > uo_base: return 1
                
    return 0

def generate_real_ai_note(
    fixture_id, goals_h, goals_a, is_correct_prob, is_correct_ev, has_ev_pick=True,
    home_team="", away_team="", prob_pick="", ev_pick="", event_timeline=None,
    return_postmortem=False,
):
    """Record verified score, official facts, and deterministic miss reasons."""
    normalized_stats = []
    try:
        stat_res = api_get("/fixtures/statistics", params={"fixture": fixture_id}, timeout=5, purpose="scoring")
        raw_stats = stat_res.json().get("response", []) if stat_res.status_code == 200 else []
        normalized_stats = normalize_official_stats(raw_stats)
    except Exception:
        normalized_stats = []

    result_parts = [
        f"최종 추천픽 {'적중' if is_correct_prob == 1 else '미적중'}",
        (
            f"기존 배당형 대안픽 {'적중' if is_correct_ev == 1 else '미적중'}"
            if has_ev_pick else "공식 예측은 최종 추천픽 1개"
        ),
    ]
    payload = build_postmortem(
        home_team=home_team,
        away_team=away_team,
        prob_pick=prob_pick,
        ev_pick=ev_pick,
        goals_h=int(goals_h),
        goals_a=int(goals_a),
        is_correct_prob=is_correct_prob,
        is_correct_ev=is_correct_ev,
        has_ev_pick=has_ev_pick,
        official_stats=normalized_stats,
        event_timeline=event_timeline or [],
    )
    note_parts = [
        f"[채점 결과] {' · '.join(result_parts)}.",
        f"[최종 점수] {int(goals_h)}:{int(goals_a)}.",
    ]
    stats_text = official_stats_text(normalized_stats)
    if stats_text:
        note_parts.append(f"[공식 경기 통계] {stats_text}.")
    review_text = postmortem_text(payload)
    if review_text:
        note_parts.append(review_text)
    note = "\n\n".join(note_parts)
    encoded = postmortem_json(payload)
    return (note, encoded) if return_postmortem else note
