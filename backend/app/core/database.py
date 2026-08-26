from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类，所有 ORM 模型的父类。"""
    pass


engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """依赖注入：获取一个异步数据库会话。"""
    async with SessionFactory() as session:
        yield session


async def ensure_schema() -> None:
    """确保数据库 schema 已创建，并执行幂等的增量迁移语句。"""
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        if connection.dialect.name == "postgresql":
            # create_all intentionally does not mutate existing tables. Keep the
            # small demo migration additive and idempotent until Alembic lands.
            for statement in (
                "ALTER TABLE trades ADD COLUMN IF NOT EXISTS slippage NUMERIC(20,8)",
                "ALTER TABLE trades ADD COLUMN IF NOT EXISTS strategy VARCHAR(120)",
                "ALTER TABLE trades ADD COLUMN IF NOT EXISTS account VARCHAR(120)",
                "ALTER TABLE trades ADD COLUMN IF NOT EXISTS tags JSONB NOT NULL DEFAULT '[]'::jsonb",
                "ALTER TABLE trades ADD COLUMN IF NOT EXISTS notes TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE trades ADD COLUMN IF NOT EXISTS attachments JSONB NOT NULL DEFAULT '[]'::jsonb",
                "ALTER TABLE analysis_runs ADD COLUMN IF NOT EXISTS reasoning_content TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE analysis_tasks ADD COLUMN IF NOT EXISTS analysis_symbol VARCHAR(100)",
                "ALTER TABLE analysis_tasks ADD COLUMN IF NOT EXISTS analysis_period VARCHAR(20)",
                """
                UPDATE analysis_tasks
                SET analysis_symbol = UPPER(BTRIM(config_json ->> 'symbol')),
                    analysis_period = config_json ->> 'period'
                WHERE kind = 'analysis'
                  AND (analysis_symbol IS NULL OR analysis_period IS NULL)
                """,
                "DROP TABLE IF EXISTS analysis_history",
            ):
                await connection.execute(text(statement))
        elif connection.dialect.name == "sqlite":
            existing_task_cols = {
                row[1] for row in (await connection.execute(text("PRAGMA table_info(analysis_tasks)"))).fetchall()
            } if (await connection.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='analysis_tasks'"))).first() else set()
            if existing_task_cols:
                if "analysis_symbol" not in existing_task_cols:
                    await connection.execute(text("ALTER TABLE analysis_tasks ADD COLUMN analysis_symbol VARCHAR(100)"))
                if "analysis_period" not in existing_task_cols:
                    await connection.execute(text("ALTER TABLE analysis_tasks ADD COLUMN analysis_period VARCHAR(20)"))
                await connection.execute(text("""
                    UPDATE analysis_tasks
                    SET analysis_symbol = UPPER(TRIM(json_extract(config_json, '$.symbol'))),
                        analysis_period = json_extract(config_json, '$.period')
                    WHERE kind = 'analysis'
                      AND (analysis_symbol IS NULL OR analysis_period IS NULL)
                """))
            await connection.execute(text("DROP TABLE IF EXISTS analysis_history"))
