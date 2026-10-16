from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类。"""


engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """依赖注入：获取一个异步数据库会话。"""
    async with SessionFactory() as session:
        yield session


async def ensure_schema() -> None:
    """幂等创建当前 ORM Schema，不执行迁移或删除。"""
    async with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            from app.market.archive import ensure_archive_schema
            from sqlalchemy import text
            await connection.execute(text("SELECT pg_advisory_xact_lock(724019221)"))
            await ensure_archive_schema(connection)
        await connection.run_sync(Base.metadata.create_all)
        if connection.dialect.name == "postgresql" and "market_collection_states" in Base.metadata.tables:
            await connection.execute(text("ALTER TABLE public.market_collection_states ADD COLUMN IF NOT EXISTS covered_until timestamptz"))
