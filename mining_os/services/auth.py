"""Account/workspace auth helpers for Mining OS."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from mining_os.config import settings
from mining_os.db import get_engine

_PASSWORD_PREFIX = "scrypt"
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 64


@dataclass(frozen=True)
class AuthContext:
    user_id: int
    email: str
    username: str
    display_name: str | None
    is_system_admin: bool
    active_account_id: int
    active_account_name: str
    session_id: int


_AUTH_CONTEXT: ContextVar[AuthContext | None] = ContextVar("mining_os_auth_context", default=None)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _normalize_username(username: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]", "", (username or "").strip().lower())
    return cleaned


def _hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    if not password or len(password) < 8:
        raise ValueError("Password must be at least 8 characters")
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    salt_b64 = base64.b64encode(salt).decode("ascii")
    digest_b64 = base64.b64encode(digest).decode("ascii")
    return f"{_PASSWORD_PREFIX}${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt_b64}${digest_b64}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algo, n, r, p, salt_b64, digest_b64 = stored_hash.split("$", 5)
    except ValueError:
        return False
    if algo != _PASSWORD_PREFIX:
        return False
    try:
        salt = base64.b64decode(salt_b64.encode("ascii"))
        expected = base64.b64decode(digest_b64.encode("ascii"))
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except Exception:
        return False
    return hmac.compare_digest(candidate, expected)


def set_auth_context(ctx: AuthContext | None) -> Token:
    return _AUTH_CONTEXT.set(ctx)


def reset_auth_context(token: Token) -> None:
    _AUTH_CONTEXT.reset(token)


def get_auth_context() -> AuthContext | None:
    return _AUTH_CONTEXT.get()


def require_auth_context() -> AuthContext:
    ctx = get_auth_context()
    if ctx is None:
        raise PermissionError("Authentication required")
    return ctx


def current_account_id() -> int:
    return require_auth_context().active_account_id


def has_any_users() -> bool:
    eng = get_engine()
    with eng.begin() as conn:
        count = conn.execute(text("SELECT COUNT(*)::int FROM users")).scalar() or 0
    return bool(count)


def needs_bootstrap() -> bool:
    return not has_any_users()


def _craig_account_id(conn) -> int:
    account_id = conn.execute(
        text("SELECT id FROM accounts WHERE lower(name) = 'craig' LIMIT 1")
    ).scalar()
    if account_id:
        return int(account_id)
    row = conn.execute(
        text("INSERT INTO accounts (name) VALUES ('Craig') RETURNING id")
    ).first()
    return int(row[0])


def _seed_account_defaults(conn, account_id: int) -> None:
    craig_account_id = _craig_account_id(conn)
    conn.execute(
        text(
            """
            INSERT INTO minerals_of_interest (account_id, name, sort_order)
            SELECT :account_id, m.name, m.sort_order
            FROM minerals_of_interest m
            WHERE m.account_id = :craig_account_id
            ON CONFLICT (account_id, name) DO UPDATE
            SET sort_order = EXCLUDED.sort_order,
                updated_at = now()
            """
        ),
        {"account_id": account_id, "craig_account_id": craig_account_id},
    )
    conn.execute(
        text(
            """
            INSERT INTO discovery_prompts (account_id, mineral_name, system_instruction, user_prompt_template)
            SELECT :account_id, dp.mineral_name, dp.system_instruction, dp.user_prompt_template
            FROM discovery_prompts dp
            WHERE dp.account_id = :craig_account_id
            ON CONFLICT (account_id, mineral_name) DO UPDATE
            SET system_instruction = EXCLUDED.system_instruction,
                user_prompt_template = EXCLUDED.user_prompt_template,
                updated_at = now()
            """
        ),
        {"account_id": account_id, "craig_account_id": craig_account_id},
    )


def _build_context(conn, *, session_id: int, user_id: int, active_account_id: int) -> AuthContext | None:
    row = conn.execute(
        text(
            """
            SELECT
              u.id AS user_id,
              u.email,
              u.username,
              u.display_name,
              u.is_system_admin,
              s.id AS session_id,
              s.active_account_id,
              a.name AS active_account_name
            FROM user_sessions s
            JOIN users u ON u.id = s.user_id
            JOIN accounts a ON a.id = s.active_account_id
            WHERE s.id = :session_id
              AND u.id = :user_id
              AND s.active_account_id = :active_account_id
              AND u.is_active = true
            """
        ),
        {
            "session_id": session_id,
            "user_id": user_id,
            "active_account_id": active_account_id,
        },
    ).mappings().first()
    if not row:
        return None
    return AuthContext(
        user_id=int(row["user_id"]),
        email=row["email"],
        username=row["username"],
        display_name=row.get("display_name"),
        is_system_admin=bool(row["is_system_admin"]),
        active_account_id=int(row["active_account_id"]),
        active_account_name=row["active_account_name"],
        session_id=int(row["session_id"]),
    )


def _session_memberships(conn, user_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            """
            SELECT am.account_id, a.name AS account_name, am.role
            FROM account_memberships am
            JOIN accounts a ON a.id = am.account_id
            WHERE am.user_id = :user_id
            ORDER BY a.name
            """
        ),
        {"user_id": user_id},
    ).mappings().all()
    return [
        {
            "account_id": int(r["account_id"]),
            "account_name": r["account_name"],
            "role": r["role"],
        }
        for r in rows
    ]


def _create_session(
    conn,
    *,
    user_id: int,
    active_account_id: int,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> tuple[str, AuthContext]:
    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_session_token(raw_token)
    expires_at = _utcnow() + timedelta(days=max(1, settings.SESSION_TTL_DAYS))
    row = conn.execute(
        text(
            """
            INSERT INTO user_sessions
              (user_id, active_account_id, session_token_hash, expires_at, user_agent, ip_address)
            VALUES
              (:user_id, :active_account_id, :token_hash, :expires_at, :user_agent, :ip_address)
            RETURNING id
            """
        ),
        {
            "user_id": user_id,
            "active_account_id": active_account_id,
            "token_hash": token_hash,
            "expires_at": expires_at,
            "user_agent": user_agent,
            "ip_address": ip_address,
        },
    ).first()
    session_id = int(row[0])
    ctx = _build_context(conn, session_id=session_id, user_id=user_id, active_account_id=active_account_id)
    if ctx is None:
        raise RuntimeError("Failed to build auth context")
    return raw_token, ctx


def bootstrap_first_admin(
    *,
    email: str,
    username: str,
    password: str,
    display_name: str | None = None,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> tuple[str, dict[str, Any]]:
    if has_any_users():
        raise ValueError("Bootstrap is already complete")
    email_norm = _normalize_email(email)
    username_norm = _normalize_username(username)
    if not email_norm:
        raise ValueError("Email is required")
    if not username_norm:
        raise ValueError("Username is required")
    password_hash = hash_password(password)
    eng = get_engine()
    with eng.begin() as conn:
        craig_account_id = _craig_account_id(conn)
        user_row = conn.execute(
            text(
                """
                INSERT INTO users (email, username, display_name, password_hash, is_system_admin)
                VALUES (:email, :username, :display_name, :password_hash, true)
                RETURNING id
                """
            ),
            {
                "email": email_norm,
                "username": username_norm,
                "display_name": (display_name or "").strip() or None,
                "password_hash": password_hash,
            },
        ).first()
        user_id = int(user_row[0])
        conn.execute(
            text(
                """
                INSERT INTO account_memberships (user_id, account_id, role)
                VALUES (:user_id, :account_id, 'owner')
                ON CONFLICT (user_id, account_id) DO NOTHING
                """
            ),
            {"user_id": user_id, "account_id": craig_account_id},
        )
        token, ctx = _create_session(
            conn,
            user_id=user_id,
            active_account_id=craig_account_id,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        return token, me_payload_for_context(conn, ctx)


def login(
    *,
    identifier: str,
    password: str,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> tuple[str, dict[str, Any]]:
    ident = (identifier or "").strip().lower()
    eng = get_engine()
    with eng.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT id, email, username, display_name, password_hash, is_active
                FROM users
                WHERE lower(email) = :ident OR lower(username) = :ident
                LIMIT 1
                """
            ),
            {"ident": ident},
        ).mappings().first()
        if not row or not row["is_active"] or not verify_password(password, row["password_hash"]):
            raise ValueError("Invalid username/email or password")
        membership = conn.execute(
            text(
                """
                SELECT am.account_id
                FROM account_memberships am
                WHERE am.user_id = :user_id
                ORDER BY am.id
                LIMIT 1
                """
            ),
            {"user_id": int(row["id"])},
        ).first()
        if not membership:
            raise ValueError("This user is not assigned to any account")
        account_id = int(membership[0])
        token, ctx = _create_session(
            conn,
            user_id=int(row["id"]),
            active_account_id=account_id,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        return token, me_payload_for_context(conn, ctx)


def resolve_session(session_token: str | None) -> AuthContext | None:
    if not session_token:
        return None
    eng = get_engine()
    with eng.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT s.id AS session_id, s.user_id, s.active_account_id
                FROM user_sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.session_token_hash = :token_hash
                  AND s.expires_at > now()
                  AND u.is_active = true
                LIMIT 1
                """
            ),
            {"token_hash": _hash_session_token(session_token)},
        ).mappings().first()
        if not row:
            return None
        conn.execute(
            text("UPDATE user_sessions SET last_seen_at = now() WHERE id = :id"),
            {"id": int(row["session_id"])},
        )
        return _build_context(
            conn,
            session_id=int(row["session_id"]),
            user_id=int(row["user_id"]),
            active_account_id=int(row["active_account_id"]),
        )


def logout(session_token: str | None) -> None:
    if not session_token:
        return
    eng = get_engine()
    with eng.begin() as conn:
        conn.execute(
            text("DELETE FROM user_sessions WHERE session_token_hash = :token_hash"),
            {"token_hash": _hash_session_token(session_token)},
        )


def me_payload_for_context(conn, ctx: AuthContext) -> dict[str, Any]:
    memberships = _session_memberships(conn, ctx.user_id)
    return {
        "user": {
            "id": ctx.user_id,
            "email": ctx.email,
            "username": ctx.username,
            "display_name": ctx.display_name,
            "is_system_admin": ctx.is_system_admin,
        },
        "active_account": {
            "id": ctx.active_account_id,
            "name": ctx.active_account_name,
        },
        "memberships": memberships,
    }


def me_payload(session_token: str | None) -> dict[str, Any] | None:
    ctx = resolve_session(session_token)
    if ctx is None:
        return None
    eng = get_engine()
    with eng.begin() as conn:
        return me_payload_for_context(conn, ctx)


def switch_account(session_token: str, account_id: int) -> dict[str, Any]:
    ctx = resolve_session(session_token)
    if ctx is None:
        raise ValueError("Authentication required")
    eng = get_engine()
    with eng.begin() as conn:
        membership = conn.execute(
            text(
                """
                SELECT 1
                FROM account_memberships
                WHERE user_id = :user_id AND account_id = :account_id
                LIMIT 1
                """
            ),
            {"user_id": ctx.user_id, "account_id": account_id},
        ).first()
        if not membership:
            raise ValueError("You do not have access to that account")
        conn.execute(
            text(
                """
                UPDATE user_sessions
                SET active_account_id = :account_id, last_seen_at = now()
                WHERE id = :session_id
                """
            ),
            {"account_id": account_id, "session_id": ctx.session_id},
        )
        new_ctx = _build_context(conn, session_id=ctx.session_id, user_id=ctx.user_id, active_account_id=account_id)
        if new_ctx is None:
            raise RuntimeError("Failed to switch accounts")
        return me_payload_for_context(conn, new_ctx)


def list_accounts_for_admin() -> list[dict[str, Any]]:
    eng = get_engine()
    with eng.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                  a.id,
                  a.name,
                  a.created_at,
                  COUNT(DISTINCT am.user_id)::int AS member_count,
                  COUNT(DISTINCT af.id)::int AS target_count
                FROM accounts a
                LEFT JOIN account_memberships am ON am.account_id = a.id
                LEFT JOIN areas_of_focus af ON af.account_id = a.id
                GROUP BY a.id, a.name, a.created_at
                ORDER BY a.name
                """
            )
        ).mappings().all()
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "id": int(row["id"]),
                "name": row["name"],
                "created_at": row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else row["created_at"],
                "member_count": int(row["member_count"] or 0),
                "target_count": int(row["target_count"] or 0),
            }
        )
    return out


def create_account_with_admin(
    *,
    requester_user_id: int,
    account_name: str,
    admin_email: str,
    admin_username: str,
    admin_password: str,
    admin_display_name: str | None = None,
) -> dict[str, Any]:
    acct_name = (account_name or "").strip()
    if not acct_name:
        raise ValueError("Account name is required")
    email_norm = _normalize_email(admin_email)
    username_norm = _normalize_username(admin_username)
    if not email_norm:
        raise ValueError("Admin email is required")
    if not username_norm:
        raise ValueError("Admin username is required")
    pw_hash = hash_password(admin_password)
    eng = get_engine()
    with eng.begin() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM accounts WHERE lower(name) = lower(:name)"),
            {"name": acct_name},
        ).first()
        if exists:
            raise ValueError("An account with that name already exists")
        email_exists = conn.execute(
            text("SELECT 1 FROM users WHERE lower(email) = :email"),
            {"email": email_norm},
        ).first()
        if email_exists:
            raise ValueError("That email is already in use")
        username_exists = conn.execute(
            text("SELECT 1 FROM users WHERE lower(username) = :username"),
            {"username": username_norm},
        ).first()
        if username_exists:
            raise ValueError("That username is already in use")

        account_row = conn.execute(
            text("INSERT INTO accounts (name) VALUES (:name) RETURNING id, name, created_at"),
            {"name": acct_name},
        ).mappings().first()
        account_id = int(account_row["id"])
        _seed_account_defaults(conn, account_id)
        user_row = conn.execute(
            text(
                """
                INSERT INTO users (email, username, display_name, password_hash, is_system_admin)
                VALUES (:email, :username, :display_name, :password_hash, false)
                RETURNING id, email, username, display_name, created_at
                """
            ),
            {
                "email": email_norm,
                "username": username_norm,
                "display_name": (admin_display_name or "").strip() or None,
                "password_hash": pw_hash,
            },
        ).mappings().first()
        admin_user_id = int(user_row["id"])

        conn.execute(
            text(
                """
                INSERT INTO account_memberships (user_id, account_id, role)
                VALUES (:user_id, :account_id, 'owner')
                """
            ),
            {"user_id": admin_user_id, "account_id": account_id},
        )
        conn.execute(
            text(
                """
                INSERT INTO account_memberships (user_id, account_id, role)
                VALUES (:user_id, :account_id, 'admin')
                ON CONFLICT (user_id, account_id) DO NOTHING
                """
            ),
            {"user_id": requester_user_id, "account_id": account_id},
        )

        return {
            "account": {
                "id": account_id,
                "name": account_row["name"],
                "created_at": account_row["created_at"].isoformat() if hasattr(account_row["created_at"], "isoformat") else account_row["created_at"],
            },
            "admin_user": {
                "id": admin_user_id,
                "email": user_row["email"],
                "username": user_row["username"],
                "display_name": user_row.get("display_name"),
                "created_at": user_row["created_at"].isoformat() if hasattr(user_row["created_at"], "isoformat") else user_row["created_at"],
            },
        }
