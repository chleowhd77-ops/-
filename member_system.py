"""D.J SPORTS ANALYTICS 회원·권한·게시판 기반 모듈.

현재는 별도 설치 없이 실행할 수 있도록 SQLite를 사용한다. 상용 운영 전에는
DJ_MEMBER_DB_PATH를 AWS의 영구 볼륨 경로로 지정하거나 PostgreSQL 어댑터로
교체해야 한다.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


ROLE_GUEST = "guest"
ROLE_MEMBER = "member"
ROLE_SUPPORTER = "supporter"
ROLE_ADMIN = "admin"
VALID_ROLES = {ROLE_MEMBER, ROLE_SUPPORTER, ROLE_ADMIN}

ROLE_LABELS = {
    ROLE_GUEST: "비회원",
    ROLE_MEMBER: "일반회원",
    ROLE_SUPPORTER: "후원회원",
    ROLE_ADMIN: "관리자",
}

PBKDF2_ROUNDS = 240_000
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9가-힣_]{3,24}$")
MAX_POST_IMAGE_BYTES = 2 * 1024 * 1024
ALLOWED_POST_IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp"}
NOTICE_MODES = {"banner", "popup"}
NOTICE_AUDIENCES = {"all", "guest", "member", "supporter"}
AUTH_SESSION_DAYS = max(1, min(int(os.getenv("DJ_AUTH_SESSION_DAYS", "30")), 90))
AUTH_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
KST = timezone(timedelta(hours=9))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def kst_today() -> str:
    return datetime.now(KST).date().isoformat()


def get_db_path() -> Path:
    configured = os.getenv("DJ_MEMBER_DB_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    # 기존 배포판의 users.db를 그대로 사용해 기존 회원을 자동 이전한다.
    return Path(__file__).resolve().with_name("users.db")


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS
    )
    return digest.hex(), salt.hex()


def _verify_password(password: str, stored_hash: str, salt_hex: str) -> bool:
    if stored_hash.startswith("legacy$"):
        legacy = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return hmac.compare_digest(legacy, stored_hash.removeprefix("legacy$"))
    candidate, _ = hash_password(password, salt_hex)
    return hmac.compare_digest(candidate, stored_hash)


def init_member_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'member'
                    CHECK(role IN ('member', 'supporter', 'admin')),
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK(status IN ('active', 'suspended')),
                created_at TEXT NOT NULL,
                last_login_at TEXT
            );

            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                author_id INTEGER NOT NULL,
                category TEXT NOT NULL DEFAULT 'proof'
                    CHECK(category IN ('proof', 'free', 'notice')),
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                image_mime TEXT,
                image_data BLOB,
                notice_mode TEXT NOT NULL DEFAULT 'banner',
                notice_audience TEXT NOT NULL DEFAULT 'all',
                notice_start_at TEXT,
                notice_end_at TEXT,
                notice_link TEXT,
                status TEXT NOT NULL DEFAULT 'visible'
                    CHECK(status IN ('visible', 'hidden', 'deleted')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(author_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS support_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                depositor_name TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'approved', 'rejected')),
                created_at TEXT NOT NULL,
                reviewed_at TEXT,
                reviewed_by INTEGER,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(reviewed_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id INTEGER,
                action TEXT NOT NULL,
                target TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(actor_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                last_seen_at TEXT,
                revoked_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE INDEX IF NOT EXISTS idx_auth_sessions_user_id
                ON auth_sessions(user_id);
            """
        )
        # 기존 users.db도 그대로 사용할 수 있도록 새 열만 안전하게 추가한다.
        post_columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(posts)")
        }
        if "image_mime" not in post_columns:
            conn.execute("ALTER TABLE posts ADD COLUMN image_mime TEXT")
        if "image_data" not in post_columns:
            conn.execute("ALTER TABLE posts ADD COLUMN image_data BLOB")
        if "notice_mode" not in post_columns:
            conn.execute(
                "ALTER TABLE posts ADD COLUMN notice_mode TEXT NOT NULL DEFAULT 'banner'"
            )
        if "notice_audience" not in post_columns:
            conn.execute(
                "ALTER TABLE posts ADD COLUMN notice_audience TEXT NOT NULL DEFAULT 'all'"
            )
        if "notice_start_at" not in post_columns:
            conn.execute("ALTER TABLE posts ADD COLUMN notice_start_at TEXT")
        if "notice_end_at" not in post_columns:
            conn.execute("ALTER TABLE posts ADD COLUMN notice_end_at TEXT")
        if "notice_link" not in post_columns:
            conn.execute("ALTER TABLE posts ADD COLUMN notice_link TEXT")
        _migrate_legacy_users(conn)


def _migrate_legacy_users(conn: sqlite3.Connection) -> None:
    old_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='userstable'"
    ).fetchone()
    if not old_table:
        return

    for row in conn.execute("SELECT username, password, is_vip FROM userstable"):
        username = str(row[0]).strip()
        if not username:
            continue
        role = ROLE_ADMIN if username.lower() == "admin" else (
            ROLE_SUPPORTER if int(row[2] or 0) else ROLE_MEMBER
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO users
                (username, display_name, password_hash, password_salt, role, created_at)
            VALUES (?, ?, ?, '', ?, ?)
            """,
            (username, username, f"legacy${row[1]}", role, utc_now()),
        )


def register_user(username: str, password: str, display_name: str = "") -> tuple[bool, str]:
    username = username.strip()
    display_name = (display_name or username).strip()[:30]
    if not USERNAME_PATTERN.fullmatch(username):
        return False, "아이디는 한글·영문·숫자·밑줄로 3~24자까지 입력해주세요."
    if len(password) < 8:
        return False, "비밀번호는 8자 이상으로 만들어주세요."

    password_hash, salt = hash_password(password)
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO users
                    (username, display_name, password_hash, password_salt, role, created_at)
                VALUES (?, ?, ?, ?, 'member', ?)
                """,
                (username, display_name, password_hash, salt, utc_now()),
            )
        return True, "가입이 완료되었습니다."
    except sqlite3.IntegrityError:
        return False, "이미 사용 중인 아이디입니다."
    except sqlite3.Error:
        return False, "회원 정보를 저장하지 못했습니다. 잠시 후 다시 시도해주세요."


def authenticate_user(username: str, password: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username.strip(),)
        ).fetchone()
        if not row or row["status"] != "active":
            return None
        if not _verify_password(password, row["password_hash"], row["password_salt"]):
            return None

        if row["password_hash"].startswith("legacy$"):
            new_hash, new_salt = hash_password(password)
            conn.execute(
                "UPDATE users SET password_hash=?, password_salt=? WHERE id=?",
                (new_hash, new_salt, row["id"]),
            )
        conn.execute(
            "UPDATE users SET last_login_at=? WHERE id=?", (utc_now(), row["id"])
        )
        return {
            "id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"],
            "role": row["role"],
            "status": row["status"],
        }


def _session_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_login_session(user_id: int, days: int | None = None) -> str | None:
    """Create a revocable browser login token and store only its hash."""
    lifetime_days = AUTH_SESSION_DAYS if days is None else max(1, min(int(days), 90))
    token = secrets.token_urlsafe(32)
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat(timespec="seconds")
    expires_at = (now_dt + timedelta(days=lifetime_days)).isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute(
            "DELETE FROM auth_sessions WHERE expires_at<=? OR revoked_at IS NOT NULL",
            (now,),
        )
        user = conn.execute(
            "SELECT id FROM users WHERE id=? AND status='active'", (int(user_id),)
        ).fetchone()
        if not user:
            return None
        conn.execute(
            """
            INSERT INTO auth_sessions(
                token_hash, user_id, created_at, expires_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (_session_token_hash(token), int(user_id), now, expires_at, now),
        )
    return token


def authenticate_session(token: str) -> dict[str, Any] | None:
    """Restore one active user from a valid persistent browser token."""
    token = (token or "").strip()
    if not AUTH_TOKEN_PATTERN.fullmatch(token):
        return None
    now = utc_now()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT u.id, u.username, u.display_name, u.role, u.status
            FROM auth_sessions s
            JOIN users u ON u.id=s.user_id
            WHERE s.token_hash=? AND s.revoked_at IS NULL
              AND s.expires_at>? AND u.status='active'
            """,
            (_session_token_hash(token), now),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE auth_sessions SET last_seen_at=? WHERE token_hash=?",
            (now, _session_token_hash(token)),
        )
    return dict(row)


def revoke_login_session(token: str) -> None:
    """Invalidate the current browser token on an explicit logout."""
    token = (token or "").strip()
    if not AUTH_TOKEN_PATTERN.fullmatch(token):
        return
    with _connect() as conn:
        conn.execute(
            "UPDATE auth_sessions SET revoked_at=? WHERE token_hash=?",
            (utc_now(), _session_token_hash(token)),
        )


def list_users() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, username, display_name, role, status, created_at, last_login_at
            FROM users ORDER BY created_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def bootstrap_admin(
    user_id: int,
    username: str,
    supplied_token: str,
    configured_username: str,
    configured_token: str,
) -> tuple[bool, str]:
    """Promote the configured owner account after a one-time secret check.

    The configured values must come from the hosting service's private secrets,
    never from the public repository. Requiring both an existing signed-in user
    and the private token prevents a display name such as "관리자" from granting
    any permission.
    """
    configured_username = configured_username.strip()
    configured_token = configured_token.strip()
    supplied_token = supplied_token.strip()
    if not configured_username or not configured_token:
        return False, "서버에 최초 관리자 설정이 아직 등록되지 않았습니다."
    if len(configured_token) < 16:
        return False, "관리자 인증키는 16자 이상으로 설정해주세요."
    if not hmac.compare_digest(username.casefold(), configured_username.casefold()):
        return False, "이 계정은 서버에 등록된 운영자 아이디와 다릅니다."
    if not hmac.compare_digest(supplied_token, configured_token):
        return False, "관리자 인증키가 일치하지 않습니다."

    with _connect() as conn:
        user = conn.execute(
            "SELECT id, username, status, role FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if not user or user["status"] != "active":
            return False, "정상 상태의 로그인 계정을 찾지 못했습니다."
        if not hmac.compare_digest(str(user["username"]).casefold(), username.casefold()):
            return False, "로그인 정보가 변경되었습니다. 다시 로그인해주세요."
        if user["role"] == ROLE_ADMIN:
            return True, "이미 관리자 계정으로 설정되어 있습니다."
        conn.execute("UPDATE users SET role=? WHERE id=?", (ROLE_ADMIN, user_id))
        conn.execute(
            "INSERT INTO audit_log(actor_id, action, target, created_at) VALUES(?,?,?,?)",
            (user_id, "bootstrap_admin", str(user["username"]), utc_now()),
        )
    return True, "최초 관리자 인증이 완료되었습니다."


def set_user_role(actor_id: int, username: str, role: str) -> tuple[bool, str]:
    if role not in VALID_ROLES:
        return False, "지원하지 않는 등급입니다."
    with _connect() as conn:
        actor = conn.execute("SELECT role FROM users WHERE id=?", (actor_id,)).fetchone()
        if not actor or actor["role"] != ROLE_ADMIN:
            return False, "관리자만 등급을 변경할 수 있습니다."
        target = conn.execute(
            "SELECT id, role, status FROM users WHERE username=? COLLATE NOCASE", (username.strip(),)
        ).fetchone()
        if not target:
            return False, "해당 회원을 찾지 못했습니다."
        if target["role"] == ROLE_ADMIN and role != ROLE_ADMIN:
            active_admins = conn.execute(
                "SELECT COUNT(*) FROM users WHERE role=? AND status='active'",
                (ROLE_ADMIN,),
            ).fetchone()[0]
            if int(active_admins) <= 1:
                return False, "마지막 정상 관리자 계정은 강등할 수 없습니다."
        conn.execute("UPDATE users SET role=? WHERE id=?", (role, target["id"]))
        conn.execute(
            "INSERT INTO audit_log(actor_id, action, target, created_at) VALUES(?,?,?,?)",
            (actor_id, "set_role", f"{username}:{role}", utc_now()),
        )
    return True, f"{username} 회원의 등급을 변경했습니다."


def set_user_status(actor_id: int, username: str, status: str) -> tuple[bool, str]:
    if status not in {"active", "suspended"}:
        return False, "지원하지 않는 상태입니다."
    with _connect() as conn:
        actor = conn.execute("SELECT role FROM users WHERE id=?", (actor_id,)).fetchone()
        if not actor or actor["role"] != ROLE_ADMIN:
            return False, "관리자만 계정 상태를 변경할 수 있습니다."
        target = conn.execute(
            "SELECT id, role, status FROM users WHERE username=? COLLATE NOCASE", (username.strip(),)
        ).fetchone()
        if not target:
            return False, "해당 회원을 찾지 못했습니다."
        if target["role"] == ROLE_ADMIN and target["status"] == "active" and status == "suspended":
            active_admins = conn.execute(
                "SELECT COUNT(*) FROM users WHERE role=? AND status='active'",
                (ROLE_ADMIN,),
            ).fetchone()[0]
            if int(active_admins) <= 1:
                return False, "마지막 정상 관리자 계정은 정지할 수 없습니다."
        conn.execute("UPDATE users SET status=? WHERE id=?", (status, target["id"]))
        conn.execute(
            "INSERT INTO audit_log(actor_id, action, target, created_at) VALUES(?,?,?,?)",
            (actor_id, "set_status", f"{username}:{status}", utc_now()),
        )
    return True, f"{username} 회원의 상태를 변경했습니다."


def can_write_board(role: str) -> bool:
    return role in {ROLE_SUPPORTER, ROLE_ADMIN}


def _detect_post_image_mime(image_bytes: bytes) -> str | None:
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if (
        len(image_bytes) >= 12
        and image_bytes[:4] == b"RIFF"
        and image_bytes[8:12] == b"WEBP"
    ):
        return "image/webp"
    return None


def create_post(
    author_id: int,
    title: str,
    body: str,
    category: str = "proof",
    image_bytes: bytes | None = None,
    image_mime: str | None = None,
) -> tuple[bool, str]:
    title = title.strip()
    body = body.strip()
    if category not in {"proof", "free", "notice"}:
        category = "proof"
    if len(title) < 2 or len(title) > 80:
        return False, "제목은 2~80자로 입력해주세요."
    if len(body) < 5 or len(body) > 5000:
        return False, "내용은 5~5000자로 입력해주세요."

    stored_image: bytes | None = None
    stored_mime: str | None = None
    if image_bytes:
        if category != "proof":
            return False, "사진은 적중 인증 글에만 첨부할 수 있습니다."
        stored_image = bytes(image_bytes)
        if len(stored_image) > MAX_POST_IMAGE_BYTES:
            return False, "사진은 2MB 이하로 올려주세요."
        stored_mime = _detect_post_image_mime(stored_image)
        if stored_mime not in ALLOWED_POST_IMAGE_MIMES:
            return False, "JPG, PNG, WEBP 사진만 올릴 수 있습니다."
        if image_mime and image_mime not in ALLOWED_POST_IMAGE_MIMES:
            return False, "지원하지 않는 사진 형식입니다."

    with _connect() as conn:
        user = conn.execute(
            "SELECT role, status FROM users WHERE id=?", (author_id,)
        ).fetchone()
        if not user or user["status"] != "active" or not can_write_board(user["role"]):
            return False, "후원회원과 관리자만 글을 작성할 수 있습니다."
        if category == "notice" and user["role"] != ROLE_ADMIN:
            return False, "공지사항은 관리자만 작성할 수 있습니다."
        now = utc_now()
        conn.execute(
            """
            INSERT INTO posts(
                author_id, category, title, body, image_mime, image_data,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                author_id, category, title, body, stored_mime, stored_image,
                now, now,
            ),
        )
    return True, "게시글을 등록했습니다."


def list_posts(limit: int = 100, include_hidden: bool = False) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 200))
    status_clause = "p.status IN ('visible', 'hidden')" if include_hidden else "p.status='visible'"
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT p.id, p.author_id, p.category, p.title, p.body, p.status,
                   p.created_at, p.updated_at,
                   CASE WHEN p.image_data IS NULL THEN 0 ELSE 1 END AS has_image,
                   p.image_mime, u.username, u.display_name, u.role
            FROM posts p JOIN users u ON u.id = p.author_id
            WHERE p.category IN ('proof', 'free') AND {status_clause}
            ORDER BY p.created_at DESC LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_post_image(post_id: int, include_hidden: bool = False) -> tuple[str, bytes] | None:
    """Return one attachment without loading every board image into memory."""
    status_clause = "status IN ('visible', 'hidden')" if include_hidden else "status='visible'"
    with _connect() as conn:
        row = conn.execute(
            f"""
            SELECT image_mime, image_data FROM posts
            WHERE id=? AND {status_clause} AND image_data IS NOT NULL
            """,
            (post_id,),
        ).fetchone()
    if not row:
        return None
    image_data = bytes(row["image_data"])
    detected_mime = _detect_post_image_mime(image_data)
    if detected_mime not in ALLOWED_POST_IMAGE_MIMES:
        return None
    return detected_mime, image_data


def list_notices(include_hidden: bool = False, limit: int = 20) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 100))
    status_clause = "p.status IN ('visible', 'hidden')" if include_hidden else "p.status='visible'"
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT p.id, p.title, p.body, p.status, p.created_at, p.updated_at,
                   p.notice_mode, p.notice_audience, p.notice_start_at,
                   p.notice_end_at, p.notice_link, p.image_mime,
                   CASE WHEN p.image_data IS NULL THEN 0 ELSE 1 END AS has_image,
                   u.username, u.display_name
            FROM posts p JOIN users u ON u.id=p.author_id
            WHERE p.category='notice' AND {status_clause}
            ORDER BY p.created_at DESC LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def _valid_notice_link(link: str) -> bool:
    return not link or bool(re.fullmatch(r"https?://[^\s]+", link, flags=re.IGNORECASE))


def create_notice(
    actor_id: int,
    title: str,
    body: str,
    notice_mode: str = "banner",
    notice_audience: str = "all",
    notice_start_at: str | None = None,
    notice_end_at: str | None = None,
    notice_link: str = "",
    image_bytes: bytes | None = None,
    image_mime: str | None = None,
) -> tuple[bool, str]:
    title = title.strip()
    body = body.strip()
    notice_link = notice_link.strip()
    notice_start_at = (notice_start_at or kst_today()).strip()
    notice_end_at = (notice_end_at or "").strip() or None
    if len(title) < 2 or len(title) > 80:
        return False, "제목은 2~80자로 입력해주세요."
    if len(body) < 5 or len(body) > 5000:
        return False, "내용은 5~5000자로 입력해주세요."
    if notice_mode not in NOTICE_MODES:
        return False, "공지 표시 방식을 확인해주세요."
    if notice_audience not in NOTICE_AUDIENCES:
        return False, "공지 공개 대상을 확인해주세요."
    try:
        start_date = datetime.strptime(notice_start_at, "%Y-%m-%d").date()
        end_date = (
            datetime.strptime(notice_end_at, "%Y-%m-%d").date()
            if notice_end_at else None
        )
    except ValueError:
        return False, "공지 날짜를 확인해주세요."
    if end_date and end_date < start_date:
        return False, "종료일은 시작일보다 빠를 수 없습니다."
    if not _valid_notice_link(notice_link):
        return False, "연결 주소는 http:// 또는 https://로 시작해야 합니다."

    stored_image = bytes(image_bytes) if image_bytes else None
    stored_mime = None
    if stored_image:
        if len(stored_image) > MAX_POST_IMAGE_BYTES:
            return False, "팝업 이미지는 2MB 이하로 올려주세요."
        stored_mime = _detect_post_image_mime(stored_image)
        if stored_mime not in ALLOWED_POST_IMAGE_MIMES:
            return False, "팝업 이미지는 JPG, PNG, WEBP만 사용할 수 있습니다."
        if image_mime and image_mime not in ALLOWED_POST_IMAGE_MIMES:
            return False, "팝업 이미지 형식을 확인해주세요."

    with _connect() as conn:
        actor = conn.execute(
            "SELECT role, status FROM users WHERE id=?", (actor_id,)
        ).fetchone()
        if not actor or actor["status"] != "active" or actor["role"] != ROLE_ADMIN:
            return False, "관리자만 공지를 등록할 수 있습니다."
        now = utc_now()
        cursor = conn.execute(
            """
            INSERT INTO posts(
                author_id, category, title, body, notice_mode, notice_audience,
                notice_start_at, notice_end_at, notice_link, image_mime, image_data,
                created_at, updated_at
            ) VALUES (?, 'notice', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                actor_id, title, body, notice_mode, notice_audience,
                notice_start_at, notice_end_at, notice_link or None,
                stored_mime, stored_image, now, now,
            ),
        )
        conn.execute(
            "INSERT INTO audit_log(actor_id, action, target, created_at) VALUES(?,?,?,?)",
            (actor_id, "create_notice", str(cursor.lastrowid), now),
        )
    return True, "공지를 등록했습니다."


def list_active_notices(
    role: str,
    notice_mode: str | None = None,
    limit: int = 20,
    today: str | None = None,
) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 100))
    current_day = (today or kst_today()).strip()
    if notice_mode is not None and notice_mode not in NOTICE_MODES:
        return []
    if role not in {ROLE_GUEST, ROLE_MEMBER, ROLE_SUPPORTER, ROLE_ADMIN}:
        role = ROLE_GUEST

    audience_clause = "1=1"
    audience_params: list[Any] = []
    if role != ROLE_ADMIN:
        allowed_audiences = {
            ROLE_GUEST: ("all", "guest"),
            ROLE_MEMBER: ("all", "member"),
            ROLE_SUPPORTER: ("all", "supporter"),
        }[role]
        audience_clause = "p.notice_audience IN (?, ?)"
        audience_params.extend(allowed_audiences)

    mode_clause = ""
    mode_params: list[Any] = []
    if notice_mode:
        mode_clause = "AND p.notice_mode=?"
        mode_params.append(notice_mode)

    params = [current_day, current_day, *audience_params, *mode_params, safe_limit]
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT p.id, p.title, p.body, p.status, p.created_at, p.updated_at,
                   p.notice_mode, p.notice_audience, p.notice_start_at,
                   p.notice_end_at, p.notice_link, p.image_mime,
                   CASE WHEN p.image_data IS NULL THEN 0 ELSE 1 END AS has_image,
                   u.username, u.display_name
            FROM posts p JOIN users u ON u.id=p.author_id
            WHERE p.category='notice' AND p.status='visible'
              AND (p.notice_start_at IS NULL OR p.notice_start_at='' OR p.notice_start_at<=?)
              AND (p.notice_end_at IS NULL OR p.notice_end_at='' OR p.notice_end_at>=?)
              AND {audience_clause}
              {mode_clause}
            ORDER BY p.created_at DESC LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def update_post(
    actor_id: int, post_id: int, title: str, body: str
) -> tuple[bool, str]:
    title = title.strip()
    body = body.strip()
    if len(title) < 2 or len(title) > 80:
        return False, "제목은 2~80자로 입력해주세요."
    if len(body) < 5 or len(body) > 5000:
        return False, "내용은 5~5000자로 입력해주세요."
    with _connect() as conn:
        actor = conn.execute(
            "SELECT role, status FROM users WHERE id=?", (actor_id,)
        ).fetchone()
        post = conn.execute(
            "SELECT author_id, category, status FROM posts WHERE id=?", (post_id,)
        ).fetchone()
        if not actor or actor["status"] != "active" or not post:
            return False, "게시글을 찾지 못했습니다."
        if post["category"] == "notice" or post["status"] == "deleted":
            return False, "수정할 수 없는 게시글입니다."
        if actor["role"] != ROLE_ADMIN and int(post["author_id"]) != int(actor_id):
            return False, "수정 권한이 없습니다."
        now = utc_now()
        conn.execute(
            "UPDATE posts SET title=?, body=?, updated_at=? WHERE id=?",
            (title, body, now, post_id),
        )
        conn.execute(
            "INSERT INTO audit_log(actor_id, action, target, created_at) VALUES(?,?,?,?)",
            (actor_id, "update_post", str(post_id), now),
        )
    return True, "게시글을 수정했습니다."


def set_post_visibility(
    actor_id: int, post_id: int, visible: bool
) -> tuple[bool, str]:
    with _connect() as conn:
        actor = conn.execute(
            "SELECT role, status FROM users WHERE id=?", (actor_id,)
        ).fetchone()
        if not actor or actor["status"] != "active" or actor["role"] != ROLE_ADMIN:
            return False, "관리자만 게시글 공개 상태를 변경할 수 있습니다."
        post = conn.execute(
            "SELECT category, status FROM posts WHERE id=?", (post_id,)
        ).fetchone()
        if not post or post["category"] == "notice" or post["status"] == "deleted":
            return False, "게시글을 찾지 못했습니다."
        next_status = "visible" if visible else "hidden"
        now = utc_now()
        conn.execute(
            "UPDATE posts SET status=?, updated_at=? WHERE id=?",
            (next_status, now, post_id),
        )
        conn.execute(
            "INSERT INTO audit_log(actor_id, action, target, created_at) VALUES(?,?,?,?)",
            (actor_id, "set_post_visibility", f"{post_id}:{next_status}", now),
        )
    return True, "게시글을 공개했습니다." if visible else "게시글을 숨겼습니다."


def set_notice_visibility(
    actor_id: int, post_id: int, visible: bool
) -> tuple[bool, str]:
    with _connect() as conn:
        actor = conn.execute("SELECT role FROM users WHERE id=?", (actor_id,)).fetchone()
        if not actor or actor["role"] != ROLE_ADMIN:
            return False, "관리자만 공지 상태를 변경할 수 있습니다."
        notice = conn.execute(
            "SELECT id, status FROM posts WHERE id=? AND category='notice'",
            (post_id,),
        ).fetchone()
        if not notice or notice["status"] == "deleted":
            return False, "공지사항을 찾지 못했습니다."
        next_status = "visible" if visible else "hidden"
        conn.execute(
            "UPDATE posts SET status=?, updated_at=? WHERE id=?",
            (next_status, utc_now(), post_id),
        )
        conn.execute(
            "INSERT INTO audit_log(actor_id, action, target, created_at) VALUES(?,?,?,?)",
            (actor_id, "set_notice_visibility", f"{post_id}:{next_status}", utc_now()),
        )
    return True, "공지를 공개했습니다." if visible else "공지를 숨겼습니다."


def delete_post(actor_id: int, post_id: int) -> tuple[bool, str]:
    with _connect() as conn:
        actor = conn.execute("SELECT role FROM users WHERE id=?", (actor_id,)).fetchone()
        post = conn.execute("SELECT author_id FROM posts WHERE id=?", (post_id,)).fetchone()
        if not actor or not post:
            return False, "게시글을 찾지 못했습니다."
        if actor["role"] != ROLE_ADMIN and post["author_id"] != actor_id:
            return False, "삭제 권한이 없습니다."
        conn.execute(
            "UPDATE posts SET status='deleted', updated_at=? WHERE id=?",
            (utc_now(), post_id),
        )
    return True, "게시글을 삭제했습니다."


def request_supporter(
    user_id: int, depositor_name: str, note: str = ""
) -> tuple[bool, str]:
    depositor_name = depositor_name.strip()
    if len(depositor_name) < 2 or len(depositor_name) > 30:
        return False, "입금자명을 확인해주세요."
    with _connect() as conn:
        existing = conn.execute(
            """
            SELECT id FROM support_requests
            WHERE user_id=? AND status='pending'
            """,
            (user_id,),
        ).fetchone()
        if existing:
            return False, "이미 확인 대기 중인 요청이 있습니다."
        conn.execute(
            """
            INSERT INTO support_requests(user_id, depositor_name, note, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, depositor_name, note.strip()[:500], utc_now()),
        )
    return True, "확인 요청이 접수되었습니다."


def list_support_requests() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT r.id, r.depositor_name, r.note, r.status, r.created_at,
                   u.username, u.display_name
            FROM support_requests r JOIN users u ON u.id=r.user_id
            ORDER BY CASE r.status WHEN 'pending' THEN 0 ELSE 1 END, r.created_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def review_support_request(
    actor_id: int, request_id: int, decision: str
) -> tuple[bool, str]:
    """Approve or reject one supporter request and keep an audit record."""
    if decision not in {"approved", "rejected"}:
        return False, "지원하지 않는 처리 방식입니다."
    with _connect() as conn:
        actor = conn.execute(
            "SELECT role, status FROM users WHERE id=?", (actor_id,)
        ).fetchone()
        if not actor or actor["role"] != ROLE_ADMIN or actor["status"] != "active":
            return False, "관리자만 후원 확인 요청을 처리할 수 있습니다."
        request = conn.execute(
            "SELECT id, user_id, status FROM support_requests WHERE id=?", (request_id,)
        ).fetchone()
        if not request:
            return False, "확인 요청을 찾지 못했습니다."
        if request["status"] != "pending":
            return False, "이미 처리된 요청입니다."

        now = utc_now()
        conn.execute(
            """
            UPDATE support_requests
            SET status=?, reviewed_at=?, reviewed_by=?
            WHERE id=?
            """,
            (decision, now, actor_id, request_id),
        )
        if decision == "approved":
            conn.execute(
                """
                UPDATE users SET role=?
                WHERE id=? AND role!=?
                """,
                (ROLE_SUPPORTER, request["user_id"], ROLE_ADMIN),
            )
        conn.execute(
            "INSERT INTO audit_log(actor_id, action, target, created_at) VALUES(?,?,?,?)",
            (actor_id, f"support_request_{decision}", str(request_id), now),
        )
    action_label = "승인" if decision == "approved" else "거절"
    return True, f"후원회원 전환 요청을 {action_label}했습니다."


init_member_db()
