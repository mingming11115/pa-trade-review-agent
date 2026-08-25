"""Langfuse tracing integration.

Wraps the Langfuse v4 Python SDK behind the simpler v2-style API used by
``client.py``: ``start_trace`` returns a trace wrapper, each wrapper exposes
a ``.generation(...)`` method, and each generation exposes ``.end(...)``.

When ``LANGFUSE_PUBLIC_KEY`` and ``LANGFUSE_SECRET_KEY`` are both set in the
environment, tracing is enabled. Otherwise all calls become no-ops, so
business logic is unaffected when Langfuse is not configured.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.core.config import get_settings

if TYPE_CHECKING:
    from langfuse import Langfuse

logger = logging.getLogger(__name__)

_client: Langfuse | None = None
_initialized = False


def _get_client() -> Langfuse | None:
    global _client, _initialized
    if _initialized:
        return _client
    _initialized = True
    settings = get_settings()
    if not settings.langfuse_enabled:
        logger.debug("Langfuse tracing disabled (credentials not configured)")
        return None
    try:
        from langfuse import Langfuse

        _client = Langfuse(
            host=settings.langfuse_host,
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
        )
        logger.info("Langfuse tracing enabled (host=%s)", settings.langfuse_host)
    except Exception:
        logger.warning("Langfuse init failed, tracing disabled", exc_info=True)
        _client = None
    return _client


def is_enabled() -> bool:
    return _get_client() is not None


class _GenerationWrapper:
    __slots__ = ("_obs",)

    def __init__(self, obs: Any) -> None:
        self._obs = obs

    def end(
        self,
        *,
        output: Any = None,
        usage: dict[str, int] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        try:
            update_kwargs: dict[str, Any] = {}
            if output is not None:
                update_kwargs["output"] = output
            if metadata:
                update_kwargs["metadata"] = metadata
            if usage:
                u = usage
                update_kwargs["usage_details"] = {
                    "input": u.get("prompt_tokens", 0),
                    "output": u.get("completion_tokens", 0),
                }
            if update_kwargs:
                self._obs.update(**update_kwargs)
            self._obs.end()
        except Exception:
            logger.debug("Langfuse generation end failed", exc_info=True)


class _TraceWrapper:
    __slots__ = ("_client", "_root_obs")

    def __init__(self, client: Any, root_obs: Any) -> None:
        self._client = client
        self._root_obs = root_obs

    def generation(
        self,
        *,
        name: str,
        model: str | None = None,
        input: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> _GenerationWrapper | None:
        try:
            obs = self._root_obs.start_observation(
                name=name,
                as_type="generation",
                model=model or None,
                input=input,
                metadata=metadata or {},
            )
            return _GenerationWrapper(obs)
        except Exception:
            logger.debug("Langfuse generation creation failed", exc_info=True)
            return None

    def update(self, **kwargs: Any) -> None:
        try:
            self._root_obs.update(**kwargs)
        except Exception:
            logger.debug("Langfuse trace update failed", exc_info=True)

    def end(self) -> None:
        try:
            self._root_obs.end()
        except Exception:
            logger.debug("Langfuse trace end failed", exc_info=True)


def start_trace(
    name: str,
    *,
    user_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> _TraceWrapper | None:
    client = _get_client()
    if client is None:
        return None
    try:
        root_obs = client.start_observation(
            name=name,
            as_type="span",
            user_id=user_id,
            metadata=metadata or {},
        )
        return _TraceWrapper(client, root_obs)
    except Exception:
        logger.debug("Langfuse start_trace failed", exc_info=True)
        return None


def end_generation(
    gen: _GenerationWrapper | None,
    *,
    output: Any = None,
    usage: dict[str, int] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    if gen is None:
        return
    gen.end(output=output, usage=usage, metadata=metadata)


def flush() -> None:
    client = _get_client()
    if client is None:
        return
    try:
        client.flush()
    except Exception:
        pass


def shutdown() -> None:
    global _client, _initialized
    if _client is not None:
        try:
            _client.shutdown()
        except Exception:
            logger.warning("Langfuse shutdown failed", exc_info=True)
    _client = None
    _initialized = False
