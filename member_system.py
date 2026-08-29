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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_db_path() -> Path:
    configured = os.getenv("DJ_MEMBER_DB_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    # 기존 배포판의 users.db를 그대로 사용해 기존 회원을 자동 이전한다.
    return Path(__file__).resolve().with_name("users.db")


def _connect() -> sqlite3.Connection:
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


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
            """
        )
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


def list_users() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, username, display_name, role, status, created_at, last_login_at
            FROM users ORDER BY created_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def set_user_role(actor_id: int, username: str, role: str) -> tuple[bool, str]:
    if role not in VALID_ROLES:
        return False, "지원하지 않는 등급입니다."
    with _connect() as conn:
        actor = conn.execute("SELECT role FROM users WHERE id=?", (actor_id,)).fetchone()
        if not actor or actor["role"] != ROLE_ADMIN:
            return False, "관리자만 등급을 변경할 수 있습니다."
        target = conn.execute(
            "SELECT id FROM users WHERE username=? COLLATE NOCASE", (username.strip(),)
        ).fetchone()
        if not target:
            return False, "해당 회원을 찾지 못했습니다."
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
            "SELECT id FROM users WHERE username=? COLLATE NOCASE", (username.strip(),)
        ).fetchone()
        if not target:
            return False, "해당 회원을 찾지 못했습니다."
        conn.execute("UPDATE users SET status=? WHERE id=?", (status, target["id"]))
        conn.execute(
            "INSERT INTO audit_log(actor_id, action, target, created_at) VALUES(?,?,?,?)",
            (actor_id, "set_status", f"{username}:{status}", utc_now()),
        )
    return True, f"{username} 회원의 상태를 변경했습니다."


def can_write_board(role: str) -> bool:
    return role in {ROLE_SUPPORTER, ROLE_ADMIN}


def create_post(
    author_id: int, title: str, body: str, category: str = "proof"
) -> tuple[bool, str]:
    title = title.strip()
    body = body.strip()
    if category not in {"proof", "free", "notice"}:
        category = "proof"
    if len(title) < 2 or len(title) > 80:
        return False, "제목은 2~80자로 입력해주세요."
    if len(body) < 5 or len(body) > 5000:
        return False, "내용은 5~5000자로 입력해주세요."

    with _connect() as conn:
        user = conn.execute(
            "SELECT role, status FROM users WHERE id=?", (author_id,)
        ).fetchone()
        if not user or user["status"] != "active" or not can_write_board(user["role"]):
            return False, "후원회원과 관리자만 글을 작성할 수 있습니다."
        now = utc_now()
        conn.execute(
            """
            INSERT INTO posts(author_id, category, title, body, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (author_id, category, title, body, now, now),
        )
    return True, "게시글을 등록했습니다."


def list_posts(limit: int = 100) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 200))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT p.id, p.category, p.title, p.body, p.status,
                   p.created_at, p.updated_at, u.username, u.display_name, u.role
            FROM posts p JOIN users u ON u.id = p.author_id
            WHERE p.status = 'visible'
            ORDER BY p.created_at DESC LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    return [dict(row) for row in rows]


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


init_member_db()
