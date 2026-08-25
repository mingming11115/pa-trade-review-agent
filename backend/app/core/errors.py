from __future__ import annotations

import re
from typing import Any


SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization:\s*basic\s+)[^\s]+"),
    re.compile(r"(?i)(api[_-]?key[=:]\s*)[^\s,;]+"),
)


def redact_text(value: str, secrets: tuple[str, ...] = ()) -> str:
    """脱敏处理：替换已知密钥和匹配通用模式的敏感信息为 [REDACTED]。"""
    redacted = value
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(r"\1[REDACTED]", redacted)
    return redacted


class AppError(Exception):
    """应用级业务异常，携带错误码、消息、HTTP 状态码和详细明细。"""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or []


class ProviderError(AppError):
    """上游数据提供者错误。"""
    pass
