from __future__ import annotations

import hashlib
import secrets
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import Cookie, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, JSON, String, Text, func, select
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import Settings, get_settings
from app.core.database import Base, SessionFactory, ensure_schema
from app.core.errors import AppError
from app.personal.service import set_settings_scope


UTC = timezone.utc
SESSION_COOKIE = "pa_session"


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(20), default="user")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class UserSession(Base):
    __tablename__ = "user_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(120), index=True)
    target: Mapped[str] = mapped_column(String(300), default="")
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)


class UserPublic(BaseModel):
    id: uuid.UUID | None
    username: str
    role: Literal["user", "admin"]
    auth_required: bool


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=300)


def hash_password(password: str, salt: bytes | None = None) -> str:
    """使用 scrypt 算法对密码进行哈希，返回 'scrypt$salt$digest' 格式的字符串。"""
    salt = salt or secrets.token_bytes(16)  # 生成随机盐
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """验证密码是否与已编码的哈希匹配，使用恒定时间比较防止时序攻击。"""
    try:
        _, salt_hex, expected = encoded.split("$", 2)
        actual = hash_password(password, bytes.fromhex(salt_hex)).split("$", 2)[2]
        return secrets.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    """对会话 token 进行 SHA-256 哈希，用于数据库索引比对。"""
    return hashlib.sha256(token.encode()).hexdigest()


async def bootstrap_admin(settings: Settings | None = None) -> None:
    """若数据库中无用户，则根据配置创建初始管理员账号。"""
    config = settings or get_settings()
    if not config.admin_username or not config.admin_password:
        return
    await ensure_schema()
    async with SessionFactory() as session:
        exists = await session.scalar(select(func.count()).select_from(User))
        if not exists:
            session.add(User(username=config.admin_username, password_hash=hash_password(config.admin_password), role="admin"))
            await session.commit()


async def login(payload: LoginInput, response: Response) -> UserPublic:
    """验证用户凭据，创建会话并设置 Cookie，记录审计日志。"""
    await ensure_schema()
    async with SessionFactory() as session:
        user = await session.scalar(select(User).where(User.username == payload.username))
        if user is None or not user.active or not verify_password(payload.password, user.password_hash):
            raise AppError("invalid_credentials", "用户名或密码错误", 401)
        token = secrets.token_urlsafe(36)
        session.add(UserSession(token_hash=_token_hash(token), user_id=user.id, expires_at=datetime.now(UTC) + timedelta(hours=get_settings().session_hours)))
        session.add(AuditEvent(user_id=user.id, action="login", target=user.username))
        await session.commit()
    response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=get_settings().app_env == "production", samesite="lax", max_age=get_settings().session_hours * 3600)
    return UserPublic(id=user.id, username=user.username, role=user.role, auth_required=get_settings().auth_required)


async def current_user(pa_session: str | None = Cookie(None, alias=SESSION_COOKIE)) -> UserPublic:
    """从会话 Cookie 解析当前登录用户；未启用认证时返回本地管理员。"""
    settings = get_settings()
    if not settings.auth_required and not pa_session:
        set_settings_scope("local")
        return UserPublic(id=None, username="local", role="admin", auth_required=False)
    if not pa_session:
        raise AppError("authentication_required", "请先登录", 401)
    await ensure_schema()
    async with SessionFactory() as session:
        row = await session.get(UserSession, _token_hash(pa_session))
        user = await session.get(User, row.user_id) if row and row.expires_at > datetime.now(UTC) else None
    if user is None or not user.active:
        raise AppError("session_expired", "登录已过期，请重新登录", 401)
    set_settings_scope(user.username)
    return UserPublic(id=user.id, username=user.username, role=user.role, auth_required=settings.auth_required)


async def require_admin(user: UserPublic = Depends(current_user)) -> UserPublic:
    """依赖注入：要求当前用户为管理员，否则抛出 403。"""
    if user.role != "admin":
        raise AppError("admin_required", "需要管理员权限", 403)
    return user


async def logout(response: Response, pa_session: str | None = Cookie(None, alias=SESSION_COOKIE)) -> None:
    """删除会话记录并清除 Cookie。"""
    if pa_session:
        await ensure_schema()
        async with SessionFactory() as session:
            row = await session.get(UserSession, _token_hash(pa_session))
            if row: await session.delete(row); await session.commit()
    response.delete_cookie(SESSION_COOKIE)


async def audit(user: UserPublic, action: str, target: str, detail: dict[str, Any] | None = None) -> None:
    """记录审计事件到数据库，失败时静默忽略。"""
    try:
        await ensure_schema()
        async with SessionFactory() as session:
            session.add(AuditEvent(user_id=user.id, action=action, target=target, detail=detail)); await session.commit()
    except Exception:
        pass


class SlidingWindowLimiter:
    """滑动窗口限流器：基于 deque 记录请求时间戳，超出窗口或限额时拒绝。"""

    def __init__(self): self.events: dict[str, deque[float]] = defaultdict(deque)
    def check(self, key: str, limit: int, window_seconds: int, now: float) -> None:
        """检查指定 key 在窗口内的请求次数是否超限，超限则抛出 429。"""
        queue = self.events[key]
        while queue and queue[0] <= now - window_seconds: queue.popleft()
        if len(queue) >= limit: raise AppError("rate_limited", "请求过于频繁，请稍后重试", 429, [{"retry_after_seconds": max(1, int(window_seconds - (now - queue[0])))}])
        queue.append(now)


limiter = SlidingWindowLimiter()


async def limit_expensive(request: Request, user: UserPublic = Depends(current_user)) -> UserPublic:
    """依赖注入：对高开销接口进行滑动窗口限流。"""
    limiter.check(f"{user.username}:{request.url.path}", get_settings().expensive_rate_limit, 60, datetime.now(UTC).timestamp())
    return user
