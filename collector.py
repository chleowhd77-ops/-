import argparse
import base64
import hashlib
import json
import math
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests
import schedule
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

try:
    from webdriver_manager.chrome import ChromeDriverManager
except Exception:
    ChromeDriverManager = None

try:
    from webdriver_manager.microsoft import EdgeChromiumDriverManager
except Exception:
    EdgeChromiumDriverManager = None

from config import *
from api_engine import *
from api_engine import _fetch_date_fixtures_api, _normalize_player_name
from grading_postmortem import (
    build_postmortem,
    events_from_note,
    postmortem_json,
    postmortem_text,
    stats_from_note,
)


APP_DIR = Path(__file__).resolve().parent
STATUS_FILE = APP_DIR / "collector_status.json"
WORLD_DASHBOARD_FILE = APP_DIR / "world_dashboard.json"
KST = timezone(timedelta(hours=9))
UNDERDOG_GATE_VERSION = "U3-alternative-pick-20260902"
PICK_AUDIT_SCHEMA_VERSION = "pick-audit.v1"
# 종료 상태는 화면 표시용이 아니라 중복 추적 방지용 내부 캐시로만 잠시 보존합니다.
LIVE_RETENTION_HOURS = max(1, int(os.getenv("LIVE_RETENTION_HOURS", "2")))
LIVE_LOOKAROUND_HOURS = max(2, int(os.getenv("LIVE_LOOKAROUND_HOURS", "6")))
SQUAD_CACHE_TTL_HOURS = max(24, min(720, int(os.getenv("SQUAD_CACHE_TTL_HOURS", "168"))))
FIXTURE_IDENTITY_CACHE_HOURS = max(
    1, min(24, int(os.getenv("FIXTURE_IDENTITY_CACHE_HOURS", "6")))
)
FIXTURE_IDENTITY_RETRY_HOURS = max(
    1, min(6, int(os.getenv("FIXTURE_IDENTITY_RETRY_HOURS", "1")))
)
PROTO_MIN_SCRAPE_ROWS = max(1, int(os.getenv("PROTO_MIN_SCRAPE_ROWS", "3")))
# 승무패 14는 소액 참고 조합으로 운영한다. 서버 환경변수에 예전 64가
# 남아 있어도 8조합(8,000원)을 넘지 않도록 상한을 강제한다.
TOTO14_MAX_COMBINATIONS = max(1, min(8, int(os.getenv("TOTO14_MAX_COMBINATIONS", "8"))))
TOTO14_UNIT_PRICE = max(100, int(os.getenv("TOTO14_UNIT_PRICE", "1000")))
LIVE_STATUSES = {'1H', 'HT', '2H', 'ET', 'BT', 'P', 'SUSP', 'INT', 'LIVE'}
TERMINAL_STATUSES = {'FT', 'AET', 'PEN'}
CANCELED_STATUSES = {'CANC', 'ABD', 'AWD', 'WO'}
POSTPONED_STATUSES = {'PST', 'PSTP'}

# 이 목록은 번역·리그 평균값을 위한 우선 리그 표이지 분석 허용 목록이 아니다.
# 전체경기 대상은 아래 표에 없는 리그라도 실제 정규시간 배당이 있으면 포함한다.
WORLD_LEAGUES = {
    39: {"name": "잉글랜드 프리미어리그", "tier": 1},
    140: {"name": "스페인 라리가", "tier": 1},
    135: {"name": "이탈리아 세리에 A", "tier": 1},
    78: {"name": "독일 분데스리가", "tier": 1},
    61: {"name": "프랑스 리그 1", "tier": 1},
    88: {"name": "네덜란드 에레디비시", "tier": 1},
    94: {"name": "포르투갈 프리메이라리가", "tier": 1},
    292: {"name": "대한민국 K리그1", "tier": 1},
    98: {"name": "일본 J1리그", "tier": 1},
    253: {"name": "미국 MLS", "tier": 1},
    71: {"name": "브라질 세리에 A", "tier": 1},
}
WORLD_SCHEDULE_DAYS = max(1, min(3, int(os.getenv("WORLD_SCHEDULE_DAYS", "2"))))
WORLD_MAX_SCHEDULE_MATCHES = max(
    20, min(300, int(os.getenv("WORLD_MAX_SCHEDULE_MATCHES", "160")))
)
WORLD_MAX_DEEP_ANALYSES_DAILY = max(
    1, min(30, int(os.getenv("WORLD_MAX_DEEP_ANALYSES_DAILY", "30")))
)
WORLD_MAX_DEEP_ANALYSES_PER_LEAGUE = max(
    1, min(5, int(os.getenv("WORLD_MAX_DEEP_ANALYSES_PER_LEAGUE", "5")))
)
WORLD_ANALYSIS_HORIZON_HOURS = max(
    3, min(36, int(os.getenv("WORLD_ANALYSIS_HORIZON_HOURS", "24")))
)
WORLD_SCHEDULE_REFRESH_HOURS = max(
    2, min(12, int(os.getenv("WORLD_SCHEDULE_REFRESH_HOURS", "6")))
)
WORLD_ANALYSIS_INTERVAL_MINUTES = max(
    10, min(60, int(os.getenv("WORLD_ANALYSIS_INTERVAL_MINUTES", "15")))
)
WORLD_ODDS_MAX_PAGES_PER_DAY = max(
    10, min(100, int(os.getenv("WORLD_ODDS_MAX_PAGES_PER_DAY", "100")))
)
# PROTO의 현행 분석 버전은 그대로 둔다. WORLD가 기존 정밀 입력 세트를
# 빠짐없이 사용하도록 맞춘 변경만 별도 모델 표식으로 남긴다.
WORLD_ANALYSIS_VERSION = f"{ANALYSIS_VERSION}-world-full-context-v2"
WORLD_TEAM_NAME_KO_OVERRIDES = {
    "Lyon": "올랭피크 리옹",
    "Auxerre": "AJ오세르",
    "Sparta Rotterdam": "스파르타 로테르담",
    "PEC Zwolle": "PEC즈볼러",
    "VfB Stuttgart": "VfB 슈투트가르트",
    "1. FC Köln": "쾰른",
    "FC Koln": "쾰른",
}
WORLD_REJECTION_LABELS = {
    "INVALID_LEAGUE_ID": "공식 리그 ID 없음",
    "NO_VALID_MARKET": "사설 배팅용 정규시간 배당 없음",
    "INVALID_FIXTURE_ID": "공식 경기 ID 없음",
    "INVALID_TEAM_ID": "홈·원정 팀 ID 확인 실패",
    "DUPLICATE_TEAM_ID": "홈·원정 팀 ID가 동일함",
    "TEAM_TBD": "출전 팀 미정",
    "KICKOFF_TBD": "킥오프 시간 미정",
    "UNSUPPORTED_STATUS": "분석 전 일정 상태가 아님",
    "ALREADY_STARTED": "수집 전에 이미 시작 또는 종료",
    "CANCELED_OR_POSTPONED": "취소·연기·중단 경기",
    "DUPLICATE_FIXTURE": "중복 경기 ID",
    "SCHEDULE_CAP": "세계 일정 안전 상한 초과",
}


def _world_team_display_name(api_name, team_id=0):
    """Return a verified Korean-first label without changing API identity."""
    api_name = str(api_name or "").strip()
    team_id = int(team_id or 0)
    if api_name in WORLD_TEAM_NAME_KO_OVERRIDES:
        return WORLD_TEAM_NAME_KO_OVERRIDES[api_name]

    def normalized(value):
        return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())

    def has_hangul(value):
        return bool(re.search(r"[가-힣]", str(value or "")))

    target = normalized(api_name)
    candidates = []
    for korean_name, english_name in {**TEAM_NAME_MAP, **MANUAL_TEAM_MAP}.items():
        if target and normalized(english_name) == target and has_hangul(korean_name):
            candidates.append((1, str(korean_name).strip()))
    if team_id > 0:
        for korean_name, info in DIRECT_TEAM_INFO.items():
            if (
                isinstance(info, dict)
                and int(info.get("id") or 0) == team_id
                and has_hangul(korean_name)
            ):
                candidates.append((2, str(korean_name).strip()))
    if not candidates:
        return api_name
    # 검증된 ID 이름을 우선하고, 축약명보다 설명력이 높은 전체 이름을 쓴다.
    return max(candidates, key=lambda row: (row[0], len(row[1])))[1]


def _refresh_world_source_meta(payload):
    """Rebuild counters from matches so schedule refresh cannot reset them."""
    if not isinstance(payload, dict):
        return payload
    matches = [item for item in payload.get("matches", []) or [] if isinstance(item, dict)]
    eligible = [
        item for item in matches
        if str(item.get("visibility_status") or "SHADOW").upper() != "QUARANTINED"
    ]
    analyzed = [
        item for item in eligible
        if item.get("analysis")
        or str(item.get("analysis_status") or "").upper() in {
            "ANALYZED_SHADOW", "FROZEN_SHADOW"
        }
    ]
    source_meta = payload.setdefault("source_meta", {})
    source_meta.update({
        "eligible_shadow_count": len(eligible),
        "analyzed_shadow_count": len(analyzed),
        "frozen_shadow_count": sum(
            1 for item in eligible
            if item.get("frozen_at")
            or str(item.get("analysis_status") or "").upper() == "FROZEN_SHADOW"
        ),
        "analysis_error_count": sum(
            1 for item in eligible
            if str(item.get("analysis_status") or "").upper() == "ANALYSIS_ERROR"
        ),
        "public_count": sum(
            1 for item in eligible
            if str(item.get("visibility_status") or "").upper() == "PUBLIC"
        ),
        "analysis_started": bool(analyzed),
        "analysis_version": WORLD_ANALYSIS_VERSION,
        "system_version": SYSTEM_VERSION,
    })
    return payload


def _local_path(path):
    path = Path(path)
    return path if path.is_absolute() else APP_DIR / path


def _read_json(path, default=None):
    try:
        with open(_local_path(path), "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return {} if default is None else default


def _atomic_write_bytes(path, payload):
    """Write in the destination directory and replace only after fsync succeeds."""
    final_path = _local_path(path)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = final_path.with_name(
        f".{final_path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )
    try:
        with open(temp_path, "wb") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, final_path)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass


def _atomic_write_json(path, value, indent=None):
    payload = json.dumps(value, ensure_ascii=False, indent=indent).encode("utf-8")
    _atomic_write_bytes(path, payload)


def _pid_is_running(pid):
    try:
        pid = int(pid)
        if pid <= 0:
            return False
        if os.name == "nt":
            # On Windows os.kill(pid, 0) calls TerminateProcess rather than the
            # POSIX-style existence probe.  Query the handle without mutating it.
            import ctypes
            from ctypes import wintypes

            process_query_limited_information = 0x1000
            still_active = 259
            kernel32 = ctypes.windll.kernel32
            kernel32.OpenProcess.argtypes = (
                wintypes.DWORD, wintypes.BOOL, wintypes.DWORD
            )
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetExitCodeProcess.argtypes = (
                wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)
            )
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.OpenProcess(
                process_query_limited_information, False, pid
            )
            if not handle:
                # Access denied still proves that the process exists.
                return int(kernel32.GetLastError()) == 5
            try:
                exit_code = wintypes.DWORD()
                queried = kernel32.GetExitCodeProcess(
                    handle, ctypes.byref(exit_code)
                )
                return bool(queried and exit_code.value == still_active)
            finally:
                kernel32.CloseHandle(handle)
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError, TypeError, ValueError):
        return False


def _try_acquire_lock(name, stale_after=3600, wait_seconds=0):
    lock_path = APP_DIR / f".collector-{name}.lock"
    deadline = time.monotonic() + max(0, wait_seconds)
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            lock_data = json.dumps({"pid": os.getpid(), "created_at": time.time()}).encode()
            os.write(fd, lock_data)
            os.fsync(fd)
            return fd, lock_path
        except FileExistsError:
            existing = _read_json(lock_path, {})
            lock_pid = existing.get("pid") if isinstance(existing, dict) else None
            try:
                created_at = float(existing.get("created_at", 0) or 0) if isinstance(existing, dict) else 0
            except (TypeError, ValueError):
                created_at = 0
            # A slow but healthy worker must never lose its lock merely because a
            # wall-clock timeout elapsed.  The scheduler owns hard timeouts and
            # terminates the worker first; here we only reap locks whose PID is
            # demonstrably gone (or whose malformed payload never acquired one).
            stale = not _pid_is_running(lock_pid)
            if lock_pid is None and created_at:
                stale = time.time() - created_at > min(stale_after, 5)
            if stale:
                try:
                    lock_path.unlink(missing_ok=True)
                    continue
                except Exception:
                    pass
            if time.monotonic() >= deadline:
                return None, lock_path
            time.sleep(0.05)


def _release_lock(fd, lock_path):
    if fd is not None:
        try:
            os.close(fd)
        except Exception:
            pass
    try:
        Path(lock_path).unlink(missing_ok=True)
    except Exception:
        pass


@contextmanager
def _job_lock(name, stale_after):
    fd, lock_path = _try_acquire_lock(name, stale_after=stale_after)
    if fd is None:
        yield False
        return
    try:
        yield True
    finally:
        _release_lock(fd, lock_path)


def _utc_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _update_collector_status(job_name, state, **details):
    fd, lock_path = _try_acquire_lock("status", stale_after=30, wait_seconds=2)
    if fd is None:
        return
    try:
        status = _read_json(STATUS_FILE, {})
        if not isinstance(status, dict):
            status = {}
        jobs = status.setdefault("jobs", {})
        previous = jobs.get(job_name, {}) if isinstance(jobs.get(job_name), dict) else {}
        entry = dict(previous)
        entry.update(details)
        entry["state"] = state
        entry["heartbeat_at"] = _utc_iso()
        entry["pid"] = os.getpid()
        if state == "running":
            if previous.get("state") != "running" or not entry.get("last_started_at"):
                entry["last_started_at"] = entry["heartbeat_at"]
        elif state == "success":
            entry["last_success_at"] = entry["heartbeat_at"]
            entry.pop("last_error", None)
        elif state == "failed":
            entry["last_failure_at"] = entry["heartbeat_at"]
        jobs[job_name] = entry
        status["updated_at"] = entry["heartbeat_at"]
        status["version"] = 1
        _atomic_write_json(STATUS_FILE, status, indent=2)
        return True
    except Exception as error:
        print(f"⚠️ collector_status.json 갱신 실패: {error}")
        return False
    finally:
        _release_lock(fd, lock_path)


def _parse_kst_match_time(value):
    if not value or value in {"시간 미정", "마감/진행중"}:
        return None
    match = re.search(
        r'(?:(\d{2,4})\.)?(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})',
        str(value),
    )
    if not match:
        return None
    try:
        year_text, month, day, hour, minute = match.groups()
        month, day, hour, minute = map(int, (month, day, hour, minute))
        if year_text:
            year = int(year_text)
            if year < 100:
                year += 2000
            return datetime(year, month, day, hour, minute, tzinfo=KST)
        now = datetime.now(KST)
        candidates = [
            datetime(year, month, day, hour, minute, tzinfo=KST)
            for year in (now.year - 1, now.year, now.year + 1)
        ]
        return min(candidates, key=lambda item: abs((item - now).total_seconds()))
    except (TypeError, ValueError):
        return None

def _validate_sqlite_file(path):
    path = _local_path(path)
    try:
        if not path.exists() or path.stat().st_size < 100:
            return False
        with open(path, "rb") as file:
            if file.read(16) != b"SQLite format 3\x00":
                return False
        uri = path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=10)
        try:
            check = conn.execute("PRAGMA quick_check").fetchone()
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            return bool(
                check and str(check[0]).lower() == "ok" and "predictions" in tables
            )
        finally:
            conn.close()
    except Exception as error:
        print(f"⚠️ SQLite 검증 실패({path.name}): {error}")
        return False


def download_latest_db_from_github():
    print(f"\n[🔄 {time.strftime('%Y-%m-%d %H:%M:%S')}] 기존 기록 보호를 위해 GitHub에서 최신 DB를 가져옵니다...")
    url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/ai_predictions.db?t={int(time.time())}"
    final_path = _local_path("ai_predictions.db")
    temp_path = final_path.with_name(f".{final_path.name}.{os.getpid()}.download")
    backup_path = final_path.with_name(f"{final_path.name}.last_good")
    try:
        res = requests.get(url, timeout=30)
        if res.status_code != 200:
            print(f"⚠️ GitHub DB 다운로드 실패: HTTP {res.status_code} (로컬 정상본 보존)")
            return False
        with open(temp_path, "wb") as file:
            file.write(res.content)
            file.flush()
            os.fsync(file.fileno())
        if not _validate_sqlite_file(temp_path):
            print("❌ 원격 DB가 유효한 SQLite가 아니어서 교체하지 않습니다.")
            return False
        if _validate_sqlite_file(final_path):
            _atomic_write_bytes(backup_path, final_path.read_bytes())
        os.replace(temp_path, final_path)
        print("✅ 검증된 기존 DB를 원자적으로 동기화했습니다.")
        return True
    except Exception as error:
        print(f"❌ DB 다운로드 에러: {error} (로컬 정상본 보존)")
        return False
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass


def upload_to_github(file_path, remote_path=None):
    local_path = _local_path(file_path)
    remote_path = str(remote_path or Path(file_path).name).replace("\\", "/")
    if not GITHUB_TOKEN:
        print(f"⚠️ GitHub 토큰이 없어 업로드를 건너뜁니다: {remote_path}")
        return False
    if not local_path.exists():
        print(f"⚠️ 업로드할 파일이 없습니다: {local_path}")
        return False

    fd, lock_path = _try_acquire_lock("github-upload", stale_after=180, wait_seconds=30)
    if fd is None:
        print(f"⚠️ 다른 업로드가 진행 중이어서 건너뜁니다: {remote_path}")
        return False
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{remote_path}"
        git_headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        }
        with open(local_path, "rb") as file:
            content = file.read()
        b64_content = base64.b64encode(content).decode("utf-8")

        for attempt in range(2):
            sha = None
            r_get = requests.get(url, headers=git_headers, timeout=15)
            if r_get.status_code == 200:
                sha = r_get.json().get("sha")
            elif r_get.status_code not in (404,):
                print(f"⚠️ GitHub 현재 버전 조회 실패({remote_path}): HTTP {r_get.status_code}")
            data = {"message": f"Auto update {remote_path}", "content": b64_content}
            if sha:
                data["sha"] = sha
            r_put = requests.put(url, headers=git_headers, json=data, timeout=45)
            if r_put.status_code in (200, 201):
                print(f"✅ GitHub 동기화 완료: {remote_path}")
                return True
            if r_put.status_code not in (409, 422) or attempt == 1:
                try:
                    detail = r_put.json()
                except Exception:
                    detail = r_put.text[:300]
                print(f"❌ GitHub 동기화 실패 ({remote_path}): {detail}")
                return False
            time.sleep(1)
        return False
    except Exception as error:
        print(f"❌ [관제 봇 떡밥] GitHub 업로드 에러: {error}")
        return False
    finally:
        _release_lock(fd, lock_path)


def upload_sqlite_to_github(db_path="ai_predictions.db"):
    """Upload a consistent SQLite snapshot rather than a file mid-transaction."""
    source_path = _local_path(db_path)
    if not _validate_sqlite_file(source_path):
        print("❌ 로컬 DB 검증 실패로 업로드하지 않습니다.")
        return False
    publish_fd, publish_lock = _try_acquire_lock(
        "db-publish", stale_after=300, wait_seconds=90
    )
    if publish_fd is None:
        print("⚠️ 다른 DB 게시 작업이 진행 중이어서 이번 업로드를 건너뜁니다.")
        return False
    temp_path = None
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix="ai_predictions.", suffix=".snapshot.db", dir=APP_DIR
        )
        os.close(fd)
        temp_path = Path(temp_name)
        source = None
        target = None
        try:
            source = sqlite3.connect(str(source_path), timeout=30)
            target = sqlite3.connect(str(temp_path), timeout=30)
            source.backup(target)
            target.commit()
        finally:
            if target is not None:
                target.close()
            if source is not None:
                source.close()
        if not _validate_sqlite_file(temp_path):
            print("❌ DB 스냅샷 검증 실패로 업로드하지 않습니다.")
            return False
        return upload_to_github(temp_path, remote_path=Path(db_path).name)
    except Exception as error:
        print(f"❌ DB 스냅샷 업로드 실패: {error}")
        return False
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass
        _release_lock(publish_fd, publish_lock)


def _normalize_toto14_picks(picks):
    normalized = []
    for pick in picks or []:
        pick = str(pick).strip()
        if pick in {"승", "무", "패"} and pick not in normalized:
            normalized.append(pick)
    return normalized[:2]


def _toto14_picks_from_display(display, home_team, away_team):
    display = str(display or "")
    picks = []
    if f"{home_team} 승" in display:
        picks.append("승")
    if "무승부" in display:
        picks.append("무")
    if f"{away_team} 승" in display:
        picks.append("패")
    return _normalize_toto14_picks(picks)


def _render_toto14_picks_html(picks):
    picks = set(_normalize_toto14_picks(picks))
    styles = {
        "승": "background: #00F2FE; color: #0B0F19; font-weight: 900; border: 1px solid #00F2FE;" if "승" in picks else "background: transparent; color: #64748B; border: 1px solid #1E293B;",
        "무": "background: #10B981; color: #0B0F19; font-weight: 900; border: 1px solid #10B981;" if "무" in picks else "background: transparent; color: #64748B; border: 1px solid #1E293B;",
        "패": "background: #EF4444; color: #0B0F19; font-weight: 900; border: 1px solid #EF4444;" if "패" in picks else "background: transparent; color: #64748B; border: 1px solid #1E293B;",
    }
    return "".join(
        f"<div style='flex: 1; text-align: center; padding: 12px; border-radius: 6px; font-size: 14px; {styles[pick]}'>{pick}</div>"
        for pick in ("승", "무", "패")
    )


def _render_team_availability_status(injury_data, diff_hours, lineup_confirmed, lineup_msg=""):
    """Show whether squad data is clean, unavailable, or still awaiting lineups."""
    badges = []
    injury_data = injury_data if isinstance(injury_data, dict) else {}
    if injury_data.get("available"):
        if int(injury_data.get("count", 0) or 0) == 0:
            badges.append(
                "<div class='injury-badge' style='color:#10B981; border-color:#10B981;'>"
                "🏥 부상자료 확인 · 등록 결장 없음</div>"
            )
        elif not injury_data.get("ace_missing"):
            badges.append(
                "<div class='injury-badge' style='color:#94A3B8; border-color:#475569;'>"
                "🏥 부상자료 확인 · 핵심 결장 없음</div>"
            )
    else:
        badges.append(
            "<div class='injury-badge' style='color:#F59E0B; border-color:#F59E0B;'>"
            "⚠️ 부상자료 미수신</div>"
        )

    if not lineup_msg:
        if lineup_confirmed:
            badges.append(
                "<div class='injury-badge' style='color:#10B981; border-color:#10B981;'>"
                "👥 선발 확인 · 핵심 제외 없음</div>"
            )
        elif float(diff_hours or 0) <= 1.5:
            badges.append(
                "<div class='injury-badge' style='color:#F59E0B; border-color:#F59E0B;'>"
                "⏳ 선발 발표 대기</div>"
            )
        else:
            badges.append(
                "<div class='injury-badge' style='color:#64748B; border-color:#334155;'>"
                "👥 선발 확인 예정</div>"
            )
    return "".join(badges)


def _choose_toto14_picks(probs_dict, current_combinations, max_combinations=None):
    """Choose unique marks without exceeding the configured ticket budget."""
    max_combinations = max(1, int(max_combinations or TOTO14_MAX_COMBINATIONS))
    sorted_probs = sorted(
        ((pick, float(probs_dict.get(pick, 0) or 0)) for pick in ("승", "무", "패")),
        key=lambda item: item[1],
        reverse=True,
    )
    first_pick, first_pct = sorted_probs[0]
    second_pick, second_pct = sorted_probs[1]
    wants_double = first_pct - second_pct <= 7.0
    can_afford_double = int(current_combinations) * 2 <= max_combinations
    picks = [first_pick, second_pick] if wants_double and can_afford_double else [first_pick]
    return _normalize_toto14_picks(picks), first_pct, wants_double and not can_afford_double


def _ensure_toto14_freeze_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS toto14_prediction_freezes (
            match_id TEXT PRIMARY KEY,
            home_team TEXT NOT NULL,
            away_team TEXT NOT NULL,
            match_time TEXT,
            payload_json TEXT NOT NULL,
            frozen_at TEXT NOT NULL
        )
    """)


def _load_toto14_freezes():
    conn = None
    try:
        conn = sqlite3.connect(str(_local_path("ai_predictions.db")), timeout=30)
        conn.execute("PRAGMA busy_timeout = 30000")
        _ensure_toto14_freeze_table(conn)
        conn.commit()
        rows = conn.execute(
            "SELECT match_id, home_team, away_team, payload_json FROM toto14_prediction_freezes"
        ).fetchall()
        result = {}
        for match_id, home_team, away_team, payload_json in rows:
            try:
                payload = json.loads(payload_json)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                result[str(match_id)] = {
                    "home_team": str(home_team),
                    "away_team": str(away_team),
                    "payload": payload,
                }
        return result
    except Exception as error:
        print(f"⚠️ 승무패 14 동결본 조회 실패: {error}")
        return {}
    finally:
        if conn is not None:
            conn.close()


def _freeze_toto14_prediction(match_id, home_team, away_team, match_time, payload):
    """Persist the first final recommendation; INSERT OR IGNORE makes it immutable."""
    conn = None
    try:
        frozen_payload = dict(payload)
        frozen_payload["picks"] = _normalize_toto14_picks(
            frozen_payload.get("picks")
            or _toto14_picks_from_display(
                frozen_payload.get("best_pick_display"), home_team, away_team
            )
        )
        frozen_payload["prediction_frozen"] = True
        frozen_payload.setdefault("frozen_at", _utc_iso())
        conn = sqlite3.connect(str(_local_path("ai_predictions.db")), timeout=30)
        conn.execute("PRAGMA busy_timeout = 30000")
        _ensure_toto14_freeze_table(conn)
        conn.execute(
            """
            INSERT OR IGNORE INTO toto14_prediction_freezes
                (match_id, home_team, away_team, match_time, payload_json, frozen_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(match_id), str(home_team), str(away_team), str(match_time or ""),
                json.dumps(frozen_payload, ensure_ascii=False),
                str(frozen_payload["frozen_at"]),
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT home_team, away_team, payload_json FROM toto14_prediction_freezes WHERE match_id = ?",
            (str(match_id),),
        ).fetchone()
        if not row or str(row[0]) != str(home_team) or str(row[1]) != str(away_team):
            return None
        stored = json.loads(row[2])
        return stored if isinstance(stored, dict) else None
    except Exception as error:
        print(f"⚠️ 승무패 14 예측 동결 실패({match_id}): {error}")
        return None
    finally:
        if conn is not None:
            conn.close()


def _locked_toto14_fallback(match):
    """Use the last database prediction after kickoff; never calculate hindsight."""
    match_id = f"TOTO14_{match.get('id', '')}"
    conn = None
    try:
        conn = sqlite3.connect(str(_local_path("ai_predictions.db")), timeout=15)
        identity = conn.execute(
            """
            SELECT home_team, away_team
            FROM predictions WHERE match_id = ?
            """,
            (match_id,),
        ).fetchone()
        if (
            not identity
            or str(identity[0]) != str(match.get("home"))
            or str(identity[1]) != str(match.get("away"))
        ):
            return None
        kickoff = _parse_kst_match_time(match.get("match_time"))
        if kickoff is None:
            return None
        cutoff = kickoff.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        row = conn.execute(
            """
            SELECT prob_pick, prob_pick_prob, stage, analysis_version
            FROM prediction_snapshots
            WHERE match_id = ? AND created_at <= ?
            ORDER BY created_at DESC, id DESC LIMIT 1
            """,
            (match_id, cutoff),
        ).fetchone()
        if not row:
            return None
        picks = _toto14_picks_from_display(row[0], identity[0], identity[1])
        return {
            "match": dict(match),
            "home_logo": None,
            "away_logo": None,
            "best_pick_display": str(row[0] or "동결 예측 없음"),
            "p_h": 0.0,
            "p_d": 0.0,
            "p_a": 0.0,
            "probabilities_unavailable": True,
            "picks": picks,
            "picks_html": _render_toto14_picks_html(picks),
            # 이미 동결된 예측은 당시 버전을 그대로 표시해야 새 버전의
            # 성적으로 잘못 섞이지 않는다.
            "analysis_version": str(row[3] or ANALYSIS_VERSION),
            "analysis_confidence": 0.0,
            "analysis_stage": "locked",
            "frozen_from_stage": str(row[2] or "regular"),
            "prediction_frozen": True,
            "home_form": "",
            "away_form": "",
            "h_rank_html": "",
            "a_rank_html": "",
        }
    except Exception as error:
        print(f"⚠️ 승무패 14 잠금 예측 복구 실패({match_id}): {error}")
        return None
    finally:
        if conn is not None:
            conn.close()


def _unavailable_toto14_item(match):
    return {
        "match": dict(match),
        "home_logo": None,
        "away_logo": None,
        "best_pick_display": "킥오프 전 동결본 없음",
        "p_h": 0.0,
        "p_d": 0.0,
        "p_a": 0.0,
        "probabilities_unavailable": True,
        "picks": [],
        "picks_html": _render_toto14_picks_html([]),
        "analysis_version": ANALYSIS_VERSION,
        "analysis_confidence": 0.0,
        "analysis_stage": "locked_unavailable",
        "prediction_frozen": True,
        "home_form": "",
        "away_form": "",
        "h_rank_html": "",
        "a_rank_html": "",
    }


def save_dual_predictions_to_local_db(m_id, league, home_team, away_team, prob_pick, prob_val, ev_pick, ev_val, odd_h, odd_d, odd_a, match_time, is_toto14, fixture_id, analysis_stage="regular", confidence=0.0, analysis_version=None):
    target_analysis_version = str(analysis_version or ANALYSIS_VERSION)
    conn = sqlite3.connect(str(_local_path("ai_predictions.db")), timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT stage, confidence, prob_pick, prob_pick_prob, ev_pick, ev_pick_prob,
                   analysis_version
            FROM prediction_snapshots
            WHERE match_id = ?
            ORDER BY id DESC LIMIT 1
        """, (str(m_id),))
        previous = cursor.fetchone()
        cursor.execute(
            """
            SELECT api_fixture_id, actual_result, match_time, home_team, away_team
            FROM predictions WHERE match_id = ?
            """,
            (m_id,),
        )
        row = cursor.fetchone()
        if not row:
            # A first prediction created after kickoff is hindsight, not a forecast.
            if analysis_stage == "locked":
                print(f"⚠️ 킥오프 후 신규 예측 저장 차단: {home_team} vs {away_team}")
                return False
            cursor.execute("""
                INSERT INTO predictions 
                (match_id, league, home_team, away_team, prob_pick, prob_pick_prob,
                 ev_pick, ev_pick_prob, odd_h, odd_d, odd_a, match_time,
                 is_toto14, api_fixture_id, analysis_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                m_id, league, home_team, away_team, prob_pick, prob_val,
                ev_pick, ev_val, odd_h, odd_d, odd_a, match_time,
                is_toto14, fixture_id, target_analysis_version,
            ))
        else:
            existing_fix_id, actual_result, stored_match_time, stored_home, stored_away = row
            if str(stored_home) != str(home_team) or str(stored_away) != str(away_team):
                print(
                    f"❌ 예측 ID 충돌 차단({m_id}): "
                    f"{stored_home} vs {stored_away} != {home_team} vs {away_team}"
                )
                return False
            stored_kickoff = _parse_kst_match_time(stored_match_time)
            kickoff_passed = bool(stored_kickoff and datetime.now(KST) >= stored_kickoff)
            prediction_locked = (
                analysis_stage == "locked"
                or str(actual_result or "PENDING") != "PENDING"
                or kickoff_passed
                or (
                    int(is_toto14 or 0) == 1
                    and previous
                    and str(previous[0]) in {"T-30-final", "locked"}
                )
            )
            if prediction_locked:
                # Fixture identity may be recovered once, but prediction/odds/team
                # fields are immutable after the Toto final snapshot or kickoff.
                # Grading is handled elsewhere.
                if (
                    str(actual_result or "PENDING") == "PENDING"
                    and not int(existing_fix_id or 0)
                    and int(fixture_id or 0)
                ):
                    cursor.execute(
                        "UPDATE predictions SET api_fixture_id = ? WHERE match_id = ?",
                        (int(fixture_id), m_id),
                    )
                conn.commit()
                return True

            final_fix_id = int(fixture_id or 0) or int(existing_fix_id or 0)
            cursor.execute("""
                UPDATE predictions
                SET api_fixture_id = ?, match_time = ?, league = ?,
                    prob_pick = ?, prob_pick_prob = ?, ev_pick = ?, ev_pick_prob = ?,
                    analysis_version = ?
                WHERE match_id = ?
            """, (
                final_fix_id, match_time, league, prob_pick, prob_val,
                ev_pick, ev_val, target_analysis_version, m_id,
            ))

        current = (
            str(analysis_stage), round(float(confidence or 0), 4), str(prob_pick),
            round(float(prob_val or 0), 2), str(ev_pick), round(float(ev_val or 0), 2),
            target_analysis_version,
        )
        previous_normalized = None
        if previous:
            previous_normalized = (
                str(previous[0]), round(float(previous[1] or 0), 4), str(previous[2]),
                round(float(previous[3] or 0), 2), str(previous[4]), round(float(previous[5] or 0), 2),
                str(previous[6] or ""),
            )
        if previous_normalized != current:
            cursor.execute("""
                INSERT INTO prediction_snapshots (
                    match_id, analysis_version, stage, confidence,
                    prob_pick, prob_pick_prob, ev_pick, ev_pick_prob,
                    odd_h, odd_d, odd_a, api_fixture_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(m_id), target_analysis_version, analysis_stage, float(confidence or 0),
                prob_pick, prob_val, ev_pick, ev_val, odd_h, odd_d, odd_a, int(fixture_id or 0)
            ))
        conn.commit()
        return True
    except Exception as e: print(f"⚠️ [DB 에러] 듀얼 예측 저장 실패: {e}")
    finally: conn.close()
    return False

# === 🧠 [V3/V4 통합 엔진] 주전 선발 & 전력 누수(WAR) 계산기 ===
POSITION_WEIGHTS = {
    "Goalkeeper": 1.5,
    "Defender": 1.3,
    "Midfielder": 1.1,
    "Attacker": 1.0
}
SQUAD_CACHE = {} 

def fetch_team_squad_cached(team_id):
    if not team_id:
        return []
    team_id = int(team_id)
    if team_id in SQUAD_CACHE:
        return SQUAD_CACHE[team_id]

    cache_key = f"team_squad_v2_{team_id}"
    cached_players = get_db_cache(cache_key, SQUAD_CACHE_TTL_HOURS)
    if isinstance(cached_players, list):
        SQUAD_CACHE[team_id] = cached_players
        return cached_players

    # 프로세스가 20분마다 새로 시작되어도 같은 선수단을 다시 부르지 않도록
    # SQLite 캐시를 사용한다. 일시 오류 때는 오래된 정상본을 사용한다.
    stale_players = get_db_cache(cache_key, 24 * 365 * 2)
    retry_guard = get_db_cache(
        f"{cache_key}_retry_guard", FIXTURE_IDENTITY_RETRY_HOURS
    )
    if retry_guard is not None:
        players = stale_players if isinstance(stale_players, list) else []
        SQUAD_CACHE[team_id] = players
        return players
    try:
        res = api_get("/players/squads", params={"team": team_id}, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if not data.get("errors"):
                response_rows = data.get("response") or []
                players = (
                    response_rows[0].get("players", [])
                    if response_rows and isinstance(response_rows[0], dict)
                    else []
                )
                players = players if isinstance(players, list) else []
                SQUAD_CACHE[team_id] = players
                set_db_cache(cache_key, players)
                return players
    except Exception:
        pass
    set_db_cache(f"{cache_key}_retry_guard", {"failed": True})
    players = stale_players if isinstance(stale_players, list) else []
    SQUAD_CACHE[team_id] = players
    return players

def calculate_war_penalty(team_name, ace_names, total_inj_count, team_id):
    squad = fetch_team_squad_cached(team_id)
    war_penalty = 0.0
    details = []
    missing_aces_found = 0
    
    for ace in ace_names:
        pos = "Attacker" 
        for p in squad:
            if p.get("name") and (ace.lower() in p["name"].lower() or p["name"].lower() in ace.lower()):
                pos = p.get("position", "Attacker")
                break
        
        weight = POSITION_WEIGHTS.get(pos, 1.0)
        gap = 1.5 
        drop = gap * weight
        war_penalty += drop
        
        pos_kr = {"Goalkeeper": "GK", "Defender": "DF", "Midfielder": "MF", "Attacker": "FW"}.get(pos, "FW")
        details.append(f"{ace}({pos_kr})")
        missing_aces_found += 1
        
    regular_injuries = max(0, total_inj_count - missing_aces_found)
    effective_bench_injuries = min(regular_injuries, 3) 
    
    if effective_bench_injuries > 0:
        drop = effective_bench_injuries * 0.3
        war_penalty += drop
        if regular_injuries >= 4:
            details.append("서브 결장 다수")
        elif effective_bench_injuries >= 2:
            details.append(f"기타 서브 {effective_bench_injuries}명")
        elif effective_bench_injuries == 1:
            details.append("서브 1명")

    scaled_penalty = min(0.35, war_penalty * 0.08) 
    return scaled_penalty, details, war_penalty


def get_expected_core_players(team_id, league_id, season):
    """득점/도움 상위 선수와 보호 선수 중 현재 해당 팀 소속인 선수만 반환한다."""
    if not team_id:
        return []
    core_players = []
    key_players = fetch_league_key_players(league_id, season) if league_id and season else {}
    for name, stats in key_players.items():
        if stats.get("team_id") == team_id and name not in core_players:
            core_players.append(name)

    for player in fetch_team_squad_cached(team_id):
        actual_name = player.get("name", "")
        if actual_name and find_protected_star(actual_name) and actual_name not in core_players:
            core_players.append(actual_name)
    return core_players[:8]


def find_missing_core_players(core_players, starters):
    normalized_starters = [_normalize_player_name(name) for name in starters]
    missing = []
    for core in core_players:
        target = _normalize_player_name(core)
        found = any(
            target == starter or (len(target) >= 6 and (target in starter or starter in target))
            for starter in normalized_starters if starter
        )
        if not found:
            missing.append(core)
    return missing


def calculate_data_confidence(home_info, away_info, fixture_id, h_stand, a_stand, h_long, a_long,
                              h_recent, a_recent, h_stats, a_stats, h_inj_data, a_inj_data,
                              diff_hours, lineup_confirmed):
    """데이터가 부족한 경기는 확률을 낮춰 보이게 하고 TOP3 우선순위도 낮춘다."""
    score = 0.35
    if home_info.get("id") and away_info.get("id"):
        score += 0.10
    if fixture_id:
        score += 0.10
    if min(h_long.get("home_total", 0), a_long.get("away_total", 0)) >= 5:
        score += 0.12
    if h_stand.get("rank", 99) != 99 and a_stand.get("rank", 99) != 99:
        score += 0.08
    if min(h_recent.get("matches", 0), a_recent.get("matches", 0)) >= 4:
        score += 0.08
    if min(h_stats.get("sample_size", 0), a_stats.get("sample_size", 0)) >= 1:
        score += 0.04
    if h_inj_data.get("available") and a_inj_data.get("available"):
        score += 0.05
    if diff_hours > 1.5:
        score += 0.03
    elif lineup_confirmed:
        score += 0.08
    return round(max(0.35, min(0.95, score)), 3)


def prediction_stage(diff_hours, lineup_confirmed=False):
    if diff_hours <= 0:
        return "locked"
    if lineup_confirmed or diff_hours <= 0.5:
        return "T-30-final"
    if diff_hours <= 1.0:
        return "T-60"
    if diff_hours <= 1.5:
        return "T-90"
    return "regular"


MARKET_LABELS = {
    "1x2": "승무패",
    "handicap": "핸디캡",
    "totals": "언더오버",
}


def infer_pick_market(pick_or_text):
    if isinstance(pick_or_text, dict):
        explicit = str(pick_or_text.get("market_key") or "").strip().lower()
        if explicit in MARKET_LABELS:
            return explicit
        sort_id = int(pick_or_text.get("sort_id") or 0)
        if sort_id == 1:
            return "totals"
        if sort_id == 2:
            return "handicap"
        text = str(pick_or_text.get("raw_pick") or "")
    else:
        text = str(pick_or_text or "")
    lowered = text.lower().replace(" ", "")
    if "언더" in lowered or "오버" in lowered or "u/o" in lowered:
        return "totals"
    if "핸디" in lowered or "마핸" in lowered or "플핸" in lowered:
        return "handicap"
    return "1x2"


def load_market_performance(league_name=None):
    """Read honest market records, then cautiously blend a matching league."""
    summary = {
        key: {"samples": 0, "hits": 0, "hit_rate": 0.5, "selection_share": 0.0}
        for key in MARKET_LABELS
    }
    candidate_rows = []
    try:
        conn = sqlite3.connect(str(_local_path("ai_predictions.db")), timeout=15)
        tables = {
            str(row[0]) for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "prediction_candidate_results" in tables:
            result_columns = {
                str(row[1]) for row in conn.execute(
                    "PRAGMA table_info(prediction_candidate_results)"
                )
            }
            if "market_rank" in result_columns:
                prediction_columns = {
                    str(row[1]) for row in conn.execute("PRAGMA table_info(predictions)")
                }
                if "league" in prediction_columns:
                    candidate_rows = conn.execute(
                        """
                        SELECT result.market_key, result.is_correct, prediction.league
                        FROM prediction_candidate_results AS result
                        LEFT JOIN predictions AS prediction
                          ON prediction.match_id = result.match_id
                        WHERE result.market_rank = 1
                        ORDER BY result.id DESC LIMIT 1800
                        """
                    ).fetchall()
                else:
                    candidate_rows = [
                        (market, hit, "") for market, hit in conn.execute(
                            """
                            SELECT market_key, is_correct
                            FROM prediction_candidate_results
                            WHERE market_rank = 1
                            ORDER BY id DESC LIMIT 900
                            """
                        ).fetchall()
                    ]
        prediction_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(predictions)")
        }
        required_columns = {
            "prob_pick", "is_correct_prob", "ev_pick", "is_correct_ev",
            "actual_result", "match_time",
        }
        rows = (
            conn.execute(
                """
                SELECT prob_pick, is_correct_prob, ev_pick, is_correct_ev
                FROM predictions
                WHERE actual_result = 'FINISHED'
                ORDER BY match_time DESC LIMIT 300
                """
            ).fetchall()
            if required_columns.issubset(prediction_columns)
            else []
        )
        conn.close()
    except Exception:
        return summary

    high_market_counts = {key: 0 for key in MARKET_LABELS}
    for prob_pick, prob_hit, ev_pick, ev_hit in rows:
        prob_market = infer_pick_market(prob_pick)
        high_market_counts[prob_market] += 1
        for pick_text, hit in ((prob_pick, prob_hit), (ev_pick, ev_hit)):
            if not pick_text:
                continue
            market = infer_pick_market(pick_text)
            summary[market]["samples"] += 1
            summary[market]["hits"] += 1 if int(hit or 0) == 1 else 0

    # The old record contains only picks the selector happened to expose, so it
    # is selection-biased.  New snapshots grade the top candidate inside every
    # available market.  As soon as those exist, they become the calibration
    # source and let 1X2, handicap and totals compete on their actual record.
    if candidate_rows:
        for market in MARKET_LABELS:
            summary[market]["samples"] = 0
            summary[market]["hits"] = 0
        for market, hit, _league in candidate_rows:
            market = str(market or "")
            if market not in summary:
                continue
            summary[market]["samples"] += 1
            summary[market]["hits"] += 1 if int(hit or 0) == 1 else 0

    total_high = sum(high_market_counts.values())
    target_league = " ".join(str(league_name or "").casefold().split())
    for market, values in summary.items():
        # 작은 표본이 한 시장을 과도하게 올리거나 내리지 않도록 베타 사전분포로 보정한다.
        global_rate = (values["hits"] + 4.0) / (values["samples"] + 8.0)
        league_rows = [
            int(hit or 0)
            for row_market, hit, row_league in candidate_rows
            if str(row_market or "") == market
            and target_league
            and " ".join(str(row_league or "").casefold().split()) == target_league
        ]
        league_samples = len(league_rows)
        league_hits = sum(1 for hit in league_rows if hit == 1)
        values["league_samples"] = league_samples
        values["league_hits"] = league_hits
        if league_samples >= 8:
            league_rate = (league_hits + 4.0) / (league_samples + 8.0)
            league_weight = min(0.50, league_samples / (league_samples + 24.0))
            values["hit_rate"] = round(
                global_rate * (1.0 - league_weight) + league_rate * league_weight,
                4,
            )
            values["scope"] = "global+league"
        else:
            values["hit_rate"] = round(global_rate, 4)
            values["scope"] = "global"
        values["selection_share"] = round(
            high_market_counts[market] / total_high, 4
        ) if total_high else 0.0
    return summary


def calibrate_market_candidates(picks, market_performance, confidence):
    """세 시장을 같은 척도로 보정하고 확률ㆍ공정확률ㆍ오차범위를 저장한다."""
    confidence = max(0.35, min(0.95, float(confidence or 0.35)))
    grouped = {key: [] for key in MARKET_LABELS}
    for pick in picks:
        market = infer_pick_market(pick)
        pick["market_key"] = market
        grouped[market].append(pick)

    for market, market_picks in grouped.items():
        if not market_picks:
            continue
        history = market_performance.get(market, {})
        samples = int(history.get("samples") or 0)
        hit_rate = float(history.get("hit_rate") or 0.5)
        history_weight = min(0.18, (samples / (samples + 30.0)) * 0.18)
        adjusted_values = []
        for pick in market_picks:
            raw_probability = max(0.001, min(0.999, float(pick.get("prob") or 0)))
            fair_probability = max(0.0, min(1.0, float(pick.get("market_prob") or 0)))
            pick["raw_model_prob"] = round(raw_probability, 6)
            if fair_probability > 0:
                history_skill = 0.75 + (0.50 * hit_rate)
                history_target = fair_probability + (
                    (raw_probability - fair_probability) * history_skill
                )
                adjusted = (
                    raw_probability * (1.0 - history_weight)
                    + history_target * history_weight
                )
            else:
                adjusted = raw_probability
            adjusted_values.append(max(0.001, adjusted))

        normalized = normalize_probabilities(adjusted_values)
        neutral = 1.0 / len(market_picks)
        for pick, probability in zip(market_picks, normalized):
            fair_probability = max(0.0, min(1.0, float(pick.get("market_prob") or 0)))
            edge = probability - fair_probability if fair_probability > 0 else 0.0
            conviction = (probability - neutral) / max(0.01, 1.0 - neutral)
            error_margin = min(
                0.18,
                0.04 + ((1.0 - confidence) * 0.14) + (0.03 if samples < 20 else 0.0),
            )
            pick["prob"] = round(probability, 6)
            pick["ev"] = round(probability * float(pick.get("odd") or 0), 6)
            pick["fair_prob"] = round(fair_probability, 6) if fair_probability > 0 else None
            pick["edge"] = round(edge, 6)
            pick["market_hit_rate"] = round(hit_rate, 4)
            pick["market_history_samples"] = samples
            pick["market_history_scope"] = str(history.get("scope") or "global")
            pick["learning_weight"] = round(history_weight, 4)
            pick["data_confidence"] = round(confidence, 4)
            pick["error_margin"] = round(error_margin, 4)
            pick["probability_interval"] = {
                "low": round(max(0.0, probability - error_margin), 6),
                "high": round(min(1.0, probability + error_margin), 6),
            }
            # 특정 시장이 자주 선택됐다는 이유만으로 감점하지 않는다.
            # 새 동결 기록에서 각 시장 1위 후보의 실제 성적을 채점한 뒤 그
            # 검증된 성적으로 보정한다.
            pick["balanced_score"] = round(
                (probability * 0.55)
                + (conviction * 0.35)
                + (max(edge, 0.0) * 0.10),
                6,
            )
    return picks


def build_analysis_evidence(context):
    """실제 값이 있는 항목만 근거에 포함하고 누락 항목은 신뢰도에서 제외한다."""
    evidence = []
    available_weight = 0.0

    def add(name, weight, available, value):
        nonlocal available_weight
        if not available:
            return
        available_weight += weight
        evidence.append({"name": name, "weight": weight, "value": str(value)})

    h_recent = context["h_recent"]
    a_recent = context["a_recent"]
    h_stats = context["h_stats"]
    a_stats = context["a_stats"]
    h_long = context["h_long"]
    a_long = context["a_long"]
    h_inj = context["h_inj"]
    a_inj = context["a_inj"]
    add(
        "최근 성적", 0.13,
        min(h_recent.get("matches", 0), a_recent.get("matches", 0)) >= 3,
        f"최근 {h_recent.get('matches', 0)}/{a_recent.get('matches', 0)}경기, "
        f"PPG {h_recent.get('ppg', 0):.2f}/{a_recent.get('ppg', 0):.2f}",
    )
    add(
        "xG", 0.12,
        h_stats.get("xg") is not None and a_stats.get("xg") is not None,
        f"{h_stats.get('xg')}/{a_stats.get('xg')}",
    )
    add(
        "슈팅 품질", 0.10,
        min(h_stats.get("sample_size", 0), a_stats.get("sample_size", 0)) >= 1,
        f"유효슈팅 {h_stats.get('shots_on_goal', 0):.1f}/{a_stats.get('shots_on_goal', 0):.1f}",
    )
    add(
        "홈·원정", 0.10,
        h_long.get("home_total", 0) >= 3 and a_long.get("away_total", 0) >= 3,
        f"홈 {h_long.get('home_wins', 0)}승/{h_long.get('home_total', 0)}경기, "
        f"원정 {a_long.get('away_wins', 0)}승/{a_long.get('away_total', 0)}경기",
    )
    add(
        "상대 전적", 0.08, context["h2h_total"] > 0,
        f"{context['h_wins']}승-{context['draws']}무-{context['a_wins']}승",
    )
    add(
        "선수 결장", 0.11,
        bool(h_inj.get("available") and a_inj.get("available")),
        f"결장 {h_inj.get('count', 0)}명/{a_inj.get('count', 0)}명",
    )
    add(
        "휴식일", 0.06,
        max(context["h_rest_days"], context["a_rest_days"]) < 90,
        f"{context['h_rest_days']}일/{context['a_rest_days']}일",
    )
    add("날씨", 0.05, bool(context.get("weather")), context.get("weather"))
    add("심판", 0.05, bool(context.get("referee")), context.get("referee"))
    tactical_text = context.get("tactical_text")
    add("전술 상성", 0.05, bool(tactical_text), tactical_text)
    movement_text = context.get("movement_text")
    add("배당 변동", 0.05, bool(movement_text), movement_text)
    add(
        "팀 신원·경기 연결", 0.10,
        bool(context.get("home_id") and context.get("away_id") and context.get("fixture_id")),
        f"팀ID {context.get('home_id')}/{context.get('away_id')}, 경기ID {context.get('fixture_id')}",
    )
    return evidence, round(max(0.0, min(1.0, available_weight)), 4)


def attach_underdog_signals(picks, home_team, away_team, metrics):
    """역배 후보에 서로 독립적인 실제 지지 근거를 붙인다."""
    for pick in picks:
        pick["support_signals"] = []
        market = infer_pick_market(pick)
        side = str(pick.get("selection_side") or "")
        pick["is_true_underdog"] = bool(
            market in {"1x2", "handicap"}
            and side in {"home", "away"}
            and side == metrics.get("underdog_side")
        )
        if not pick["is_true_underdog"] or float(pick.get("odd") or 0) < 2.0:
            continue
        opponent = "away" if side == "home" else "home"
        signals = []
        signal_groups = []

        def add_signal(group, label):
            # 결장과 선발 이탈처럼 같은 원인을 두 개의
            # 독립 근거로 중복 계산하지 않는다.
            if group in signal_groups:
                return
            signal_groups.append(group)
            signals.append(label)

        if metrics[f"{opponent}_absence"] - metrics[f"{side}_absence"] >= 0.05:
            add_signal("squad", "상대 핵심 결장·선발 누수")
        if metrics[f"{side}_market_bonus"] > 0:
            add_signal("market", "해외 배당 하락·시장 지지")
        if metrics[f"{side}_tactical"] > 0:
            add_signal("matchup", "상대 전적·전술 상성 우위")
        if metrics[f"{side}_recent"] - metrics[f"{opponent}_recent"] >= 0.035:
            add_signal("form", "최근 경기력 지표 우위")
        if metrics[f"{side}_rest"] - metrics[f"{opponent}_rest"] >= 2:
            add_signal("schedule", "휴식일 우위")
        if metrics[f"{opponent}_lineup"] - metrics[f"{side}_lineup"] >= 0.08:
            add_signal("squad", "상대 선발 핵심 이탈")
        pick["support_signals"] = signals
        pick["support_signal_groups"] = signal_groups
        pick["independent_support_count"] = len(signal_groups)
    return picks


def _has_korean_final_consonant(text):
    """Return whether the last Hangul syllable has a 받침."""
    for char in reversed(str(text or "").strip()):
        code = ord(char)
        if 0xAC00 <= code <= 0xD7A3:
            return (code - 0xAC00) % 28 != 0
        if char.isalnum():
            return False
    return False


def _subject_form(name):
    name = str(name or "팀").strip()
    return f"{name}{'이' if _has_korean_final_consonant(name) else '가'}"


def _human_pick_label(raw_pick, home_team=""):
    """Explain Betman handicap notation without changing the stored grading key."""
    raw = str(raw_pick or "").strip()
    matched = re.match(
        r"^\[\s*([+-]?\d+(?:\.\d+)?)\s*\]\s*(.*?)\s*핸디(승|무|패)$",
        raw,
    )
    if not matched:
        return raw
    handicap, team_name, result = matched.groups()
    team_name = team_name.strip() or str(home_team or "홈팀").strip()
    try:
        handicap = f"{float(handicap):+.1f}"
    except (TypeError, ValueError):
        pass
    return f"{team_name} {handicap} 적용 후 {result}"


def _report_pick_line(pick, home_team=""):
    if not pick:
        return "계산 가능한 선택지 없음"
    probability = float(pick.get("prob") or 0)
    fair_probability = pick.get("fair_prob")
    comparison = "시장 공정확률 자료 없음"
    if fair_probability is not None:
        comparison = (
            f"시장 공정확률 {float(fair_probability) * 100:.1f}%, "
            f"차이 {float(pick.get('edge') or 0) * 100:+.1f}%p"
        )
    display_pick = _human_pick_label(pick.get("raw_pick", ""), home_team)
    return f"{display_pick} · 모델 {probability * 100:.1f}% · {comparison}"


def build_detailed_report(
    selected_pick, evidence, confidence, candidates=None, categories=None, context=None
):
    """픽 확정 뒤 실제 사용값만 풀어 쓰는 고객용 상세 분석문을 만든다."""
    if not selected_pick:
        return "실제 배당과 모델 확률을 함께 비교할 수 있는 선택지가 없어 기존 분석을 유지했습니다."
    candidates = list(candidates or [])
    categories = categories or {}
    context = context or {}
    probability = float(selected_pick.get("prob") or 0)
    fair_probability = selected_pick.get("fair_prob")
    edge = selected_pick.get("edge")
    interval = selected_pick.get("probability_interval") or {}
    evidence_by_name = {
        str(item.get("name")): str(item.get("value"))
        for item in evidence if item.get("name") and item.get("value")
    }
    expected_names = (
        "최근 성적", "xG", "슈팅 품질", "홈·원정", "상대 전적", "선수 결장",
        "휴식일", "날씨", "심판", "전술 상성", "배당 변동", "팀 신원·경기 연결",
    )
    missing_names = [name for name in expected_names if name not in evidence_by_name]
    evidence_text = ", ".join(
        f"{name}: {value}" for name, value in evidence_by_name.items()
    ) or "확인 가능한 기본 경기 정보만 사용"
    fair_text = (
        f"베팅업체 마진 제거 공정확률은 {float(fair_probability) * 100:.1f}%이고 "
        f"모델과의 차이는 {float(edge or 0) * 100:+.1f}%p입니다."
        if fair_probability is not None else "비교 가능한 시장 공정확률 자료는 없습니다."
    )
    market_best = {}
    for market in MARKET_LABELS:
        market_candidates = [item for item in candidates if infer_pick_market(item) == market]
        market_best[market] = max(
            market_candidates,
            key=lambda item: float(item.get("prob") or 0),
            default=None,
        )

    home = str(context.get("home") or "홈팀")
    away = str(context.get("away") or "원정팀")
    exp_h = float(context.get("exp_h") or 0)
    exp_a = float(context.get("exp_a") or 0)
    if exp_h - exp_a >= 0.30:
        flow = f"{_subject_form(home)} 공격 주도권을 더 오래 가져갈 가능성을 높게 봅니다."
    elif exp_a - exp_h >= 0.30:
        flow = f"{_subject_form(away)} 역습과 전환 과정에서 더 위협적인 장면을 만들 가능성을 높게 봅니다."
    else:
        flow = "기대 득점 차이가 작아 한쪽의 일방적인 주도보다 접전 가능성을 높게 봅니다."
    weather = str(context.get("weather") or "")
    weather_note = ""
    if weather in {"Rain", "Snow"}:
        weather_ko = "비" if weather == "Rain" else "눈"
        weather_note = (
            f" 현지 {weather_ko} 예보는 기대 득점을 낮추는 방향으로 반영했지만, "
            "기상 정보만으로 언더를 확정하지는 않았습니다."
        )

    honey = categories.get("honey")
    vip = categories.get("vip_underdog")
    final_parts = [f"최종 추천픽은 {_report_pick_line(selected_pick, home)}입니다."]
    if vip:
        signals = ", ".join(vip.get("support_signals") or []) or "복수 지표 동시 지지"
        final_parts.append(
            f"이 최종픽은 보수적 가치와 독립 근거 기준을 모두 통과해 VIP 검증 등급이며, "
            f"독립 근거는 {signals}입니다. 별도의 반대 픽을 함께 제시하지 않습니다."
        )
    elif honey:
        final_parts.append("이 최종픽은 실제 배당과 보수적 우위까지 확인되어 배당가치 우수 등급입니다.")
    else:
        final_parts.append("세 시장을 모두 비교해 한 방향을 골랐으며, 별도 가치·VIP 등급은 부여하지 않았습니다.")
    missing_text = (
        f"확보하지 못한 항목({', '.join(missing_names)})은 임의로 추측하지 않고 신뢰도에서 감점했습니다."
        if missing_names else "요구된 핵심 데이터 항목이 모두 연결되어 있습니다."
    )
    return "\n\n".join([
        (
            f"[종합 경기 흐름] 예상 정규시간 득점은 {home} {exp_h:.2f}골, "
            f"{away} {exp_a:.2f}골입니다. {flow}{weather_note}"
        ),
        f"[일반 승무패 분석] {_report_pick_line(market_best.get('1x2'), home)}.",
        f"[핸디캡 분석] {_report_pick_line(market_best.get('handicap'), home)}.",
        f"[언더오버 분석] {_report_pick_line(market_best.get('totals'), home)}.",
        f"[실제 사용 근거] {evidence_text}. {missing_text}",
        (
            f"[최종 선택과 신뢰도] {' '.join(final_parts)} 선택된 최종픽의 보정 확률은 "
            f"{probability * 100:.1f}%이며, {fair_text} 예상 오차범위는 "
            f"{float(interval.get('low', 0)) * 100:.1f}%~{float(interval.get('high', 1)) * 100:.1f}%, "
            f"데이터 신뢰도는 {confidence * 100:.1f}%입니다. 이 수치는 적중을 보장하지 않습니다."
        ),
    ])


def _audit_number(value, default=None):
    try:
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            return default
        return round(number, 6)
    except (TypeError, ValueError):
        return default


def build_pick_selection_audit(candidates, categories, confidence):
    """Create one deterministic, machine-readable record of the full decision.

    Customer HTML is intentionally excluded.  The learning robot needs the
    values that existed before kickoff, the complete candidate set, and the
    exact reason a candidate won; it must not try to reconstruct them later.
    """
    candidates = list(candidates or [])
    categories = categories or {}
    category_membership = {}
    compact_categories = {}
    for category_key in ("high_probability", "honey", "vip_underdog"):
        selected = categories.get(category_key)
        if not selected:
            compact_categories[category_key] = None
            continue
        raw_pick = str(selected.get("raw_pick") or "")
        category_membership.setdefault(raw_pick, []).append(category_key)
        compact_categories[category_key] = {
            "raw_pick": raw_pick,
            "market_key": infer_pick_market(selected),
            "probability": _audit_number(selected.get("prob"), 0.0),
            "odd": _audit_number(selected.get("odd"), 0.0),
            "fair_probability": _audit_number(selected.get("fair_prob")),
            "robust_edge": _audit_number(selected.get("robust_edge"), 0.0),
            "value_pick_tier": str(selected.get("value_pick_tier") or ""),
            "final_pick_grade": str(selected.get("final_pick_grade") or "standard"),
            "learning_robot": dict(selected.get("learning_robot") or {}),
            "independent_support_count": int(
                selected.get("independent_support_count", 0) or 0
            ),
        }

    ranked = sorted(
        candidates,
        key=lambda item: (
            float(item.get("prob", 0) or 0),
            float(item.get("balanced_score", 0) or 0),
            float(item.get("safe_score", 0) or 0),
            float(item.get("recommendation_score", 0) or 0),
        ),
        reverse=True,
    )
    rank_by_identity = {
        (infer_pick_market(item), str(item.get("raw_pick") or "")): index
        for index, item in enumerate(ranked, start=1)
    }
    market_rank_by_identity = {}
    for market in MARKET_LABELS:
        market_ranked = sorted(
            [item for item in candidates if infer_pick_market(item) == market],
            key=lambda item: (
                float(item.get("prob", 0) or 0),
                float(item.get("balanced_score", 0) or 0),
                str(item.get("raw_pick") or ""),
            ),
            reverse=True,
        )
        for index, item in enumerate(market_ranked, start=1):
            market_rank_by_identity[
                (market, str(item.get("raw_pick") or ""))
            ] = index
    candidate_rows = []
    for item in candidates:
        raw_pick = str(item.get("raw_pick") or "")
        market_key = infer_pick_market(item)
        selected_as = list(category_membership.get(raw_pick, []))
        rank = rank_by_identity.get((market_key, raw_pick))
        candidate_rows.append({
            "market_key": market_key,
            "label": str(item.get("label") or ""),
            "raw_pick": raw_pick,
            "selection_side": str(item.get("selection_side") or ""),
            "handicap_base": _audit_number(item.get("handicap_base")),
            "sort_id": int(item.get("sort_id", 0) or 0),
            "model_probability": _audit_number(item.get("prob"), 0.0),
            "raw_model_probability": _audit_number(item.get("raw_model_prob")),
            "fair_probability": _audit_number(item.get("fair_prob")),
            "odd": _audit_number(item.get("odd"), 0.0),
            "edge": _audit_number(item.get("edge"), 0.0),
            "robust_probability": _audit_number(
                item.get("robust_probability"), 0.0
            ),
            "robust_edge": _audit_number(item.get("robust_edge"), 0.0),
            "robust_ev": _audit_number(item.get("robust_ev"), 0.0),
            "balanced_score": _audit_number(item.get("balanced_score"), 0.0),
            "safe_score": _audit_number(item.get("safe_score"), 0.0),
            "recommendation_score": _audit_number(
                item.get("recommendation_score"), 0.0
            ),
            "market_hit_rate": _audit_number(item.get("market_hit_rate"), 0.5),
            "market_history_samples": int(
                item.get("market_history_samples", 0) or 0
            ),
            "market_history_scope": str(item.get("market_history_scope") or "global"),
            "learning_weight": _audit_number(item.get("learning_weight"), 0.0),
            "data_confidence": _audit_number(
                item.get("data_confidence"), confidence
            ),
            "error_margin": _audit_number(item.get("error_margin"), 0.0),
            "probability_interval": {
                "low": _audit_number(
                    (item.get("probability_interval") or {}).get("low"), 0.0
                ),
                "high": _audit_number(
                    (item.get("probability_interval") or {}).get("high"), 1.0
                ),
            },
            "is_true_underdog": bool(item.get("is_true_underdog")),
            "is_qualified_underdog": bool(item.get("is_qualified_underdog")),
            "support_signals": list(item.get("support_signals") or []),
            "independent_support_count": int(
                item.get("independent_support_count", 0) or 0
            ),
            "selection_rank": rank,
            "market_rank": market_rank_by_identity.get((market_key, raw_pick)),
            "selected_as": selected_as,
            "selection_reason": (
                "세 시장 통합 최종 추천픽"
                if "high_probability" in selected_as
                else (
                    "최종 추천픽의 배당가치 등급"
                    if "honey" in selected_as
                    else f"통합 비교 순위 {rank}위"
                )
            ),
        })

    market_counts = {
        market: sum(1 for item in candidate_rows if item["market_key"] == market)
        for market in MARKET_LABELS
    }
    selected_high = compact_categories.get("high_probability") or {}
    decision = {
        "schema_version": PICK_AUDIT_SCHEMA_VERSION,
        "analysis_version": ANALYSIS_VERSION,
        "selector": "unified-accuracy-value-learning-v1",
        "score_order": [
            "value_eligibility", "robust_probability", "model_probability",
            "robust_edge", "robust_ev",
        ],
        "candidate_count": len(candidate_rows),
        "market_candidate_counts": market_counts,
        "missing_markets": [
            market for market, count in market_counts.items() if count == 0
        ],
        "data_confidence": _audit_number(confidence, 0.0),
        "selected_pick": selected_high.get("raw_pick", ""),
        "selected_market": selected_high.get("market_key", ""),
        "selection_reason": (
            "승무패·언더오버·핸디캡을 함께 비교하고, 검증된 가치 후보 안에서 "
            "보수확률이 가장 높은 한 방향만 최종 추천픽으로 확정"
        ),
        "final_pick_grade": str(selected_high.get("final_pick_grade") or "standard"),
        "value_badge": compact_categories.get("honey") is not None,
        "vip_promoted": compact_categories.get("vip_underdog") is not None,
        "learning_robot": dict(selected_high.get("learning_robot") or {
            "mode": "controlled_adviser", "influence_cap": 0.18,
            "history_rewrite": False, "self_modifying": False,
        }),
    }
    return candidate_rows, compact_categories, decision


def _ensure_prediction_analysis_tables(conn):
    conn.execute(
        """
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
        """
    )
    columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(prediction_analysis)")
    }
    migrations = {
        "analysis_version": "TEXT DEFAULT ''",
        "stage": "TEXT DEFAULT ''",
        "odds_source": "TEXT DEFAULT ''",
        "categories_json": "TEXT DEFAULT '{}'",
        "decision_json": "TEXT DEFAULT '{}'",
    }
    for column, declaration in migrations.items():
        if column not in columns:
            conn.execute(
                f"ALTER TABLE prediction_analysis ADD COLUMN {column} {declaration}"
            )
    conn.execute(
        """
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
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_analysis_snapshots_match "
        "ON prediction_analysis_snapshots(match_id, id)"
    )
    conn.execute(
        """
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
        """
    )
    candidate_result_columns = {
        str(row[1]) for row in conn.execute(
            "PRAGMA table_info(prediction_candidate_results)"
        )
    }
    if "market_rank" not in candidate_result_columns:
        conn.execute(
            "ALTER TABLE prediction_candidate_results ADD COLUMN market_rank INTEGER"
        )


def save_prediction_analysis(
    match_id, pick, confidence, evidence, candidates, report,
    categories=None, analysis_stage="regular", odds_source="",
    analysis_version=None,
):
    """Freeze every market candidate and the exact pre-kickoff decision path."""
    if not pick or str(analysis_stage or "").startswith("locked"):
        return False
    conn = None
    try:
        conn = sqlite3.connect(str(_local_path("ai_predictions.db")), timeout=30)
        conn.execute("PRAGMA busy_timeout = 30000")
        _ensure_prediction_analysis_tables(conn)
        prediction_row = conn.execute(
            "SELECT actual_result, match_time FROM predictions WHERE match_id = ?",
            (str(match_id),),
        ).fetchone()
        if prediction_row:
            stored_kickoff = _parse_kst_match_time(prediction_row[1])
            if (
                str(prediction_row[0] or "PENDING") != "PENDING"
                or (stored_kickoff and datetime.now(KST) >= stored_kickoff)
            ):
                return False

        target_analysis_version = str(analysis_version or ANALYSIS_VERSION)
        interval = pick.get("probability_interval") or {}
        candidate_rows, compact_categories, decision = build_pick_selection_audit(
            candidates, categories or {"high_probability": pick}, confidence
        )
        evidence_json = json.dumps(evidence or [], ensure_ascii=False, sort_keys=True)
        candidates_json = json.dumps(
            candidate_rows, ensure_ascii=False, sort_keys=True
        )
        categories_json = json.dumps(
            compact_categories, ensure_ascii=False, sort_keys=True
        )
        decision_json = json.dumps(decision, ensure_ascii=False, sort_keys=True)
        fingerprint_payload = "|".join((
            target_analysis_version, str(analysis_stage), str(odds_source or ""),
            evidence_json, candidates_json, categories_json, decision_json,
        ))
        fingerprint = hashlib.sha256(
            fingerprint_payload.encode("utf-8")
        ).hexdigest()

        conn.execute(
            """
            INSERT OR REPLACE INTO prediction_analysis (
                match_id, analysis_version, stage, odds_source,
                selected_market, selected_pick, model_probability,
                fair_probability, edge, confidence, error_margin,
                probability_low, probability_high, evidence_json, markets_json,
                categories_json, decision_json, report_text, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                str(match_id), target_analysis_version, str(analysis_stage),
                str(odds_source or ""), infer_pick_market(pick),
                pick.get("raw_pick"), pick.get("prob"), pick.get("fair_prob"),
                pick.get("edge"), confidence, pick.get("error_margin"),
                interval.get("low"), interval.get("high"), evidence_json,
                candidates_json, categories_json, decision_json, report,
            ),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO prediction_analysis_snapshots (
                match_id, analysis_version, stage, odds_source, confidence,
                selected_market, selected_pick, candidate_count, evidence_json,
                candidates_json, categories_json, decision_json, report_text,
                fingerprint
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(match_id), target_analysis_version, str(analysis_stage),
                str(odds_source or ""), float(confidence or 0),
                infer_pick_market(pick), str(pick.get("raw_pick") or ""),
                len(candidate_rows), evidence_json, candidates_json,
                categories_json, decision_json, str(report or ""), fingerprint,
            ),
        )
        conn.commit()
        return True
    except Exception as error:
        print(f"⚠️ 통합 분석값 DB 저장 실패({match_id}): {error}")
        return False
    finally:
        if conn is not None:
            conn.close()


def annotate_pick_metrics(picks, confidence):
    for pick in picks:
        probability = float(pick.get("prob", 0) or 0)
        market_probability = float(pick.get("market_prob", 0) or 0)
        odd = float(pick.get("odd", 0) or 0)
        edge = probability - market_probability if market_probability > 0 else 0.0
        pick["edge"] = round(edge, 4)
        pick["safe_score"] = round(probability * confidence, 4)
        pick["value_score"] = round(max(0.0, edge) * confidence, 4)
        error_margin = max(0.0, float(pick.get("error_margin", 0) or 0))
        # 역배는 한 번의 추정치가 튀기 쉬워 오차범위의 30%를
        # 먼저 뺀 보수적 확률과 가치로 다시 검증한다.
        robust_probability = max(0.0, probability - (error_margin * 0.30))
        robust_edge = (
            robust_probability - market_probability
            if market_probability > 0 else 0.0
        )
        pick["robust_probability"] = round(robust_probability, 6)
        pick["robust_edge"] = round(robust_edge, 6)
        pick["robust_ev"] = round(robust_probability * odd, 6)
        pick["underdog_gate_version"] = UNDERDOG_GATE_VERSION
        pick["is_qualified_underdog"] = bool(
            pick.get("is_true_underdog")
            and 2.20 <= odd <= 5.00
            and probability >= 0.22
            and robust_edge >= 0.015
            and float(pick.get("robust_ev", 0) or 0) >= 1.02
            and confidence >= 0.55
            and int(pick.get("independent_support_count", 0) or 0) >= 1
        )
        pick["recommendation_score"] = round(
            (probability * 0.72) + (confidence * 0.20) + (min(max(edge, 0.0), 0.15) * 0.55), 4
        )
    return picks


PICK_CATEGORY_LABELS = {
    "high_probability": "최종 추천픽",
    "honey": "배당가치 우수",
    "vip_underdog": "VIP 검증 등급",
}


def _tag_pick_category(pick, category_key):
    """원본 후보를 건드리지 않고 화면용 카테고리 정보를 붙입니다."""
    if not pick:
        return None
    selected = dict(pick)
    selected["category_key"] = category_key
    selected["category_label"] = PICK_CATEGORY_LABELS[category_key]
    return selected


def _pick_possible_wdl_results(pick):
    """Return the regulation-time 1X2 results compatible with a candidate."""
    market = infer_pick_market(pick)
    side = str((pick or {}).get("selection_side") or "").strip().lower()
    all_sides = {"home", "draw", "away"}
    if market == "totals":
        return all_sides
    if market == "1x2":
        return {side} if side in all_sides else all_sides
    if market != "handicap" or side not in all_sides:
        return all_sides

    handicap = (pick or {}).get("handicap_base")
    if handicap is None:
        matched = re.search(
            r"\[\s*([+-]?\d+(?:\.\d+)?)\s*\]",
            str((pick or {}).get("raw_pick") or ""),
        )
        handicap = matched.group(1) if matched else None
    try:
        handicap = float(handicap)
    except (TypeError, ValueError):
        return all_sides

    possible = set()
    for goal_difference in range(-20, 21):
        adjusted = goal_difference + handicap
        handicap_side = (
            "home" if adjusted > 1e-9
            else ("away" if adjusted < -1e-9 else "draw")
        )
        if handicap_side != side:
            continue
        possible.add(
            "home" if goal_difference > 0
            else ("away" if goal_difference < 0 else "draw")
        )
    return possible


def picks_can_coexist(primary, alternative):
    """True only when two different-market picks can win on one final score."""
    if not primary or not alternative:
        return False
    primary_market = infer_pick_market(primary)
    alternative_market = infer_pick_market(alternative)
    if primary_market == alternative_market:
        return False
    if "totals" in {primary_market, alternative_market}:
        return True
    return bool(
        _pick_possible_wdl_results(primary)
        & _pick_possible_wdl_results(alternative)
    )


def select_pick_categories(picks, confidence):
    """Select exactly one official pick; value/VIP are badges on that pick."""
    categories = {
        "high_probability": None,
        "honey": None,
        "vip_underdog": None,
    }
    if not picks:
        return categories, []

    confidence = float(confidence or 0)
    available = [pick for pick in picks if float(pick.get("prob", 0) or 0) > 0]
    if not available:
        return categories, []

    # 실제 가격과 보수적 우위가 검증된 후보가 있으면 그 집합 안에서
    # 적중 가능성(보수확률)을 먼저 비교한다. 배당은 확률을 대신하지 않고,
    # 같은 수준의 후보에서만 우선순위를 가르는 자격조건/동점 기준이다.
    value_edge_floor = 0.01 + max(0.0, 0.65 - confidence) * 0.05
    value_candidates = [
        pick for pick in available
        if pick.get("fair_prob") is not None
        and 1.20 <= float(pick.get("odd", 0) or 0) <= 5.00
        and float(pick.get("robust_edge", 0) or 0) >= value_edge_floor
        and float(pick.get("robust_ev", 0) or 0) >= 1.01
        and confidence >= 0.50
    ]
    selection_pool = value_candidates or available
    high_source = max(
        selection_pool,
        key=lambda pick: (
            float(pick.get("robust_probability", pick.get("prob", 0)) or 0),
            float(pick.get("prob", 0) or 0),
            float(pick.get("robust_edge", 0) or 0),
            float(pick.get("robust_ev", 0) or 0),
            float(pick.get("balanced_score", 0) or 0),
        ),
    )
    high_source = dict(high_source)
    value_qualified = any(
        str(pick.get("raw_pick") or "") == str(high_source.get("raw_pick") or "")
        and infer_pick_market(pick) == infer_pick_market(high_source)
        for pick in value_candidates
    )
    high_source["value_pick_tier"] = "qualified" if value_qualified else "not_qualified"
    high_source["odds_verified"] = bool(
        high_source.get("fair_prob") is not None
        and float(high_source.get("odd", 0) or 0) > 1.0
    )
    high_source["official_final_pick"] = True
    high_source["learning_robot"] = {
        "mode": "controlled_adviser",
        "market": infer_pick_market(high_source),
        "samples": int(high_source.get("market_history_samples", 0) or 0),
        "hit_rate": round(float(high_source.get("market_hit_rate", 0.5) or 0.5), 4),
        "scope": str(high_source.get("market_history_scope") or "global"),
        "influence_weight": round(float(high_source.get("learning_weight", 0) or 0), 4),
        "influence_cap": 0.18,
        "history_rewrite": False,
        "self_modifying": False,
    }

    # 꿀픽과 VIP는 다른 예측이 아니다. 최종 추천픽 하나가 각 기준을
    # 통과했을 때만 동일한 raw_pick에 등급 배지를 붙인다.
    honey_source = high_source if value_qualified else None
    vip_source = None
    if value_qualified:
        vip_passed = bool(
            high_source.get("is_true_underdog")
            and float(high_source.get("robust_edge", 0) or 0) >= 0.03
            and float(high_source.get("robust_ev", 0) or 0) >= 1.08
            and confidence >= 0.68
            and int(high_source.get("independent_support_count", 0) or 0) >= 3
            and 2.20 <= float(high_source.get("odd", 0) or 0) <= 4.50
        )
        if vip_passed:
            vip_source = high_source

    high_source["final_pick_grade"] = (
        "vip" if vip_source else "value" if value_qualified else "standard"
    )

    categories["high_probability"] = _tag_pick_category(high_source, "high_probability")
    categories["honey"] = _tag_pick_category(honey_source, "honey")
    categories["vip_underdog"] = _tag_pick_category(vip_source, "vip_underdog")
    return categories, [categories["high_probability"]]


_PLACEHOLDER_TEAM_NAMES = {
    "", "-", "미정", "미확정", "tbd", "unknown", "홈팀", "원정팀",
    "home", "away", "team1", "team2",
}


def _is_placeholder_team_name(value):
    normalized = re.sub(r"[\s._-]+", "", str(value or "").strip().casefold())
    return normalized in {
        re.sub(r"[\s._-]+", "", name.casefold())
        for name in _PLACEHOLDER_TEAM_NAMES
    }


def _is_placeholder_match(match):
    if not isinstance(match, dict):
        return True
    home = str(match.get("home") or "").strip()
    away = str(match.get("away") or "").strip()
    if _is_placeholder_team_name(home) or _is_placeholder_team_name(away):
        return True
    return re.sub(r"\W+", "", home.casefold()) == re.sub(
        r"\W+", "", away.casefold()
    )


def _build_grading_snapshot():
    """Publish lightweight grading rows so the web never downloads the full DB."""
    conn = None
    try:
        conn = sqlite3.connect(str(_local_path("ai_predictions.db")), timeout=30)
        conn.row_factory = sqlite3.Row
        columns = [row[1] for row in conn.execute("PRAGMA table_info(predictions)")]
        if "ev_pick" not in columns:
            return {"finished": [], "pending": [], "generated_at": _utc_iso()}
        finished = [
            dict(row) for row in conn.execute(
                "SELECT * FROM predictions WHERE actual_result = 'FINISHED'"
            ).fetchall()
        ]
        pending = [
            dict(row) for row in conn.execute(
                "SELECT * FROM predictions WHERE actual_result = 'PENDING' AND is_toto14 = 0"
            ).fetchall()
        ]
        snapshot_versions = {
            str(row["match_id"]): str(row["analysis_version"] or "")
            for row in conn.execute(
                """
                SELECT ps.match_id, ps.analysis_version
                FROM prediction_snapshots ps
                INNER JOIN (
                    SELECT match_id, MAX(id) AS latest_id
                    FROM prediction_snapshots
                    GROUP BY match_id
                ) latest ON latest.latest_id = ps.id
                """
            ).fetchall()
        }
        for row in finished + pending:
            stored_version = str(row.get("analysis_version") or "").strip()
            row["analysis_version"] = stored_version or snapshot_versions.get(
                str(row.get("match_id")), ""
            )

        def sort_timestamp(row):
            parsed = _parse_kst_match_time(row.get("match_time"))
            return parsed.timestamp() if parsed else 0

        finished.sort(key=sort_timestamp, reverse=True)
        pending.sort(key=sort_timestamp, reverse=True)
        return {
            "finished": finished,
            "pending": pending,
            "generated_at": _utc_iso(),
        }
    except Exception as error:
        print(f"⚠️ 채점 스냅샷 생성 실패: {error}")
        return {"finished": [], "pending": [], "generated_at": _utc_iso()}
    finally:
        if conn is not None:
            conn.close()


def _refresh_dashboard_grading_snapshot():
    """Attach the latest grading rows without rebuilding any prediction.

    The score worker runs more often than the heavy analysis worker.  Publishing
    only the refreshed grading block makes a completed match appear in the
    grading note on the same cycle while leaving the pre-kickoff probabilities,
    picks, reports, and version labels untouched.
    """
    dashboard = _read_json("dashboard_data.json", {})
    if not isinstance(dashboard, dict) or not dashboard:
        print("⚠️ 채점 화면 갱신 보류: 기존 대시보드 데이터가 없습니다.")
        return False
    dashboard["grading"] = _build_grading_snapshot()
    source_meta = dashboard.get("source_meta")
    if not isinstance(source_meta, dict):
        source_meta = {}
        dashboard["source_meta"] = source_meta
    source_meta["grading_generated_at"] = _utc_iso()
    _atomic_write_json("dashboard_data.json", dashboard)
    return True


def _valid_three_way_odds(values):
    try:
        return len(values) == 3 and all(float(value or 0) > 1.0 for value in values)
    except (TypeError, ValueError):
        return False


def _world_fixture_datetime(fixture_data):
    fixture = (fixture_data or {}).get("fixture", {}) or {}
    try:
        timestamp = int(fixture.get("timestamp") or 0)
    except (TypeError, ValueError):
        timestamp = 0
    if timestamp > 0:
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(KST)
        except (OSError, OverflowError, ValueError):
            pass
    raw_date = str(fixture.get("date") or "").strip()
    if raw_date:
        try:
            parsed = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(KST)
        except ValueError:
            pass
    return None


def _world_fixture_candidate(fixture_data, now=None):
    """Turn one provider fixture into a verified WORLD shadow-schedule row."""
    now = (now or datetime.now(KST)).astimezone(KST)
    fixture_data = fixture_data if isinstance(fixture_data, dict) else {}
    fixture = fixture_data.get("fixture", {}) or {}
    league = fixture_data.get("league", {}) or {}
    teams = fixture_data.get("teams", {}) or {}
    home = teams.get("home", {}) or {}
    away = teams.get("away", {}) or {}
    status = fixture.get("status", {}) or {}

    try:
        league_id = int(league.get("id") or 0)
    except (TypeError, ValueError):
        league_id = 0
    if league_id <= 0:
        return None, "INVALID_LEAGUE_ID"

    try:
        fixture_id = int(fixture.get("id") or 0)
    except (TypeError, ValueError):
        fixture_id = 0
    if fixture_id <= 0:
        return None, "INVALID_FIXTURE_ID"

    try:
        home_id = int(home.get("id") or 0)
        away_id = int(away.get("id") or 0)
    except (TypeError, ValueError):
        home_id, away_id = 0, 0
    if home_id <= 0 or away_id <= 0:
        return None, "INVALID_TEAM_ID"
    if home_id == away_id:
        return None, "DUPLICATE_TEAM_ID"

    home_name = str(home.get("name") or "").strip()
    away_name = str(away.get("name") or "").strip()
    tbd_names = {"", "tbd", "to be defined", "winner", "unknown"}
    if home_name.casefold() in tbd_names or away_name.casefold() in tbd_names:
        return None, "TEAM_TBD"

    kickoff = _world_fixture_datetime(fixture_data)
    if kickoff is None:
        return None, "KICKOFF_TBD"

    status_short = str(status.get("short") or "NS").upper().strip()
    if status_short == "TBD":
        return None, "KICKOFF_TBD"
    if status_short in CANCELED_STATUSES | POSTPONED_STATUSES | {"SUSP", "INT"}:
        return None, "CANCELED_OR_POSTPONED"
    if status_short in LIVE_STATUSES | TERMINAL_STATUSES or kickoff <= now:
        return None, "ALREADY_STARTED"
    if status_short != "NS":
        return None, "UNSUPPORTED_STATUS"

    weekday = "월화수목금토일"[kickoff.weekday()]
    match_time = kickoff.strftime(f"%y.%m.%d ({weekday}) %H:%M")
    league_config = WORLD_LEAGUES.get(league_id, {})
    league_display_name = str(
        league_config.get("name") or league.get("name") or "세계 축구"
    )
    fixture_meta = fixture_data.get("fixture", {}) or {}
    venue = fixture_meta.get("venue", {}) or {}
    match = {
        "id": f"WORLD_{fixture_id}",
        "fixture_id": fixture_id,
        "league_id": league_id,
        "season": league.get("season"),
        "league": str(league.get("name") or league_display_name),
        "league_name_ko": league_display_name,
        "country": str(league.get("country") or ""),
        "round": str(league.get("round") or ""),
        "home": home_name,
        "away": away_name,
        "home_name_ko": _world_team_display_name(home_name, home_id),
        "away_name_ko": _world_team_display_name(away_name, away_id),
        "home_team_id": home_id,
        "away_team_id": away_id,
        "home_logo": str(home.get("logo") or ""),
        "away_logo": str(away.get("logo") or ""),
        "city": str(venue.get("city") or ""),
        "referee": str(fixture_meta.get("referee") or ""),
        "match_time": match_time,
        "kickoff_at": kickoff.isoformat(),
        "status": status_short,
    }
    return {
        "source_type": "WORLD",
        "visibility_status": "SHADOW",
        "analysis_status": "PENDING_SHADOW_ANALYSIS",
        "pick_status": "NOT_ANALYZED",
        "identity_verified": True,
        "match": match,
        "api_fixture_id": fixture_id,
        "final_match_time": match_time,
        "timestamp": kickoff.timestamp(),
    }, None


def _has_valid_world_market(snapshot):
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    return bool(
        int(snapshot.get("bookmaker_count") or 0) > 0
        and any(snapshot.get(key) for key in ("1x2", "totals", "handicap"))
    )


def build_world_schedule_payload(
    fixtures_by_date, now=None, market_snapshots_by_fixture=None
):
    """Build the isolated schedule; production targets only bettable fixtures."""
    now = (now or datetime.now(KST)).astimezone(KST)
    market_filter_enabled = market_snapshots_by_fixture is not None
    market_snapshots_by_fixture = market_snapshots_by_fixture or {}
    matches = []
    seen_fixture_ids = set()
    rejected_counts = {key: 0 for key in WORLD_REJECTION_LABELS}
    rejected_samples = []
    raw_count = 0

    for date_key, fixtures in (fixtures_by_date or {}).items():
        for fixture_data in fixtures or []:
            raw_count += 1
            item, reason = _world_fixture_candidate(fixture_data, now=now)
            if item is not None:
                fixture_id = int(item.get("api_fixture_id") or 0)
                market_snapshot = (
                    market_snapshots_by_fixture.get(fixture_id)
                    or market_snapshots_by_fixture.get(str(fixture_id))
                )
                if market_filter_enabled and not _has_valid_world_market(market_snapshot):
                    reason = "NO_VALID_MARKET"
                    item = None
                elif fixture_id in seen_fixture_ids:
                    reason = "DUPLICATE_FIXTURE"
                    item = None
                else:
                    seen_fixture_ids.add(fixture_id)
                    if _has_valid_world_market(market_snapshot):
                        item["market_snapshot"] = dict(market_snapshot)
            if item is not None:
                matches.append(item)
                continue

            reason = reason or "INVALID_FIXTURE_ID"
            rejected_counts[reason] = int(rejected_counts.get(reason, 0)) + 1
            if len(rejected_samples) < 30:
                fixture_data = fixture_data if isinstance(fixture_data, dict) else {}
                rejected_samples.append({
                    "date": str(date_key),
                    "fixture_id": (fixture_data.get("fixture", {}) or {}).get("id"),
                    "league": (fixture_data.get("league", {}) or {}).get("name"),
                    "home": ((fixture_data.get("teams", {}) or {}).get("home", {}) or {}).get("name"),
                    "away": ((fixture_data.get("teams", {}) or {}).get("away", {}) or {}).get("name"),
                    "reason_code": reason,
                    "reason": WORLD_REJECTION_LABELS.get(reason, reason),
                })

    matches.sort(key=lambda item: (float(item.get("timestamp") or 0), int(item.get("api_fixture_id") or 0)))
    if len(matches) > WORLD_MAX_SCHEDULE_MATCHES:
        overflow = len(matches) - WORLD_MAX_SCHEDULE_MATCHES
        rejected_counts["SCHEDULE_CAP"] += overflow
        matches = matches[:WORLD_MAX_SCHEDULE_MATCHES]

    league_counts = {}
    for item in matches:
        league_name = item.get("match", {}).get("league_name_ko") or item.get("match", {}).get("league")
        league_counts[str(league_name)] = int(league_counts.get(str(league_name), 0)) + 1

    rejected_counts = {
        key: value for key, value in rejected_counts.items() if int(value or 0) > 0
    }
    payload = {
        "schema_version": "world-schedule.v1",
        "source_type": "WORLD",
        "stage": "COLLECTION_ONLY",
        "default_visibility_status": "SHADOW",
        "generated_at": now.isoformat(),
        "scheduled_dates": list((fixtures_by_date or {}).keys()),
        "matches": matches,
        "rejected_summary": [
            {
                "reason_code": key,
                "reason": WORLD_REJECTION_LABELS.get(key, key),
                "count": int(value),
            }
            for key, value in sorted(rejected_counts.items())
        ],
        "rejected_samples": rejected_samples,
        "source_meta": {
            "raw_fixture_count": raw_count,
            "eligible_shadow_count": len(matches),
            "rejected_count": sum(rejected_counts.values()),
            "league_counts": league_counts,
            "selection_rule": "valid_fulltime_bookmaker_market",
            "market_filter_enabled": market_filter_enabled,
            "bettable_fixture_count": len(matches),
            "preferred_league_ids": sorted(WORLD_LEAGUES),
            "schedule_days": WORLD_SCHEDULE_DAYS,
            "analysis_started": False,
            "public_count": 0,
            "api_usage": get_api_usage_status(),
        },
    }
    return _refresh_world_source_meta(payload)


def collect_world_schedule():
    """Fetch fixtures plus batched odds and preserve the last good file on failure."""
    now = datetime.now(KST)
    previous_payload = _read_json(WORLD_DASHBOARD_FILE, {})
    fixtures_by_date = {}
    market_snapshots_by_fixture = {}
    for day_offset in range(WORLD_SCHEDULE_DAYS):
        date_key = (now + timedelta(days=day_offset)).strftime("%Y-%m-%d")
        fixtures = _fetch_date_fixtures_api(date_key, ttl_h=2, purpose="world")
        if fixtures is None:
            print(f"❌ 세계경기 일정 수집 실패({date_key}) - 마지막 정상본을 유지합니다.")
            return False
        fixtures_by_date[date_key] = fixtures
        date_markets = _fetch_world_market_snapshots_by_date(date_key)
        if date_markets is None:
            print(
                f"❌ 세계경기 배당 목록 수집 실패({date_key}) - "
                "마지막 정상본을 유지합니다."
            )
            return False
        market_snapshots_by_fixture.update(date_markets)

    payload = build_world_schedule_payload(
        fixtures_by_date,
        now=now,
        market_snapshots_by_fixture=market_snapshots_by_fixture,
    )
    _carry_world_shadow_analyses(payload, previous_payload)
    _atomic_write_json(WORLD_DASHBOARD_FILE, payload, indent=2)
    source_meta = payload.get("source_meta", {})
    print(
        "🌍 세계경기 1단계 수집 완료: "
        f"수집 목록 {source_meta.get('raw_fixture_count', 0)} / "
        f"배당 확인 {source_meta.get('eligible_shadow_count', 0)} / "
        f"제외 {source_meta.get('rejected_count', 0)}"
    )
    return True


WORLD_ANALYSIS_FIELDS = (
    "analysis_status", "pick_status", "analysis_version", "system_version", "analysis_stage",
    "analyzed_at", "frozen_at", "data_quality_score", "data_quality_grade",
    "missing_data", "lineup_confirmed", "lineup_attempts", "analysis",
)


def _carry_world_shadow_analyses(payload, previous_payload):
    """Keep pre-kickoff analyses across the six-hour schedule refresh."""
    if not isinstance(payload, dict) or not isinstance(previous_payload, dict):
        return payload
    previous_by_fixture = {
        int(item.get("api_fixture_id") or 0): item
        for item in previous_payload.get("matches", []) or []
        if isinstance(item, dict) and int(item.get("api_fixture_id") or 0) > 0
    }
    for item in payload.get("matches", []) or []:
        previous = previous_by_fixture.get(int(item.get("api_fixture_id") or 0))
        if not previous:
            continue
        old_kickoff = str((previous.get("match") or {}).get("kickoff_at") or "")
        new_kickoff = str((item.get("match") or {}).get("kickoff_at") or "")
        if old_kickoff and new_kickoff and old_kickoff != new_kickoff:
            item["analysis_status"] = "PENDING_SHADOW_ANALYSIS"
            item["schedule_changed_after_analysis"] = True
            continue
        for field in WORLD_ANALYSIS_FIELDS:
            if field in previous:
                item[field] = previous[field]
    _refresh_world_source_meta(payload)
    return payload


def _world_median(values):
    ordered = []
    for value in values or []:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number > 1.0 and math.isfinite(number):
            ordered.append(number)
    ordered.sort()
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[middle], 3)
    return round((ordered[middle - 1] + ordered[middle]) / 2.0, 3)


def _world_market_snapshot_from_response(odds_rows, fixture_id=0, fetched_at=None):
    """Extract comparable median 1X2, totals and three-way handicap prices."""
    wdl_samples = {"home": [], "draw": [], "away": []}
    totals_by_line = {}
    handicap_by_line = {}
    bookmaker_ids = set()

    def number_from(value):
        matched = re.search(r"([+-]?\d+(?:\.\d+)?)", str(value or ""))
        try:
            return float(matched.group(1)) if matched else None
        except (TypeError, ValueError):
            return None

    def is_fulltime_market(bet_name, base_names):
        """Reject half/team/corner props that reuse over-under or handicap words."""
        name = str(bet_name or "").strip().casefold()
        non_fulltime_markers = (
            "first half", "1st half", "first-half", "1st-half",
            "second half", "2nd half", "second-half", "2nd-half",
            "half time", "halftime", "half-time",
            "home team", "away team", "team total",
            "corner", "booking", "card", "throw-in", "offside",
        )
        if any(marker in name for marker in non_fulltime_markers):
            return False
        return any(
            name == base
            or name.startswith(f"{base} ")
            or name.startswith(f"{base} -")
            for base in base_names
        )

    for odds_row in odds_rows or []:
        for bookmaker in (odds_row or {}).get("bookmakers", []) or []:
            bookmaker_ids.add(str(bookmaker.get("id") or bookmaker.get("name") or len(bookmaker_ids)))
            for bet in bookmaker.get("bets", []) or []:
                bet_name = str(bet.get("name") or "").strip().casefold()
                values = bet.get("values", []) or []
                if bet_name in {"match winner", "1x2", "fulltime result", "full time result"}:
                    found = {}
                    for value in values:
                        label = str(value.get("value") or "").strip().casefold()
                        side = {
                            "home": "home", "1": "home", "draw": "draw", "x": "draw",
                            "away": "away", "2": "away",
                        }.get(label)
                        try:
                            odd = float(value.get("odd") or 0)
                        except (TypeError, ValueError):
                            odd = 0.0
                        if side and odd > 1.0:
                            found[side] = odd
                    if len(found) == 3:
                        for side, odd in found.items():
                            wdl_samples[side].append(odd)
                    continue

                if is_fulltime_market(
                    bet_name,
                    ("goals over/under", "goals over under", "total goals"),
                ):
                    found_by_line = {}
                    for value in values:
                        label = str(value.get("value") or "").strip().casefold()
                        side = "under" if "under" in label else ("over" if "over" in label else "")
                        line = number_from(label)
                        try:
                            odd = float(value.get("odd") or 0)
                        except (TypeError, ValueError):
                            odd = 0.0
                        if side and line is not None and odd > 1.0:
                            found_by_line.setdefault(round(line, 2), {})[side] = odd
                    for line, found in found_by_line.items():
                        if {"under", "over"}.issubset(found):
                            target = totals_by_line.setdefault(line, {"under": [], "over": []})
                            target["under"].append(found["under"])
                            target["over"].append(found["over"])
                    continue

                if not is_fulltime_market(
                    bet_name,
                    ("handicap result", "3 way handicap", "european handicap"),
                ):
                    continue
                # ``3 Way Handicap``의 3은 시장 종류(3방향)이지 실제 기준선이 아니다.
                # 시장명에서 이 고정 문구를 먼저 지워 +3.0 가짜 선으로 해석하지 않는다.
                handicap_name_without_market_count = re.sub(
                    r"\b3\s*way\b", "", bet_name, flags=re.IGNORECASE
                )
                bet_line = number_from(handicap_name_without_market_count)
                label_lines = {
                    round(float(label_line), 2)
                    for value in values
                    for label_line in [number_from(value.get("value"))]
                    if label_line is not None
                }
                shared_line = bet_line
                if shared_line is None and len(label_lines) == 1:
                    shared_line = next(iter(label_lines))
                found_by_line = {}
                for value in values:
                    label = str(value.get("value") or "").strip().casefold()
                    side = (
                        "home" if label.startswith("home") or label == "1"
                        else ("draw" if label.startswith("draw") or label == "x"
                              else ("away" if label.startswith("away") or label == "2" else ""))
                    )
                    label_line = number_from(label)
                    line = label_line if label_line is not None else shared_line
                    try:
                        odd = float(value.get("odd") or 0)
                    except (TypeError, ValueError):
                        odd = 0.0
                    if side and line is not None and odd > 1.0:
                        found_by_line.setdefault(round(float(line), 2), {})[side] = odd
                for line, found in found_by_line.items():
                    if not {"home", "draw", "away"}.issubset(found):
                        continue
                    target = handicap_by_line.setdefault(
                        line, {"home": [], "draw": [], "away": []}
                    )
                    for side in target:
                        target[side].append(found[side])

    result = {
        "fixture_id": int(fixture_id or 0),
        "fetched_at": fetched_at or datetime.now(KST).isoformat(),
        "bookmaker_count": len(bookmaker_ids),
        "odds_source": "api_football_bookmaker_median",
        "1x2": None,
        "totals": None,
        "handicap": None,
    }
    if all(wdl_samples[side] for side in wdl_samples):
        result["1x2"] = {
            side: _world_median(wdl_samples[side]) for side in ("home", "draw", "away")
        }
        result["1x2"]["sample_count"] = min(
            len(wdl_samples[side]) for side in ("home", "draw", "away")
        )
    complete_totals = [
        (line, values) for line, values in totals_by_line.items()
        if values["under"] and values["over"]
    ]
    if complete_totals:
        def totals_mainline_key(row):
            line, values = row
            under = _world_median(values["under"])
            over = _world_median(values["over"])
            inverse_total = (1.0 / under) + (1.0 / over)
            balance = abs((1.0 / under) / inverse_total - (1.0 / over) / inverse_total)
            coverage = min(len(values["under"]), len(values["over"]))
            return (round(balance, 6), abs(float(line) - 2.5), -coverage)

        line, values = min(
            complete_totals,
            key=totals_mainline_key,
        )
        under = _world_median(values["under"])
        over = _world_median(values["over"])
        inverse_total = (1.0 / under) + (1.0 / over)
        result["totals"] = {
            "line": float(line),
            "under": under,
            "over": over,
            "sample_count": min(len(values["under"]), len(values["over"])),
            "market_balance": round(
                abs((1.0 / under) / inverse_total - (1.0 / over) / inverse_total), 4
            ),
            "selection_method": "balanced_mainline",
        }
    complete_handicaps = [
        (line, values) for line, values in handicap_by_line.items()
        if all(values[side] for side in ("home", "draw", "away"))
        and abs(float(line)) >= 0.25
    ]
    if complete_handicaps:
        def handicap_mainline_key(row):
            line, values = row
            medians = {
                side: _world_median(values[side])
                for side in ("home", "draw", "away")
            }
            inverse_total = sum(1.0 / medians[side] for side in medians)
            probabilities = [(1.0 / medians[side]) / inverse_total for side in medians]
            balance = max(probabilities) - min(probabilities)
            coverage = min(len(values[side]) for side in medians)
            return (round(balance, 6), abs(abs(float(line)) - 1.0), -coverage)

        line, values = min(
            complete_handicaps,
            key=handicap_mainline_key,
        )
        medians = {
            side: _world_median(values[side])
            for side in ("home", "draw", "away")
        }
        inverse_total = sum(1.0 / medians[side] for side in medians)
        probabilities = [(1.0 / medians[side]) / inverse_total for side in medians]
        result["handicap"] = {
            "line": float(line),
            "home": medians["home"],
            "draw": medians["draw"],
            "away": medians["away"],
            "sample_count": min(
                len(values[side]) for side in ("home", "draw", "away")
            ),
            "market_balance": round(max(probabilities) - min(probabilities), 4),
            "selection_method": "balanced_mainline",
        }
    result["available_markets"] = [
        market for market in ("1x2", "totals", "handicap") if result.get(market)
    ]
    return result


def _world_market_map_from_batch_cache(payload):
    snapshots = (payload or {}).get("snapshots") if isinstance(payload, dict) else None
    if not isinstance(snapshots, dict):
        return None
    normalized = {}
    for fixture_id, snapshot in snapshots.items():
        try:
            fixture_key = int(fixture_id)
        except (TypeError, ValueError):
            continue
        if fixture_key > 0 and _has_valid_world_market(snapshot):
            normalized[fixture_key] = dict(snapshot)
    return normalized


def _fetch_world_market_snapshots_by_date(date_key):
    """Fetch bookmaker-covered fixtures by date, page once, and cache as a batch."""
    date_key = str(date_key or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_key):
        return None
    cache_key = f"world_odds_by_date_v1_{date_key}"
    cached_payload = get_db_cache(cache_key, 2)
    cached = _world_market_map_from_batch_cache(cached_payload)
    if cached is not None:
        return cached
    stale_payload = get_db_cache(cache_key, 24)
    stale = _world_market_map_from_batch_cache(stale_payload)
    grouped_rows = {}
    page = 1
    try:
        while page <= WORLD_ODDS_MAX_PAGES_PER_DAY:
            response = api_get(
                "/odds",
                params={
                    "date": date_key,
                    "timezone": "Asia/Seoul",
                    "page": page,
                },
                timeout=15,
                purpose="world",
            )
            payload = response.json() if response.status_code == 200 else {}
            if response.status_code != 200 or payload.get("errors"):
                raise RuntimeError(
                    f"odds HTTP {response.status_code}: {payload.get('errors')}"
                )
            for odds_row in payload.get("response", []) or []:
                try:
                    fixture_id = int(
                        ((odds_row or {}).get("fixture") or {}).get("id") or 0
                    )
                except (TypeError, ValueError):
                    fixture_id = 0
                if fixture_id > 0:
                    grouped_rows.setdefault(fixture_id, []).append(odds_row)

            paging = payload.get("paging") or {}
            current_page = int(paging.get("current") or page)
            total_pages = int(paging.get("total") or current_page)
            if current_page >= total_pages:
                break
            if page >= WORLD_ODDS_MAX_PAGES_PER_DAY:
                raise RuntimeError(
                    f"odds paging safety cap {WORLD_ODDS_MAX_PAGES_PER_DAY}/{total_pages}"
                )
            page = current_page + 1

        fetched_at = datetime.now(KST).isoformat()
        snapshots = {}
        for fixture_id, odds_rows in grouped_rows.items():
            snapshot = _world_market_snapshot_from_response(
                odds_rows, fixture_id=fixture_id, fetched_at=fetched_at
            )
            if _has_valid_world_market(snapshot):
                snapshots[fixture_id] = snapshot
        set_db_cache(
            cache_key,
            {
                "date": date_key,
                "fetched_at": fetched_at,
                "pages": page,
                "snapshots": {str(key): value for key, value in snapshots.items()},
            },
        )
        return snapshots
    except Exception as error:
        print(f"⚠️ 세계경기 날짜별 배당 조회 실패({date_key}): {error}")
        return stale


def fetch_world_market_snapshot(fixture_id, diff_hours):
    fixture_id = int(fixture_id or 0)
    if fixture_id <= 0:
        return None
    ttl_h = 4.0 if diff_hours > 3 else (0.5 if diff_hours > 1 else 0.2)
    # v1은 3 Way 숫자 오인, v2는 전반전 총득점 시장이 섞일 수 있었다.
    # 정규시간 시장 범위를 고친 뒤 새 세대 키로 실제 배당을 다시 받는다.
    cache_key = f"world_market_v3_{fixture_id}"
    cached = get_db_cache(cache_key, ttl_h)
    if isinstance(cached, dict):
        return cached
    stale = get_db_cache(cache_key, 24)
    try:
        response = api_get("/odds", params={"fixture": fixture_id}, timeout=10)
        payload = response.json() if response.status_code == 200 else {}
        if response.status_code != 200 or payload.get("errors"):
            raise RuntimeError(f"odds HTTP {response.status_code}: {payload.get('errors')}")
        snapshot = _world_market_snapshot_from_response(
            payload.get("response", []), fixture_id=fixture_id
        )
        set_db_cache(cache_key, snapshot)
        return snapshot
    except Exception as error:
        print(f"⚠️ 세계경기 배당 조회 실패({fixture_id}): {error}")
        if isinstance(stale, dict):
            preserved = dict(stale)
            preserved["stale"] = True
            return preserved
        return None


def _world_market_snapshot_for_analysis(item, fixture_id, diff_hours, now=None):
    """Reuse the batch quote while fresh; later stages still refresh movement."""
    now = (now or datetime.now(KST)).astimezone(KST)
    ttl_h = 4.0 if diff_hours > 3 else (0.5 if diff_hours > 1 else 0.2)
    preloaded = (item or {}).get("market_snapshot")
    if _has_valid_world_market(preloaded):
        try:
            fetched_at = datetime.fromisoformat(
                str(preloaded.get("fetched_at") or "").replace("Z", "+00:00")
            )
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=KST)
            age_hours = (
                now - fetched_at.astimezone(KST)
            ).total_seconds() / 3600.0
        except (TypeError, ValueError):
            age_hours = ttl_h + 1.0
        if age_hours <= ttl_h:
            return dict(preloaded)
    return fetch_world_market_snapshot(fixture_id, diff_hours) or {}


def _ensure_odds_movement_table(conn):
    """Persist genuine time-series snapshots without making extra API calls."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS odds_movement_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fixture_id INTEGER NOT NULL,
            source_type TEXT NOT NULL,
            analysis_stage TEXT DEFAULT '',
            odds_json TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(fixture_id, source_type, fingerprint)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_odds_movement_fixture "
        "ON odds_movement_snapshots(fixture_id, source_type, id)"
    )


def _movement_market_probabilities(market_name, market):
    market = market if isinstance(market, dict) else {}
    sides = {
        "1x2": ("home", "draw", "away"),
        "totals": ("under", "over"),
        "handicap": ("home", "draw", "away"),
    }.get(str(market_name), ())
    try:
        odds = [float(market.get(side) or 0) for side in sides]
    except (TypeError, ValueError):
        return {}, []
    if not sides or not all(odd > 1.0 for odd in odds):
        return {}, []
    probabilities = normalize_probabilities([1.0 / odd for odd in odds])
    return dict(zip(sides, probabilities)), odds


def _detect_odds_movement(
    previous, current, opening=None, elapsed_hours=None,
    opening_elapsed_hours=None, analysis_stage="",
):
    """Audit broad bookmaker movement without inventing betting-volume data.

    Price direction, bookmaker breadth, persistence from the first captured
    quote, speed and cross-market agreement are evaluated from already cached
    snapshots.  True reverse-line movement still requires public betting-share
    data, so it is never claimed from prices alone.
    """
    previous = previous if isinstance(previous, dict) else {}
    current = current if isinstance(current, dict) else {}
    opening = opening if isinstance(opening, dict) else {}
    result = {
        "has_history": bool(previous),
        "has_opening": bool(opening),
        "qualified": False,
        "summary": "",
        "signals": [],
        "line_movements": [],
        "cross_market_agreement": False,
        "agreement_sides": [],
        "underdog_move": False,
        "movement_speed_available": bool(elapsed_hours and elapsed_hours > 0),
        "analysis_stage": str(analysis_stage or ""),
        "timing": "late" if str(analysis_stage or "") in {"T-60-lineup", "T-30-final"} else "regular",
        "reverse_line_movement_confirmed": False,
        "reverse_line_reason": "공개 베팅 비중 자료가 없어 배당 움직임만으로 역행배당을 단정하지 않음",
        "home_bonus": 0.0,
        "away_bonus": 0.0,
    }
    labels = {
        ("1x2", "home"): "홈승",
        ("1x2", "draw"): "무승부",
        ("1x2", "away"): "원정승",
        ("totals", "under"): "언더",
        ("totals", "over"): "오버",
        ("handicap", "home"): "핸디승",
        ("handicap", "draw"): "핸디무",
        ("handicap", "away"): "핸디패",
    }
    summaries = []
    directional_markets = {"home": set(), "away": set()}
    opening_wdl = opening.get("1x2") or previous.get("1x2") or {}
    try:
        underdog_side = (
            "home" if float(opening_wdl.get("home") or 0) > float(opening_wdl.get("away") or 0)
            else ("away" if float(opening_wdl.get("away") or 0) > float(opening_wdl.get("home") or 0) else "")
        )
    except (TypeError, ValueError):
        underdog_side = ""
    for market_name in ("1x2", "totals", "handicap"):
        old_market = previous.get(market_name) or {}
        new_market = current.get(market_name) or {}
        if market_name in {"totals", "handicap"}:
            try:
                old_line = float(old_market.get("line"))
                new_line = float(new_market.get("line"))
                line_shift = new_line - old_line
                if abs(line_shift) > 1e-9:
                    old_count = int(old_market.get("sample_count") or previous.get("bookmaker_count") or 0)
                    new_count = int(new_market.get("sample_count") or current.get("bookmaker_count") or 0)
                    if min(old_count, new_count) >= 3 and abs(line_shift) >= 0.25:
                        if market_name == "totals":
                            line_side = "over" if line_shift > 0 else "under"
                            line_label = "오버" if line_shift > 0 else "언더"
                        else:
                            line_side = "home" if line_shift < 0 else "away"
                            line_label = "홈 방향" if line_shift < 0 else "원정 방향"
                            directional_markets[line_side].add("handicap_line")
                        line_signal = {
                            "market": market_name,
                            "side": line_side,
                            "previous_line": old_line,
                            "current_line": new_line,
                            "bookmaker_sample_count": min(old_count, new_count),
                        }
                        result["line_movements"].append(line_signal)
                        summaries.append(
                            f"{line_label} 기준선 {old_line:g}→{new_line:g} "
                            f"({line_signal['bookmaker_sample_count']}개 업체)"
                        )
                    continue
            except (TypeError, ValueError):
                continue
        old_count = int(old_market.get("sample_count") or previous.get("bookmaker_count") or 0)
        new_count = int(new_market.get("sample_count") or current.get("bookmaker_count") or 0)
        if min(old_count, new_count) < 3:
            continue
        old_probs, old_odds = _movement_market_probabilities(market_name, old_market)
        new_probs, new_odds = _movement_market_probabilities(market_name, new_market)
        if not old_probs or not new_probs:
            continue
        opening_market = opening.get(market_name) or {}
        opening_probs, opening_odds = _movement_market_probabilities(
            market_name, opening_market
        )
        for index, side in enumerate(old_probs):
            probability_shift = float(new_probs[side]) - float(old_probs[side])
            odds_drop = float(old_odds[index]) - float(new_odds[index])
            # Both price and margin-free implied probability must move.  A
            # 1.8%p move plus a 0.10 price drop is large enough to track while
            # routine rounding noise is ignored.
            if probability_shift < 0.018 or odds_drop < 0.10:
                continue
            persistent = False
            opening_shift = None
            if opening_probs and side in opening_probs:
                opening_index = list(opening_probs).index(side)
                opening_shift = float(new_probs[side]) - float(opening_probs[side])
                opening_drop = float(opening_odds[opening_index]) - float(new_odds[index])
                persistent = opening_shift >= 0.018 and opening_drop >= 0.10
            speed_pph = (
                probability_shift / float(elapsed_hours)
                if elapsed_hours and float(elapsed_hours) > 0 else None
            )
            strength = "강한" if probability_shift >= 0.04 else "유의미한"
            signal = {
                "market": market_name,
                "side": side,
                "label": labels.get((market_name, side), side),
                "previous_odd": round(float(old_odds[index]), 3),
                "current_odd": round(float(new_odds[index]), 3),
                "implied_probability_shift": round(probability_shift, 4),
                "bookmaker_sample_count": min(old_count, new_count),
                "strength": strength,
                "persistent_from_opening": persistent,
                "opening_probability_shift": (
                    round(opening_shift, 4) if opening_shift is not None else None
                ),
                "probability_shift_per_hour": (
                    round(speed_pph, 4) if speed_pph is not None else None
                ),
            }
            result["signals"].append(signal)
            if market_name in {"1x2", "handicap"} and side in {"home", "away"}:
                directional_markets[side].add(market_name)
            if market_name == "1x2" and side == underdog_side:
                result["underdog_move"] = True
            speed_text = (
                f", 시간당 {speed_pph * 100:+.1f}%p"
                if speed_pph is not None else ""
            )
            persistence_text = ", 시초 대비 지속" if persistent else ""
            summaries.append(
                f"{signal['label']} {signal['previous_odd']:.2f}→{signal['current_odd']:.2f} "
                f"({probability_shift * 100:+.1f}%p{speed_text}, "
                f"{signal['bookmaker_sample_count']}개 업체 중앙값{persistence_text})"
            )
            if market_name == "1x2" and side in {"home", "away"}:
                base_bonus = 0.015 + probability_shift * 0.4
                if persistent:
                    base_bonus += 0.005
                if speed_pph is not None and speed_pph >= 0.01:
                    base_bonus += 0.003
                result[f"{side}_bonus"] = max(
                    float(result[f"{side}_bonus"]), min(0.05, base_bonus)
                )

    agreement_sides = [
        side for side, markets in directional_markets.items()
        if "1x2" in markets and ({"handicap", "handicap_line"} & markets)
    ]
    result["agreement_sides"] = agreement_sides
    result["cross_market_agreement"] = bool(agreement_sides)
    for side in agreement_sides:
        result[f"{side}_bonus"] = min(0.05, float(result[f"{side}_bonus"]) + 0.005)
    if agreement_sides:
        summaries.append(
            "승무패·핸디캡 같은 방향 확인(" + ", ".join(
                "홈" if side == "home" else "원정" for side in agreement_sides
            ) + ")"
        )
    if result["underdog_move"]:
        summaries.append("초기 열세팀 배당 수축 확인")
    result["qualified"] = bool(result["signals"] or result["line_movements"])
    result["summary"] = " · ".join(summaries)
    return result


def capture_odds_movement(fixture_id, source_type, analysis_stage, snapshot):
    """Store the current quote and return movement versus the last changed quote."""
    fixture_id = int(fixture_id or 0)
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    if fixture_id <= 0 or not any(snapshot.get(key) for key in ("1x2", "totals", "handicap")):
        return _detect_odds_movement({}, snapshot)
    normalized = {
        key: snapshot.get(key)
        for key in ("bookmaker_count", "1x2", "totals", "handicap")
        if snapshot.get(key) is not None
    }
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    conn = sqlite3.connect(str(_local_path("ai_predictions.db")), timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        _ensure_odds_movement_table(conn)
        previous_row = conn.execute(
            """
            SELECT odds_json, captured_at FROM odds_movement_snapshots
            WHERE fixture_id = ? AND source_type = ? AND fingerprint != ?
            ORDER BY id DESC LIMIT 1
            """,
            (fixture_id, str(source_type), fingerprint),
        ).fetchone()
        opening_row = conn.execute(
            """
            SELECT odds_json, captured_at FROM odds_movement_snapshots
            WHERE fixture_id = ? AND source_type = ?
            ORDER BY id ASC LIMIT 1
            """,
            (fixture_id, str(source_type)),
        ).fetchone()
        try:
            previous = json.loads(previous_row[0]) if previous_row else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            previous = {}
        try:
            opening = json.loads(opening_row[0]) if opening_row else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            opening = {}

        def elapsed_since(row):
            if not row or not row[1]:
                return None
            try:
                captured = datetime.fromisoformat(str(row[1]).replace("Z", "+00:00"))
                if captured.tzinfo is None:
                    captured = captured.replace(tzinfo=timezone.utc)
                return max(
                    1.0 / 60.0,
                    (datetime.now(timezone.utc) - captured.astimezone(timezone.utc)).total_seconds() / 3600.0,
                )
            except (TypeError, ValueError):
                return None

        movement = _detect_odds_movement(
            previous, normalized, opening=opening,
            elapsed_hours=elapsed_since(previous_row),
            opening_elapsed_hours=elapsed_since(opening_row),
            analysis_stage=analysis_stage,
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO odds_movement_snapshots (
                fixture_id, source_type, analysis_stage, odds_json, fingerprint
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (fixture_id, str(source_type), str(analysis_stage or ""), encoded, fingerprint),
        )
        conn.commit()
        return movement
    except Exception as error:
        print(f"⚠️ 시간대별 해외배당 기록 실패({fixture_id}): {error}")
        return _detect_odds_movement({}, snapshot)
    finally:
        conn.close()


def fetch_world_injuries_snapshot(fixture_id, home_id, away_id, league_id, season, ttl_h):
    fixture_id = int(fixture_id or 0)
    team_ids = {int(home_id or 0), int(away_id or 0)}
    default = {
        str(team_id): {
            "count": 0, "ace_missing": False, "ace_names": [],
            "missing_goals": 0, "available": False, "source": "none",
        }
        for team_id in team_ids if team_id > 0
    }
    if fixture_id <= 0 or len(default) != 2:
        return default
    cache_key = f"world_injuries_v1_{fixture_id}"
    cached = get_db_cache(cache_key, ttl_h)
    if isinstance(cached, dict):
        return cached
    stale = get_db_cache(cache_key, max(24, ttl_h))
    try:
        response = api_get("/injuries", params={"fixture": fixture_id}, timeout=10)
        payload = response.json() if response.status_code == 200 else {}
        if response.status_code != 200 or payload.get("errors"):
            raise RuntimeError(f"injuries HTTP {response.status_code}: {payload.get('errors')}")
        names_by_team = {team_id: [] for team_id in team_ids}
        for row in payload.get("response", []) or []:
            team_id = int((row.get("team") or {}).get("id") or 0)
            player_name = str((row.get("player") or {}).get("name") or "").strip()
            if team_id in names_by_team and player_name:
                names_by_team[team_id].append(player_name)
        key_players = {}
        if any(names_by_team.values()) and league_id and season:
            key_players = fetch_league_key_players(league_id, season)
        result = {}
        for team_id, names in names_by_team.items():
            names = sorted(set(names))
            ace_names = []
            missing_goals = 0
            for name in names:
                normalized = _normalize_player_name(name)
                matched = next((
                    stats for key_name, stats in key_players.items()
                    if stats.get("team_id") in (None, team_id)
                    and (
                        normalized == _normalize_player_name(key_name)
                        or (
                            len(normalized) >= 6
                            and (
                                normalized in _normalize_player_name(key_name)
                                or _normalize_player_name(key_name) in normalized
                            )
                        )
                    )
                ), None)
                if matched or find_protected_star(name):
                    ace_names.append(name)
                    missing_goals += int((matched or {}).get("goals", 0) or 0)
            result[str(team_id)] = {
                "count": len(names),
                "ace_missing": bool(ace_names),
                "ace_names": ace_names,
                "missing_goals": missing_goals,
                "available": True,
                "source": "target_fixture",
            }
        set_db_cache(cache_key, result)
        return result
    except Exception as error:
        print(f"⚠️ 세계경기 부상자 조회 실패({fixture_id}): {error}")
        if isinstance(stale, dict):
            return stale
        return default


def _world_analysis_stage(diff_hours):
    if diff_hours <= 0:
        return "LOCKED_AFTER_KICKOFF"
    if diff_hours <= 0.5:
        return "T-30-final"
    if diff_hours <= 1.0:
        return "T-60-lineup"
    if diff_hours <= 3.0:
        return "T-3-refresh"
    if diff_hours <= WORLD_ANALYSIS_HORIZON_HOURS:
        return "T-24-initial"
    return "WAITING_T24_ANALYSIS"


def _world_data_quality(match, odds, h_recent, a_recent, h_long, a_long,
                        h_stand, a_stand, injuries, lineup_confirmed):
    score = 0
    missing = []
    if (
        int(match.get("fixture_id") or 0) > 0
        and int(match.get("home_team_id") or 0) > 0
        and int(match.get("away_team_id") or 0) > 0
        and match.get("kickoff_at")
    ):
        score += 15
    else:
        missing.append("경기·팀 신원 또는 시간")

    if odds and odds.get("1x2"):
        score += 15
    else:
        missing.append("승무패 배당")
    if odds and odds.get("totals"):
        score += 5
    else:
        missing.append("언더오버 배당")
    if odds and odds.get("handicap"):
        score += 5
    else:
        missing.append("핸디캡 배당")
    if odds and odds.get("stale"):
        score = max(0, score - 5)
        missing.append("최신 배당")

    recent_sample = min(int(h_recent.get("matches") or 0), int(a_recent.get("matches") or 0))
    long_sample = min(int(h_long.get("home_total") or 0), int(a_long.get("away_total") or 0))
    if recent_sample >= 5 and long_sample >= 3:
        score += 20
    elif recent_sample >= 3:
        score += 12
        missing.append("충분한 최근 경기 표본")
    elif recent_sample > 0:
        score += 6
        missing.append("충분한 최근 경기 표본")
    else:
        missing.append("최근 경기 표본")

    if (
        int(h_stand.get("rank") or 99) != 99
        and int(a_stand.get("rank") or 99) != 99
        and long_sample >= 3
    ):
        score += 15
    elif int(h_stand.get("rank") or 99) != 99 and int(a_stand.get("rank") or 99) != 99:
        score += 8
        missing.append("홈·원정 공격수비 표본")
    else:
        missing.append("순위·홈원정 공격수비")

    if injuries.get("home", {}).get("available") and injuries.get("away", {}).get("available"):
        score += 10
    elif injuries.get("home", {}).get("available") or injuries.get("away", {}).get("available"):
        score += 5
        missing.append("양 팀 전체 부상·징계")
    else:
        missing.append("부상·징계")
    if lineup_confirmed:
        score += 10
    else:
        missing.append("확정 선발명단")
    # 검증된 fixture_id는 종료 후 결과·사건 조회 대상으로 사용할 수 있다.
    if int(match.get("fixture_id") or 0) > 0:
        score += 5
    else:
        missing.append("경기 후 사건·통계 지원")

    score = max(0, min(100, int(score)))
    grade = "정상" if score >= 80 else ("주의" if score >= 60 else "보조 분석")
    return score, grade, list(dict.fromkeys(missing))


def _ensure_world_analysis_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS world_prediction_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fixture_id INTEGER NOT NULL,
            match_id TEXT NOT NULL,
            league_id INTEGER NOT NULL,
            season INTEGER,
            analysis_version TEXT NOT NULL,
            analysis_stage TEXT NOT NULL,
            analysis_day TEXT NOT NULL,
            kickoff_at TEXT NOT NULL,
            analyzed_at TEXT NOT NULL,
            frozen_at TEXT,
            data_quality_score INTEGER NOT NULL,
            odds_json TEXT NOT NULL DEFAULT '{}',
            inputs_json TEXT NOT NULL DEFAULT '{}',
            candidates_json TEXT NOT NULL DEFAULT '[]',
            categories_json TEXT NOT NULL DEFAULT '{}',
            decision_json TEXT NOT NULL DEFAULT '{}',
            report_text TEXT NOT NULL DEFAULT '',
            fingerprint TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(fixture_id, analysis_version, analysis_stage, fingerprint)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_world_snapshot_day "
        "ON world_prediction_snapshots(analysis_day, league_id, fixture_id)"
    )


def _world_analysis_usage():
    day_key = datetime.now(KST).strftime("%Y-%m-%d")
    conn = sqlite3.connect(str(_local_path("ai_predictions.db")), timeout=30)
    try:
        _ensure_world_analysis_table(conn)
        today_rows = conn.execute(
            "SELECT DISTINCT fixture_id, league_id FROM world_prediction_snapshots "
            "WHERE analysis_day = ?", (day_key,),
        ).fetchall()
        all_rows = conn.execute(
            "SELECT DISTINCT fixture_id FROM world_prediction_snapshots"
        ).fetchall()
        conn.commit()
    finally:
        conn.close()
    today_ids = {int(row[0]) for row in today_rows}
    all_ids = {int(row[0]) for row in all_rows}
    league_counts = {}
    for _, league_id in today_rows:
        league_counts[int(league_id)] = int(league_counts.get(int(league_id), 0)) + 1
    return day_key, today_ids, all_ids, league_counts


def _save_world_analysis_snapshot(match, analysis):
    analysis_version = str(analysis.get("analysis_version") or WORLD_ANALYSIS_VERSION)
    payload_parts = {
        "odds": analysis.get("odds_snapshot") or {},
        "inputs": analysis.get("inputs_snapshot") or {},
        "candidates": analysis.get("candidates") or [],
        "categories": analysis.get("categories") or {},
        "decision": analysis.get("decision") or {},
    }
    encoded = {
        key: json.dumps(value, ensure_ascii=False, sort_keys=True)
        for key, value in payload_parts.items()
    }
    fingerprint = hashlib.sha256(
        "|".join((
            analysis_version, str(analysis.get("analysis_stage") or ""),
            encoded["odds"], encoded["inputs"], encoded["candidates"],
            encoded["categories"], encoded["decision"],
        )).encode("utf-8")
    ).hexdigest()
    conn = sqlite3.connect(str(_local_path("ai_predictions.db")), timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        _ensure_world_analysis_table(conn)
        conn.execute(
            """
            INSERT OR IGNORE INTO world_prediction_snapshots (
                fixture_id, match_id, league_id, season, analysis_version,
                analysis_stage, analysis_day, kickoff_at, analyzed_at, frozen_at,
                data_quality_score, odds_json, inputs_json, candidates_json,
                categories_json, decision_json, report_text, fingerprint
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(match.get("fixture_id") or 0), str(match.get("id") or ""),
                int(match.get("league_id") or 0), match.get("season"), analysis_version,
                str(analysis.get("analysis_stage") or ""),
                datetime.now(KST).strftime("%Y-%m-%d"), str(match.get("kickoff_at") or ""),
                str(analysis.get("analyzed_at") or ""), analysis.get("frozen_at"),
                int(analysis.get("data_quality_score") or 0), encoded["odds"],
                encoded["inputs"], encoded["candidates"], encoded["categories"],
                encoded["decision"], str(analysis.get("report") or ""), fingerprint,
            ),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def _world_learning_candidate(row):
    """Restore a WORLD audit row to the common candidate shape."""
    row = dict(row or {})
    return {
        "market_key": str(row.get("market_key") or ""),
        "label": str(row.get("label") or ""),
        "raw_pick": str(row.get("raw_pick") or ""),
        "selection_side": str(row.get("selection_side") or ""),
        "handicap_base": row.get("handicap_base"),
        "sort_id": int(row.get("sort_id") or 0),
        "prob": float(row.get("model_probability") or 0),
        "raw_model_prob": row.get("raw_model_probability"),
        "fair_prob": row.get("fair_probability"),
        "odd": float(row.get("odd") or 0),
        "edge": float(row.get("edge") or 0),
        "robust_probability": float(row.get("robust_probability") or 0),
        "robust_edge": float(row.get("robust_edge") or 0),
        "robust_ev": float(row.get("robust_ev") or 0),
        "balanced_score": float(row.get("balanced_score") or 0),
        "safe_score": float(row.get("safe_score") or 0),
        "recommendation_score": float(row.get("recommendation_score") or 0),
        "market_hit_rate": float(row.get("market_hit_rate") or 0.5),
        "market_history_samples": int(row.get("market_history_samples") or 0),
        "market_history_scope": str(row.get("market_history_scope") or "global"),
        "learning_weight": float(row.get("learning_weight") or 0),
        "data_confidence": float(row.get("data_confidence") or 0.35),
        "error_margin": float(row.get("error_margin") or 0),
        "probability_interval": dict(row.get("probability_interval") or {}),
        "is_true_underdog": bool(row.get("is_true_underdog")),
        "is_qualified_underdog": bool(row.get("is_qualified_underdog")),
        "support_signals": list(row.get("support_signals") or []),
        "independent_support_count": int(row.get("independent_support_count") or 0),
    }


def _save_world_learning_record(match, analysis):
    """Feed WORLD forecasts into the same immutable grading/learning pipeline."""
    candidates = [
        _world_learning_candidate(row)
        for row in (analysis.get("candidates") or [])
        if isinstance(row, dict)
    ]
    by_pick = {
        (item.get("market_key"), item.get("raw_pick")): item
        for item in candidates
    }
    categories = {}
    for key in ("high_probability", "honey", "vip_underdog"):
        summary = (analysis.get("categories") or {}).get(key) or {}
        source = by_pick.get((
            str(summary.get("market_key") or ""),
            str(summary.get("raw_pick") or ""),
        ))
        categories[key] = ({**source, **summary} if source else None)
    selected = categories.get("high_probability")
    if not selected:
        return False
    confidence = float(
        selected.get("data_confidence")
        or (float(analysis.get("data_quality_score") or 35) / 100.0)
    )
    odds = analysis.get("odds_snapshot") or {}
    wdl = odds.get("1x2") or {}
    prediction_saved = save_dual_predictions_to_local_db(
        str(match.get("id") or ""),
        str(match.get("league_name_ko") or match.get("league") or "세계 축구"),
        str(match.get("home") or "홈팀"),
        str(match.get("away") or "원정팀"),
        str(selected.get("raw_pick") or ""),
        round(float(selected.get("prob") or 0) * 100, 1),
        "",
        0.0,
        float(wdl.get("home") or 0),
        float(wdl.get("draw") or 0),
        float(wdl.get("away") or 0),
        str(match.get("match_time") or ""),
        0,
        int(match.get("fixture_id") or 0),
        str(analysis.get("analysis_stage") or "regular"),
        confidence,
        analysis_version=str(analysis.get("analysis_version") or WORLD_ANALYSIS_VERSION),
    )
    if not prediction_saved:
        return False
    return save_prediction_analysis(
        str(match.get("id") or ""), selected, confidence,
        analysis.get("evidence") or [], candidates,
        str(analysis.get("report") or ""), categories=categories,
        analysis_stage=str(analysis.get("analysis_stage") or "regular"),
        odds_source="world_bookmaker_median",
        analysis_version=str(analysis.get("analysis_version") or WORLD_ANALYSIS_VERSION),
    )


def _analyze_world_match(item, now, market_performance):
    """Analyze WORLD with the same full-context inputs and common selector."""
    match = dict((item or {}).get("match") or {})
    fixture_id = int(match.get("fixture_id") or item.get("api_fixture_id") or 0)
    home_id = int(match.get("home_team_id") or 0)
    away_id = int(match.get("away_team_id") or 0)
    raw_home = str(match.get("home") or "홈팀")
    raw_away = str(match.get("away") or "원정팀")
    home = str(match.get("home_name_ko") or _world_team_display_name(raw_home, home_id) or raw_home)
    away = str(match.get("away_name_ko") or _world_team_display_name(raw_away, away_id) or raw_away)
    league_id = int(match.get("league_id") or 0)
    season = match.get("season")
    kickoff = datetime.fromisoformat(str(match.get("kickoff_at") or "").replace("Z", "+00:00"))
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=KST)
    kickoff = kickoff.astimezone(KST)
    diff_hours = (kickoff - now).total_seconds() / 3600.0
    analysis_stage = _world_analysis_stage(diff_hours)
    if analysis_stage in {"LOCKED_AFTER_KICKOFF", "WAITING_T24_ANALYSIS"}:
        raise ValueError(f"not analyzable in stage {analysis_stage}")

    heavy_ttl = 24
    injury_ttl = 0.5 if diff_hours <= 3 else 12
    odds = _world_market_snapshot_for_analysis(
        item, fixture_id, diff_hours, now=now
    )
    odds_movement = capture_odds_movement(
        fixture_id, "WORLD", analysis_stage, odds
    )
    h_market_bonus = float(odds_movement.get("home_bonus") or 0)
    a_market_bonus = float(odds_movement.get("away_bonus") or 0)
    h_long = fetch_team_long_term_stats_api(home_id, heavy_ttl)
    a_long = fetch_team_long_term_stats_api(away_id, heavy_ttl)
    h_recent = fetch_team_recent_form_metrics(home_id, heavy_ttl)
    a_recent = fetch_team_recent_form_metrics(away_id, heavy_ttl)
    h_stats = fetch_recent_team_stats_api(home_id, heavy_ttl)
    a_stats = fetch_recent_team_stats_api(away_id, heavy_ttl)
    h_stand = fetch_team_standing_api(home_id, heavy_ttl)
    a_stand = fetch_team_standing_api(away_id, heavy_ttl)
    h_survival = calculate_survival_motivation(h_stand)
    a_survival = calculate_survival_motivation(a_stand)
    h2h = fetch_fixture_details_api(home_id, away_id, heavy_ttl)
    h_manager = fetch_new_manager_status(home_id, heavy_ttl)
    a_manager = fetch_new_manager_status(away_id, heavy_ttl)
    is_derby = check_derby_match(raw_home, raw_away)
    injury_map = fetch_world_injuries_snapshot(
        fixture_id, home_id, away_id, league_id, season, injury_ttl
    )
    h_inj = injury_map.get(str(home_id), {})
    a_inj = injury_map.get(str(away_id), {})

    # 선발 예측의 첫 단계: 공식 명단을 지어내지 않고, 실제 스쿼드/출전
    # 기록에서 확인된 핵심 후보만 동결 저장한다. 공식 선발이 발표되면 같은
    # 스냅샷에서 일치/누락을 비교해 다음 분석의 학습 자료로 사용한다.
    h_core = get_expected_core_players(home_id, league_id, season) if diff_hours <= 3.0 else []
    a_core = get_expected_core_players(away_id, league_id, season) if diff_hours <= 3.0 else []
    lineup_data = {"confirmed": False}
    h_missing = []
    a_missing = []
    h_unexpected = []
    a_unexpected = []
    if diff_hours <= 1.0:
        lineup_data = fetch_lineups_api(fixture_id, 0.2)
        if lineup_data.get("confirmed"):
            h_missing = find_missing_core_players(
                sorted(set(h_core + list(h_inj.get("ace_names") or []))),
                lineup_data.get(str(home_id), []),
            )
            a_missing = find_missing_core_players(
                sorted(set(a_core + list(a_inj.get("ace_names") or []))),
                lineup_data.get(str(away_id), []),
            )
            h_injury_names = {
                _normalize_player_name(name) for name in h_inj.get("ace_names", [])
            }
            a_injury_names = {
                _normalize_player_name(name) for name in a_inj.get("ace_names", [])
            }
            h_unexpected = [
                name for name in h_missing
                if _normalize_player_name(name) not in h_injury_names
            ]
            a_unexpected = [
                name for name in a_missing
                if _normalize_player_name(name) not in a_injury_names
            ]
    lineup_confirmed = bool(lineup_data.get("confirmed"))

    h_last = fetch_team_last_match_date_api(home_id, heavy_ttl)
    a_last = fetch_team_last_match_date_api(away_id, heavy_ttl)
    h_rest = calculate_rest_days(h_last.get("date"), match.get("match_time"))
    a_rest = calculate_rest_days(a_last.get("date"), match.get("match_time"))
    h_next = fetch_team_next_fixture_api(home_id, heavy_ttl)
    a_next = fetch_team_next_fixture_api(away_id, heavy_ttl)
    referee = str(match.get("referee") or "").strip() or None
    city = str(match.get("city") or "").strip()
    weather_condition = fetch_weather_api(city, heavy_ttl) if city else None

    injuries_for_quality = {"home": h_inj, "away": a_inj}
    quality_score, quality_grade, missing_data = _world_data_quality(
        match, odds, h_recent, a_recent, h_long, a_long,
        h_stand, a_stand, injuries_for_quality, lineup_confirmed,
    )
    confidence = calculate_data_confidence(
        {"id": home_id}, {"id": away_id}, fixture_id,
        h_stand, a_stand, h_long, a_long, h_recent, a_recent,
        h_stats, a_stats, h_inj, a_inj, diff_hours, lineup_confirmed,
    )
    confidence = round(
        max(0.35, min(float(confidence), max(0.35, quality_score / 100.0))), 3
    )

    league_name = str(match.get("league_name_ko") or match.get("league") or "")
    avg_h_gf, avg_a_gf = get_league_averages(league_name)
    avg_h_ga, avg_a_ga = avg_a_gf, avg_h_gf
    has = (
        (float(h_long.get("home_gf") or 0) / float(h_long.get("home_total") or 1)) / avg_h_gf
        if int(h_long.get("home_total") or 0) > 0 else 1.0
    )
    hds = (
        (float(h_long.get("home_ga") or 0) / float(h_long.get("home_total") or 1)) / avg_h_ga
        if int(h_long.get("home_total") or 0) > 0 else 1.0
    )
    aas = (
        (float(a_long.get("away_gf") or 0) / float(a_long.get("away_total") or 1)) / avg_a_gf
        if int(a_long.get("away_total") or 0) > 0 else 1.0
    )
    ads = (
        (float(a_long.get("away_ga") or 0) / float(a_long.get("away_total") or 1)) / avg_a_ga
        if int(a_long.get("away_total") or 0) > 0 else 1.0
    )
    h_rank = int(h_stand.get("rank") or 99)
    a_rank = int(a_stand.get("rank") or 99)
    home_advantage = 1.12 if 99 not in (h_rank, a_rank) and abs(h_rank - a_rank) <= 3 else 1.08
    weather_multiplier = 0.8 if weather_condition in {"Rain", "Snow"} else 1.0
    math_exp_h = (
        has * ads * avg_h_gf * home_advantage * weather_multiplier
        * (1.0 + float(h_recent.get("strength") or 0))
    )
    math_exp_a = (
        aas * hds * avg_a_gf * weather_multiplier
        * (1.0 + float(a_recent.get("strength") or 0))
    )

    def recent_strength_multiplier(stats):
        stats = stats if isinstance(stats, dict) else {}
        actual_xg = stats.get("xg")
        xg_component = (
            (float(actual_xg) - 1.35) * 0.06 if actual_xg is not None else 0.0
        )
        return max(0.82, min(1.22,
            1.0 + xg_component
            + ((float(stats.get("possession") or 50) - 50) * 0.008)
            + ((float(stats.get("shots_on_goal") or 4.0) - 4.0) * 0.05)
            + ((float(stats.get("corners") or 4.5) - 4.5) * 0.012)
            - ((float(stats.get("yellow_cards") or 1.5) - 1.5) * 0.015)
        ))

    h_strength_multiplier = recent_strength_multiplier(h_stats)
    a_strength_multiplier = recent_strength_multiplier(a_stats)

    wdl_odds = odds.get("1x2") or {}
    valid_wdl_odds = all(float(wdl_odds.get(side) or 0) > 1.0 for side in ("home", "draw", "away"))
    if valid_wdl_odds:
        implied = normalize_probabilities([
            1.0 / float(wdl_odds["home"]),
            1.0 / float(wdl_odds["draw"]),
            1.0 / float(wdl_odds["away"]),
        ])
        base_exp_h = math_exp_h * h_strength_multiplier * 0.85 + (implied[0] * 2.8) * 0.15
        base_exp_a = math_exp_a * a_strength_multiplier * 0.85 + (implied[2] * 2.8) * 0.15
    else:
        base_exp_h = math_exp_h * h_strength_multiplier
        base_exp_a = math_exp_a * a_strength_multiplier

    h_war_pct, h_war_details, _ = calculate_war_penalty(
        home, h_inj.get("ace_names") or [], int(h_inj.get("count") or 0), home_id
    )
    a_war_pct, a_war_details, _ = calculate_war_penalty(
        away, a_inj.get("ace_names") or [], int(a_inj.get("count") or 0), away_id
    )
    h_lineup_penalty = 0.12 if len(h_unexpected) == 1 else (0.20 if len(h_unexpected) >= 2 else 0.0)
    a_lineup_penalty = 0.12 if len(a_unexpected) == 1 else (0.20 if len(a_unexpected) >= 2 else 0.0)
    h_fatigue = (0.15 if h_last.get("is_extreme_fatigue") else 0.08) if h_rest <= 3 else 0.0
    a_fatigue = (0.15 if a_last.get("is_extreme_fatigue") else 0.08) if a_rest <= 3 else 0.0
    h_rotation = 0.10 if h_next.get("is_important") and int(h_next.get("days_until_next") or 99) <= 4 else 0.0
    a_rotation = 0.10 if a_next.get("is_important") and int(a_next.get("days_until_next") or 99) <= 4 else 0.0
    h_team_goals = float(h_stand.get("team_goals") or 0)
    a_team_goals = float(a_stand.get("team_goals") or 0)
    h_one_player = 0.15 if h_team_goals > 0 and float(h_inj.get("missing_goals") or 0) / h_team_goals >= 0.25 else 0.0
    a_one_player = 0.15 if a_team_goals > 0 and float(a_inj.get("missing_goals") or 0) / a_team_goals >= 0.25 else 0.0
    h_total_teams = int(h_stand.get("total_teams") or 20)
    a_total_teams = int(a_stand.get("total_teams") or 20)
    h_played = int(h_stand.get("played") or 0)
    a_played = int(a_stand.get("played") or 0)
    h_late_season = h_played > 0 and h_played / max(1, (h_total_teams - 1) * 2) >= 0.75
    a_late_season = a_played > 0 and a_played / max(1, (a_total_teams - 1) * 2) >= 0.75
    h_vacation = 0.08 if h_late_season and 6 <= h_rank <= max(10, h_total_teams - 4) else 0.0
    a_vacation = 0.08 if a_late_season and 6 <= a_rank <= max(10, a_total_teams - 4) else 0.0
    h_depth = calculate_squad_depth_factor(h_stand)
    a_depth = calculate_squad_depth_factor(a_stand)
    h_penalties = sorted(
        [h_war_pct, h_fatigue, h_rotation, h_lineup_penalty, h_vacation, h_one_player],
        reverse=True,
    )
    a_penalties = sorted(
        [a_war_pct, a_fatigue, a_rotation, a_lineup_penalty, a_vacation, a_one_player],
        reverse=True,
    )
    h_total_penalty = min(0.30, sum(h_penalties[:2]) * h_depth)
    a_total_penalty = min(0.30, sum(a_penalties[:2]) * a_depth)
    h_rank_bonus = max(-0.18, min(0.18, (a_rank - h_rank) * 0.012)) if 99 not in (h_rank, a_rank) else 0.0
    a_rank_bonus = -h_rank_bonus
    h2h_total = int(h2h.get("total") or 0)
    h_h2h = (
        max(-0.10, min(0.10, ((int(h2h.get("h_wins") or 0) - int(h2h.get("a_wins") or 0)) / h2h_total) * 0.12))
        if h2h_total else 0.0
    )
    a_h2h = -h_h2h
    h_matchup = 0.04 if h2h_total >= 3 and int(h2h.get("h_wins") or 0) / h2h_total >= 0.65 else 0.0
    a_matchup = 0.04 if h2h_total >= 3 and int(h2h.get("a_wins") or 0) / h2h_total >= 0.65 else 0.0
    h_title = 0.06 if 1 <= h_rank <= 3 else 0.0
    a_title = 0.06 if 1 <= a_rank <= 3 else 0.0
    h_manager_buff = 0.08 if h_manager.get("is_new_manager") else 0.0
    a_manager_buff = 0.08 if a_manager.get("is_new_manager") else 0.0
    exp_h = round(max(0.3, min(3.2,
        base_exp_h * (1.0 - h_total_penalty) + a_total_penalty * 0.4
        + h_rank_bonus + h_h2h + h_matchup
        + float(h_survival.get("attack_boost") or 0)
        + float(a_survival.get("opponent_risk_boost") or 0)
        + h_title + h_manager_buff + h_market_bonus
    )), 2)
    exp_a = round(max(0.3, min(3.2,
        base_exp_a * (1.0 - a_total_penalty) + h_total_penalty * 0.4
        + a_rank_bonus + a_h2h + a_matchup
        + float(a_survival.get("attack_boost") or 0)
        + float(h_survival.get("opponent_risk_boost") or 0)
        + a_title + a_manager_buff + a_market_bonus
    )), 2)

    totals_odds = odds.get("totals") or {}
    uo_base = float(totals_odds.get("line") or 2.5)
    handicap_odds = odds.get("handicap") or {}
    preliminary = calculate_poisson_probs(exp_h, exp_a, 0.0, uo_base)
    handi_base = handicap_odds.get("line")
    if handi_base is None or abs(float(handi_base or 0)) < 0.25:
        handi_base = -1.0 if preliminary[0] >= preliminary[2] else 1.0
    handi_base = float(handi_base)
    h_win, draw, a_win, prob_u, prob_o, handi_h, handi_d, handi_a = calculate_poisson_probs(
        exp_h, exp_a, handi_base, uo_base
    )
    if valid_wdl_odds:
        h_win, draw, a_win = calibrate_three_way_probabilities(
            [h_win, draw, a_win],
            [wdl_odds["home"], wdl_odds["draw"], wdl_odds["away"]],
            confidence,
        )
    valid_totals_odds = all(float(totals_odds.get(side) or 0) > 1.0 for side in ("under", "over"))
    if valid_totals_odds:
        prob_u, prob_o = calibrate_two_way_probabilities(
            [prob_u, prob_o], [totals_odds["under"], totals_odds["over"]], confidence
        )
    valid_handicap_odds = all(float(handicap_odds.get(side) or 0) > 1.0 for side in ("home", "draw", "away"))
    if valid_handicap_odds:
        handi_h, handi_d, handi_a = calibrate_three_way_probabilities(
            [handi_h, handi_d, handi_a],
            [handicap_odds["home"], handicap_odds["draw"], handicap_odds["away"]],
            confidence,
        )

    wdl_market = normalize_probabilities([
        1.0 / float(wdl_odds["home"]), 1.0 / float(wdl_odds["draw"]), 1.0 / float(wdl_odds["away"]),
    ]) if valid_wdl_odds else [0.0, 0.0, 0.0]
    totals_market = normalize_probabilities([
        1.0 / float(totals_odds["under"]), 1.0 / float(totals_odds["over"]),
    ]) if valid_totals_odds else [0.0, 0.0]
    handicap_market = normalize_probabilities([
        1.0 / float(handicap_odds["home"]), 1.0 / float(handicap_odds["draw"]),
        1.0 / float(handicap_odds["away"]),
    ]) if valid_handicap_odds else [0.0, 0.0, 0.0]
    candidates = [
        {"label": "일반 승무패 예측", "sort_id": 3, "raw_pick": f"{home} 승", "prob": h_win, "odd": float(wdl_odds.get("home") or 0), "market_prob": wdl_market[0], "selection_side": "home"},
        {"label": "일반 승무패 예측", "sort_id": 3, "raw_pick": "무승부", "prob": draw, "odd": float(wdl_odds.get("draw") or 0), "market_prob": wdl_market[1], "selection_side": "draw"},
        {"label": "일반 승무패 예측", "sort_id": 3, "raw_pick": f"{away} 승", "prob": a_win, "odd": float(wdl_odds.get("away") or 0), "market_prob": wdl_market[2], "selection_side": "away"},
        {"label": "핸디캡 예측", "sort_id": 2, "raw_pick": f"[{handi_base:+.1f}] {home} 핸디승", "prob": handi_h, "odd": float(handicap_odds.get("home") or 0), "market_prob": handicap_market[0], "selection_side": "home", "handicap_base": handi_base},
        {"label": "핸디캡 예측", "sort_id": 2, "raw_pick": f"[{handi_base:+.1f}] 핸디무", "prob": handi_d, "odd": float(handicap_odds.get("draw") or 0), "market_prob": handicap_market[1], "selection_side": "draw", "handicap_base": handi_base},
        {"label": "핸디캡 예측", "sort_id": 2, "raw_pick": f"[{handi_base:+.1f}] {home} 핸디패", "prob": handi_a, "odd": float(handicap_odds.get("away") or 0), "market_prob": handicap_market[2], "selection_side": "away", "handicap_base": handi_base},
        {"label": "언더 예측", "sort_id": 1, "raw_pick": f"언더 (U/O {uo_base:g})", "prob": prob_u, "odd": float(totals_odds.get("under") or 0), "market_prob": totals_market[0], "selection_side": "under"},
        {"label": "오버 예측", "sort_id": 1, "raw_pick": f"오버 (U/O {uo_base:g})", "prob": prob_o, "odd": float(totals_odds.get("over") or 0), "market_prob": totals_market[1], "selection_side": "over"},
    ]
    calibrate_market_candidates(candidates, market_performance, confidence)
    underdog_side = (
        "home" if float(wdl_odds.get("home") or 0) > float(wdl_odds.get("away") or 0)
        else ("away" if float(wdl_odds.get("away") or 0) > float(wdl_odds.get("home") or 0) else "")
    ) if valid_wdl_odds else ""
    attach_underdog_signals(candidates, home, away, {
        "home_absence": h_total_penalty, "away_absence": a_total_penalty,
        "home_market_bonus": h_market_bonus, "away_market_bonus": a_market_bonus,
        "home_tactical": h_matchup, "away_tactical": a_matchup,
        "home_recent": float(h_recent.get("strength") or 0),
        "away_recent": float(a_recent.get("strength") or 0),
        "home_rest": h_rest if h_rest < 90 else 0,
        "away_rest": a_rest if a_rest < 90 else 0,
        "home_lineup": h_lineup_penalty,
        "away_lineup": a_lineup_penalty,
        "underdog_side": underdog_side,
    })
    annotate_pick_metrics(candidates, confidence)
    categories, _ = select_pick_categories(candidates, confidence)
    # World VIP is a future paid-grade candidate.  A strong price alone cannot
    # bypass the separately agreed 90/100 input-quality gate.
    if quality_score < 90:
        categories["vip_underdog"] = None
        downgraded_grade = "value" if categories.get("honey") else "standard"
        for category_key in ("high_probability", "honey"):
            if categories.get(category_key):
                categories[category_key]["final_pick_grade"] = downgraded_grade
    selected = categories.get("high_probability")
    tactical_parts = []
    if h_matchup:
        tactical_parts.append(f"{home} 천적 상성")
    if a_matchup:
        tactical_parts.append(f"{away} 천적 상성")
    if is_derby:
        tactical_parts.append("로컬 더비 변동성")
    if h_survival.get("active"):
        tactical_parts.append(f"{home} 강등권 생존 동기")
    if a_survival.get("active"):
        tactical_parts.append(f"{away} 강등권 생존 동기")
    evidence, coverage = build_analysis_evidence({
        "h_recent": h_recent, "a_recent": a_recent,
        "h_stats": h_stats, "a_stats": a_stats, "h_long": h_long, "a_long": a_long,
        "h_inj": h_inj, "a_inj": a_inj,
        "h2h_total": h2h_total, "h_wins": int(h2h.get("h_wins") or 0),
        "draws": int(h2h.get("draws") or 0), "a_wins": int(h2h.get("a_wins") or 0),
        "h_rest_days": h_rest, "a_rest_days": a_rest,
        "weather": weather_condition,
        "referee": referee,
        "tactical_text": ", ".join(tactical_parts),
        "movement_text": odds_movement.get("summary") or None,
        "home_id": home_id, "away_id": away_id, "fixture_id": fixture_id,
    })
    if odds.get("available_markets"):
        evidence.append({
            "name": "시장 배당", "weight": 0.25,
            "value": f"{', '.join(odds['available_markets'])} · {int(odds.get('bookmaker_count') or 0)}개 업체 중앙값",
        })
    evidence.append({
        "name": "데이터 품질", "weight": 0.0,
        "value": f"{quality_score}/100 ({quality_grade})",
    })
    candidate_rows, compact_categories, decision = build_pick_selection_audit(
        candidates, categories, confidence
    )
    report = build_detailed_report(
        selected, evidence, confidence, candidates, categories,
        {
            "home": home, "away": away, "exp_h": exp_h, "exp_a": exp_a,
            "weather": weather_condition,
        },
    )
    analyzed_at = datetime.now(KST).isoformat()
    frozen_at = analyzed_at if analysis_stage == "T-30-final" else None
    selected_summary = dict(compact_categories.get("high_probability") or {})
    selected_summary["display"] = _human_pick_label(selected_summary.get("raw_pick"), home)
    selected_summary["badges"] = [
        label for key, label in (
            ("honey", "배당가치 우수"), ("vip_underdog", "VIP 검증 등급")
        ) if compact_categories.get(key)
    ]
    inputs_snapshot = {
        "home_team_id": home_id, "away_team_id": away_id,
        "league_id": league_id, "season": season, "fixture_id": fixture_id,
        "kickoff_at": kickoff.isoformat(), "expected_goals": {"home": exp_h, "away": exp_a},
        "recent": {"home": h_recent, "away": a_recent},
        "recent_match_stats": {"home": h_stats, "away": a_stats},
        "long_term": {"home": h_long, "away": a_long},
        "standings": {"home": h_stand, "away": a_stand},
        "survival_motivation": {"home": h_survival, "away": a_survival},
        "injuries": injuries_for_quality,
        "lineups": {
            "confirmed": lineup_confirmed,
            "home_missing_core": h_missing, "away_missing_core": a_missing,
        },
        "lineup_learning": {
            "mode": "expected_core_foundation",
            "not_full_starting_xi": True,
            "home_predicted_core": list(h_core),
            "away_predicted_core": list(a_core),
            "home_official_starters": list(lineup_data.get(str(home_id), []) or []),
            "away_official_starters": list(lineup_data.get(str(away_id), []) or []),
            "home_confirmed_core": [name for name in h_core if name not in h_missing]
            if lineup_confirmed else [],
            "away_confirmed_core": [name for name in a_core if name not in a_missing]
            if lineup_confirmed else [],
        },
        "rest_days": {"home": h_rest, "away": a_rest},
        "next_fixture": {"home": h_next, "away": a_next},
        "manager": {"home": h_manager, "away": a_manager},
        "environment": {
            "city": city, "weather": weather_condition, "referee": referee,
            "derby": is_derby,
        },
        "adjustments": {
            "home_penalty": h_total_penalty, "away_penalty": a_total_penalty,
            "home_rank": h_rank_bonus, "away_rank": a_rank_bonus,
            "home_title": h_title, "away_title": a_title,
            "home_manager": h_manager_buff, "away_manager": a_manager_buff,
            "home_market": h_market_bonus, "away_market": a_market_bonus,
            "home_war": h_war_details, "away_war": a_war_details,
        },
        "h2h": h2h, "evidence_coverage": coverage,
        "odds_movement": odds_movement,
    }
    return {
        "analysis_version": WORLD_ANALYSIS_VERSION,
        "system_version": SYSTEM_VERSION,
        "analysis_stage": analysis_stage,
        "analyzed_at": analyzed_at,
        "frozen_at": frozen_at,
        "data_quality_score": quality_score,
        "data_quality_grade": quality_grade,
        "missing_data": missing_data,
        "lineup_confirmed": lineup_confirmed,
        "odds_snapshot": odds,
        "odds_movement": odds_movement,
        "inputs_snapshot": inputs_snapshot,
        "evidence": evidence,
        "candidates": candidate_rows,
        "categories": compact_categories,
        "selected": selected_summary,
        "alternative": {},
        "learning_robot": dict(decision.get("learning_robot") or {}),
        "decision": decision,
        "report": report,
    }


def analyze_world_schedule():
    """Run stage 6-2 without exposing any unverified prediction to customers."""
    payload = _read_json(WORLD_DASHBOARD_FILE, {})
    if not isinstance(payload, dict) or not isinstance(payload.get("matches"), list):
        print("⚠️ 세계경기 일정 정상본이 없어 그림자 분석을 건너뜁니다.")
        return False, False
    now = datetime.now(KST)
    day_key, today_ids, all_ids, league_counts = _world_analysis_usage()
    market_performance_cache = {}
    changed = False
    analyzed_now = 0
    errors_now = 0
    quota_paused = False
    stage_priority = {
        "T-30-final": 0, "T-60-lineup": 1,
        "T-3-refresh": 2, "T-24-initial": 3,
    }
    due_items = []

    for item in payload.get("matches", []):
        match = item.get("match") or {}
        home_ko = _world_team_display_name(
            match.get("home"), match.get("home_team_id")
        )
        away_ko = _world_team_display_name(
            match.get("away"), match.get("away_team_id")
        )
        if home_ko and match.get("home_name_ko") != home_ko:
            match["home_name_ko"] = home_ko
            changed = True
        if away_ko and match.get("away_name_ko") != away_ko:
            match["away_name_ko"] = away_ko
            changed = True
        try:
            kickoff = datetime.fromisoformat(str(match.get("kickoff_at") or "").replace("Z", "+00:00"))
            if kickoff.tzinfo is None:
                kickoff = kickoff.replace(tzinfo=KST)
            kickoff = kickoff.astimezone(KST)
        except (TypeError, ValueError):
            item["analysis_status"] = "ANALYSIS_ERROR"
            item["analysis_error"] = "킥오프 시간 해석 실패"
            changed = True
            errors_now += 1
            continue
        stage = _world_analysis_stage((kickoff - now).total_seconds() / 3600.0)
        if stage == "WAITING_T24_ANALYSIS":
            if item.get("analysis_status") == "PENDING_SHADOW_ANALYSIS":
                item["analysis_status"] = stage
                changed = True
            continue
        if stage == "LOCKED_AFTER_KICKOFF":
            if not item.get("analysis") and item.get("analysis_status") not in {
                "MISSED_PREKICKOFF", "FROZEN_SHADOW"
            }:
                item["analysis_status"] = "MISSED_PREKICKOFF"
                item["pick_status"] = "NOT_ANALYZED"
                changed = True
            continue
        if item.get("frozen_at") or item.get("analysis_status") == "FROZEN_SHADOW":
            continue
        previous_stage = str(item.get("analysis_stage") or "")
        previous_version = str(
            item.get("analysis_version")
            or (item.get("analysis") or {}).get("analysis_version")
            or ""
        )
        if previous_stage == stage and previous_version == WORLD_ANALYSIS_VERSION:
            if not (
                stage == "T-60-lineup"
                and not item.get("lineup_confirmed")
                and int(item.get("lineup_attempts") or 0) < 2
            ):
                continue
        due_items.append((stage_priority.get(stage, 9), float(item.get("timestamp") or 0), stage, item))

    due_items.sort(key=lambda row: (row[0], row[1]))
    world_calls_before = int(get_api_usage_status().get("world_calls") or 0)
    with api_purpose_context("world"):
        for _, _, stage, item in due_items:
            match = item.get("match") or {}
            fixture_id = int(match.get("fixture_id") or item.get("api_fixture_id") or 0)
            league_id = int(match.get("league_id") or 0)
            is_existing = fixture_id in all_ids
            if not is_existing and len(today_ids) >= WORLD_MAX_DEEP_ANALYSES_DAILY:
                if item.get("analysis_status") != "DEFERRED_DAILY_CAP":
                    item["analysis_status"] = "DEFERRED_DAILY_CAP"
                    changed = True
                continue
            if (
                not is_existing
                and int(league_counts.get(league_id, 0)) >= WORLD_MAX_DEEP_ANALYSES_PER_LEAGUE
            ):
                if item.get("analysis_status") != "DEFERRED_LEAGUE_CAP":
                    item["analysis_status"] = "DEFERRED_LEAGUE_CAP"
                    changed = True
                continue
            try:
                league_name = str(
                    match.get("league_name_ko") or match.get("league") or ""
                )
                if league_name not in market_performance_cache:
                    market_performance_cache[league_name] = load_market_performance(
                        league_name
                    )
                analysis = _analyze_world_match(
                    item, now, market_performance_cache[league_name]
                )
                if not _save_world_analysis_snapshot(match, analysis):
                    raise RuntimeError("세계경기 분석 스냅샷 저장 실패")
                if not _save_world_learning_record(match, analysis):
                    raise RuntimeError("세계경기 통합 채점 기록 저장 실패")
                item["analysis"] = analysis
                item["analysis_version"] = str(
                    analysis.get("analysis_version") or WORLD_ANALYSIS_VERSION
                )
                item["system_version"] = str(
                    analysis.get("system_version") or SYSTEM_VERSION
                )
                item["analysis_stage"] = analysis["analysis_stage"]
                item["analyzed_at"] = analysis["analyzed_at"]
                item["frozen_at"] = analysis.get("frozen_at")
                item["data_quality_score"] = analysis["data_quality_score"]
                item["data_quality_grade"] = analysis["data_quality_grade"]
                item["missing_data"] = analysis["missing_data"]
                item["lineup_confirmed"] = analysis["lineup_confirmed"]
                if stage == "T-60-lineup" and not analysis["lineup_confirmed"]:
                    item["lineup_attempts"] = int(item.get("lineup_attempts") or 0) + 1
                item["analysis_status"] = (
                    "FROZEN_SHADOW" if analysis.get("frozen_at") else "ANALYZED_SHADOW"
                )
                item["pick_status"] = "SHADOW_PICK_READY"
                changed = True
                analyzed_now += 1
                today_ids.add(fixture_id)
                all_ids.add(fixture_id)
                league_counts[league_id] = int(league_counts.get(league_id, 0)) + (0 if is_existing else 1)
                print(
                    f"🧪 세계경기 그림자 분석: {match.get('home')} vs {match.get('away')} · "
                    f"{analysis['selected'].get('display')} "
                    f"{float(analysis['selected'].get('probability') or 0) * 100:.1f}% · "
                    f"품질 {analysis['data_quality_score']}점"
                )
            except ApiQuotaUnavailable as error:
                quota_paused = True
                print(f"⚠️ 세계경기 분석 예산 보호로 중단: {error}")
                break
            except Exception as error:
                errors_now += 1
                item["analysis_status"] = "ANALYSIS_ERROR"
                item["analysis_error"] = f"{type(error).__name__}: {error}"[:500]
                changed = True
                print(f"⚠️ 세계경기 분석 실패({fixture_id}): {type(error).__name__}: {error}")

    previous_source_meta = json.dumps(
        payload.get("source_meta", {}), ensure_ascii=False, sort_keys=True
    )
    _refresh_world_source_meta(payload)
    source_meta = payload.setdefault("source_meta", {})
    source_meta.update({
        "analysis_day": day_key,
        "analysis_daily_limit": WORLD_MAX_DEEP_ANALYSES_DAILY,
        "analysis_per_league_limit": WORLD_MAX_DEEP_ANALYSES_PER_LEAGUE,
        "quota_paused": quota_paused,
        "api_usage": get_api_usage_status(),
    })
    metadata_changed = previous_source_meta != json.dumps(
        source_meta, ensure_ascii=False, sort_keys=True
    )
    if changed or metadata_changed:
        payload["schema_version"] = "world-shadow.v2"
        payload["stage"] = "SHADOW_ANALYSIS"
        payload["last_analysis_at"] = datetime.now(KST).isoformat()
        _atomic_write_json(WORLD_DASHBOARD_FILE, payload, indent=2)
    world_calls_after = int(get_api_usage_status().get("world_calls") or 0)
    print(
        f"✅ 세계경기 2단계 종료: 이번 분석 {analyzed_now}경기 / 오류 {errors_now} / "
        f"WORLD API {max(0, world_calls_after - world_calls_before)}회"
    )
    return True, bool(changed or metadata_changed)


def calculate_survival_motivation(standing):
    """Model relegation pressure without treating every rank 15+ team alike.

    The boost represents a verified increase in attacking urgency.  A smaller
    opponent-risk term is also returned because a team that must chase points
    can expose space; the old fixed +0.06 only made the weak team stronger.
    """
    standing = standing if isinstance(standing, dict) else {}
    result = {
        "active": False,
        "state": "NOT_APPLICABLE",
        "attack_boost": 0.0,
        "opponent_risk_boost": 0.0,
        "season_progress": 0.0,
        "rank": int(standing.get("rank") or 99),
        "relegation_start_rank": standing.get("relegation_start_rank"),
        "points_to_safety": standing.get("points_to_safety"),
        "points_above_zone": standing.get("points_above_zone"),
        "source": str(standing.get("relegation_zone_source") or "none"),
        "reason": "강등선 자료 없음",
    }
    try:
        rank = int(standing.get("rank") or 99)
        played = max(0, int(standing.get("played") or 0))
        total_teams = max(0, int(standing.get("total_teams") or 0))
        zone_start = int(standing.get("relegation_start_rank") or 0)
    except (TypeError, ValueError):
        return result
    if rank <= 0 or rank == 99 or played <= 0 or total_teams < 8 or zone_start <= 1:
        return result

    # 30경기 미만으로 시즌 전체를 가정하지 않아 소규모·스플릿 리그의
    # 시즌 중반을 막판으로 잘못 판단하는 일을 줄인다.
    estimated_schedule = max(30, (total_teams - 1) * 2)
    progress = min(1.0, played / max(1, estimated_schedule))
    result["season_progress"] = round(progress, 3)

    description = str(standing.get("description") or "").casefold()
    if any(marker in description for marker in ("relegated", "강등 확정", "descendido")):
        result.update({
            "state": "RELEGATION_CONFIRMED",
            "reason": "공식 순위표상 강등 확정으로 생존 버프 미적용",
        })
        return result

    in_zone = rank >= zone_start
    just_above_zone = rank == zone_start - 1
    points_above = standing.get("points_above_zone")
    try:
        points_above = int(points_above) if points_above is not None else None
    except (TypeError, ValueError):
        points_above = None
    at_risk = bool(just_above_zone and points_above is not None and points_above <= 3)
    if not in_zone and not at_risk:
        result.update({"state": "SAFE", "reason": "현재 강등권·강등선 인접 팀 아님"})
        return result
    if progress < 0.55:
        result.update({
            "state": "TOO_EARLY",
            "reason": "시즌 초중반이라 순위만으로 생존 버프 미적용",
        })
        return result

    if progress >= 0.88:
        base_attack, base_risk, state = 0.055, 0.030, "SURVIVAL_MUST_WIN"
    elif progress >= 0.72:
        base_attack, base_risk, state = 0.040, 0.020, "SURVIVAL_PRESSURE"
    else:
        base_attack, base_risk, state = 0.025, 0.012, "SURVIVAL_WATCH"

    points_to_safety = standing.get("points_to_safety")
    try:
        points_to_safety = int(points_to_safety) if points_to_safety is not None else None
    except (TypeError, ValueError):
        points_to_safety = None
    if in_zone and points_to_safety is not None:
        if 1 <= points_to_safety <= 3:
            base_attack += 0.015
        elif 4 <= points_to_safety <= 6:
            base_attack += 0.010
            base_risk += 0.005
        elif points_to_safety >= 7:
            base_risk += 0.010
    if at_risk:
        base_attack *= 0.70
        base_risk *= 0.75

    # 리그 규정 설명에서 직접 찾지 못하고 팀 수로 추정한 강등선은 보수적으로 쓴다.
    if result["source"] != "official_description":
        base_attack *= 0.70
        base_risk *= 0.70

    attack_boost = round(min(0.08, max(0.0, base_attack)), 3)
    opponent_risk = round(min(0.045, max(0.0, base_risk)), 3)
    if in_zone:
        gap_text = (
            f"잔류선까지 {points_to_safety}점"
            if points_to_safety is not None
            else "강등권"
        )
        reason = f"{rank}위·{gap_text}·시즌 {int(progress * 100)}% 진행"
    else:
        reason = f"강등선 바로 위·승점 여유 {points_above}점·시즌 {int(progress * 100)}% 진행"
    result.update({
        "active": True,
        "state": state,
        "attack_boost": attack_boost,
        "opponent_risk_boost": opponent_risk,
        "reason": reason,
    })
    return result


def calculate_squad_depth_factor(standing):
    """Scale absence damage by relative table position, not a fixed rank 15."""
    standing = standing if isinstance(standing, dict) else {}
    try:
        rank = int(standing.get("rank") or 99)
        total_teams = int(standing.get("total_teams") or 0)
        zone_start = int(standing.get("relegation_start_rank") or 0)
    except (TypeError, ValueError):
        return 1.0
    if rank <= 0 or rank == 99 or total_teams < 8:
        return 1.0
    if rank <= max(3, round(total_teams * 0.25)):
        return 0.5
    if zone_start > 1 and rank >= zone_start:
        return 1.5
    if rank >= max(1, round(total_teams * 0.75)):
        return 1.25
    return 1.0


def _waiting_odds_team_forms(home_info, away_info, ttl_h=24):
    """Return cached recent form for identified teams even before odds arrive."""
    return (
        fetch_team_form_api((home_info or {}).get("id"), ttl_h),
        fetch_team_form_api((away_info or {}).get("id"), ttl_h),
    )


def build_dashboard_data():
    print(f"\n[🧠 {time.strftime('%Y-%m-%d %H:%M:%S')}] 대시보드 {ANALYSIS_VERSION} 신뢰도 보정 엔진 가동 중...")
    betman_data = _read_json("betman_data.json", {})
    if not isinstance(betman_data, dict):
        print("❌ 베트맨 데이터가 손상되어 대시보드 정상본을 보존합니다.")
        return False
     
    raw_proto_matches = betman_data.get("proto_matches", [])
    proto_matches = [
        match for match in raw_proto_matches
        if not _is_placeholder_match(match)
    ]
    rejected_proto_count = len(raw_proto_matches) - len(proto_matches)
    if rejected_proto_count:
        print(
            f"⚠️ 팀명이 확인되지 않은 가짜 경기 {rejected_proto_count}건을 "
            "분석·화면·채점 대상에서 제외했습니다."
        )
    toto_14_matches = betman_data.get("toto_14_matches", [])
    if toto_14_matches and not _valid_toto14_round(toto_14_matches):
        toto_14_matches = _select_latest_complete_toto14_round(toto_14_matches)
        print(
            f"⚠️ 승무패 혼합/중복 회차를 제거하고 단일 완본 "
            f"{len(toto_14_matches)}경기만 분석합니다."
        )
    dashboard_proto = []
    dashboard_toto14 = []
    frozen_toto14 = _load_toto14_freezes()
    previous_dashboard = _read_json("dashboard_data.json", {})
    previous_proto = {}
    previous_toto14 = {}
    previous_generated_at = None
    if isinstance(previous_dashboard, dict):
        for item in previous_dashboard.get("proto", []):
            if not isinstance(item, dict) or not isinstance(item.get("match"), dict):
                continue
            previous_proto[str(item["match"].get("id", ""))] = item
        for item in previous_dashboard.get("toto14", []):
            if not isinstance(item, dict) or not isinstance(item.get("match"), dict):
                continue
            previous_toto14[str(item["match"].get("id", ""))] = item
        try:
            previous_generated_at = datetime.fromisoformat(
                str(previous_dashboard.get("source_meta", {}).get("generated_at", "")).replace("Z", "+00:00")
            )
            if previous_generated_at.tzinfo is None:
                previous_generated_at = previous_generated_at.replace(tzinfo=KST)
            previous_generated_at = previous_generated_at.astimezone(KST)
        except (AttributeError, TypeError, ValueError):
            previous_generated_at = None

    # 실제 채점된 시장 기록은 리그별로 한 번씩 읽어 전 세계·프로토가 함께 학습한다.
    market_performance_cache = {}
      
    for m in proto_matches:
        home_team, away_team = m["home"], m["away"]
        final_match_time = m.get("match_time") or m.get("time") or "시간 미정"
        m_dt = parse_match_time(final_match_time)
        home_info, away_info, _ = resolve_match_team_pair(
            home_team, away_team, final_match_time, ttl_h=2
        )

        now = datetime.now(timezone(timedelta(hours=9)))
        diff_hours = (m_dt - now).total_seconds() / 3600.0
        odds_ttl = 0.5 if diff_hours <= 2 else 4
        odd_h = float(m.get("odd_h") or 0)
        odd_d = float(m.get("odd_d") or 0)
        odd_a = float(m.get("odd_a") or 0)
        analysis_odds_source = "betman"
        preloaded_os_data = None
        if not _valid_three_way_odds([odd_h, odd_d, odd_a]):
            # 배당이 잠시 비었을 때 같은 경기의 마지막 정상 분석이 있으면
            # 그대로 유지한다. 새 경기라서 계산 근거 자체가 없을 때만 대기한다.
            previous_item = previous_proto.get(str(m.get("id", "")))
            previous_match = previous_item.get("match", {}) if isinstance(previous_item, dict) else {}
            if (
                isinstance(previous_item, dict)
                and previous_match.get("home") == home_team
                and previous_match.get("away") == away_team
                and isinstance(previous_item.get("pick_categories"), dict)
                and previous_item["pick_categories"].get("high_probability")
                and str(previous_item.get("odds_source") or "betman") != "model_only"
            ):
                preserved = dict(previous_item)
                preserved["match"] = dict(m)
                preserved["final_match_time"] = final_match_time
                preserved["timestamp"] = m_dt.timestamp()
                preserved["odds_temporarily_missing"] = True
                dashboard_proto.append(preserved)
                print(f"⚠️ 배당 일시 누락 - 마지막 정상 분석 유지: {home_team} vs {away_team}")
                continue

            # 신규 경기의 베트맨 배당이 비어 있으면 정확히 연결된 동일 경기의
            # 해외 1X2 중앙값을 먼저 사용한다. 해외배당도 없을 때는 배당을
            # 만들어내지 않고 팀 데이터만으로 확률픽 하나를 계산한다.
            preloaded_os_data = fetch_overseas_odds_and_fixture_api(
                home_info.get("id"), away_info.get("id"), odds_ttl,
                final_match_time, include_odds=True,
            )
            overseas_values = [
                (preloaded_os_data or {}).get("odd_h"),
                (preloaded_os_data or {}).get("odd_d"),
                (preloaded_os_data or {}).get("odd_a"),
            ]
            m = dict(m)
            m["betman_odds_pending"] = True
            if _valid_three_way_odds(overseas_values):
                odd_h, odd_d, odd_a = map(float, overseas_values)
                m.update({
                    "odd_h": odd_h,
                    "odd_d": odd_d,
                    "odd_a": odd_a,
                    "odds_source": "overseas_fallback",
                    "overseas_bookmaker_count": int(
                        (preloaded_os_data or {}).get("bookmaker_count") or 0
                    ),
                })
                analysis_odds_source = "overseas_fallback"
                print(f"🌍 베트맨 배당 대기 - 해외 1X2 임시 분석: {home_team} vs {away_team}")
            else:
                m["odds_source"] = "model_only"
                analysis_odds_source = "model_only"
                print(f"📊 모든 배당 대기 - 팀 데이터 모델 선픽: {home_team} vs {away_team}")
        handi_h = float(m.get("handi_h") or 0)
        handi_d = float(m.get("handi_d") or 0)
        handi_a = float(m.get("handi_a") or 0)
        handi_base = float(m.get("handi_base") or 0)
        uo_under = float(m.get("uo_under") or 0)
        uo_over = float(m.get("uo_over") or 0)
        uo_base = float(m.get("uo_base") or 2.5)

        # 🔥 '시간 미정' 경기들 살리기 위해 예외처리(continue) 삭제!
        heavy_ttl = 24
        inj_ttl = 0.5 if diff_hours <= 1.5 else 12
        lineup_ttl = 0.25 if diff_hours <= 1.5 else 12
         
        os_data = preloaded_os_data or fetch_overseas_odds_and_fixture_api(
            home_info.get("id"), away_info.get("id"), odds_ttl, final_match_time
        )
        api_fixture_id = os_data.get("fixture_id", 0) if os_data else 0
        referee = os_data.get("referee") if os_data else None
        city = os_data.get("city") if os_data else None
        proto_movement = _detect_odds_movement({}, {})
        overseas_three_way = [
            (os_data or {}).get("odd_h"),
            (os_data or {}).get("odd_d"),
            (os_data or {}).get("odd_a"),
        ]
        if api_fixture_id and _valid_three_way_odds(overseas_three_way):
            bookmaker_count = int((os_data or {}).get("bookmaker_count") or 0)
            proto_movement = capture_odds_movement(
                api_fixture_id,
                "PROTO",
                prediction_stage(diff_hours, False),
                {
                    "bookmaker_count": bookmaker_count,
                    "1x2": {
                        "home": float(overseas_three_way[0]),
                        "draw": float(overseas_three_way[1]),
                        "away": float(overseas_three_way[2]),
                        "sample_count": bookmaker_count,
                    },
                },
            )
        weather_condition = fetch_weather_api(city, odds_ttl)
         
        fixture_details = fetch_fixture_details_api(home_info["id"], away_info["id"], heavy_ttl)
        h_stand = fetch_team_standing_api(home_info.get("id"), heavy_ttl)
        a_stand = fetch_team_standing_api(away_info.get("id"), heavy_ttl)
        h_rank, a_rank = h_stand["rank"], a_stand["rank"]
        
        h_manager = fetch_new_manager_status(home_info.get("id"), heavy_ttl)
        a_manager = fetch_new_manager_status(away_info.get("id"), heavy_ttl)
        h_manager_buff = 0.08 if h_manager.get("is_new_manager") else 0.0
        a_manager_buff = 0.08 if a_manager.get("is_new_manager") else 0.0
        is_derby = check_derby_match(home_team, away_team)
         
        h_market_bonus, a_market_bonus = 0.0, 0.0
        if (
            analysis_odds_source == "betman"
            and os_data and os_data.get("odd_h")
            and odd_h > 1.0 and odd_a > 1.0
        ):
            if os_data["odd_h"] < odd_h - 0.15: h_market_bonus = 0.05
            if os_data["odd_a"] < odd_a - 0.15: a_market_bonus = 0.05
        # 같은 경기의 서로 다른 수집 시점에서 여러 업체 중앙값이 함께
        # 움직인 경우만 사용한다. 단일 업체 변동이나 한 번뿐인 조회는 제외한다.
        h_market_bonus = max(
            h_market_bonus, float(proto_movement.get("home_bonus") or 0)
        )
        a_market_bonus = max(
            a_market_bonus, float(proto_movement.get("away_bonus") or 0)
        )
             
        rank_diff_bonus_h = max(-0.18, min(0.18, (a_rank - h_rank) * 0.012)) if 99 not in (h_rank, a_rank) else 0.0
        rank_diff_bonus_a = max(-0.18, min(0.18, (h_rank - a_rank) * 0.012)) if 99 not in (h_rank, a_rank) else 0.0
        
        h_survival = calculate_survival_motivation(h_stand)
        a_survival = calculate_survival_motivation(a_stand)
        
        h_title_buff = 0.06 if 1 <= h_rank <= 3 else 0.0
        a_title_buff = 0.06 if 1 <= a_rank <= 3 else 0.0
        
        h_played = h_stand.get("played", 0)
        h_total_teams = h_stand.get("total_teams", 20)
        h_team_goals = h_stand.get("team_goals", 0)
        is_late_season_h = h_played > 0 and (h_played / max(1, (h_total_teams - 1) * 2)) >= 0.75
        h_vacation = 0.08 if (is_late_season_h and 6 <= h_rank <= max(10, h_total_teams - 4)) else 0.0
        
        a_played = a_stand.get("played", 0)
        a_total_teams = a_stand.get("total_teams", 20)
        a_team_goals = a_stand.get("team_goals", 0)
        is_late_season_a = a_played > 0 and (a_played / max(1, (a_total_teams - 1) * 2)) >= 0.75
        a_vacation = 0.08 if (is_late_season_a and 6 <= a_rank <= max(10, a_total_teams - 4)) else 0.0
         
        h_inj_data = fetch_team_injuries_api(home_info.get("id"), h_stand.get("league_id"), h_stand.get("season"), inj_ttl, api_fixture_id)
        a_inj_data = fetch_team_injuries_api(away_info.get("id"), a_stand.get("league_id"), a_stand.get("season"), inj_ttl, api_fixture_id)
        h_inj_count, a_inj_count = h_inj_data["count"], a_inj_data["count"]
        
        h_missing_goals = h_inj_data.get("missing_goals", 0)
        h_goal_dep_ratio = (h_missing_goals / h_team_goals) if h_team_goals > 0 else 0
        h_oneman_penalty = 0.15 if h_goal_dep_ratio >= 0.25 else 0.0
        
        a_missing_goals = a_inj_data.get("missing_goals", 0)
        a_goal_dep_ratio = (a_missing_goals / a_team_goals) if a_team_goals > 0 else 0
        a_oneman_penalty = 0.15 if a_goal_dep_ratio >= 0.25 else 0.0
        
        h_war_pct, h_war_details, h_war_score = calculate_war_penalty(home_team, h_inj_data["ace_names"], h_inj_count, home_info.get("id"))
        a_war_pct, a_war_details, a_war_score = calculate_war_penalty(away_team, a_inj_data["ace_names"], a_inj_count, away_info.get("id"))

        h_lineup_penalty, a_lineup_penalty = 0.0, 0.0
        h_lineup_msg, a_lineup_msg = "", ""
        lineup_confirmed = False
        if diff_hours <= 1.5:
            lineup_data = fetch_lineups_api(api_fixture_id, lineup_ttl)
            h_starters = lineup_data.get(str(home_info.get("id")), [])
            a_starters = lineup_data.get(str(away_info.get("id")), [])
            lineup_confirmed = bool(lineup_data.get("confirmed"))

            if lineup_confirmed:
                h_core = get_expected_core_players(home_info.get("id"), h_stand.get("league_id"), h_stand.get("season"))
                a_core = get_expected_core_players(away_info.get("id"), a_stand.get("league_id"), a_stand.get("season"))
                h_missing = find_missing_core_players(sorted(set(h_core + h_inj_data.get("ace_names", []))), h_starters)
                a_missing = find_missing_core_players(sorted(set(a_core + a_inj_data.get("ace_names", []))), a_starters)

                h_injury_names = {_normalize_player_name(name) for name in h_inj_data.get("ace_names", [])}
                a_injury_names = {_normalize_player_name(name) for name in a_inj_data.get("ace_names", [])}
                h_unexpected = [name for name in h_missing if _normalize_player_name(name) not in h_injury_names]
                a_unexpected = [name for name in a_missing if _normalize_player_name(name) not in a_injury_names]
                if h_missing:
                    h_lineup_penalty = 0.12 if len(h_unexpected) == 1 else (0.20 if len(h_unexpected) >= 2 else 0.0)
                    h_lineup_msg = f"🚨 [선발 확인] 홈팀 핵심 {', '.join(h_missing[:3])} 선발 제외"
                if a_missing:
                    a_lineup_penalty = 0.12 if len(a_unexpected) == 1 else (0.20 if len(a_unexpected) >= 2 else 0.0)
                    a_lineup_msg = f"🚨 [선발 확인] 원정팀 핵심 {', '.join(a_missing[:3])} 선발 제외"

        h_last_data = fetch_team_last_match_date_api(home_info.get("id"), heavy_ttl)
        a_last_data = fetch_team_last_match_date_api(away_info.get("id"), heavy_ttl)
        h_rest_days = calculate_rest_days(h_last_data.get("date"), final_match_time)
        a_rest_days = calculate_rest_days(a_last_data.get("date"), final_match_time)
        h_extreme_fatigue, a_extreme_fatigue = h_last_data.get("is_extreme_fatigue"), a_last_data.get("is_extreme_fatigue")
         
        h_next = fetch_team_next_fixture_api(home_info.get("id"), heavy_ttl)
        a_next = fetch_team_next_fixture_api(away_info.get("id"), heavy_ttl)
        h_long = fetch_team_long_term_stats_api(home_info.get("id"), heavy_ttl)
        a_long = fetch_team_long_term_stats_api(away_info.get("id"), heavy_ttl)
        h_recent = fetch_team_recent_form_metrics(home_info.get("id"), heavy_ttl)
        a_recent = fetch_team_recent_form_metrics(away_info.get("id"), heavy_ttl)
         
        league_n = m.get('league', '')
        if league_n not in market_performance_cache:
            market_performance_cache[league_n] = load_market_performance(league_n)
        market_performance = market_performance_cache[league_n]
        AVG_H_GF, AVG_A_GF = get_league_averages(league_n)
        AVG_H_GA, AVG_A_GA = AVG_A_GF, AVG_H_GF
         
        HAS = (h_long["home_gf"] / h_long["home_total"]) / AVG_H_GF if h_long["home_total"] > 0 else 1.0
        HDS = (h_long["home_ga"] / h_long["home_total"]) / AVG_H_GA if h_long["home_total"] > 0 else 1.0
        AAS = (a_long["away_gf"] / a_long["away_total"]) / AVG_A_GF if a_long["away_total"] > 0 else 1.0
        ADS = (a_long["away_ga"] / a_long["away_total"]) / AVG_A_GA if a_long["away_total"] > 0 else 1.0
         
        home_adv = 1.08
        if abs(h_rank - a_rank) <= 3 and h_rank != 99:
            home_adv = 1.12
            
        math_exp_h = (HAS * ADS * AVG_H_GF * home_adv) * (0.8 if weather_condition in ["Rain", "Snow"] else 1.0)
        math_exp_a = (AAS * HDS * AVG_A_GF) * (0.8 if weather_condition in ["Rain", "Snow"] else 1.0)
        math_exp_h *= 1.0 + h_recent.get("strength", 0.0)
        math_exp_a *= 1.0 + a_recent.get("strength", 0.0)

        is_cup_or_intl = any(kw in league_n.lower() for kw in ["cup", "컵", "챔피언스", "유로파", "컨퍼런스", "월드컵", "친선", "fa", "코파", "afc", "네이션스"])
        if is_cup_or_intl:
            math_exp_h *= 0.92
            math_exp_a *= 0.92
         
        h_stats = fetch_recent_team_stats_api(home_info.get("id"), heavy_ttl)
        a_stats = fetch_recent_team_stats_api(away_info.get("id"), heavy_ttl)
        
        h_corners = h_stats.get('corners', 4.5)
        h_cards = h_stats.get('yellow_cards', 1.5)
        a_corners = a_stats.get('corners', 4.5)
        a_cards = a_stats.get('yellow_cards', 1.5)

        h_actual_xg = h_stats.get("xg")
        a_actual_xg = a_stats.get("xg")
        h_xg_component = ((float(h_actual_xg) - 1.35) * 0.06) if h_actual_xg is not None else 0.0
        a_xg_component = ((float(a_actual_xg) - 1.35) * 0.06) if a_actual_xg is not None else 0.0
        h_xg_multi = max(0.82, min(1.22, 1.0 + h_xg_component + ((h_stats.get('possession',50) - 50) * 0.008) + ((h_stats.get('shots_on_goal',4.0) - 4.0) * 0.05) + ((h_corners - 4.5) * 0.012) - ((h_cards - 1.5) * 0.015)))
        a_xg_multi = max(0.82, min(1.22, 1.0 + a_xg_component + ((a_stats.get('possession',50) - 50) * 0.008) + ((a_stats.get('shots_on_goal',4.0) - 4.0) * 0.05) + ((a_corners - 4.5) * 0.012) - ((a_cards - 1.5) * 0.015)))
         
        has_market_odds = _valid_three_way_odds([odd_h, odd_d, odd_a])
        if has_market_odds:
            inverse_total = (1 / odd_h) + (1 / odd_d) + (1 / odd_a)
            base_exp_h = (
                (math_exp_h * h_xg_multi * 0.85)
                + (((1 / odd_h) / inverse_total * 2.8) * 0.15)
            )
            base_exp_a = (
                (math_exp_a * a_xg_multi * 0.85)
                + (((1 / odd_a) / inverse_total * 2.8) * 0.15)
            )
        else:
            # 배당이 전혀 없는 경기는 시장값을 만들어 넣지 않고 팀 데이터
            # 기대득점만 사용한다. 이 경우 가치픽은 만들지 않고 확률픽만 낸다.
            base_exp_h = math_exp_h * h_xg_multi
            base_exp_a = math_exp_a * a_xg_multi

        h_depth_factor = calculate_squad_depth_factor(h_stand)
        a_depth_factor = calculate_squad_depth_factor(a_stand)

        # 🔥 확률 떡락 방지 (최대 2개 악재만 반영하여 35% 캡 적용)
        h_fatigue_pct = (0.15 if h_extreme_fatigue else 0.08) if h_rest_days <= 3 else 0.0
        a_fatigue_pct = (0.15 if a_extreme_fatigue else 0.08) if a_rest_days <= 3 else 0.0
        h_rot_pct = 0.10 if h_next["is_important"] and h_next["days_until_next"] <= 4 else 0.0
        a_rot_pct = 0.10 if a_next["is_important"] and a_next["days_until_next"] <= 4 else 0.0

        # 주심 성향은 양 팀 전력 패널티가 아니라 카드/변동성 정보로만 사용한다.
        h_penalties = [h_war_pct, h_fatigue_pct, h_rot_pct, h_lineup_penalty, h_vacation, h_oneman_penalty]
        a_penalties = [a_war_pct, a_fatigue_pct, a_rot_pct, a_lineup_penalty, a_vacation, a_oneman_penalty]
        h_penalties.sort(reverse=True)
        a_penalties.sort(reverse=True)
        
        h_total_penalty = min(0.30, sum(h_penalties[:2]) * h_depth_factor)
        a_total_penalty = min(0.30, sum(a_penalties[:2]) * a_depth_factor)
        
        cross_boost_a = h_total_penalty * 0.4
        cross_boost_h = a_total_penalty * 0.4

        fixture_details = fetch_fixture_details_api(home_info["id"], away_info["id"], heavy_ttl)
        h2h_total = fixture_details.get("total", 0)
        h_wins = fixture_details.get("h_wins", 0)
        h2h_draws = fixture_details.get("draws", 0)
        a_wins = fixture_details.get("a_wins", 0)
        h_h2h_bonus = max(-0.10, min(0.10, ((h_wins - a_wins) / h2h_total) * 0.12)) if h2h_total > 0 else 0
        a_h2h_bonus = -h_h2h_bonus
        
        h_kryptonite, a_kryptonite = 0.0, 0.0
        h_matchup_msg, a_matchup_msg = "", ""
        if h2h_total >= 3:
            if (h_wins / h2h_total) >= 0.65:
                h_kryptonite = 0.04
                h_matchup_msg = f"⚔️ 천적 상성 ({h_wins}승/{h2h_total}전)"
            elif (a_wins / h2h_total) >= 0.65:
                a_kryptonite = 0.04
                a_matchup_msg = f"⚔️ 천적 상성 ({a_wins}승/{h2h_total}전)"

        exp_h = round(max(0.3, min(3.2, (base_exp_h * (1 - h_total_penalty) + cross_boost_h) + h_h2h_bonus + h_kryptonite + rank_diff_bonus_h + h_survival["attack_boost"] + a_survival["opponent_risk_boost"] + h_title_buff + h_market_bonus + h_manager_buff)), 2)
        exp_a = round(max(0.3, min(3.2, (base_exp_a * (1 - a_total_penalty) + cross_boost_a) + a_h2h_bonus + a_kryptonite + rank_diff_bonus_a + a_survival["attack_boost"] + h_survival["opponent_risk_boost"] + a_title_buff + a_market_bonus + a_manager_buff)), 2)

        base_confidence = calculate_data_confidence(
            home_info, away_info, api_fixture_id, h_stand, a_stand, h_long, a_long,
            h_recent, a_recent, h_stats, a_stats, h_inj_data, a_inj_data,
            diff_hours, lineup_confirmed,
        )
        movement_parts = []
        if h_market_bonus > 0:
            movement_parts.append(f"{home_team} 해외 승 배당 하락")
        if a_market_bonus > 0:
            movement_parts.append(f"{away_team} 해외 승 배당 하락")
        if proto_movement.get("summary"):
            movement_parts.append(str(proto_movement["summary"]))
        tactical_parts = [text for text in (h_matchup_msg, a_matchup_msg) if text]
        if is_derby:
            tactical_parts.append("로컬 더비 변동성")
        if h_survival.get("active"):
            tactical_parts.append(f"{home_team} 잔류 경쟁({h_survival['reason']})")
        if a_survival.get("active"):
            tactical_parts.append(f"{away_team} 잔류 경쟁({a_survival['reason']})")
        evidence, data_coverage = build_analysis_evidence({
            "h_recent": h_recent,
            "a_recent": a_recent,
            "h_stats": h_stats,
            "a_stats": a_stats,
            "h_long": h_long,
            "a_long": a_long,
            "h_inj": h_inj_data,
            "a_inj": a_inj_data,
            "h2h_total": h2h_total,
            "h_wins": h_wins,
            "draws": h2h_draws,
            "a_wins": a_wins,
            "h_rest_days": h_rest_days,
            "a_rest_days": a_rest_days,
            "weather": weather_condition if weather_condition not in (None, "", "Unknown") else None,
            "referee": referee,
            "tactical_text": ", ".join(tactical_parts),
            "movement_text": ", ".join(movement_parts),
            "home_id": home_info.get("id"),
            "away_id": away_info.get("id"),
            "fixture_id": api_fixture_id,
        })
        coverage_confidence = 0.35 + (0.60 * data_coverage)
        analysis_confidence = round(
            max(0.35, min(0.95, (base_confidence * 0.45) + (coverage_confidence * 0.55))),
            3,
        )
        if analysis_odds_source == "overseas_fallback":
            analysis_confidence = round(max(0.35, analysis_confidence * 0.95), 3)
        elif analysis_odds_source == "model_only":
            analysis_confidence = round(max(0.35, analysis_confidence * 0.85), 3)
         
        h_win, draw, a_win, prob_u, prob_o, prob_handi_h, prob_handi_d, prob_handi_a = calculate_poisson_probs(exp_h, exp_a, handi_base, uo_base)

        if is_derby:
            draw = min(0.55, draw * 1.15)
            prob_o = min(0.99, prob_o * 1.10)
            prob_u = 1.0 - prob_o

        is_low_score_league = any(kw in league_n.lower() for kw in ["k1", "k리그1", "k2", "k리그2", "j1", "j리그", "j2"])
        if is_low_score_league or is_cup_or_intl:
            prob_u = min(0.90, prob_u * 1.08)
            prob_o = 1.0 - prob_u
            draw = min(0.55, draw * 1.10) 

        if has_market_odds:
            h_win, draw, a_win = calibrate_three_way_probabilities(
                [h_win, draw, a_win], [odd_h, odd_d, odd_a], analysis_confidence
            )
        if uo_under > 1.0 and uo_over > 1.0:
            prob_u, prob_o = calibrate_two_way_probabilities(
                [prob_u, prob_o], [uo_under, uo_over], analysis_confidence
            )
        if min(handi_h, handi_d, handi_a) > 1.0:
            prob_handi_h, prob_handi_d, prob_handi_a = calibrate_three_way_probabilities(
                [prob_handi_h, prob_handi_d, prob_handi_a],
                [handi_h, handi_d, handi_a], analysis_confidence,
            )

        wdl_market = (
            normalize_probabilities([1 / odd_h, 1 / odd_d, 1 / odd_a])
            if has_market_odds else [0.0, 0.0, 0.0]
        )
        underdog_side = (
            "home" if odd_h > odd_a else ("away" if odd_a > odd_h else "")
        ) if has_market_odds else ""
        wdl_cands = [
            {"label": "일반 승무패 예측", "sort_id": 3, "raw_pick": f"{home_team} 승", "html_pick": f"{home_team} 승", "prob": h_win, "ev": h_win * odd_h, "odd": odd_h, "market_prob": wdl_market[0], "selection_side": "home"},
            {"label": "일반 승무패 예측", "sort_id": 3, "raw_pick": "무승부", "html_pick": "무승부", "prob": draw, "ev": draw * odd_d, "odd": odd_d, "market_prob": wdl_market[1], "selection_side": "draw"},
            {"label": "일반 승무패 예측", "sort_id": 3, "raw_pick": f"{away_team} 승", "html_pick": f"{away_team} 승", "prob": a_win, "ev": a_win * odd_a, "odd": odd_a, "market_prob": wdl_market[2], "selection_side": "away"}
        ]
         
        handi_str_raw = f"[{handi_base:+1.1f}] "
        if handi_base < 0:
            h_label = f"🔥 [마핸승] {home_team} ({handi_base:+1.1f})" 
            a_label = f"🛡️ [핸디패] {home_team} 패배 ({handi_base:+1.1f})" 
        else:
            h_label = f"🛡️ [플핸승] {home_team} ({handi_base:+1.1f})"
            a_label = f"🔥 [핸디패] {home_team} 패배 ({handi_base:+1.1f})" 
             
        h_html = f"<span style='color:#00F2FE; font-size:14px; font-weight:900;'>{h_label}</span><br>"
        a_html = f"<span style='color:#00F2FE; font-size:14px; font-weight:900;'>{a_label}</span><br>"
        d_html = f"<span style='color:#00F2FE; font-size:14px; font-weight:900;'>핸디무 ({handi_base:+1.1f})</span><br>"

        handi_cands = []
        handi_has_odds = min(handi_h, handi_d, handi_a) > 1.0
        handi_market = normalize_probabilities(
            [1 / handi_h, 1 / handi_d, 1 / handi_a]
        ) if handi_has_odds else [0, 0, 0]
        if handi_has_odds or abs(handi_base) > 0.001:
            handi_cands = [
                {"label": "핸디캡 예측", "sort_id": 2, "raw_pick": f"{handi_str_raw}{home_team} 핸디승", "html_pick": h_html, "prob": prob_handi_h, "ev": prob_handi_h * handi_h, "odd": handi_h if handi_h > 1.0 else 0.0, "market_prob": handi_market[0], "selection_side": "home", "handicap_base": handi_base},
                {"label": "핸디캡 예측", "sort_id": 2, "raw_pick": f"{handi_str_raw}핸디무", "html_pick": d_html, "prob": prob_handi_d, "ev": prob_handi_d * handi_d, "odd": handi_d if handi_d > 1.0 else 0.0, "market_prob": handi_market[1], "selection_side": "draw", "handicap_base": handi_base},
                {"label": "핸디캡 예측", "sort_id": 2, "raw_pick": f"{handi_str_raw}{home_team} 핸디패", "html_pick": a_html, "prob": prob_handi_a, "ev": prob_handi_a * handi_a, "odd": handi_a if handi_a > 1.0 else 0.0, "market_prob": handi_market[2], "selection_side": "away", "handicap_base": handi_base},
            ]
         
        uo_str_raw = f"(U/O {uo_base})"
        uo_str_html = f"<span style='color:#00F2FE; font-size:14px; font-weight:900;'>[기준 {uo_base}]</span><br>"
        uo_has_odds = uo_under > 1.0 and uo_over > 1.0
        uo_market = normalize_probabilities(
            [1 / uo_under, 1 / uo_over]
        ) if uo_has_odds else [0, 0]
        # 2.5 기준 득점 모델은 배당이 없어도 계산할 수 있다. 따라서 모든
        # 정상 경기에서 교차 시장 대안 후보를 유지하되, 배당이 없으면 가치나
        # VIP 검증에는 사용하지 않는다.
        uo_cands = [
            {"label": "언더 예측", "sort_id": 1, "raw_pick": f"언더 {uo_str_raw}", "html_pick": f"{uo_str_html}⬇️ 언더", "prob": prob_u, "ev": prob_u * uo_under, "odd": uo_under if uo_under > 1.0 else 0.0, "market_prob": uo_market[0], "selection_side": "under"},
            {"label": "오버 예측", "sort_id": 1, "raw_pick": f"오버 {uo_str_raw}", "html_pick": f"{uo_str_html}⬆️ 오버", "prob": prob_o, "ev": prob_o * uo_over, "odd": uo_over if uo_over > 1.0 else 0.0, "market_prob": uo_market[1], "selection_side": "over"},
        ]
         
        # 승무패와 핸디캡은 서로 모순되는 시장이 아니다. 예를 들어 홈팀의
        # 1골 차 승리는 일반 승과 -1.0 핸디무를 동시에 만들 수 있다.
        # 예전의 문자열 기반 "상충 후보" 제거는 실제 정답 후보를 버릴 수
        # 있으므로, 제공된 세 시장의 모든 방향을 같은 출발선에서 비교한다.
        all_market_picks = wdl_cands + handi_cands + uo_cands
        valid_all_picks = all_market_picks
        calibrate_market_candidates(
            all_market_picks, market_performance, analysis_confidence
        )
        attach_underdog_signals(
            all_market_picks,
            home_team,
            away_team,
            {
                "home_absence": h_total_penalty,
                "away_absence": a_total_penalty,
                "home_market_bonus": h_market_bonus,
                "away_market_bonus": a_market_bonus,
                "home_tactical": h_kryptonite,
                "away_tactical": a_kryptonite,
                "home_recent": h_recent.get("strength", 0.0),
                "away_recent": a_recent.get("strength", 0.0),
                "home_rest": h_rest_days if h_rest_days < 90 else 0,
                "away_rest": a_rest_days if a_rest_days < 90 else 0,
                "home_lineup": h_lineup_penalty,
                "away_lineup": a_lineup_penalty,
                "underdog_side": underdog_side,
            },
        )
        annotate_pick_metrics(all_market_picks, analysis_confidence)
        pick_categories, ev_sorted_picks = select_pick_categories(
            valid_all_picks, analysis_confidence
        )
        highest_prob_pick = pick_categories["high_probability"]
        honey_pick = pick_categories["honey"]
        vip_underdog_pick = pick_categories["vip_underdog"]

        # 공식 예측과 채점은 한 경기당 최종 추천픽 하나뿐이다. 꿀픽/VIP는
        # 그 동일 픽의 등급이므로 별도 예측 칸에 복제 저장하지 않는다.
        highest_ev_pick = None
        detailed_report = build_detailed_report(
            highest_prob_pick,
            evidence,
            analysis_confidence,
            all_market_picks,
            pick_categories,
            {
                "home": home_team,
                "away": away_team,
                "exp_h": exp_h,
                "exp_a": exp_a,
                "weather": weather_condition,
            },
        )

        badge_templates = {
            "high_probability": "<span style='background:#10B981;color:#fff;padding:3px 8px;border-radius:4px;font-size:12px;font-weight:bold;margin-right:4px;'>🎯 최종 추천픽</span>",
            "honey": "<span style='background:#F59E0B;color:#fff;padding:3px 8px;border-radius:4px;font-size:12px;font-weight:bold;margin-right:4px;'>🍯 배당가치 우수</span>",
            "vip_underdog": "<span style='background:linear-gradient(to right,#FFD700,#F59E0B);color:#000;padding:3px 8px;border-radius:4px;font-size:12px;font-weight:900;margin-right:4px;box-shadow:0 0 5px rgba(255,215,0,.5);'>💎 VIP 검증 등급</span>",
        }
        for pick in valid_all_picks:
            matching_categories = [
                category_key
                for category_key, selected in pick_categories.items()
                if selected and selected.get("raw_pick") == pick.get("raw_pick")
            ]
            if matching_categories:
                badges = "".join(badge_templates[key] for key in matching_categories)
                pick["html_pick"] = (
                    "<div style='margin-bottom:6px;'>" + badges + "</div>"
                    + pick.get("html_pick", "")
                )

        # select_pick_categories는 안전하게 복사본을 반환하므로, 배지를 적용한
        # 원본의 표시 문자열만 다시 동기화합니다.
        for selected in pick_categories.values():
            if not selected:
                continue
            source_pick = next(
                (
                    pick
                    for pick in valid_all_picks
                    if pick.get("raw_pick") == selected.get("raw_pick")
                ),
                None,
            )
            if source_pick:
                selected["html_pick"] = source_pick.get(
                    "html_pick", selected.get("html_pick", "")
                )

        analysis_stage = prediction_stage(diff_hours, lineup_confirmed)
        if analysis_odds_source == "overseas_fallback":
            analysis_stage = "overseas-preview"
        elif analysis_odds_source == "model_only":
            analysis_stage = "model-only-preview"
        reliability_score = round(
            (highest_prob_pick.get("safe_score", 0) * 0.75)
            + (highest_prob_pick.get("recommendation_score", 0) * 0.25), 4
        )
        underdog_signal = vip_underdog_pick.get("raw_pick") if vip_underdog_pick else ""
         
        save_dual_predictions_to_local_db(
            m['id'], league_n, home_team, away_team, 
            highest_prob_pick["raw_pick"], round(highest_prob_pick["prob"] * 100, 1), 
            highest_ev_pick["raw_pick"] if highest_ev_pick else "",
            round(highest_ev_pick["prob"] * 100, 1) if highest_ev_pick else 0.0,
            odd_h, odd_d, odd_a, final_match_time, 0, api_fixture_id,
            analysis_stage, analysis_confidence,
        )
        save_prediction_analysis(
            m["id"], highest_prob_pick, analysis_confidence,
            evidence, all_market_picks, detailed_report,
            categories=pick_categories,
            analysis_stage=analysis_stage,
            odds_source=analysis_odds_source,
        )

        h_form = fetch_team_form_api(home_info.get("id"), heavy_ttl)
        a_form = fetch_team_form_api(away_info.get("id"), heavy_ttl)
        story = "<br><br>".join(
            paragraph.replace("\n", "<br>")
            for paragraph in detailed_report.split("\n\n")
        )
        if analysis_odds_source == "overseas_fallback":
            story = (
                "🌍 <b>[해외 임시배당 분석]</b> 베트맨 승무패 배당 공개 전이라 "
                "동일 경기의 해외 1X2 중앙값으로 먼저 계산했습니다. 베트맨 "
                "배당이 들어오면 자동으로 다시 분석합니다.<br><br>" + story
            )
        elif analysis_odds_source == "model_only":
            story = (
                "📊 <b>[팀 데이터 모델 선픽]</b> 베트맨과 해외배당이 모두 준비되지 "
                "않아 최근 경기·득실·홈원정·선수 정보를 중심으로 최종 추천픽 하나를 "
                "먼저 계산했습니다. 실제 배당이 들어오면 같은 픽의 가치 등급을 자동으로 다시 검증합니다."
                "<br><br>" + story
            )
        
        if is_derby: story += " ⚔️ [로컬 더비 매치] 양 팀의 자존심이 걸린 치열한 라이벌전으로, 통계를 뛰어넘는 혈투와 변수(카드/극장골)가 예상됩니다."
        if h_manager_buff > 0: story += f" 👔 [경질 버프] {home_team}은(는) 새 감독 부임 이후 선수들의 주전 경쟁과 동기부여가 극에 달해 있습니다."
        if a_manager_buff > 0: story += f" 👔 [경질 버프] 원정팀 {away_team}은(는) 최근 감독 교체로 인한 '허니문 효과'가 강력하게 발동될 타이밍입니다."
        if h_vacation > 0 or a_vacation > 0: story += " 🏖️ [휴가 모드 주의] 시즌 막판 동기부여가 떨어진 중위권 팀의 안일한 경기력이 이변을 만들 수 있습니다."
        if h_survival.get("active"): story += f" 🔥 [잔류 생존전] {home_team}: {h_survival['reason']}. 공격 의지와 뒷공간 위험을 함께 반영했습니다."
        if a_survival.get("active"): story += f" 🔥 [잔류 생존전] {away_team}: {a_survival['reason']}. 공격 의지와 뒷공간 위험을 함께 반영했습니다."

        h_inj_html = _render_team_availability_status(
            h_inj_data, diff_hours, lineup_confirmed, h_lineup_msg
        )
        if h_war_score > 0:
            h_war_text = " / ".join(h_war_details)
            if h_oneman_penalty > 0: h_inj_html += f"<div class='injury-badge' style='background: rgba(220,38,38,0.2); border-color: #EF4444; color: #EF4444;'>🚨 득점루트 붕괴: 팀 득점의 {int(h_goal_dep_ratio*100)}% 이탈</div>"
            elif h_inj_data['ace_missing']: h_inj_html += f"<div class='injury-badge' style='background: rgba(220,38,38,0.2); border-color: #EF4444; color: #EF4444;'>🚨 전력누수(-{h_war_score:.1f}점): {h_war_text}</div>"
            else: h_inj_html += f"<div class='injury-badge'>🏥 전력누수(-{h_war_score:.1f}점): {h_war_text}</div>"
        if h_lineup_msg: h_inj_html += f"<div class='injury-badge' style='background: #EF4444; color: #fff;'>{h_lineup_msg}</div>"
        if h_title_buff > 0: h_inj_html += f"<div class='injury-badge' style='background: rgba(16, 185, 129, 0.2); border-color: #10B981; color: #10B981;'>🏆 우승 경쟁 버프</div>"
        if h_manager_buff > 0: h_inj_html += f"<div class='injury-badge' style='background: rgba(245, 158, 11, 0.2); border-color: #F59E0B; color: #F59E0B;'>👔 새 감독 버프 (부임 {h_manager['days_since_hired']}일차)</div>"
        if h_vacation > 0: h_inj_html += f"<div class='injury-badge' style='background: rgba(100, 116, 139, 0.2); border-color: #64748B; color: #64748B;'>🏖️ 동기부여 상실 (휴가 모드)</div>"
        if h_survival.get("active"): h_inj_html += f"<div class='injury-badge' style='background: rgba(249, 115, 22, 0.18); border-color: #F97316; color: #FB923C;'>🔥 잔류 생존전 · {h_survival['reason']}</div>"
        if h_matchup_msg: h_inj_html += f"<div class='injury-badge' style='background: rgba(59, 130, 246, 0.2); border-color: #3B82F6; color: #3B82F6;'>{h_matchup_msg}</div>"
        if is_derby: h_inj_html += f"<div class='injury-badge' style='background: rgba(239, 68, 68, 0.2); border-color: #EF4444; color: #EF4444;'>⚔️ 치열한 로컬 더비 매치</div>"

        a_inj_html = _render_team_availability_status(
            a_inj_data, diff_hours, lineup_confirmed, a_lineup_msg
        )
        if a_war_score > 0:
            a_war_text = " / ".join(a_war_details)
            if a_oneman_penalty > 0: a_inj_html += f"<div class='injury-badge' style='background: rgba(220,38,38,0.2); border-color: #EF4444; color: #EF4444;'>🚨 득점루트 붕괴: 팀 득점의 {int(a_goal_dep_ratio*100)}% 이탈</div>"
            elif a_inj_data['ace_missing']: a_inj_html += f"<div class='injury-badge' style='background: rgba(220,38,38,0.2); border-color: #EF4444; color: #EF4444;'>🚨 전력누수(-{a_war_score:.1f}점): {a_war_text}</div>"
            else: a_inj_html += f"<div class='injury-badge'>🏥 전력누수(-{a_war_score:.1f}점): {a_war_text}</div>"
        if a_lineup_msg: a_inj_html += f"<div class='injury-badge' style='background: #EF4444; color: #fff;'>{a_lineup_msg}</div>"
        if a_title_buff > 0: a_inj_html += f"<div class='injury-badge' style='background: rgba(16, 185, 129, 0.2); border-color: #10B981; color: #10B981;'>🏆 우승 경쟁 버프</div>"
        if a_manager_buff > 0: a_inj_html += f"<div class='injury-badge' style='background: rgba(245, 158, 11, 0.2); border-color: #F59E0B; color: #F59E0B;'>👔 새 감독 버프 (부임 {a_manager['days_since_hired']}일차)</div>"
        if a_vacation > 0: a_inj_html += f"<div class='injury-badge' style='background: rgba(100, 116, 139, 0.2); border-color: #64748B; color: #64748B;'>🏖️ 동기부여 상실 (휴가 모드)</div>"
        if a_survival.get("active"): a_inj_html += f"<div class='injury-badge' style='background: rgba(249, 115, 22, 0.18); border-color: #F97316; color: #FB923C;'>🔥 잔류 생존전 · {a_survival['reason']}</div>"
        if a_matchup_msg: a_inj_html += f"<div class='injury-badge' style='background: rgba(59, 130, 246, 0.2); border-color: #3B82F6; color: #3B82F6;'>{a_matchup_msg}</div>"
        if is_derby: a_inj_html += f"<div class='injury-badge' style='background: rgba(239, 68, 68, 0.2); border-color: #EF4444; color: #EF4444;'>⚔️ 치열한 로컬 더비 매치</div>"
         
        dashboard_proto.append({
            "match": m, "final_match_time": final_match_time, "timestamp": m_dt.timestamp(), "league": league_n,
            "home_logo": home_info.get("logo"), "away_logo": away_info.get("logo"),
            "story": story, "ev_sorted_picks": ev_sorted_picks,
            "pick_categories": pick_categories,
            "home_form": h_form, "away_form": a_form,
            "analysis_version": ANALYSIS_VERSION, "analysis_confidence": analysis_confidence,
            "underdog_gate_version": UNDERDOG_GATE_VERSION,
            "analysis_stage": analysis_stage, "reliability_score": reliability_score,
            "odds_source": analysis_odds_source,
            "betman_odds_pending": bool(m.get("betman_odds_pending")),
            "data_coverage": data_coverage,
            "lineup_confirmed": bool(lineup_confirmed),
            "probability_error_margin": highest_prob_pick.get("error_margin"),
            "probability_interval": highest_prob_pick.get("probability_interval"),
            "model_probability": highest_prob_pick.get("prob"),
            "fair_market_probability": highest_prob_pick.get("fair_prob"),
            "model_market_edge": highest_prob_pick.get("edge"),
            "used_feature_evidence": evidence,
            "survival_motivation": {"home": h_survival, "away": a_survival},
            "market_probability_analysis": [
                {
                    "market": infer_pick_market(pick),
                    "pick": pick.get("raw_pick"),
                    "model_probability": pick.get("prob"),
                    "fair_probability": pick.get("fair_prob"),
                    "edge": pick.get("edge"),
                    "market_hit_rate": pick.get("market_hit_rate"),
                    "history_samples": pick.get("market_history_samples"),
                }
                for pick in valid_all_picks
            ],
            "detailed_report": detailed_report,
            "underdog_signal": underdog_signal,
            "h_inj_html": h_inj_html, "a_inj_html": a_inj_html, 
            "h_rest_html": f"<div class='fatigue-badge'>💦 체력 방전</div>" if h_rest_days <= 3 else "", "a_rest_html": f"<div class='fatigue-badge'>💦 체력 방전</div>" if a_rest_days <= 3 else "",
            "h_rank_html": f"<div class='rank-badge'>🏆 순위: {h_rank}위</div>" if h_rank != 99 else "", "a_rank_html": f"<div class='rank-badge'>🏆 순위: {a_rank}위</div>" if a_rank != 99 else ""
        })

    double_pick_count = 0
    single_pick_count = 0
    unavailable_pick_count = 0
    suppressed_double_count = 0
    frozen_prediction_count = 0
    total_combinations = 1
    cost_cap_exceeded_by_frozen = False
      
    for idx, m in enumerate(toto_14_matches, 1):
        home_team, away_team = m["home"], m["away"]
        match_id = f"TOTO14_{m['id']}"
        match_time = m.get("match_time") or "시간 미정"
        now = datetime.now(KST)
        scheduled_dt = _parse_kst_match_time(match_time)
        m_dt = parse_match_time(match_time)
        diff_hours = (m_dt - now).total_seconds() / 3600.0
        home_info, away_info, identity_fixture = resolve_match_team_pair(
            home_team, away_team, match_time, ttl_h=2
        )

        frozen_item = None
        frozen_record = frozen_toto14.get(match_id)
        if (
            isinstance(frozen_record, dict)
            and str(frozen_record.get("home_team")) == str(home_team)
            and str(frozen_record.get("away_team")) == str(away_team)
            and isinstance(frozen_record.get("payload"), dict)
        ):
            frozen_item = dict(frozen_record["payload"])

        kickoff_passed = bool(scheduled_dt and now >= scheduled_dt)
        freeze_needs_persist = False
        previous_item = previous_toto14.get(str(m.get("id", "")))
        previous_match = previous_item.get("match", {}) if isinstance(previous_item, dict) else {}
        previous_is_pre_kickoff = bool(
            isinstance(previous_item, dict)
            and str(previous_match.get("home")) == str(home_team)
            and str(previous_match.get("away")) == str(away_team)
            and previous_generated_at
            and scheduled_dt
            and previous_generated_at <= scheduled_dt
        )
        previous_stage = str(previous_item.get("analysis_stage", "")) if isinstance(previous_item, dict) else ""
        if (
            frozen_item is None
            and previous_is_pre_kickoff
            and (
                kickoff_passed
                or previous_stage in {"T-30-final", "locked"}
                or previous_item.get("prediction_frozen") is True
            )
        ):
            frozen_item = dict(previous_item)
            frozen_item["frozen_from_stage"] = previous_stage or "regular"
            if kickoff_passed:
                frozen_item["analysis_stage"] = "locked"
            freeze_needs_persist = True

        if frozen_item is None:
            snapshot_item = _locked_toto14_fallback(m)
            snapshot_is_final = bool(
                snapshot_item
                and str(snapshot_item.get("frozen_from_stage", ""))
                in {"T-30-final", "locked"}
            )
            if snapshot_item and (kickoff_passed or snapshot_is_final):
                frozen_item = snapshot_item
                if not kickoff_passed and snapshot_is_final:
                    frozen_item["analysis_stage"] = snapshot_item["frozen_from_stage"]
                freeze_needs_persist = True

        if frozen_item is None and kickoff_passed:
            frozen_item = _unavailable_toto14_item(m)
            freeze_needs_persist = True

        # 예측 선택은 동결해도 잘못된 팀 ID와 로고는 동결하지 않는다.
        # 실제 경기표에서 다시 확인한 표시·신원 정보만 매 수집 때 갱신한다.
        if frozen_item is not None:
            frozen_item = dict(frozen_item)
            frozen_item["home_logo"] = home_info.get("logo")
            frozen_item["away_logo"] = away_info.get("logo")
            frozen_item["home_id"] = int(home_info.get("id") or 0)
            frozen_item["away_id"] = int(away_info.get("id") or 0)
            if identity_fixture:
                frozen_item["api_fixture_id"] = int(
                    identity_fixture.get("fixture", {}).get("id") or 0
                )

        if frozen_item is not None and freeze_needs_persist:
            stored_item = _freeze_toto14_prediction(
                match_id, home_team, away_team, match_time, frozen_item
            )
            frozen_item = stored_item or frozen_item
            frozen_toto14[match_id] = {
                "home_team": home_team,
                "away_team": away_team,
                "payload": frozen_item,
            }

        if frozen_item is not None:
            frozen_item = dict(frozen_item)
            frozen_item["match"] = dict(m)
            frozen_item["prediction_frozen"] = True
            picks = _normalize_toto14_picks(
                frozen_item.get("picks")
                or _toto14_picks_from_display(
                    frozen_item.get("best_pick_display"), home_team, away_team
                )
            )
            frozen_item["picks"] = picks
            frozen_item["picks_html"] = _render_toto14_picks_html(picks)
            if len(picks) == 2:
                total_combinations *= 2
                double_pick_count += 1
                if total_combinations > TOTO14_MAX_COMBINATIONS:
                    cost_cap_exceeded_by_frozen = True
            elif len(picks) == 1:
                single_pick_count += 1
            else:
                unavailable_pick_count += 1
            dashboard_toto14.append(frozen_item)
            frozen_prediction_count += 1
            continue

        heavy_ttl = 24
        # 경기 직전에는 결장 정보가 자주 바뀌므로 짧게 갱신한다.
        inj_ttl = 0.5 if diff_hours <= 1.5 else 12
        odds_ttl = 0.5 if diff_hours <= 2 else 4
        lineup_ttl = 0.25 if diff_hours <= 1.5 else 12
         
        os_data = fetch_overseas_odds_and_fixture_api(home_info.get("id"), away_info.get("id"), odds_ttl, m.get("match_time") or "시간 미정")
        api_fixture_id = os_data.get("fixture_id", 0) if os_data else 0
        referee = os_data.get("referee") if os_data else None
        city = os_data.get("city") if os_data else None
        weather_condition = fetch_weather_api(city, odds_ttl) 
         
        h_stand = fetch_team_standing_api(home_info.get("id"), heavy_ttl)
        a_stand = fetch_team_standing_api(away_info.get("id"), heavy_ttl)
        h_rank, a_rank = h_stand["rank"], a_stand["rank"]
        
        h_manager = fetch_new_manager_status(home_info.get("id"), heavy_ttl)
        a_manager = fetch_new_manager_status(away_info.get("id"), heavy_ttl)
        h_manager_buff = 0.08 if h_manager.get("is_new_manager") else 0.0
        a_manager_buff = 0.08 if a_manager.get("is_new_manager") else 0.0
        is_derby = check_derby_match(home_team, away_team)
        
        if h_rank != 99 and a_rank != 99:
            rank_diff_bonus_h = max(-0.18, min(0.18, (a_rank - h_rank) * 0.012))
            rank_diff_bonus_a = -rank_diff_bonus_h
        else:
            rank_diff_bonus_h = rank_diff_bonus_a = 0.0
        h_survival = calculate_survival_motivation(h_stand)
        a_survival = calculate_survival_motivation(a_stand)
        h_title_buff = 0.06 if 1 <= h_rank <= 3 else 0.0
        a_title_buff = 0.06 if 1 <= a_rank <= 3 else 0.0
        
        h_played = h_stand.get("played", 0)
        h_total_teams = h_stand.get("total_teams", 20)
        h_team_goals = h_stand.get("team_goals", 0)
        is_late_season_h = h_played > 0 and (h_played / max(1, (h_total_teams - 1) * 2)) >= 0.75
        h_vacation = 0.08 if (is_late_season_h and 6 <= h_rank <= max(10, h_total_teams - 4)) else 0.0
        
        a_played = a_stand.get("played", 0)
        a_total_teams = a_stand.get("total_teams", 20)
        a_team_goals = a_stand.get("team_goals", 0)
        is_late_season_a = a_played > 0 and (a_played / max(1, (a_total_teams - 1) * 2)) >= 0.75
        a_vacation = 0.08 if (is_late_season_a and 6 <= a_rank <= max(10, a_total_teams - 4)) else 0.0

        h_inj_data = fetch_team_injuries_api(home_info.get("id"), h_stand.get("league_id"), h_stand.get("season"), inj_ttl, api_fixture_id)
        a_inj_data = fetch_team_injuries_api(away_info.get("id"), a_stand.get("league_id"), a_stand.get("season"), inj_ttl, api_fixture_id)
        h_inj_count, a_inj_count = h_inj_data["count"], a_inj_data["count"]
        
        h_missing_goals = h_inj_data.get("missing_goals", 0)
        h_goal_dep_ratio = (h_missing_goals / h_team_goals) if h_team_goals > 0 else 0
        h_oneman_penalty = 0.15 if h_goal_dep_ratio >= 0.25 else 0.0
        
        a_missing_goals = a_inj_data.get("missing_goals", 0)
        a_goal_dep_ratio = (a_missing_goals / a_team_goals) if a_team_goals > 0 else 0
        a_oneman_penalty = 0.15 if a_goal_dep_ratio >= 0.25 else 0.0
        
        h_war_pct, h_war_details, h_war_score = calculate_war_penalty(home_team, h_inj_data["ace_names"], h_inj_count, home_info.get("id"))
        a_war_pct, a_war_details, a_war_score = calculate_war_penalty(away_team, a_inj_data["ace_names"], a_inj_count, away_info.get("id"))

        h_lineup_penalty, a_lineup_penalty = 0.0, 0.0
        h_lineup_msg, a_lineup_msg = "", ""
        lineup_confirmed = False
        if 0 < diff_hours <= 1.5 and api_fixture_id:
            lineup_data = fetch_lineups_api(api_fixture_id, lineup_ttl)
            lineup_confirmed = bool(lineup_data.get("confirmed"))
            if lineup_confirmed:
                h_starters = lineup_data.get(str(home_info.get("id")), [])
                a_starters = lineup_data.get(str(away_info.get("id")), [])
                h_core = get_expected_core_players(home_info.get("id"), h_stand.get("league_id"), h_stand.get("season"))
                a_core = get_expected_core_players(away_info.get("id"), a_stand.get("league_id"), a_stand.get("season"))
                h_missing = find_missing_core_players(sorted(set(h_core + h_inj_data.get("ace_names", []))), h_starters)
                a_missing = find_missing_core_players(sorted(set(a_core + a_inj_data.get("ace_names", []))), a_starters)
                h_injury_names = {_normalize_player_name(name) for name in h_inj_data.get("ace_names", [])}
                a_injury_names = {_normalize_player_name(name) for name in a_inj_data.get("ace_names", [])}
                h_unexpected = [name for name in h_missing if _normalize_player_name(name) not in h_injury_names]
                a_unexpected = [name for name in a_missing if _normalize_player_name(name) not in a_injury_names]
                h_lineup_penalty = 0.12 if len(h_unexpected) == 1 else (0.20 if len(h_unexpected) >= 2 else 0.0)
                a_lineup_penalty = 0.12 if len(a_unexpected) == 1 else (0.20 if len(a_unexpected) >= 2 else 0.0)
                if h_missing:
                    h_lineup_msg = f"🚨 [선발 확인] 핵심 {', '.join(h_missing[:3])} 선발 제외"
                if a_missing:
                    a_lineup_msg = f"🚨 [선발 확인] 핵심 {', '.join(a_missing[:3])} 선발 제외"

        h_inj_html = _render_team_availability_status(
            h_inj_data, diff_hours, lineup_confirmed, h_lineup_msg
        )
        a_inj_html = _render_team_availability_status(
            a_inj_data, diff_hours, lineup_confirmed, a_lineup_msg
        )
        if h_war_score > 0:
            h_war_text = " / ".join(h_war_details)
            h_inj_html += f"<div class='injury-badge'>🏥 전력누수(-{h_war_score:.1f}점): {h_war_text}</div>"
        if a_war_score > 0:
            a_war_text = " / ".join(a_war_details)
            a_inj_html += f"<div class='injury-badge'>🏥 전력누수(-{a_war_score:.1f}점): {a_war_text}</div>"
        if h_lineup_msg:
            h_inj_html += f"<div class='injury-badge' style='background:#EF4444; color:#fff;'>{h_lineup_msg}</div>"
        if a_lineup_msg:
            a_inj_html += f"<div class='injury-badge' style='background:#EF4444; color:#fff;'>{a_lineup_msg}</div>"
        if h_survival.get("active"):
            h_inj_html += f"<div class='injury-badge' style='background:rgba(249,115,22,.18); border-color:#F97316; color:#FB923C;'>🔥 잔류 생존전 · {h_survival['reason']}</div>"
        if a_survival.get("active"):
            a_inj_html += f"<div class='injury-badge' style='background:rgba(249,115,22,.18); border-color:#F97316; color:#FB923C;'>🔥 잔류 생존전 · {a_survival['reason']}</div>"

        h_last_data = fetch_team_last_match_date_api(home_info.get("id"), heavy_ttl)
        a_last_data = fetch_team_last_match_date_api(away_info.get("id"), heavy_ttl)
        h_rest_days = calculate_rest_days(h_last_data.get("date"), m.get("match_time"))
        a_rest_days = calculate_rest_days(a_last_data.get("date"), m.get("match_time"))
        h_extreme_fatigue, a_extreme_fatigue = h_last_data.get("is_extreme_fatigue"), a_last_data.get("is_extreme_fatigue")

        h_next = fetch_team_next_fixture_api(home_info.get("id"), heavy_ttl)
        a_next = fetch_team_next_fixture_api(away_info.get("id"), heavy_ttl)
        h_long = fetch_team_long_term_stats_api(home_info.get("id"), heavy_ttl)
        a_long = fetch_team_long_term_stats_api(away_info.get("id"), heavy_ttl)
        h_recent = fetch_team_recent_form_metrics(home_info.get("id"), heavy_ttl)
        a_recent = fetch_team_recent_form_metrics(away_info.get("id"), heavy_ttl)
         
        league_n_14 = m.get('league', '')
        AVG_H_GF_14, AVG_A_GF_14 = get_league_averages(league_n_14)
        AVG_H_GA_14, AVG_A_GA_14 = AVG_A_GF_14, AVG_H_GF_14

        HAS = (h_long["home_gf"] / h_long["home_total"]) / AVG_H_GF_14 if h_long["home_total"] > 0 else 1.0
        HDS = (h_long["home_ga"] / h_long["home_total"]) / AVG_H_GA_14 if h_long["home_total"] > 0 else 1.0
        AAS = (a_long["away_gf"] / a_long["away_total"]) / AVG_A_GF_14 if a_long["away_total"] > 0 else 1.0
        ADS = (a_long["away_ga"] / a_long["away_total"]) / AVG_A_GA_14 if a_long["away_total"] > 0 else 1.0
         
        home_adv = 1.08
        if abs(h_rank - a_rank) <= 3 and h_rank != 99:
            home_adv = 1.12
            
        math_exp_h = (HAS * ADS * AVG_H_GF_14 * home_adv) * (0.8 if weather_condition in ["Rain", "Snow"] else 1.0)
        math_exp_a = (AAS * HDS * AVG_A_GF_14) * (0.8 if weather_condition in ["Rain", "Snow"] else 1.0)
        math_exp_h *= 1.0 + h_recent.get("strength", 0.0)
        math_exp_a *= 1.0 + a_recent.get("strength", 0.0)

        is_cup_or_intl_14 = any(kw in league_n_14.lower() for kw in ["cup", "컵", "챔피언스", "유로파", "컨퍼런스", "월드컵", "친선", "fa", "코파", "afc", "네이션스"])
        if is_cup_or_intl_14:
            math_exp_h *= 0.92
            math_exp_a *= 0.92

        h_depth_factor = calculate_squad_depth_factor(h_stand)
        a_depth_factor = calculate_squad_depth_factor(a_stand)
        
        # 중복 악재의 이중 계산을 막고 가장 큰 두 항목만 제한적으로 반영한다.
        h_inj_pct = h_war_pct
        a_inj_pct = a_war_pct
        h_fatigue_pct = (0.15 if h_extreme_fatigue else 0.08) if h_rest_days <= 3 else 0.0
        a_fatigue_pct = (0.15 if a_extreme_fatigue else 0.08) if a_rest_days <= 3 else 0.0
        h_rot_pct = 0.10 if h_next["is_important"] and h_next["days_until_next"] <= 4 else 0.0
        a_rot_pct = 0.10 if a_next["is_important"] and a_next["days_until_next"] <= 4 else 0.0

        h_penalties = [h_inj_pct, h_fatigue_pct, h_rot_pct, h_lineup_penalty, h_vacation, h_oneman_penalty]
        a_penalties = [a_inj_pct, a_fatigue_pct, a_rot_pct, a_lineup_penalty, a_vacation, a_oneman_penalty]
        h_penalties.sort(reverse=True)
        a_penalties.sort(reverse=True)
        
        h_total_penalty = min(0.30, sum(h_penalties[:2]) * h_depth_factor)
        a_total_penalty = min(0.30, sum(a_penalties[:2]) * a_depth_factor)
        
        cross_boost_a = h_total_penalty * 0.4
        cross_boost_h = a_total_penalty * 0.4

        fixture_details = fetch_fixture_details_api(home_info["id"], away_info["id"], heavy_ttl)
        h2h_total = fixture_details.get("total", 0)
        h_wins = fixture_details.get("h_wins", 0)
        a_wins = fixture_details.get("a_wins", 0)
        h_h2h_bonus = max(-0.10, min(0.10, ((h_wins - a_wins) / h2h_total) * 0.12)) if h2h_total > 0 else 0.0
        a_h2h_bonus = -h_h2h_bonus
        
        h_kryptonite, a_kryptonite = 0.0, 0.0
        if h2h_total >= 3:
            if (h_wins / h2h_total) >= 0.65: h_kryptonite = 0.04
            elif (a_wins / h2h_total) >= 0.65: a_kryptonite = 0.04
         
        p_h, p_d, p_a = 0.34, 0.33, 0.33
        if os_data and os_data.get("odd_h"):
            total_o = (1/os_data["odd_h"]) + (1/os_data["odd_d"]) + (1/os_data["odd_a"])
            p_h = (1/os_data["odd_h"]) / total_o
            p_a = (1/os_data["odd_a"]) / total_o
         
        odds_exp_h = p_h * 2.8
        odds_exp_a = p_a * 2.8
         
        exp_h = round(max(0.3, min(3.2, ((math_exp_h * 0.75) + (odds_exp_h * 0.25)) * (1 - h_total_penalty) + cross_boost_h + h_h2h_bonus + h_kryptonite + rank_diff_bonus_h + h_survival["attack_boost"] + a_survival["opponent_risk_boost"] + h_title_buff + h_manager_buff)), 2)
        exp_a = round(max(0.3, min(3.2, ((math_exp_a * 0.75) + (odds_exp_a * 0.25)) * (1 - a_total_penalty) + cross_boost_a + a_h2h_bonus + a_kryptonite + rank_diff_bonus_a + a_survival["attack_boost"] + h_survival["opponent_risk_boost"] + a_title_buff + a_manager_buff)), 2)

        analysis_confidence = calculate_data_confidence(
            home_info, away_info, api_fixture_id, h_stand, a_stand, h_long, a_long,
            h_recent, a_recent, {}, {}, h_inj_data, a_inj_data,
            diff_hours, lineup_confirmed,
        )
         
        h_win, draw, a_win, _, _, _, _, _ = calculate_poisson_probs(exp_h, exp_a)
        
        if is_derby: draw = min(0.55, draw * 1.15)
        if is_cup_or_intl_14: draw = min(0.55, draw * 1.10)

        market_odds = []
        if os_data and min(float(os_data.get("odd_h") or 0), float(os_data.get("odd_d") or 0), float(os_data.get("odd_a") or 0)) > 1.0:
            market_odds = [os_data["odd_h"], os_data["odd_d"], os_data["odd_a"]]
        h_win, draw, a_win = calibrate_three_way_probabilities(
            [h_win, draw, a_win], market_odds, analysis_confidence
        )

        # 베트맨 투표율은 정답이 아닌 대중 심리라서, 배당을 못 찾았을 때만 8% 참고한다.
        vote_values = [m.get("vote_h"), m.get("vote_d"), m.get("vote_a")]
        if not market_odds and all(value is not None for value in vote_values):
            vote_probs = normalize_probabilities(vote_values)
            h_win, draw, a_win = normalize_probabilities([
                h_win * 0.92 + vote_probs[0] * 0.08,
                draw * 0.92 + vote_probs[1] * 0.08,
                a_win * 0.92 + vote_probs[2] * 0.08,
            ])

        pct_h = round(h_win * 100, 1)
        pct_d = round(draw * 100, 1)
        pct_a = round(100.0 - pct_h - pct_d, 1)

        probs_dict = {"승": pct_h, "무": pct_d, "패": pct_a}
        picks, first_pct, double_suppressed = _choose_toto14_picks(
            probs_dict, total_combinations
        )
        disp_texts = []
        for p in picks:
            if p == "승": disp_texts.append(f"{m['home']} 승")
            elif p == "패": disp_texts.append(f"{m['away']} 승")
            else: disp_texts.append("무승부")
        best_pick_display = ", ".join(disp_texts)
        picks_html = _render_toto14_picks_html(picks)
         
        analysis_stage = prediction_stage(diff_hours, lineup_confirmed)
        crossed_kickoff_during_analysis = bool(
            scheduled_dt and datetime.now(KST) >= scheduled_dt
        )
        if crossed_kickoff_during_analysis:
            # Discard every value calculated after the analysis began. Only a
            # snapshot whose timestamp predates kickoff may become the result.
            toto_item = _locked_toto14_fallback(m) or _unavailable_toto14_item(m)
            stored_item = _freeze_toto14_prediction(
                match_id, home_team, away_team, match_time, toto_item
            )
            toto_item = stored_item or toto_item
            toto_item["match"] = dict(m)
            double_suppressed = False
            frozen_prediction_count += 1
        else:
            prediction_saved = save_dual_predictions_to_local_db(
                match_id, '승무패 14경기', home_team, away_team,
                best_pick_display, first_pct, best_pick_display, first_pct,
                0, 0, 0, match_time, 1, api_fixture_id,
                analysis_stage, analysis_confidence,
            )
            toto_item = {
                "match": m, "home_logo": home_info.get("logo"), "away_logo": away_info.get("logo"),
                "best_pick_display": best_pick_display, "p_h": pct_h, "p_d": pct_d, "p_a": pct_a,
                "analysis_version": ANALYSIS_VERSION, "analysis_confidence": analysis_confidence,
                "analysis_stage": analysis_stage,
                "survival_motivation": {"home": h_survival, "away": a_survival},
                "picks": picks,
                "picks_html": picks_html, "h_rank_html": f"<div class='rank-badge'>🏆 리그 순위: {h_rank}위</div>" if h_rank != 99 else "", "a_rank_html": f"<div class='rank-badge'>🏆 리그 순위: {a_rank}위</div>" if a_rank != 99 else "",
                "h_inj_html": h_inj_html, "a_inj_html": a_inj_html,
                "home_form": fetch_team_form_api(home_info.get("id"), heavy_ttl), "away_form": fetch_team_form_api(away_info.get("id"), heavy_ttl)
            }
        if (
            not crossed_kickoff_during_analysis
            and analysis_stage == "T-30-final"
            and prediction_saved
        ):
            stored_item = _freeze_toto14_prediction(
                match_id, home_team, away_team, match_time, toto_item
            )
            if stored_item:
                toto_item = stored_item
                toto_item["match"] = dict(m)
                frozen_prediction_count += 1
        final_picks = _normalize_toto14_picks(
            toto_item.get("picks")
            or _toto14_picks_from_display(
                toto_item.get("best_pick_display"), home_team, away_team
            )
        )
        toto_item["picks"] = final_picks
        toto_item["picks_html"] = _render_toto14_picks_html(final_picks)
        if len(final_picks) == 2:
            total_combinations *= 2
            double_pick_count += 1
            if total_combinations > TOTO14_MAX_COMBINATIONS:
                cost_cap_exceeded_by_frozen = True
        elif len(final_picks) == 1:
            single_pick_count += 1
        else:
            unavailable_pick_count += 1
        if double_suppressed:
            suppressed_double_count += 1
        dashboard_toto14.append(toto_item)

    # 종료된 예전 경기가 TOP 3에 다시 등장하지 않도록 아직 시작하지 않은 경기만 선정한다.
    now_ts = datetime.now(timezone(timedelta(hours=9))).timestamp()
    upcoming_proto = [
        item for item in dashboard_proto
        if item.get("timestamp", 0) > now_ts and item.get("ev_sorted_picks")
    ]
    top_3_picks = sorted(
        upcoming_proto,
        key=lambda x: (x.get("reliability_score", 0), x.get("analysis_confidence", 0)),
        reverse=True,
    )[:3]

    toto_ticket_complete = len(dashboard_toto14) == 14 and unavailable_pick_count == 0
    published_combinations = total_combinations if toto_ticket_complete else 0
    cost_cap_exceeded_by_frozen = bool(
        cost_cap_exceeded_by_frozen
        or published_combinations > TOTO14_MAX_COMBINATIONS
    )
    final_output = {
        "proto": dashboard_proto, "toto14": dashboard_toto14,
        "grading": _build_grading_snapshot(),
        "toto14_meta": {
            "total_combinations": published_combinations,
            "single_pick_count": single_pick_count,
            "double_pick_count": double_pick_count,
            "unavailable_pick_count": unavailable_pick_count,
            "suppressed_double_count": suppressed_double_count,
            "frozen_prediction_count": frozen_prediction_count,
            "ticket_complete": toto_ticket_complete,
            "max_combinations": TOTO14_MAX_COMBINATIONS,
            "unit_price": TOTO14_UNIT_PRICE,
            "budget": published_combinations * TOTO14_UNIT_PRICE,
            "max_budget": TOTO14_MAX_COMBINATIONS * TOTO14_UNIT_PRICE,
            "cost_cap_exceeded_by_frozen": cost_cap_exceeded_by_frozen,
        },
        "top3": top_3_picks,
        "source_meta": {
            "analysis_version": ANALYSIS_VERSION,
            "system_version": SYSTEM_VERSION,
            "underdog_gate_version": UNDERDOG_GATE_VERSION,
            "raw_betman_proto_count": len(raw_proto_matches),
            "rejected_placeholder_count": rejected_proto_count,
            "betman_proto_count": len(proto_matches),
            "display_proto_count": len(dashboard_proto),
            "betman_toto14_count": len(toto_14_matches),
            "display_toto14_count": len(dashboard_toto14),
            "proto_parity_ok": len(proto_matches) == len(dashboard_proto),
            "toto14_parity_ok": len(toto_14_matches) == len(dashboard_toto14),
            "api_usage": get_api_usage_status(),
            "generated_at": datetime.now(timezone(timedelta(hours=9))).isoformat(),
        },
    }
    _atomic_write_json("dashboard_data.json", final_output)
    if len(proto_matches) != len(dashboard_proto):
        print(f"❌ 경기 수 불일치: 베트맨 {len(proto_matches)}경기 / 화면 데이터 {len(dashboard_proto)}경기")
    else:
        print(f"✅ 경기 수 일치 확인: 베트맨 = 화면 데이터 {len(dashboard_proto)}경기")
    print(f"✅ 대시보드 데이터 패키징 완료! ({ANALYSIS_VERSION} 과신 방지·핵심선수·신뢰도 적용)")
    return True

def _cached_fixture_identity_board(date_key, purpose="scoring"):
    """Reuse one date board across score/live workers instead of every 5 minutes."""
    date_key = str(date_key or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_key):
        return []
    cache_key = f"fixture_identity_board_v2_{date_key}"
    cached = get_db_cache(cache_key, FIXTURE_IDENTITY_CACHE_HOURS)
    if isinstance(cached, list):
        return cached

    stale = get_db_cache(cache_key, 24 * 14)
    retry_guard = get_db_cache(
        f"{cache_key}_retry_guard", FIXTURE_IDENTITY_RETRY_HOURS
    )
    if retry_guard is not None:
        return stale if isinstance(stale, list) else []

    try:
        board = _request_fixture_board(
            {"date": date_key, "timezone": "Asia/Seoul"},
            purpose=purpose,
        )
        board = board if isinstance(board, list) else []
        set_db_cache(cache_key, board)
        return board
    except Exception:
        set_db_cache(f"{cache_key}_retry_guard", {"failed": True})
        if isinstance(stale, list):
            print(f"♻️ 경기 신원판 API 오류 - 저장된 정상본 사용({date_key})")
            return stale
        raise


def _recover_due_fixture_ids(rows, conn):
    """Recover unresolved fixtures from their scheduled date, including terminal games."""
    unresolved = [row for row in rows if not int(row[6] or 0)]
    if not unresolved:
        return rows, 0

    boards = {}
    now = datetime.now(KST)
    for row in unresolved:
        match_dt = _parse_kst_match_time(row[5])
        if match_dt is None:
            continue
        age_hours = (now - match_dt).total_seconds() / 3600
        if age_hours < -LIVE_LOOKAROUND_HOURS:
            continue
        date_key = match_dt.strftime("%Y-%m-%d")
        if date_key in boards:
            continue
        try:
            boards[date_key] = _cached_fixture_identity_board(
                date_key, purpose="scoring"
            )
        except Exception as error:
            print(f"⚠️ 미연결 경기판 조회 실패({date_key}): {error}")
            boards[date_key] = []

    recovered = {}
    for row in unresolved:
        # The scoring row layout is id, home, away, prob, ev, time, fixture.
        match_id, home_team, away_team, match_time = row[0], row[1], row[2], row[5]
        match_dt = _parse_kst_match_time(match_time)
        if match_dt is None:
            continue
        candidates = []
        for item in boards.get(match_dt.strftime("%Y-%m-%d"), []):
            api_dt = _api_fixture_datetime(item)
            if api_dt is None or abs((api_dt - match_dt).total_seconds()) > 3 * 3600:
                continue
            teams = item.get("teams", {})
            api_home = teams.get("home", {})
            api_away = teams.get("away", {})
            if not team_matches_api(home_team, api_home.get("name"), api_home.get("id")):
                continue
            if not team_matches_api(away_team, api_away.get("name"), api_away.get("id")):
                continue
            candidates.append((abs((api_dt - match_dt).total_seconds()), item))
        if not candidates:
            continue
        candidates.sort(key=lambda value: value[0])
        fixture_id = int(candidates[0][1].get("fixture", {}).get("id") or 0)
        if fixture_id:
            recovered[str(match_id)] = fixture_id

    if recovered:
        conn.executemany(
            "UPDATE predictions SET api_fixture_id = ? WHERE match_id = ? AND COALESCE(api_fixture_id, 0) = 0",
            [(fixture_id, match_id) for match_id, fixture_id in recovered.items()],
        )
        conn.commit()

    updated = []
    for row in rows:
        fixture_id = int(row[6] or 0) or recovered.get(str(row[0]), 0)
        updated.append(tuple(row[:6]) + (fixture_id,))
    return updated, len(recovered)


def _scoring_row_matches_fixture(row, match_info):
    """Require ordered teams and a tight kickoff window before grading."""
    api_dt = _api_fixture_datetime(match_info)
    local_dt = _parse_kst_match_time(row[5])
    if api_dt is None or local_dt is None:
        return False
    if abs((local_dt - api_dt).total_seconds()) > 3 * 3600:
        return False
    teams = match_info.get("teams", {}) or {}
    api_home = teams.get("home", {}) or {}
    api_away = teams.get("away", {}) or {}
    return (
        team_matches_api(row[1], api_home.get("name"), api_home.get("id"))
        and team_matches_api(row[2], api_away.get("name"), api_away.get("id"))
    )


def _ensure_postmortem_column(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(predictions)")}
    if "postmortem_json" not in columns:
        conn.execute("ALTER TABLE predictions ADD COLUMN postmortem_json TEXT DEFAULT '{}'")
        conn.commit()


def _backfill_finished_postmortems(conn):
    """Give older graded rows deterministic learning labels without API calls."""
    _ensure_postmortem_column(conn)
    rows = conn.execute(
        """
        SELECT match_id, home_team, away_team, prob_pick, ev_pick,
               actual_score, is_correct_prob, is_correct_ev, ai_note
        FROM predictions
        WHERE actual_result = 'FINISHED'
          AND COALESCE(NULLIF(postmortem_json, ''), '{}') = '{}'
        """
    ).fetchall()
    updates = []
    for row in rows:
        score_match = re.match(r"^\s*(\d+)\s*:\s*(\d+)\s*$", str(row[5] or ""))
        if not score_match:
            continue
        goals_h, goals_a = int(score_match.group(1)), int(score_match.group(2))
        events = events_from_note(row[8]) or _load_stored_event_timeline(row[0], limit=8)
        payload = build_postmortem(
            home_team=row[1],
            away_team=row[2],
            prob_pick=row[3],
            ev_pick=row[4],
            goals_h=goals_h,
            goals_a=goals_a,
            is_correct_prob=row[6],
            is_correct_ev=row[7],
            has_ev_pick=bool(str(row[4] or "").strip()),
            official_stats=stats_from_note(row[8]),
            event_timeline=events,
        )
        updates.append((postmortem_json(payload), row[0]))
    if updates:
        conn.executemany(
            "UPDATE predictions SET postmortem_json = ? WHERE match_id = ?",
            updates,
        )
        conn.commit()
    return len(updates)


def _repair_finished_handicap_grades(conn):
    """Repair only derived handicap grades; frozen forecasts stay immutable."""
    _ensure_postmortem_column(conn)
    rows = conn.execute(
        """
        SELECT match_id, home_team, away_team, prob_pick, ev_pick,
               actual_score, is_correct_prob, is_correct_ev, ai_note
        FROM predictions
        WHERE actual_result = 'FINISHED'
          AND (
              prob_pick LIKE '%핸디%' OR ev_pick LIKE '%핸디%'
              OR prob_pick LIKE '%적용 후%' OR ev_pick LIKE '%적용 후%'
          )
        """
    ).fetchall()
    repaired_predictions = 0
    for row in rows:
        score_match = re.match(r"^\s*(\d+)\s*:\s*(\d+)\s*$", str(row[5] or ""))
        if not score_match:
            continue
        goals_h, goals_a = int(score_match.group(1)), int(score_match.group(2))
        prob_hit = int(evaluate_single_pick(row[3], row[1], row[2], goals_h, goals_a))
        has_ev_pick = bool(str(row[4] or "").strip())
        ev_hit = int(evaluate_single_pick(row[4], row[1], row[2], goals_h, goals_a))
        if prob_hit == int(row[6] or 0) and ev_hit == int(row[7] or 0):
            continue
        official_stats = stats_from_note(row[8])
        events = events_from_note(row[8]) or _load_stored_event_timeline(row[0], limit=8)
        payload = build_postmortem(
            home_team=row[1], away_team=row[2], prob_pick=row[3], ev_pick=row[4],
            goals_h=goals_h, goals_a=goals_a,
            is_correct_prob=prob_hit, is_correct_ev=ev_hit,
            has_ev_pick=has_ev_pick, official_stats=official_stats,
            event_timeline=events,
        )
        result_parts = [f"최종 추천픽 {'적중' if prob_hit else '미적중'}"]
        if has_ev_pick:
            result_parts.append(
                f"기존 배당형 대안픽 {'적중' if ev_hit else '미적중'}"
            )
        note_parts = [
            f"[채점 결과] {' · '.join(result_parts)}.",
            f"[최종 점수] {goals_h}:{goals_a}.",
        ]
        stats_text = official_stats_text(official_stats)
        if stats_text:
            note_parts.append(f"[공식 경기 통계] {stats_text}.")
        review_text = postmortem_text(payload)
        if review_text:
            note_parts.append(review_text)
        if events:
            note_parts.append("🎬 주요 사건 기록(최대 8건)\n" + "\n".join(events[-8:]))
        conn.execute(
            """
            UPDATE predictions
            SET is_correct_prob = ?, is_correct_ev = ?, ai_note = ?,
                postmortem_json = ?
            WHERE match_id = ? AND actual_result = 'FINISHED'
            """,
            (prob_hit, ev_hit, "\n\n".join(note_parts), postmortem_json(payload), row[0]),
        )
        repaired_predictions += 1

    repaired_candidates = 0
    _ensure_prediction_analysis_tables(conn)
    candidate_rows = conn.execute(
        """
        SELECT result.id, result.raw_pick, result.is_correct,
               prediction.home_team, prediction.away_team, prediction.actual_score
        FROM prediction_candidate_results AS result
        JOIN predictions AS prediction ON prediction.match_id = result.match_id
        WHERE prediction.actual_result = 'FINISHED'
          AND (result.raw_pick LIKE '%핸디%' OR result.raw_pick LIKE '%적용 후%')
        """
    ).fetchall()
    for result_id, raw_pick, old_hit, home_team, away_team, score in candidate_rows:
        score_match = re.match(r"^\s*(\d+)\s*:\s*(\d+)\s*$", str(score or ""))
        if not score_match:
            continue
        new_hit = int(evaluate_single_pick(
            raw_pick, home_team, away_team,
            int(score_match.group(1)), int(score_match.group(2)),
        ))
        if new_hit != int(old_hit or 0):
            conn.execute(
                "UPDATE prediction_candidate_results SET is_correct = ?, "
                "graded_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_hit, int(result_id)),
            )
            repaired_candidates += 1
    if repaired_predictions or repaired_candidates:
        conn.commit()
    return repaired_predictions, repaired_candidates


def _grade_prediction_candidates(
    conn, match_id, home_team, away_team, goals_h, goals_a
):
    """Grade the last frozen full-market audit without changing its forecast."""
    _ensure_prediction_analysis_tables(conn)
    snapshot = conn.execute(
        """
        SELECT id, analysis_version, stage, candidates_json, evidence_json
        FROM prediction_analysis_snapshots
        WHERE match_id = ?
        ORDER BY id DESC LIMIT 1
        """,
        (str(match_id),),
    ).fetchone()
    if not snapshot:
        return None
    try:
        candidates = json.loads(snapshot[3] or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(candidates, list):
        return None
    try:
        evidence = json.loads(snapshot[4] or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        evidence = []
    odds_movement_evidence = ""
    for item in evidence if isinstance(evidence, list) else []:
        if not isinstance(item, dict) or str(item.get("name") or "") != "배당 변동":
            continue
        odds_movement_evidence = str(item.get("value") or "").strip()
        if odds_movement_evidence:
            break

    result_rows = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        raw_pick = str(candidate.get("raw_pick") or "").strip()
        market_key = str(candidate.get("market_key") or "").strip()
        if not raw_pick or market_key not in MARKET_LABELS:
            continue
        is_correct = int(evaluate_single_pick(
            raw_pick, home_team, away_team, int(goals_h), int(goals_a)
        ))
        selected_as = list(candidate.get("selected_as") or [])
        conn.execute(
            """
            INSERT OR REPLACE INTO prediction_candidate_results (
                match_id, analysis_snapshot_id, analysis_version, stage,
                market_key, raw_pick, model_probability, fair_probability,
                odd, selection_rank, market_rank, selected_as, is_correct,
                actual_score, graded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                str(match_id), int(snapshot[0]), str(snapshot[1] or ""),
                str(snapshot[2] or ""), market_key, raw_pick,
                float(candidate.get("model_probability") or 0),
                candidate.get("fair_probability"),
                float(candidate.get("odd") or 0),
                candidate.get("selection_rank"),
                candidate.get("market_rank"),
                json.dumps(selected_as, ensure_ascii=False), is_correct,
                f"{int(goals_h)}:{int(goals_a)}",
            ),
        )
        result_rows.append({
            "market_key": market_key,
            "market_label": MARKET_LABELS[market_key],
            "raw_pick": raw_pick,
            "model_probability": _audit_number(
                candidate.get("model_probability"), 0.0
            ),
            "selection_rank": candidate.get("selection_rank"),
            "selected_as": selected_as,
            "is_correct": is_correct,
        })

    hits = [item for item in result_rows if item["is_correct"] == 1]
    selected = [
        item for item in result_rows if "high_probability" in item["selected_as"]
    ]
    unselected_hits = [
        item for item in hits if "high_probability" not in item["selected_as"]
    ]
    unselected_hits.sort(
        key=lambda item: (
            float(item.get("model_probability") or 0),
            -(int(item.get("selection_rank") or 999)),
        ),
        reverse=True,
    )
    return {
        "schema_version": PICK_AUDIT_SCHEMA_VERSION,
        "analysis_snapshot_id": int(snapshot[0]),
        "analysis_version": str(snapshot[1] or ""),
        "stage": str(snapshot[2] or ""),
        "graded_candidate_count": len(result_rows),
        "hit_candidate_count": len(hits),
        "selected_pick_hit": bool(selected and selected[0]["is_correct"] == 1),
        "selected_pick": selected[0] if selected else None,
        "market_answers": hits,
        "highest_probability_unselected_answer": (
            unselected_hits[0] if unselected_hits else None
        ),
        "odds_movement_evidence": odds_movement_evidence,
    }


def _candidate_review_text(review):
    if not review:
        return ""
    answer = review.get("highest_probability_unselected_answer")
    base = (
        f"[전체 시장 복기] 킥오프 전 동결 후보 "
        f"{int(review.get('graded_candidate_count') or 0)}개를 동일 점수로 채점했습니다."
    )
    movement = str(review.get("odds_movement_evidence") or "").strip()
    movement_note = f" 당시 확인된 해외배당 흐름: {movement}." if movement else ""
    if review.get("selected_pick_hit"):
        return base + " 최종 확률픽이 실제 결과 조건을 충족했습니다." + movement_note
    if answer:
        return (
            base
            + f" 확률픽은 미적중했고, 미선택 후보 중 실제 조건을 충족한 "
            f"최상위 방향은 {answer.get('market_label')} · "
            f"{answer.get('raw_pick')}({float(answer.get('model_probability') or 0) * 100:.1f}%)였습니다. "
            "이는 경기 후 확인한 복기 자료이며 과거 예측을 소급 변경하지 않습니다."
            + movement_note
        )
    return (
        base
        + " 미선택 후보까지 포함해 원인을 다음 도전자 모델의 학습 자료로 보존했습니다."
        + movement_note
    )


def auto_score_matches():
    print(f"\n[🤖 {time.strftime('%Y-%m-%d %H:%M:%S')}] 🔥 불도저 채점 엔진 가동 (정밀 API 고유 ID 추적)...")
    scoring_calls_before = int(get_api_usage_status().get("scoring_calls") or 0)
    conn = None
    try:
        conn = sqlite3.connect(str(_local_path("ai_predictions.db")), timeout=30)
        conn.execute("PRAGMA busy_timeout = 30000")
        repaired_predictions, repaired_candidates = _repair_finished_handicap_grades(conn)
        if repaired_predictions or repaired_candidates:
            print(
                "✅ 핸디캡 결과 재판정 완료: "
                f"공식픽 {repaired_predictions}건 / 시장후보 {repaired_candidates}건 "
                "(예측·확률·버전은 보존)"
            )
        backfilled_count = _backfill_finished_postmortems(conn)
        if backfilled_count:
            print(f"✅ 기존 오답노트 학습 태그 보강: {backfilled_count}건")
        cursor = conn.cursor()
        cursor.execute("""
            SELECT match_id, home_team, away_team, prob_pick, ev_pick,
                   match_time, api_fixture_id
            FROM predictions
            WHERE actual_result = 'PENDING'
        """)
        pending_matches = cursor.fetchall()
        api_call_count = 0
        now = datetime.now(KST)
        due_matches = []
        max_age = timedelta(days=max(2, int(os.getenv("SCORE_LOOKBACK_DAYS", "7"))))
        for row in pending_matches:
            match_dt = _parse_kst_match_time(row[5])
            if match_dt is None or now < match_dt or now - match_dt > max_age:
                continue
            due_matches.append(row)

        due_matches, recovered_count = _recover_due_fixture_ids(due_matches, conn)
        if recovered_count:
            print(f"✅ 종료 경기 fixture ID 복구: {recovered_count}건")

        # One failed batch must not roll back completed batches or stop later runs.
        linked_matches = [row for row in due_matches if int(row[6] or 0)]
        for offset in range(0, len(linked_matches), 20):
            batch = linked_matches[offset:offset + 20]
            fixture_ids = sorted({str(int(row[6])) for row in batch if row[6]})
            if not fixture_ids:
                continue
            try:
                res = api_get(
                    "/fixtures",
                    params={"ids": "-".join(fixture_ids), "timezone": "Asia/Seoul"},
                    timeout=12,
                    purpose="scoring",
                )
                api_call_count += 1
                payload = res.json() if res.status_code == 200 else {}
                if res.status_code != 200 or payload.get("errors"):
                    print(f"⚠️ 묶음 채점 API 오류: HTTP {res.status_code} {payload.get('errors', '')}")
                    continue
                fixture_map = {
                    int(item.get("fixture", {}).get("id") or 0): item
                    for item in payload.get("response", [])
                }

                for row in batch:
                    match_id, h_team, a_team, prob_pick, ev_pick, _, fixture_id = row
                    match_info = fixture_map.get(int(fixture_id))
                    if not match_info:
                        continue
                    if not _scoring_row_matches_fixture(row, match_info):
                        # Never grade a fixture ID that points to another match.
                        # Clearing it lets the date/team recovery path repair it on
                        # the next independent scoring cycle.
                        cursor.execute(
                            "UPDATE predictions SET api_fixture_id = 0 "
                            "WHERE match_id = ? AND actual_result = 'PENDING'",
                            (match_id,),
                        )
                        print(f"⚠️ 잘못 연결된 fixture ID 해제: {h_team} vs {a_team}")
                        continue
                    status = match_info.get('fixture', {}).get('status', {}).get('short', '')

                    if status in TERMINAL_STATUSES:
                        final_h = match_info.get('goals', {}).get('home')
                        final_a = match_info.get('goals', {}).get('away')
                        if final_h is None or final_a is None:
                            print(f"⚠️ 종료 상태지만 점수가 비어 있어 채점 보류: {h_team} vs {a_team}")
                            continue
                        final_h, final_a = int(final_h), int(final_a)
                        regulation = match_info.get("score", {}).get("fulltime", {}) or {}
                        eval_h = regulation.get("home")
                        eval_a = regulation.get("away")
                        eval_h = final_h if eval_h is None else int(eval_h)
                        eval_a = final_a if eval_a is None else int(eval_a)
                        score_str = f"{final_h}:{final_a}"
                        is_corr_prob = evaluate_single_pick(prob_pick, h_team, a_team, eval_h, eval_a)
                        is_corr_ev = evaluate_single_pick(ev_pick, h_team, a_team, eval_h, eval_a)
                        candidate_review = _grade_prediction_candidates(
                            conn, match_id, h_team, a_team, eval_h, eval_a
                        )
                        event_timeline = _load_stored_event_timeline(match_id, limit=8)
                        ai_note, postmortem_data = generate_real_ai_note(
                            fixture_id, final_h, final_a, is_corr_prob, is_corr_ev,
                            has_ev_pick=bool(str(ev_pick or "").strip()),
                            home_team=h_team,
                            away_team=a_team,
                            prob_pick=prob_pick,
                            ev_pick=ev_pick,
                            event_timeline=event_timeline,
                            return_postmortem=True,
                        )
                        if candidate_review:
                            try:
                                postmortem_payload = json.loads(postmortem_data or "{}")
                                postmortem_payload["candidate_review"] = candidate_review
                                postmortem_data = json.dumps(
                                    postmortem_payload,
                                    ensure_ascii=False,
                                    sort_keys=True,
                                )
                            except (TypeError, ValueError, json.JSONDecodeError):
                                pass
                            review_text = _candidate_review_text(candidate_review)
                            if review_text:
                                ai_note += "\n\n" + review_text
                        if event_timeline:
                            ai_note += "\n\n🎬 주요 사건 기록(최대 8건)\n" + "\n".join(event_timeline)
                        cursor.execute("""
                            UPDATE predictions
                            SET actual_score = ?, actual_result = 'FINISHED',
                                is_correct_prob = ?, is_correct_ev = ?, ai_note = ?,
                                postmortem_json = ?
                            WHERE match_id = ? AND actual_result = 'PENDING'
                        """, (
                            score_str, is_corr_prob, is_corr_ev, ai_note,
                            postmortem_data, match_id,
                        ))
                        print(f"  ✨ [정밀 채점 완료] {h_team} vs {a_team} ({score_str})")
                    elif status in CANCELED_STATUSES:
                        cursor.execute("""
                            UPDATE predictions
                            SET actual_result = 'CANCELED',
                                ai_note = '💡 경기 취소/중단/몰수로 인한 무효 처리'
                            WHERE match_id = ? AND actual_result = 'PENDING'
                        """, (match_id,))
                        print(f"  ⚠️ [경기 무효 처리] {h_team} vs {a_team}")
                    elif status in POSTPONED_STATUSES:
                        print(f"  ⏸️ [경기 연기 - 추후 재확인] {h_team} vs {a_team}")
                conn.commit()
            except Exception as batch_error:
                conn.rollback()
                print(f"⚠️ 채점 묶음 처리 실패(다음 주기 재시도): {batch_error}")

        scoring_calls_after = int(get_api_usage_status().get("scoring_calls") or 0)
        scoring_calls_used = max(0, scoring_calls_after - scoring_calls_before)
        print(
            "✅ 스마트 채점 사이클 종료 "
            f"(전체 채점 API {scoring_calls_used}회 / 결과 묶음 {api_call_count}회)"
        )
        return True
    except Exception as error:
        print(f"❌ [관제 봇 떡밥] 채점 중 오류: {error}")
        return False
    finally:
        if conn is not None:
            conn.close()

def _api_fixture_datetime(match_info):
    raw_date = match_info.get("fixture", {}).get("date")
    if not raw_date:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone(timedelta(hours=9)))
    except (TypeError, ValueError):
        return None


def _request_fixture_board(params, purpose="live"):
    """일시적인 API 지연에는 한 번 재시도하고 오류 응답은 데이터로 쓰지 않는다."""
    last_error = None
    for attempt in range(2):
        try:
            response = api_get(
                "/fixtures",
                params=params,
                timeout=12,
                purpose=purpose,
            )
            if response.status_code != 200:
                raise RuntimeError(f"API HTTP {response.status_code}")
            payload = response.json()
            if payload.get("errors"):
                raise RuntimeError(str(payload.get("errors")))
            return payload.get("response", [])
        except Exception as error:
            last_error = error
            if attempt == 0:
                time.sleep(1.5)
    raise RuntimeError(str(last_error or "라이브 API 응답 없음"))


def _event_record(raw_event):
    event_time = raw_event.get("time", {}) or {}
    event_type = str(raw_event.get("type", "") or "")
    detail = str(raw_event.get("detail", "") or "")
    player = raw_event.get("player", {}) or {}
    assist = raw_event.get("assist", {}) or {}
    team = raw_event.get("team", {}) or {}
    elapsed = int(event_time.get("elapsed") or 0)
    extra = int(event_time.get("extra") or 0)
    player_name = str(player.get("name", "") or "")
    assist_name = str(assist.get("name", "") or "")
    team_name = str(team.get("name", "") or "")
    minute = f"{elapsed}+{extra}" if extra else str(elapsed)
    event_key = event_type.casefold()
    detail_key = detail.casefold()
    subject = player_name or team_name or detail

    if event_key == "goal":
        text = f"{minute}분 | ⚽ 득점 | {team_name} | {subject}"
    elif event_key == "card" and ("red" in detail_key or "second yellow" in detail_key):
        text = f"{minute}분 | 🟥 퇴장 | {team_name} | {subject}"
    elif event_key == "card" and "yellow" in detail_key:
        text = f"{minute}분 | 🟨 경고 | {team_name} | {subject}"
    elif event_key in {"subst", "substitution"}:
        change = f"나간 선수: {player_name}"
        if assist_name:
            change += f" / 들어온 선수: {assist_name}"
        text = f"{minute}분 | 🔄 교체 | {team_name} | {change}"
    elif event_key == "var":
        text = f"{minute}분 | 📺 VAR | {team_name} | {detail or subject}"
    elif event_key == "injury" or "injur" in detail_key:
        text = f"{minute}분 | 🚑 부상 | {team_name} | {subject}"
    else:
        return None
    key = "|".join(map(str, (
        elapsed, extra, team.get("id") or team.get("name") or "",
        event_type, detail, player.get("id") or player_name,
    )))
    return {
        "key": key,
        "elapsed": elapsed,
        "extra": extra,
        "type": event_type,
        "detail": detail,
        "player": player_name,
        "team": str(team.get("name", "") or ""),
        "text": text,
    }


def _load_stored_event_timeline(match_id, limit=8):
    try:
        live_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "live_scores.json"
        )
        with open(live_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        record = payload.get(str(match_id), {}) if isinstance(payload, dict) else {}
        events = record.get("events", []) if isinstance(record, dict) else []
        texts = []
        signatures = set()
        for event in events:
            if not isinstance(event, dict):
                continue
            text = str(event.get("text") or "").strip()
            signature = re.sub(r"[^0-9a-z가-힣]+", "", text.casefold())
            if text and signature and signature not in signatures:
                signatures.add(signature)
                texts.append(text)
        return texts[-max(1, int(limit)):]
    except Exception:
        return []


def _merge_event_history(previous_entries, api_events):
    merged = {}
    for entry in previous_entries or []:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key", "") or "")
        if key:
            merged[key] = dict(entry)
    for raw_event in api_events or []:
        normalized = _event_record(raw_event)
        if normalized:
            merged[normalized["key"]] = normalized
    history = sorted(
        merged.values(),
        key=lambda item: (int(item.get("elapsed", 0)), int(item.get("extra", 0)), item.get("key", "")),
    )[-80:]
    latest = history[-1].get("text", "") if history else ""
    return history, latest


def _entry_not_expired(entry, now):
    raw_expiry = entry.get("retain_until") if isinstance(entry, dict) else None
    if not raw_expiry:
        return True
    try:
        expiry = datetime.fromisoformat(str(raw_expiry).replace("Z", "+00:00"))
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc) <= expiry.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return True


def _retention_deadline(anchor=None):
    base = datetime.now(timezone.utc)
    if anchor:
        try:
            parsed = datetime.fromisoformat(str(anchor).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            base = parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            pass
    return (base + timedelta(hours=LIVE_RETENTION_HOURS)).isoformat(timespec="seconds")


def _row_matches_fixture(row, match_info):
    api_dt = _api_fixture_datetime(match_info)
    local_dt = _parse_kst_match_time(row[4])
    if api_dt is None or local_dt is None:
        return False
    if abs((local_dt - api_dt).total_seconds()) > 3 * 3600:
        return False
    teams = match_info.get("teams", {}) or {}
    api_home = teams.get("home", {}) or {}
    api_away = teams.get("away", {}) or {}
    return (
        team_matches_api(row[2], api_home.get("name"), api_home.get("id"))
        and team_matches_api(row[3], api_away.get("name"), api_away.get("id"))
    )


def update_live_scores():
    print(f"\n[📡 {time.strftime('%Y-%m-%d %H:%M:%S')}] 실시간 라이브 데이터 업데이트 (5분 주기)...")
    previous = _read_json("live_scores.json", {})
    if not isinstance(previous, dict):
        previous = {}
    conn = None
    try:
        conn = sqlite3.connect(str(_local_path("ai_predictions.db")), timeout=30)
        conn.execute("PRAGMA busy_timeout = 30000")
        cursor = conn.cursor()
        cursor.execute("""
            SELECT match_id, api_fixture_id, home_team, away_team, match_time,
                   actual_result, actual_score
            FROM predictions
            WHERE actual_result IN ('PENDING', 'FINISHED')
        """)
        all_rows = cursor.fetchall()

        now_kst = datetime.now(KST)
        relevant_rows = []
        for row in all_rows:
            match_dt = _parse_kst_match_time(row[4])
            if match_dt is None:
                if str(row[0]) in previous:
                    relevant_rows.append(row)
                continue
            delta_hours = (match_dt - now_kst).total_seconds() / 3600
            if -LIVE_RETENTION_HOURS * 2 <= delta_hours <= LIVE_LOOKAROUND_HOURS:
                relevant_rows.append(row)

        rows_by_fixture = {}
        for row in relevant_rows:
            fixture_id = int(row[1] or 0)
            if fixture_id:
                rows_by_fixture.setdefault(fixture_id, []).append(row)

        # live=all is authoritative for current games. Previously tracked IDs are
        # queried too, so the same record can transition to FT instead of vanishing.
        fixture_candidates = {}
        board = _request_fixture_board({"live": "all", "timezone": "Asia/Seoul"})
        for item in board:
            fixture_id = int(item.get("fixture", {}).get("id") or 0)
            if fixture_id:
                fixture_candidates[fixture_id] = item

        tracked_ids = set(rows_by_fixture)
        for value in previous.values():
            if isinstance(value, dict) and int(value.get("fixture_id") or 0):
                tracked_ids.add(int(value.get("fixture_id")))
        missing_ids = sorted(tracked_ids - set(fixture_candidates))
        for offset in range(0, len(missing_ids), 20):
            ids = missing_ids[offset:offset + 20]
            try:
                tracked_board = _request_fixture_board({
                    "ids": "-".join(map(str, ids)),
                    "timezone": "Asia/Seoul",
                })
                for item in tracked_board:
                    fixture_id = int(item.get("fixture", {}).get("id") or 0)
                    if fixture_id:
                        fixture_candidates[fixture_id] = item
            except Exception as tracked_error:
                print(f"⚠️ 추적 경기 상태 조회 실패(마지막 정상 점수 유지): {tracked_error}")

        # Recover unresolved matches from the scheduled date. Invalid/unknown times
        # never trigger repeated date-board calls.
        recovery_dates = []
        for row in relevant_rows:
            if int(row[1] or 0) or str(row[5]) != "PENDING":
                continue
            match_dt = _parse_kst_match_time(row[4])
            if match_dt is None:
                continue
            date_key = match_dt.strftime("%Y-%m-%d")
            if date_key not in recovery_dates:
                recovery_dates.append(date_key)
        for date_key in recovery_dates[:3]:
            try:
                date_board = _cached_fixture_identity_board(
                    date_key, purpose="live"
                )
                for item in date_board:
                    fixture_id = int(item.get("fixture", {}).get("id") or 0)
                    if fixture_id:
                        fixture_candidates.setdefault(fixture_id, item)
            except Exception as date_error:
                print(f"⚠️ 미연결 경기 복구판 조회 실패({date_key}): {date_error}")

        current_entries = {}
        score_updates = []
        fixture_updates = []
        recovered_links = 0
        used_match_ids = set()
        now_iso = _utc_iso()
        retain_until = _retention_deadline()

        for fixture_id, match_info in fixture_candidates.items():
            fixture = match_info.get("fixture", {}) or {}
            status_info = fixture.get("status", {}) or {}
            status = str(status_info.get("short", "") or "")
            if status not in LIVE_STATUSES | TERMINAL_STATUSES | CANCELED_STATUSES | POSTPONED_STATUSES:
                continue

            matched_rows = []
            for row in rows_by_fixture.get(fixture_id, []):
                if str(row[0]) not in used_match_ids and _row_matches_fixture(row, match_info):
                    matched_rows.append(row)
            matched_ids = {str(row[0]) for row in matched_rows}

            for row in relevant_rows:
                match_id = str(row[0])
                if match_id in matched_ids or match_id in used_match_ids:
                    continue
                if _row_matches_fixture(row, match_info):
                    matched_rows.append(row)
                    matched_ids.add(match_id)
                    if int(row[1] or 0) != fixture_id and str(row[5]) == "PENDING":
                        fixture_updates.append((fixture_id, match_id))
                        recovered_links += 1

            if not matched_rows:
                continue

            goals_h = match_info.get('goals', {}).get('home')
            goals_a = match_info.get('goals', {}).get('away')
            prior_for_fixture = [previous.get(str(row[0]), {}) for row in matched_rows]
            prior_score = next(
                (str(entry.get("score")) for entry in prior_for_fixture if entry.get("score")),
                "- : -",
            )
            if goals_h is None or goals_a is None:
                score_display = prior_score
                score_db = prior_score.replace(" : ", ":")
            else:
                score_db = f"{int(goals_h)}:{int(goals_a)}"
                score_display = score_db.replace(":", " : ")

            prior_history = []
            prior_event = ""
            for entry in prior_for_fixture:
                if isinstance(entry.get("events"), list):
                    prior_history.extend(entry.get("events", []))
                if not prior_event and entry.get("event"):
                    prior_event = str(entry.get("event"))
            event_history, event_str = _merge_event_history(prior_history, [])
            event_str = event_str or prior_event

            should_fetch_events = (
        os.getenv("ENABLE_LIVE_EVENTS", "1") == "1"
                and (status in LIVE_STATUSES or status in TERMINAL_STATUSES)
                and not (status in TERMINAL_STATUSES and any(entry.get("final") for entry in prior_for_fixture))
            )
            if should_fetch_events:
                try:
                    evt_res = api_get(
                        "/fixtures/events",
                        params={"fixture": fixture_id},
                        timeout=8,
                        purpose="live",
                    )
                    evt_payload = evt_res.json() if evt_res.status_code == 200 else {}
                    if evt_res.status_code == 200 and not evt_payload.get("errors"):
                        event_history, latest_event = _merge_event_history(
                            event_history, evt_payload.get("response", [])
                        )
                        event_str = latest_event or event_str
                except Exception as event_error:
                    print(f"⚠️ 라이브 이벤트 조회 실패({fixture_id}): {event_error} (이전 이벤트 유지)")

            is_live = status in LIVE_STATUSES
            is_final = status in TERMINAL_STATUSES or status in CANCELED_STATUSES
            for row in matched_rows:
                match_id = str(row[0])
                used_match_ids.add(match_id)
                prior_entry = previous.get(match_id, {}) if isinstance(previous.get(match_id), dict) else {}
                terminal_at = prior_entry.get("terminal_at")
                if is_final and not terminal_at:
                    terminal_at = now_iso
                entry_retain_until = (
                    _retention_deadline(terminal_at) if is_final else retain_until
                )
                next_entry = {
                    "score": score_display,
                    "event": event_str,
                    "events": event_history,
                    "is_live": is_live,
                    "final": is_final,
                    "stale": False,
                    "status": status,
                    "elapsed": int(status_info.get("elapsed") or 0),
                    "fixture_id": fixture_id,
                    "updated_at": now_iso,
                    "last_seen_at": now_iso,
                    "terminal_at": terminal_at,
                    "retain_until": entry_retain_until,
                }
                if is_final and not _entry_not_expired(next_entry, now_kst):
                    continue
                current_entries[match_id] = next_entry
                if goals_h is not None and goals_a is not None:
                    score_updates.append((score_db, match_id))

        if score_updates:
            cursor.executemany(
                "UPDATE predictions SET actual_score = ? WHERE match_id = ?",
                score_updates,
            )
        if fixture_updates:
            cursor.executemany(
                "UPDATE predictions SET api_fixture_id = ? WHERE match_id = ? AND actual_result = 'PENDING'",
                fixture_updates,
            )
        if score_updates or fixture_updates:
            conn.commit()

        # Last-known-good merge: a successful but empty/partial board is not proof
        # that a score should disappear. Expiry is the only implicit removal path.
        merged = {}
        for match_id, entry in previous.items():
            if not isinstance(entry, dict) or match_id in current_entries:
                continue
            preserved = dict(entry)
            if not preserved.get("retain_until"):
                preserved["retain_until"] = retain_until
            if not _entry_not_expired(preserved, now_kst):
                continue
            preserved["stale"] = True
            merged[str(match_id)] = preserved
        merged.update(current_entries)

        _atomic_write_json("live_scores.json", merged)
        if recovered_links:
            print(f"✅ 라이브 팀명/시간 자동 연결 복구: {recovered_links}건")
        live_count = sum(1 for value in merged.values() if value.get("is_live") is True)
        final_count = sum(1 for value in merged.values() if value.get("final") is True)
        print(f"✅ 라이브 업데이트 완료! (LIVE {live_count} / 종료 보존 {final_count} / 전체 {len(merged)})")
        return True
    except Exception as error:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        print(f"❌ [관제 봇 떡밥] 라이브 스코어 에러: {error} (마지막 정상 파일 보존)")
        return False
    finally:
        if conn is not None:
            conn.close()

def _parse_odd_buttons(market):
    values = []
    for button in market.select("button.btnChk"):
        odd_node = button.select_one(".db")
        if not odd_node:
            continue
        match = re.search(r'\d+(?:\.\d+)?', odd_node.get_text(" ", strip=True).replace(',', ''))
        if match:
            values.append(float(match.group(0)))
    return values


def parse_betman_proto_html(html):
    """베트맨 프로토 페이지에서 축구 한 경기당 하나의 실제 시장을 추출한다."""
    soup = BeautifulSoup(html, 'html.parser')
    parsed, seen = [], set()

    for row in soup.select(".box-data-group [data-rowname]"):
        row_name = row.get("data-rowname", "")
        if not row_name or row_name in seen:
            continue
        seen.add(row_name)

        team_nodes = row.select(".teams .team")
        if len(team_nodes) < 2:
            continue
        home = team_nodes[0].get_text(" ", strip=True)
        away = team_nodes[1].get_text(" ", strip=True)
        if not home or not away:
            continue

        row_text = row.get_text(" ", strip=True)
        time_match = re.search(r'\d{2}\.\d{2}\s*\([^)]+\)\s*\d{2}:\d{2}', row_text)
        if not time_match:
            continue
        match_time = re.sub(r'\s+', ' ', time_match.group(0)).strip()
        league_node = row.select_one(".competition")
        league = league_node.get_text(" ", strip=True) if league_node else "축구"

        market_root = row.select_one(".accordion-content")
        if market_root is None:
            market_root = row.find_next_sibling(class_=lambda value: value and "accordion-content" in value)
        if market_root is None:
            parent = row.parent
            market_root = parent.find_next_sibling(class_=lambda value: value and "accordion-content" in value) if parent else None
        if market_root is None:
            parent = row.parent
            market_root = parent.select_one(".accordion-content") if parent else None
        if market_root is None:
            continue

        deadline_match = re.search(r'(\d{2}:\d{2})\s*마감', market_root.get_text(" ", strip=True))
        deadline_time = f"{deadline_match.group(1)} 마감" if deadline_match else ""

        main_market = None
        handicap_market = None
        under_over_market = None
        for market in market_root.select("ul.list-proto-detail > li[data-matchseq]"):
            game_node = market.select_one(".competition-detail .game")
            game_name = re.sub(r'\s+', ' ', game_node.get_text(" ", strip=True)) if game_node else ""
            if game_name == "축구 승무패" and main_market is None:
                main_market = market
            elif game_name == "축구 핸디캡" and handicap_market is None:
                handicap_market = market
            elif game_name == "축구 언더오버" and under_over_market is None:
                under_over_market = market

        if main_market is None:
            continue
        odds_1x2 = _parse_odd_buttons(main_market)
        # Keep the fixture even while Betman is publishing or withdrawing odds.
        # build_dashboard_data already has a waiting-odds representation for zeros.
        if len(odds_1x2) < 3 or min(odds_1x2[:3]) <= 1.0:
            odds_1x2 = [0.0, 0.0, 0.0]

        odds_handi = _parse_odd_buttons(handicap_market) if handicap_market else []
        odds_uo = _parse_odd_buttons(under_over_market) if under_over_market else []
        handicap_text = handicap_market.get_text(" ", strip=True) if handicap_market else ""
        under_over_text = under_over_market.get_text(" ", strip=True) if under_over_market else ""
        handicap_match = re.search(r'H\s*([+-]?\d+(?:\.\d+)?)', handicap_text)
        under_over_match = re.search(r'U/O\s*(\d+(?:\.\d+)?)', under_over_text)
        match_id = str(main_market.get("data-matchseq") or row_name)

        parsed.append({
            "id": match_id,
            "league": league,
            "time": match_time,
            "match_time": match_time,
            "deadline_time": deadline_time,
            "home": home,
            "away": away,
            "odd_h": odds_1x2[0],
            "odd_d": odds_1x2[1],
            "odd_a": odds_1x2[2],
            "handi_h": odds_handi[0] if len(odds_handi) >= 3 else 0.0,
            "handi_d": odds_handi[1] if len(odds_handi) >= 3 else 0.0,
            "handi_a": odds_handi[2] if len(odds_handi) >= 3 else 0.0,
            "handi_base": float(handicap_match.group(1)) if handicap_match else 0.0,
            "uo_under": odds_uo[0] if len(odds_uo) >= 2 else 0.0,
            "uo_over": odds_uo[1] if len(odds_uo) >= 2 else 0.0,
            "uo_base": float(under_over_match.group(1)) if under_over_match else 0.0,
        })
    return parsed


def parse_betman_toto14_html(html, round_id="current"):
    """축구 승무패 표의 공식 행 번호, 팀, 시간, 투표율을 그대로 읽는다."""
    soup = BeautifulSoup(html, 'html.parser')
    parsed = []
    rows = soup.select("table#grid_victory tbody#grid_victory_tbody > tr")
    if not rows:
        # Betman has occasionally omitted the tbody id while keeping the table id.
        rows = soup.select("table#grid_victory tbody > tr")
    for row in rows:
        cells = row.find_all("td", recursive=False)
        if len(cells) < 6:
            continue
        num_match = re.search(r'(\d+)\s*경기', cells[0].get_text(" ", strip=True))
        if not num_match:
            continue
        num = int(num_match.group(1))

        raw_time = cells[1].get_text(" ", strip=True)
        time_match = re.search(r'(?:\d{2}\.)?(\d{2}\.\d{2}\s*\([^)]+\)\s*\d{2}:\d{2})', raw_time)
        match_time = re.sub(r'\s+', ' ', time_match.group(1)).strip() if time_match else "시간 미정"

        teams_box = cells[2].select_one(".vsDIv") or cells[2]
        team_parts = teams_box.find_all("div", recursive=False)
        if len(team_parts) >= 2:
            home = team_parts[0].get_text(" ", strip=True)
            away = re.sub(r'^\s*v\s*s\s*', '', team_parts[1].get_text(" ", strip=True), flags=re.IGNORECASE).strip()
        else:
            team_text = teams_box.get_text(" ", strip=True)
            split_teams = re.split(r'\s+v\s*s\s+', team_text, maxsplit=1, flags=re.IGNORECASE)
            if len(split_teams) != 2:
                continue
            home, away = (part.strip() for part in split_teams)
        if not home or not away:
            continue

        vote_values = []
        for cell in cells[3:6]:
            vote_match = re.search(r'(\d+(?:\.\d+)?)\s*%', cell.get_text(" ", strip=True))
            vote_values.append(float(vote_match.group(1)) if vote_match else None)

        parsed.append({
            "id": f"{round_id}_{num}",
            "round_id": str(round_id),
            "num": num,
            "league": "축구 승무패",
            "home": home,
            "away": away,
            "match_time": match_time,
            "vote_h": vote_values[0],
            "vote_d": vote_values[1],
            "vote_a": vote_values[2],
        })
    return sorted(parsed, key=lambda item: item["num"])


_BETMAN_WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")


def _betman_epoch_text(value):
    """Format Betman's millisecond timestamp in the same KST form as the page."""
    try:
        timestamp = float(value) / 1000.0
        moment = datetime.fromtimestamp(timestamp, KST)
    except (TypeError, ValueError, OSError, OverflowError):
        return "시간 미정"
    return (
        moment.strftime("%y.%m.%d")
        + f" ({_BETMAN_WEEKDAYS[moment.weekday()]}) "
        + moment.strftime("%H:%M")
    )


def _betman_float(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _expand_betman_rows(value):
    """Expand Betman's compact {keys, datas} table into ordinary dictionaries."""
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if not isinstance(value, dict):
        return []
    keys = value.get("keys")
    datas = value.get("datas")
    if not isinstance(keys, list) or not isinstance(datas, list):
        return []
    rows = []
    for values in datas:
        if isinstance(values, list):
            rows.append(dict(zip(keys, values)))
    return rows


def parse_betman_proto_json(payload):
    """Parse the same official JSON that Betman's Proto page renders."""
    if not isinstance(payload, dict):
        return []
    rows = [
        row
        for row in _expand_betman_rows(payload.get("compSchedules"))
        if str(row.get("itemCode", "")).upper() == "SC"
    ]
    groups = {}
    for row in rows:
        key = str(row.get("gameKey") or "").strip()
        if not key:
            key = "|".join(
                str(row.get(field) or "")
                for field in ("gameDate", "homeName", "awayName")
            )
        groups.setdefault(key, []).append(row)

    parsed = []
    for group in groups.values():
        main_market = next(
            (row for row in group if str(row.get("betTypId")) == "1"),
            None,
        )
        if main_market is None:
            continue
        home = str(main_market.get("homeName") or "").strip()
        away = str(main_market.get("awayName") or "").strip()
        if not home or not away:
            continue

        handicap_market = next(
            (row for row in group if str(row.get("betTypId")) == "4"),
            None,
        )
        under_over_market = next(
            (row for row in group if str(row.get("betTypId")) == "7"),
            None,
        )
        odds_1x2 = [
            _betman_float(main_market.get("winAllot")),
            _betman_float(main_market.get("drawAllot")),
            _betman_float(main_market.get("loseAllot")),
        ]
        if min(odds_1x2) <= 1.0:
            odds_1x2 = [0.0, 0.0, 0.0]

        match_time = _betman_epoch_text(main_market.get("gameDate"))
        deadline_text = _betman_epoch_text(main_market.get("endDate"))
        deadline_time = ""
        if deadline_text != "시간 미정":
            deadline_time = f"{deadline_text[-5:]} 마감"

        parsed.append({
            "id": str(main_market.get("matchSeq") or ""),
            "league": str(
                main_market.get("leagueShortName")
                or main_market.get("leagueName")
                or "축구"
            ).strip(),
            "time": match_time,
            "match_time": match_time,
            "deadline_time": deadline_time,
            "home": home,
            "away": away,
            "odd_h": odds_1x2[0],
            "odd_d": odds_1x2[1],
            "odd_a": odds_1x2[2],
            "handi_h": _betman_float((handicap_market or {}).get("winAllot")),
            "handi_d": _betman_float((handicap_market or {}).get("drawAllot")),
            "handi_a": _betman_float((handicap_market or {}).get("loseAllot")),
            # winHandi is the exact H value printed by Betman's own JavaScript.
            "handi_base": _betman_float((handicap_market or {}).get("winHandi")),
            "uo_under": _betman_float((under_over_market or {}).get("winAllot")),
            "uo_over": _betman_float((under_over_market or {}).get("loseAllot")),
            # For U/O, Betman also renders winHandi as the displayed goal line.
            "uo_base": _betman_float((under_over_market or {}).get("winHandi")),
        })
    return sorted(parsed, key=lambda item: int(item["id"]) if item["id"].isdigit() else 0)


def parse_betman_toto14_json(payload, round_id="current"):
    """Parse the official Toto14 schedule and convert vote counts to percentages."""
    if not isinstance(payload, dict):
        return []
    schedules = payload.get("schedulesList")
    if not isinstance(schedules, list):
        return []
    vote_status = payload.get("voteStatus") or {}
    vote_rows = vote_status.get("homeVoteStatusList") or []
    parsed = []
    for index, schedule_row in enumerate(schedules):
        if not isinstance(schedule_row, dict):
            continue
        try:
            number = int(schedule_row.get("matchSeq"))
        except (TypeError, ValueError):
            continue
        home = str(schedule_row.get("homeName") or "").strip()
        away = str(schedule_row.get("awayName") or "").strip()
        if not home or not away:
            continue

        counts = []
        vote_row = vote_rows[index] if index < len(vote_rows) else {}
        away_votes = vote_row.get("awayVoteStatusList") if isinstance(vote_row, dict) else []
        for vote in (away_votes or [])[:3]:
            counts.append(_betman_float(vote.get("voteCount") if isinstance(vote, dict) else 0))
        while len(counts) < 3:
            counts.append(0.0)
        total_votes = sum(counts)
        percentages = (
            [round(value * 100.0 / total_votes, 1) for value in counts]
            if total_votes > 0
            else [None, None, None]
        )

        raw_time = str(schedule_row.get("gameDateStr") or "").strip()
        match_time = re.sub(r"\s+", " ", raw_time) if raw_time else ""
        if not re.search(r"\d{2}\.\d{2}.*\d{2}:\d{2}", match_time):
            match_time = _betman_epoch_text(schedule_row.get("gameDate"))
        parsed.append({
            "id": f"{round_id}_{number}",
            "round_id": str(round_id),
            "num": number,
            "league": "축구 승무패",
            "home": home,
            "away": away,
            "match_time": match_time,
            "vote_h": percentages[0],
            "vote_d": percentages[1],
            "vote_a": percentages[2],
        })
    return sorted(parsed, key=lambda item: item["num"])


def _valid_toto14_round(records):
    if not isinstance(records, list) or len(records) != 14:
        return False
    round_ids = set()
    numbers = []
    record_ids = set()
    for record in records:
        if not isinstance(record, dict):
            return False
        if not record.get("id") or not record.get("home") or not record.get("away"):
            return False
        try:
            numbers.append(int(record.get("num")))
        except (TypeError, ValueError):
            return False
        round_ids.add(str(record.get("round_id", "")))
        record_ids.add(str(record["id"]))
    return (
        len(round_ids) == 1
        and len(record_ids) == 14
        and sorted(numbers) == list(range(1, 15))
    )


BETMAN_BASE_URL = os.getenv("BETMAN_BASE_URL", "https://www.betman.co.kr").rstrip("/")
BETMAN_HUB_URL = urljoin(
    BETMAN_BASE_URL + "/",
    "main/mainPage/gamebuy/buyableGameList.do",
)

BETMAN_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
    # Betman closes these responses itself. Explicitly closing avoids reusing a
    # half-closed keep-alive socket, which caused intermittent EC2 resets.
    "Connection": "close",
}


def _betman_request(session, method, url, attempts=3, **kwargs):
    """Make a small official Betman request with restrained reset/timeout retries."""
    last_error = None
    for attempt in range(max(1, attempts)):
        try:
            request_method = getattr(session, method.lower())
            response = request_method(url, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException as error:
            last_error = error
            if attempt + 1 >= attempts:
                break
            time.sleep(2 ** attempt)
    raise last_error or RuntimeError(f"베트맨 {method.upper()} 요청 실패: {url}")


def _betman_round_target(row, base_url=BETMAN_BASE_URL):
    """Validate a Betman round-list row and build its direct game-slip URL."""
    if not isinstance(row, dict):
        return None
    gm_id = str(row.get("gmId", "")).strip()
    gm_ts = str(row.get("gmTs", "")).strip()
    if not re.fullmatch(r"G\d{3}", gm_id) or not gm_ts.isdigit():
        return None
    return {
        "gm_id": gm_id,
        "gm_ts": gm_ts,
        "display_round": str(row.get("gmOsidTs", "") or gm_ts),
        "sale_end": int(row.get("saleEndDate") or 0),
        "url": urljoin(
            base_url.rstrip("/") + "/",
            f"main/mainPage/gamebuy/gameSlip.do?gmId={gm_id}&gmTs={gm_ts}",
        ),
    }


def _fetch_betman_round_targets(session=None):
    """Read current Proto/Toto round IDs without rendering Betman's heavy hub.

    Betman's hub fills its tables through this JSON endpoint. Reading that
    endpoint first is much lighter and avoids the EC2 Edge timeout that occurs
    while the hub is still loading third-party scripts and assets.
    """
    owned_session = session is None
    session = session or requests.Session()
    common_headers = BETMAN_REQUEST_HEADERS
    try:
        # The first GET establishes the anonymous session cookie expected by
        # the JSON endpoint. It does not wait for any browser-side assets.
        hub_response = _betman_request(
            session,
            "get",
            BETMAN_HUB_URL,
            headers=common_headers,
            timeout=(10, 30),
        )
        # requests follows official redirects. Build every later address from
        # the final host, rather than assuming Betman's domain never changes.
        resolved_base_url = urljoin(hub_response.url, "/").rstrip("/")
        round_list_url = urljoin(
            resolved_base_url + "/",
            "buyPsblGame/inqBuyAbleGameInfoList.do",
        )
        payload = {"_sbmInfo": {"_sbmInfo": {"debugMode": "false"}}}
        response = _betman_request(
            session,
            "post",
            round_list_url,
            json=payload,
            headers={
                **common_headers,
                "Referer": BETMAN_HUB_URL,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/javascript, */*; q=0.01",
            },
            timeout=(10, 30),
        )
        result = response.json()
        if not isinstance(result, dict):
            raise RuntimeError("베트맨 회차 조회 응답이 JSON 객체가 아님")
        status = result.get("rsMsg") or {}
        if status and status.get("statusCode") not in (None, "S"):
            raise RuntimeError(f"베트맨 회차 조회 상태 오류: {status.get('statusCode')}")

        def latest(rows, gm_id):
            targets = [
                target
                for target in (
                    _betman_round_target(row, resolved_base_url) for row in (rows or [])
                )
                if target and target["gm_id"] == gm_id
            ]
            if not targets:
                return None
            return max(targets, key=lambda item: (item["sale_end"], int(item["gm_ts"])))

        return {
            "proto": latest(result.get("protoGames"), "G101"),
            "toto14": latest(result.get("totoGames"), "G011"),
        }
    finally:
        if owned_session:
            session.close()


def _fetch_betman_game_data(session, target):
    """Read the official JSON used by a Proto or Toto14 game-slip page."""
    if not isinstance(target, dict) or not target.get("gm_id") or not target.get("gm_ts"):
        raise RuntimeError("베트맨 경기 회차 주소가 없음")
    target_url = str(target.get("url") or BETMAN_HUB_URL)
    base_url = urljoin(target_url, "/").rstrip("/")
    endpoint = urljoin(base_url + "/", "buyPsblGame/gameInfoInq.do")
    payload = {
        "gmId": str(target["gm_id"]),
        "gmTs": str(target["gm_ts"]),
        "gameYear": "",
        "_sbmInfo": {"_sbmInfo": {"debugMode": "false"}},
    }
    response = _betman_request(
        session,
        "post",
        endpoint,
        json=payload,
        headers={
            **BETMAN_REQUEST_HEADERS,
            "Referer": target_url,
            "Origin": base_url,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/json; charset=UTF-8",
        },
        timeout=(10, 30),
    )
    result = response.json()
    if not isinstance(result, dict):
        raise RuntimeError("베트맨 경기 응답이 JSON 객체가 아님")
    status = result.get("rsMsg") or {}
    if status and status.get("statusCode") not in (None, "S"):
        raise RuntimeError(f"베트맨 경기 조회 상태 오류: {status.get('statusCode')}")
    return result


def _select_latest_complete_toto14_round(records):
    groups = {}
    order = {}
    for index, record in enumerate(records or []):
        if not isinstance(record, dict):
            continue
        round_id = str(record.get("round_id", "") or "")
        if not round_id:
            match = re.match(r"(.+)_\d+$", str(record.get("id", "")))
            round_id = match.group(1) if match else ""
        if not round_id:
            continue
        record_id = str(record.get("id", "") or "")
        if not record_id:
            continue
        groups.setdefault(round_id, {})[record_id] = record
        order[round_id] = index
    valid = {
        round_id: sorted(group.values(), key=lambda item: int(item.get("num", 0)))
        for round_id, group in groups.items()
        if _valid_toto14_round(list(group.values()))
    }
    if not valid:
        return []

    def sort_key(round_id):
        digits = re.findall(r"\d+", round_id)
        numeric = int(digits[-1]) if digits else -1
        return numeric, order.get(round_id, -1)

    return list(valid[max(valid, key=sort_key)])


def _accept_alert(driver):
    try:
        driver.switch_to.alert.accept()
    except Exception:
        pass


def _browser_arguments():
    # Betman renders a large JavaScript game table.  Artificially capping the
    # renderer at 256 MB and two processes made Edge 151 disconnect its
    # renderer (InvalidSessionId / tab crashed) on the EC2 collector.
    return [
        '--headless=new', '--no-sandbox', '--disable-dev-shm-usage',
        '--disable-gpu', '--disable-extensions',
        '--disable-background-networking', '--disable-component-update',
        '--disable-default-apps', '--disable-sync', '--metrics-recording-only',
        '--no-first-run',
        '--window-size=1365,900', '--blink-settings=imagesEnabled=false',
        '--disable-features=Translate,BackForwardCache,OptimizationHints,MediaRouter',
    ]


def _chrome_options(binary=None):
    options = ChromeOptions()
    for argument in _browser_arguments():
        options.add_argument(argument)
    if binary:
        options.binary_location = binary
    options.page_load_strategy = 'eager'
    return options


def _edge_options(binary=None):
    options = EdgeOptions()
    for argument in _browser_arguments():
        options.add_argument(argument)
    if binary:
        options.binary_location = binary
    options.page_load_strategy = 'eager'
    return options


def _create_webdriver():
    """Prefer provisioned Chromium; keep Edge only as a compatibility fallback."""
    errors = []
    browser_binary = os.getenv("CHROME_BINARY", "").strip() or next(
        (path for path in (
            shutil.which("chromium"), shutil.which("chromium-browser"),
            shutil.which("google-chrome"), shutil.which("google-chrome-stable"),
            shutil.which("chrome"),
        ) if path),
        None,
    )
    driver_binary = os.getenv("CHROMEDRIVER_PATH", "").strip() or shutil.which("chromedriver")

    attempts = []
    if driver_binary:
        attempts.append((
            f"Chromium ({driver_binary})",
            lambda: webdriver.Chrome(
                service=ChromeService(executable_path=driver_binary),
                options=_chrome_options(browser_binary),
            ),
        ))
    attempts.append((
        "Chromium/Selenium Manager",
        lambda: webdriver.Chrome(options=_chrome_options(browser_binary)),
    ))
    if ChromeDriverManager is not None and os.getenv("DISABLE_DRIVER_DOWNLOAD", "0") != "1":
        attempts.append((
            "Chromium/webdriver-manager",
            lambda: webdriver.Chrome(
                service=ChromeService(ChromeDriverManager().install()),
                options=_chrome_options(browser_binary),
            ),
        ))

    edge_binary = os.getenv("EDGE_BINARY", "").strip() or shutil.which("microsoft-edge") or shutil.which("msedge")
    allow_edge = edge_binary or os.name == "nt" or os.getenv("ALLOW_EDGE_FALLBACK", "0") == "1"
    if allow_edge:
        attempts.append((
            "Microsoft Edge/Selenium Manager fallback",
            lambda: webdriver.Edge(options=_edge_options(edge_binary)),
        ))
    if EdgeChromiumDriverManager is not None and allow_edge:
        attempts.append((
            "Microsoft Edge/webdriver-manager fallback",
            lambda: webdriver.Edge(
                service=EdgeService(EdgeChromiumDriverManager().install()),
                options=_edge_options(edge_binary),
            ),
        ))

    for label, factory in attempts:
        try:
            driver = factory()
            driver.set_page_load_timeout(90)
            driver.set_script_timeout(45)
            print(f"✅ 브라우저 시작: {label}")
            return driver
        except Exception as error:
            errors.append(f"{label}: {type(error).__name__}: {error}")
    raise RuntimeError(" / ".join(errors) or "사용 가능한 Chromium/Edge 드라이버 없음")


def _wait_for_stable_rows(driver, selector, timeout=18, stable_samples=3):
    deadline = time.monotonic() + timeout
    previous_count = -1
    stable_count = 0
    latest_count = 0
    while time.monotonic() < deadline:
        latest_count = len(driver.find_elements(By.CSS_SELECTOR, selector))
        if latest_count > 0 and latest_count == previous_count:
            stable_count += 1
            if stable_count >= stable_samples:
                return True, latest_count
        else:
            stable_count = 0
            previous_count = latest_count
        time.sleep(1)
    return False, latest_count


def _betman_link_target(element, base_url):
    """Return the real game-slip URL hidden in either href or JavaScript."""
    candidates = [
        (element.get_attribute("href") or "").strip(),
        (element.get_attribute("onclick") or "").strip(),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        if not candidate.lower().startswith(("javascript:", "#")):
            return urljoin(base_url, candidate)
        # The hub currently exposes links like:
        # javascript:leftA.checkLeftSlipData(5,'/main/.../gameSlip.do?...')
        embedded = re.search(
            r"['\"]((?:https?://[^'\"]+|/[^'\"]*gameSlip\.do\?[^'\"]+))['\"]",
            candidate,
            re.IGNORECASE,
        )
        if embedded:
            return urljoin(base_url, embedded.group(1).replace("&amp;", "&"))
    return None


def _open_link_target(driver, element, base_url):
    """새 창 여부와 관계없이 베트맨 경기표 링크의 실제 주소로 이동한다."""
    target_url = _betman_link_target(element, base_url)
    if target_url:
        driver.get(target_url)
        return target_url

    previous_handles = set(driver.window_handles)
    driver.execute_script("arguments[0].click();", element)
    try:
        WebDriverWait(driver, 8).until(
            lambda current: len(set(current.window_handles) - previous_handles) > 0
        )
        new_handles = list(set(driver.window_handles) - previous_handles)
        if new_handles:
            driver.switch_to.window(new_handles[-1])
    except Exception:
        # 같은 창에서 자바스크립트로 이동하는 링크도 있으므로 그대로 계속한다.
        pass
    return driver.current_url or ""


def _scrape_with_fresh_browser(label, operation):
    last_error = None
    for attempt in range(2):
        driver = None
        try:
            driver = _create_webdriver()
            return operation(driver)
        except Exception as error:
            last_error = error
            print(f"❌ {label} 수집 실패({attempt + 1}/2): {type(error).__name__}: {error}")
        finally:
            if driver is not None:
                try:
                    driver.quit()
                    print("🧹 브라우저 프로세스 정상 종료 완료.")
                except Exception:
                    pass
        # Cleanup must finish before the next WebDriver starts.  Starting the
        # retry while Edge renderer children are still exiting can make the
        # next session inherit the same renderer-disconnect failure.
        if attempt == 0:
            time.sleep(5)
    raise RuntimeError(str(last_error or f"{label} 수집 실패"))


def _load_pending_match_ids():
    try:
        conn = sqlite3.connect(str(_local_path("ai_predictions.db")), timeout=15)
        try:
            return {
                str(row[0])
                for row in conn.execute(
                    "SELECT match_id FROM predictions WHERE actual_result = 'PENDING'"
                ).fetchall()
            }
        finally:
            conn.close()
    except Exception:
        return set()


def _record_is_still_active(record, pending_ids, prefix=""):
    match_id = f"{prefix}{record.get('id', '')}"
    match_dt = _parse_kst_match_time(record.get("match_time") or record.get("time"))
    if match_dt is not None:
        return datetime.now(KST) <= match_dt + timedelta(hours=2 + LIVE_RETENTION_HOURS)
    # Unknown kickoff records get the DB state as a conservative fallback, but
    # dated stale rounds cannot grow the retained pool forever merely because a
    # fixture has not been graded yet.
    return match_id in pending_ids


def _merge_active_records(fresh, previous, pending_ids, prefix=""):
    merged = []
    seen = set()
    for record in fresh:
        match_id = str(record.get("id", ""))
        if not match_id or match_id in seen:
            continue
        seen.add(match_id)
        merged.append(record)
    for record in previous:
        match_id = str(record.get("id", ""))
        if not match_id or match_id in seen:
            continue
        if _record_is_still_active(record, pending_ids, prefix=prefix):
            retained = dict(record)
            retained["retained_from_last_good"] = True
            merged.append(retained)
            seen.add(match_id)
    return merged


def _valid_scrape_records(records):
    if not isinstance(records, list):
        return False
    ids = []
    for record in records:
        if not isinstance(record, dict):
            return False
        if not record.get("id") or not record.get("home") or not record.get("away"):
            return False
        ids.append(str(record["id"]))
    return len(ids) == len(set(ids))


def scrape_betman():
    print(f"\n[🔄 {time.strftime('%Y-%m-%d %H:%M:%S')}] 베트맨 실제 경기/배당 수집 가동...")
    hub_url = BETMAN_HUB_URL
    old_data = _read_json("betman_data.json", {})
    if not isinstance(old_data, dict):
        old_data = {}
    old_proto = old_data.get("proto_matches", []) if isinstance(old_data.get("proto_matches"), list) else []
    old_toto14_primary = old_data.get("toto_14_matches", []) if isinstance(old_data.get("toto_14_matches"), list) else []
    old_toto14_retained = old_data.get("toto_14_retained_matches", []) if isinstance(old_data.get("toto_14_retained_matches"), list) else []
    old_toto14_by_id = {}
    for record in old_toto14_primary + old_toto14_retained:
        if isinstance(record, dict) and record.get("id"):
            old_toto14_by_id[str(record["id"])] = record
    old_toto14 = list(old_toto14_by_id.values())

    proto_stable = False
    toto_stable = False
    matches = []
    matches_14 = []

    round_targets = {}
    betman_session = requests.Session()
    try:
        round_targets = _fetch_betman_round_targets(betman_session)
        proto_round = (round_targets.get("proto") or {}).get("display_round", "-")
        toto_round = (round_targets.get("toto14") or {}).get("display_round", "-")
        print(f"✅ 베트맨 현재 회차 직접 확인: 프로토 {proto_round} / 승무패 {toto_round}")
    except Exception as error:
        # Keep the old hub navigation as a safety fallback if Betman changes
        # the lightweight endpoint in the future.
        print(f"⚠️ 베트맨 회차 직접 조회 실패, 기존 화면 탐색으로 전환: {error}")

    # A reset while reading the round list must not prevent a direct data retry
    # for the last confirmed round. Toto14 records already carry their official
    # gmTs; V7 also stores both source round IDs after every successful cycle.
    saved_source_status = old_data.get("source_status", {})
    if not isinstance(saved_source_status, dict):
        saved_source_status = {}
    if not round_targets.get("proto"):
        saved_proto_status = saved_source_status.get("proto", {})
        saved_proto_round = (
            str(saved_proto_status.get("round_id") or "")
            if isinstance(saved_proto_status, dict)
            else ""
        )
        if saved_proto_round.isdigit():
            round_targets["proto"] = _betman_round_target({
                "gmId": "G101",
                "gmTs": saved_proto_round,
                "gmOsidTs": saved_proto_round,
            })
            print(f"↪️ 저장된 프로토 {saved_proto_round}회차로 공식 JSON 재시도")
    if not round_targets.get("toto14"):
        saved_toto_status = saved_source_status.get("toto14", {})
        saved_toto_round = (
            str(saved_toto_status.get("round_id") or "")
            if isinstance(saved_toto_status, dict)
            else ""
        )
        if not saved_toto_round:
            complete_old_toto = _select_latest_complete_toto14_round(old_toto14)
            old_round_ids = {
                str(record.get("round_id") or "")
                for record in complete_old_toto
                if isinstance(record, dict)
            }
            if len(old_round_ids) == 1:
                saved_toto_round = next(iter(old_round_ids))
        if saved_toto_round.isdigit():
            round_targets["toto14"] = _betman_round_target({
                "gmId": "G011",
                "gmTs": saved_toto_round,
                "gmOsidTs": saved_toto_round,
            })
            print(f"↪️ 저장된 승무패 {saved_toto_round}회차로 공식 JSON 재시도")

    # Betman's browser page renders these two payloads through AJAX. Reading
    # them directly avoids Edge renderer crashes and does not depend on DOM or
    # on the game-slip URL shape. Each source is accepted independently.
    if round_targets.get("proto"):
        try:
            proto_payload = _fetch_betman_game_data(betman_session, round_targets["proto"])
            matches = parse_betman_proto_json(proto_payload)
            proto_stable = bool(matches and _valid_scrape_records(matches))
            if not proto_stable:
                raise RuntimeError(f"공식 프로토 JSON에 정상 축구 경기 없음: {len(matches)}건")
            print(f"✅ 공식 JSON 프로토 축구 경기 추출: {len(matches)}경기 (브라우저 미사용)")
        except Exception as error:
            matches = []
            proto_stable = False
            print(f"⚠️ 공식 JSON 프로토 조회 실패, 브라우저 1회 대체: {error}")

    if round_targets.get("toto14"):
        try:
            toto_target = round_targets["toto14"]
            toto_payload = _fetch_betman_game_data(betman_session, toto_target)
            round_id = str(toto_target.get("gm_ts") or toto_target.get("display_round"))
            matches_14 = parse_betman_toto14_json(toto_payload, round_id)
            toto_stable = _valid_toto14_round(matches_14)
            if not toto_stable:
                raise RuntimeError(f"공식 승무패 JSON이 14경기 완본 아님: {len(matches_14)}건")
            print(
                "✅ 공식 JSON 승무패 "
                f"{toto_target.get('display_round', round_id)}회차 추출: 14경기 (브라우저 미사용)"
            )
        except Exception as error:
            matches_14 = []
            toto_stable = False
            print(f"⚠️ 공식 JSON 승무패 조회 실패, 브라우저 1회 대체: {error}")
    betman_session.close()

    def scrape_proto_page(driver):
        proto_target = round_targets.get("proto") or {}
        proto_target_url = proto_target.get("url")
        if proto_target_url:
            print(f"➡️ 프로토 {proto_target.get('display_round', '-')}회차 직접 접속")
            driver.get(proto_target_url)
            _accept_alert(driver)
        else:
            driver.get(hub_url)
            _accept_alert(driver)
            # We navigate with the href itself, so visibility/clickability is
            # not required and can be blocked by Betman's loading overlay.
            proto_btn = WebDriverWait(driver, 35).until(EC.presence_of_element_located((
                By.XPATH,
                "//a[contains(normalize-space(.), '프로토 승부식') and contains(normalize-space(.), '회차')]",
            )))
            proto_target_url = _open_link_target(driver, proto_btn, hub_url)
        proto_row_selector = ".box-data-group [data-rowname]"
        try:
            WebDriverWait(driver, 45).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, proto_row_selector))
            )
        except Exception as error:
            row_count = len(driver.find_elements(By.CSS_SELECTOR, proto_row_selector))
            raise RuntimeError(
                "프로토 경기행 대기 실패 "
                f"(행={row_count}, 현재주소={driver.current_url or '-'}, "
                f"직접주소={proto_target_url or '-'}, 제목={driver.title or '-'})"
            ) from error
        for _ in range(20):
            visible_more = [button for button in driver.find_elements(
                By.XPATH,
                "//*[self::button or self::a][contains(normalize-space(.), '더보기')]",
            ) if button.is_displayed()]
            if not visible_more:
                break
            driver.execute_script("arguments[0].click();", visible_more[0])
            time.sleep(0.8)
        stable, _ = _wait_for_stable_rows(driver, proto_row_selector)
        if not stable:
            raise RuntimeError("프로토 행 개수가 제한시간 안에 안정되지 않음")
        return parse_betman_proto_html(driver.page_source), stable

    def scrape_toto_page(driver):
        toto_target = round_targets.get("toto14") or {}
        target_url = toto_target.get("url")
        round_hints = [
            toto_target.get("gm_ts", ""),
            toto_target.get("display_round", ""),
            target_url or "",
        ]
        if target_url:
            print(f"➡️ 승무패 {toto_target.get('display_round', '-')}회차 직접 접속")
            driver.get(target_url)
            _accept_alert(driver)
        else:
            driver.get(hub_url)
            _accept_alert(driver)
            toto_btn = WebDriverWait(driver, 45).until(EC.presence_of_element_located((
                By.XPATH,
                "//a[contains(normalize-space(.), '축구 승무패') and contains(normalize-space(.), '회차')]",
            )))
            round_hints.extend([
                toto_btn.get_attribute("href") or "",
                toto_btn.get_attribute("onclick") or "",
                toto_btn.text or "",
            ])
            target_url = _open_link_target(driver, toto_btn, hub_url)
        row_selector = "table#grid_victory tbody tr, #grid_victory_tbody > tr"
        try:
            WebDriverWait(driver, 60).until(
                lambda current: len(current.find_elements(By.CSS_SELECTOR, row_selector)) >= 14
            )
        except Exception as error:
            row_count = len(driver.find_elements(By.CSS_SELECTOR, row_selector))
            raise RuntimeError(
                "승무패 표 14행 대기 실패 "
                f"(행={row_count}, 현재주소={driver.current_url or '-'}, "
                f"직접주소={target_url or '-'}, 제목={driver.title or '-'})"
            ) from error
        stable, _ = _wait_for_stable_rows(
            driver,
            row_selector,
            timeout=30,
            stable_samples=2,
        )
        round_hints.append(driver.current_url or "")
        round_id = None
        for hint in round_hints:
            round_match = re.search(r'gmTs\s*[=:]\s*["\']?(\d+)', str(hint), re.IGNORECASE)
            if not round_match:
                round_match = re.search(r'(\d+)\s*회차', str(hint))
            if round_match:
                round_id = round_match.group(1)
                break
        if not round_id:
            raise RuntimeError("승무패 회차 ID를 확인할 수 없어 기존 동결본을 보호합니다.")
        parsed = parse_betman_toto14_html(driver.page_source, round_id)
        if not stable or not _valid_toto14_round(parsed):
            raise RuntimeError(f"승무패 완본 미확인: 안정={stable}, 행={len(parsed)}")
        return parsed, stable, round_id

    try:
        if not proto_stable:
            matches, proto_stable = _scrape_with_fresh_browser("프로토 승부식", scrape_proto_page)
        proto_limit = 0
        if os.getenv("ALLOW_PROTO_LIMIT", "0") == "1":
            try:
                proto_limit = max(0, int(os.getenv("MAX_PROTO_MATCHES", "0")))
            except ValueError:
                proto_limit = 0
        if proto_limit:
            matches = matches[:proto_limit]
        print(f"✅ 프로토 실제 축구 경기 발견: {len(matches)}경기")
    except Exception as error:
        print(f"❌ 프로토 최종 수집 실패: {error}")

    try:
        if not toto_stable:
            matches_14, toto_stable, round_id = _scrape_with_fresh_browser("축구 승무패", scrape_toto_page)
        else:
            round_id = str((round_targets.get("toto14") or {}).get("gm_ts") or "current")
        print(f"✅ 축구 승무패 {round_id}회차 추출: {len(matches_14)}경기")
    except Exception as error:
        print(f"❌ 축구 승무패 최종 수집 실패: {error}")

    pending_ids = _load_pending_match_ids()
    old_active_proto = [
        item for item in old_proto if _record_is_still_active(item, pending_ids)
    ]
    proto_accepted = bool(matches and proto_stable and _valid_scrape_records(matches))
    if (
        proto_accepted
        and os.getenv("ALLOW_PROTO_LIMIT", "0") != "1"
        and len(matches) < PROTO_MIN_SCRAPE_ROWS
        and len(old_active_proto) >= PROTO_MIN_SCRAPE_ROWS
    ):
        proto_accepted = False
        print(f"⚠️ 프로토 급감({len(matches)}건)을 부분 수집으로 판정해 정상본을 유지합니다.")

    # A football pools round is complete only with fourteen unique rows.
    toto_accepted = bool(
        matches_14 and toto_stable and _valid_toto14_round(matches_14)
    )
    if matches_14 and not toto_accepted:
        print(f"⚠️ 승무패가 14경기 완본이 아니어서({len(matches_14)}건) 정상본을 유지합니다.")

    fresh_proto = matches if proto_accepted else []
    fresh_toto14 = matches_14 if toto_accepted else []
    final_proto = _merge_active_records(fresh_proto, old_proto, pending_ids)
    # A complete new pools round supersedes older rounds. Mixing retained rows
    # from another round produced 28+ cards and exponential phantom cost.
    final_toto14 = (
        sorted(fresh_toto14, key=lambda item: int(item.get("num", 0)))
        if toto_accepted
        else _select_latest_complete_toto14_round(old_toto14)
    )
    final_toto14_ids = {str(item.get("id")) for item in final_toto14}
    retained_toto14 = [
        dict(item, retained_from_last_good=True)
        for item in old_toto14
        if str(item.get("id")) not in final_toto14_ids
        and _record_is_still_active(item, pending_ids, prefix="TOTO14_")
    ]

    if not proto_accepted:
        print("⚠️ 이번 프로토 수집을 채택하지 않고 마지막 정상/진행 기록을 보존합니다.")
    if not toto_accepted:
        print("⚠️ 이번 승무패 수집을 채택하지 않고 마지막 정상/진행 기록을 보존합니다.")
    if not proto_accepted and not toto_accepted:
        print("❌ 새 완전 데이터가 없어 대시보드 재분석을 중단합니다.")
        return False

    now_iso = datetime.now(KST).isoformat(timespec="seconds")
    old_source_status = old_data.get("source_status", {})
    if not isinstance(old_source_status, dict):
        old_source_status = {}
    old_proto_status = old_source_status.get("proto", {})
    old_toto_status = old_source_status.get("toto14", {})
    if not isinstance(old_proto_status, dict):
        old_proto_status = {}
    if not isinstance(old_toto_status, dict):
        old_toto_status = {}
    result = {
        "proto_matches": final_proto,
        "toto_14_matches": final_toto14,
        "toto_14_retained_matches": retained_toto14,
        "collected_at": now_iso,
        "source_status": {
            "proto": {
                "fresh": proto_accepted,
                "fresh_count": len(matches),
                "published_count": len(final_proto),
                "round_id": (
                    (round_targets.get("proto") or {}).get("gm_ts")
                    if proto_accepted
                    else old_proto_status.get("round_id")
                ),
                "last_success_at": now_iso if proto_accepted else old_proto_status.get("last_success_at"),
            },
            "toto14": {
                "fresh": toto_accepted,
                "fresh_count": len(matches_14),
                "published_count": len(final_toto14),
                "retained_lifecycle_count": len(retained_toto14),
                "round_id": (
                    (round_targets.get("toto14") or {}).get("gm_ts")
                    if toto_accepted
                    else old_toto_status.get("round_id")
                ),
                "last_success_at": now_iso if toto_accepted else old_toto_status.get("last_success_at"),
            },
        },
    }
    _atomic_write_json("betman_data.json", result, indent=2)
    print(f"✅ 수집 완료: 프로토 {len(final_proto)}경기 / 승무패 {len(final_toto14)}경기")
    return True

def run_live_score_job():
    success = update_live_scores()
    if not success:
        return False
    return upload_to_github("live_scores.json")


def run_master_job():
    if not scrape_betman():
        return False
    if not build_dashboard_data():
        return False
    # Publish the DB snapshot first. If it cannot be published, keep the remote
    # dashboard at its last-known-good version instead of exposing unmatched JSON.
    if not upload_sqlite_to_github("ai_predictions.db"):
        return False
    return upload_to_github("dashboard_data.json")


def run_score_job():
    success = auto_score_matches()
    if not success:
        return False
    if not upload_sqlite_to_github("ai_predictions.db"):
        return False
    if not _refresh_dashboard_grading_snapshot():
        return False
    return upload_to_github("dashboard_data.json")


def _world_schedule_refresh_due(now=None):
    """Refresh the broad schedule slowly while allowing frequent cached analysis."""
    now = now or datetime.now(KST)
    payload = _read_json(WORLD_DASHBOARD_FILE, {})
    generated_at = str((payload or {}).get("generated_at") or "")
    if not generated_at:
        return True
    try:
        generated = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=KST)
        return (now - generated.astimezone(KST)).total_seconds() >= (
            WORLD_SCHEDULE_REFRESH_HOURS * 3600
        )
    except (TypeError, ValueError):
        return True


def run_world_job():
    """Run independently so WORLD failures never block PROTO/LIVE/scoring."""
    schedule_refreshed = False
    if _world_schedule_refresh_due():
        if not collect_world_schedule():
            return False
        schedule_refreshed = True

    analysis_ok, analysis_changed = analyze_world_schedule()
    if not analysis_ok:
        return False

    # Store the private learning snapshot before publishing its matching admin JSON.
    if analysis_changed and not upload_sqlite_to_github("ai_predictions.db"):
        return False
    if schedule_refreshed or analysis_changed:
        return upload_to_github(
            WORLD_DASHBOARD_FILE, remote_path=WORLD_DASHBOARD_FILE.name
        )
    return True


def _initialize_db_safely():
    fd, lock_path = _try_acquire_lock("db-init", stale_after=180, wait_seconds=45)
    if fd is None:
        return _validate_sqlite_file("ai_predictions.db")
    try:
        init_cache_db()
        return _validate_sqlite_file("ai_predictions.db")
    finally:
        _release_lock(fd, lock_path)


JOB_FUNCTIONS = {
    "master": run_master_job,
    "live": run_live_score_job,
    "score": run_score_job,
    "world": run_world_job,
}
JOB_TIMEOUTS = {
    "master": max(900, int(os.getenv("MASTER_JOB_TIMEOUT_SECONDS", "2700"))),
    "live": max(90, int(os.getenv("LIVE_JOB_TIMEOUT_SECONDS", "180"))),
    "score": max(120, int(os.getenv("SCORE_JOB_TIMEOUT_SECONDS", "600"))),
    "world": max(600, int(os.getenv("WORLD_JOB_TIMEOUT_SECONDS", "1200"))),
}


def _publish_status():
    try:
        return upload_to_github(STATUS_FILE, remote_path=STATUS_FILE.name)
    except Exception as error:
        print(f"⚠️ 수집기 상태 게시 실패: {error}")
        return False


def _execute_job(job_name):
    timeout = JOB_TIMEOUTS[job_name]
    with _job_lock(job_name, stale_after=timeout + 120) as acquired:
        if not acquired:
            print(f"⏭️ {job_name} 작업이 이미 실행 중이어서 중복 실행을 건너뜁니다.")
            return 0
        started = time.monotonic()
        _update_collector_status(job_name, "running")
        _publish_status()
        try:
            if not _initialize_db_safely():
                raise RuntimeError("ai_predictions.db 초기화/검증 실패")
            success = JOB_FUNCTIONS[job_name]()
            duration = round(time.monotonic() - started, 2)
            if success is False:
                raise RuntimeError(f"{job_name} 작업이 정상 결과를 게시하지 못함")
            _update_collector_status(job_name, "success", duration_seconds=duration)
            _publish_status()
            return 0
        except Exception as error:
            duration = round(time.monotonic() - started, 2)
            error_text = f"{type(error).__name__}: {error}"
            print(f"❌ {job_name} 작업 실패: {error_text}")
            traceback.print_exc()
            _update_collector_status(
                job_name,
                "failed",
                duration_seconds=duration,
                last_error=error_text[:1000],
            )
            _publish_status()
            return 1


_JOB_PROCESSES = {}


def _launch_isolated_job(job_name):
    existing = _JOB_PROCESSES.get(job_name)
    if existing and existing["process"].poll() is None:
        _update_collector_status(
            job_name,
            "running",
            child_pid=existing["process"].pid,
            elapsed_seconds=round(time.monotonic() - existing["started"], 1),
            overlap_skips=int(existing.get("overlap_skips", 0)) + 1,
        )
        existing["overlap_skips"] = int(existing.get("overlap_skips", 0)) + 1
        print(f"⏭️ {job_name} 이전 실행이 남아 있어 이번 주기를 겹치지 않습니다.")
        return False

    command = [sys.executable, "-u", str(Path(__file__).resolve()), "--mode", job_name]
    popen_kwargs = {
        "cwd": str(APP_DIR),
        "env": {**os.environ, "PYTHONUNBUFFERED": "1"},
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(command, **popen_kwargs)
    except Exception as error:
        error_text = f"{type(error).__name__}: {error}"
        print(f"❌ {job_name} 분리 작업 시작 실패: {error_text}")
        _update_collector_status(job_name, "failed", last_error=error_text[:1000])
        return False
    _JOB_PROCESSES[job_name] = {
        "process": process,
        "started": time.monotonic(),
        "timeout": JOB_TIMEOUTS[job_name],
        "overlap_skips": 0,
    }
    _update_collector_status(job_name, "running", child_pid=process.pid)
    print(f"🚀 분리 작업 시작: {job_name} (PID {process.pid})")
    return True


def _terminate_process_tree(process):
    if process.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                timeout=10,
            )
        process.wait(timeout=8)
    except Exception:
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except Exception:
            pass


def _reap_job_processes():
    now = time.monotonic()
    for job_name, info in list(_JOB_PROCESSES.items()):
        process = info["process"]
        elapsed = now - info["started"]
        return_code = process.poll()
        if return_code is None and elapsed > info["timeout"]:
            print(f"❌ {job_name} 작업 제한시간 초과({int(elapsed)}초) - 프로세스 트리 종료")
            _terminate_process_tree(process)
            _update_collector_status(
                job_name,
                "failed",
                child_pid=process.pid,
                duration_seconds=round(elapsed, 1),
                last_error=f"hard timeout after {int(elapsed)} seconds",
            )
            _publish_status()
            _JOB_PROCESSES.pop(job_name, None)
        elif return_code is not None:
            if return_code != 0:
                _update_collector_status(
                    job_name,
                    "failed",
                    child_pid=process.pid,
                    duration_seconds=round(elapsed, 1),
                    last_error=f"worker exited with code {return_code}",
                )
            _JOB_PROCESSES.pop(job_name, None)


def _heartbeat_active_jobs():
    now = time.monotonic()
    for job_name, info in list(_JOB_PROCESSES.items()):
        process = info["process"]
        if process.poll() is None:
            _update_collector_status(
                job_name,
                "running",
                child_pid=process.pid,
                elapsed_seconds=round(now - info["started"], 1),
                overlap_skips=int(info.get("overlap_skips", 0)),
            )


def run_scheduler():
    os.chdir(APP_DIR)
    _update_collector_status("scheduler", "running", supervisor_pid=os.getpid())
    download_latest_db_from_github()
    if not _initialize_db_safely():
        raise RuntimeError("수집기 DB 초기화 실패")

    schedule.clear()
    # Live and scoring start immediately and independently; master may take 20+ min.
    _launch_isolated_job("live")
    _launch_isolated_job("score")
    _launch_isolated_job("master")
    _launch_isolated_job("world")
    schedule.every(5).minutes.do(_launch_isolated_job, "live")
    schedule.every(5).minutes.do(_launch_isolated_job, "score")
    schedule.every(20).minutes.do(_launch_isolated_job, "master")
    schedule.every(WORLD_ANALYSIS_INTERVAL_MINUTES).minutes.do(
        _launch_isolated_job, "world"
    )

    print(
        "\n🚀 [감시 스케줄러] master/live/score/world 분리 · 중복 방지 · "
        f"WORLD {WORLD_ANALYSIS_INTERVAL_MINUTES}분 분석/{WORLD_SCHEDULE_REFRESH_HOURS}시간 일정"
    )
    last_heartbeat = 0.0
    while True:
        try:
            schedule.run_pending()
            _reap_job_processes()
            if time.monotonic() - last_heartbeat >= 30:
                _heartbeat_active_jobs()
                _update_collector_status(
                    "scheduler",
                    "running",
                    supervisor_pid=os.getpid(),
                    active_jobs={
                        name: info["process"].pid
                        for name, info in _JOB_PROCESSES.items()
                        if info["process"].poll() is None
                    },
                )
                last_heartbeat = time.monotonic()
            time.sleep(2)
        except KeyboardInterrupt:
            print("수집기 종료 신호를 받았습니다.")
            for info in list(_JOB_PROCESSES.values()):
                _terminate_process_tree(info["process"])
            _update_collector_status("scheduler", "stopped")
            return 0
        except Exception as error:
            print(f"⚠️ 스케줄러 루프 오류(계속 실행): {type(error).__name__}: {error}")
            _update_collector_status("scheduler", "running", last_error=str(error)[:1000])
            time.sleep(5)


def main(argv=None):
    parser = argparse.ArgumentParser(description="D.J SPORTS collector")
    parser.add_argument(
        "--mode",
        choices=("scheduler", "master", "live", "score", "world"),
        default="scheduler",
        help="scheduler supervises isolated workers; other modes run one job once",
    )
    args = parser.parse_args(argv)
    os.chdir(APP_DIR)
    if args.mode == "scheduler":
        try:
            with _job_lock("scheduler", stale_after=86400) as acquired:
                if not acquired:
                    print("⏭️ 수집기 스케줄러가 이미 실행 중입니다.")
                    return 0
                return run_scheduler()
        except Exception as error:
            _update_collector_status(
                "scheduler", "failed", last_error=f"{type(error).__name__}: {error}"[:1000]
            )
            traceback.print_exc()
            return 1
    return _execute_job(args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
