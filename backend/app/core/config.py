from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[3]
load_dotenv(ROOT_DIR / ".env")
KLINE_SYMBOLS = ("ES", "NQ")


def _as_bool(value: str | None) -> bool:
    """将字符串安全转换为布尔值（'1'/'true'/'yes'/'on' 为真）。"""
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_env: str
    hist_base_url: str
    hist_api_key: str
    hist_allow_insecure_http: bool
    hist_timeout_seconds: float
    hist_max_retries: int
    frontend_origin: str
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/pa"
    collector_enabled: bool = True
    collector_symbols: tuple[str, ...] = KLINE_SYMBOLS
    collector_lookback_minutes: int = 30
    collector_max_catchup_minutes: int = 360
    live_ws_enabled: bool = False
    live_ws_url: str = "wss://dbws.massiveprivateserver.site/live"
    live_ws_symbols: tuple[str, ...] = ("ES.c.0", "NQ.c.0")
    auth_required: bool = False
    admin_username: str = ""
    admin_password: str = ""
    session_hours: int = 24
    expensive_rate_limit: int = 20
    log_level: str = "INFO"
    log_file_path: str = str(ROOT_DIR / "logs" / "app.log")
    langfuse_host: str = "http://localhost:3000"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    @property
    def langfuse_enabled(self) -> bool:
        """是否已配置 Langfuse 密钥（同时有 public + secret key 才算启用）。"""
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

    @property
    def provider_configured(self) -> bool:
        """是否已配置上游数据 API Key。"""
        return bool(self.hist_api_key)

    @property
    def insecure_http_allowed(self) -> bool:
        """是否允许非加密 HTTP（仅开发环境且显式开启时为真）。"""
        return self.app_env == "development" and self.hist_allow_insecure_http


@lru_cache
def get_settings() -> Settings:
    """从环境变量读取并构建 Settings 实例（带 LRU 缓存）。"""
    return Settings(
        app_env=os.getenv("APP_ENV", "development"),
        hist_base_url=os.getenv(
            "HIST_BASE_URL", "https://hist.massiveprivateserver.site"
        ).rstrip("/"),
        hist_api_key=os.getenv("HIST_API_KEY", ""),
        hist_allow_insecure_http=_as_bool(
            os.getenv("HIST_ALLOW_INSECURE_HTTP", "false")
        ),
        hist_timeout_seconds=float(os.getenv("HIST_TIMEOUT_SECONDS", "60")),
        hist_max_retries=max(0, int(os.getenv("HIST_MAX_RETRIES", "2"))),
        frontend_origin=os.getenv("FRONTEND_ORIGIN", "http://localhost:5173"),
        database_url=os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres@localhost:5432/pa",
        ),
        # 默认开启分钟采集；本地/生产都持续回补已收盘 1m。设 COLLECTOR_ENABLED=false 可关。
        collector_enabled=_as_bool(os.getenv("COLLECTOR_ENABLED", "true")),
        collector_symbols=tuple(
            symbol
            for item in os.getenv("COLLECTOR_SYMBOLS", "ES,NQ").split(",")
            if (symbol := item.strip().upper()) in KLINE_SYMBOLS
        ),
        collector_lookback_minutes=max(2, int(os.getenv("COLLECTOR_LOOKBACK_MINUTES", "30"))),
        collector_max_catchup_minutes=max(
            30, int(os.getenv("COLLECTOR_MAX_CATCHUP_MINUTES", "360"))
        ),
        live_ws_enabled=_as_bool(os.getenv("LIVE_WS_ENABLED", "false")),
        live_ws_url=os.getenv(
            "LIVE_WS_URL", "wss://dbws.massiveprivateserver.site/live"
        ).strip(),
        live_ws_symbols=tuple(
            item.strip()
            for item in os.getenv("LIVE_WS_SYMBOLS", "ES.c.0,NQ.c.0").split(",")
            if item.strip()
        ),
        auth_required=_as_bool(os.getenv("AUTH_REQUIRED", "false")),
        admin_username=os.getenv("ADMIN_USERNAME", ""),
        admin_password=os.getenv("ADMIN_PASSWORD", ""),
        session_hours=max(1, int(os.getenv("SESSION_HOURS", "24"))),
        expensive_rate_limit=max(1, int(os.getenv("EXPENSIVE_RATE_LIMIT", "20"))),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
        log_file_path=os.getenv("LOG_FILE_PATH", str(ROOT_DIR / "logs" / "app.log")).strip(),
        langfuse_host=os.getenv("LANGFUSE_HOST", "http://localhost:3000").strip().rstrip("/"),
        langfuse_public_key=os.getenv("LANGFUSE_PUBLIC_KEY", "").strip(),
        langfuse_secret_key=os.getenv("LANGFUSE_SECRET_KEY", "").strip(),
    )
