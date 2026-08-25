from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import ValidationError

from app.core.config import Settings
from app.core.contracts import resolve_contract_symbol
from app.core.errors import ProviderError
from app.core.logging_context import get_request_id
from app.core.models import Bar, HistoricalQuery


TIMESTAMP_FIELDS = ("timestamp", "ts_event", "ts", "time", "datetime")
logger = logging.getLogger(__name__)


class MassiveHistoricalProvider:
    """上游历史 K 线数据提供者，通过 HTTP API 获取 OHLCV 数据。"""

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport

    async def get_range(self, query: HistoricalQuery) -> list[Bar]:
        """从上游 API 获取指定查询范围的 K 线数据，自动处理数据未发布和数据截断。"""
        self._validate_configuration()
        url = f"{self.settings.hist_base_url}/v0/timeseries.get_range"
        payload = {
            "dataset": query.dataset,
            "symbols": resolve_contract_symbol(query.symbol, query.start, query.end),
            "schema": query.provider_schema,
            "start": query.start.isoformat(),
            "end": query.end.isoformat(),
            "encoding": "json",
        }
        logger.info(
            "market_bars upstream_request request_id=%s dataset=%s symbol=%s schema=%s start=%s end=%s",
            get_request_id(),
            payload["dataset"],
            payload["symbols"],
            payload["schema"],
            payload["start"],
            payload["end"],
        )

        response = await self._request_with_retries(url, payload)

        try:
            first_detail = response.json().get("detail", {}) if response.status_code == 422 else {}
        except (ValueError, AttributeError):
            first_detail = {}
        if first_detail.get("case") == "data_start_after_available_end":
            # Upstream has nothing at/after start yet (common when local clock is ahead of
            # published available_end). Treat as empty rather than a hard failure.
            logger.info(
                "market_bars upstream_empty request_id=%s start=%s available_end=%s symbol=%s",
                get_request_id(),
                payload["start"],
                (first_detail.get("payload") or {}).get("available_end"),
                payload["symbols"],
            )
            return []
        if first_detail.get("case") == "data_end_after_available_end":
            available_end = (first_detail.get("payload") or {}).get("available_end")
            if available_end:
                logger.info(
                    "market_bars upstream_clamped request_id=%s requested_end=%s available_end=%s symbol=%s",
                    get_request_id(),
                    payload["end"],
                    available_end,
                    payload["symbols"],
                )
                if available_end <= payload["start"]:
                    return []
                payload = {**payload, "end": str(available_end)}
                response = await self._request_with_retries(url, payload)

        if response.status_code >= 400:
            try:
                upstream_detail = response.json().get("detail", {})
            except (ValueError, AttributeError):
                upstream_detail = {"body": response.text[:500]}
            logger.warning(
                "market_bars upstream_rejected request_id=%s status=%s dataset=%s symbol=%s schema=%s start=%s end=%s detail=%s",
                get_request_id(),
                response.status_code,
                payload["dataset"],
                payload["symbols"],
                payload["schema"],
                payload["start"],
                payload["end"],
                upstream_detail,
            )

        self._raise_for_status(response)
        records = self._decode_records(response)
        bars = self._normalize_records(records)
        logger.info(
            "market_bars upstream_response request_id=%s symbol=%s bars=%d",
            get_request_id(),
            payload["symbols"],
            len(bars),
        )
        return bars

    async def _request_with_retries(
        self, url: str, payload: dict[str, str]
    ) -> httpx.Response:
        """发送 HTTP 请求并支持指数退避重试（429/5xx 触发重试）。"""
        attempts = self.settings.hist_max_retries + 1
        last_error: httpx.HTTPError | None = None

        async with httpx.AsyncClient(
            auth=(self.settings.hist_api_key, ""),
            timeout=self.settings.hist_timeout_seconds,
            transport=self.transport,
            trust_env=False,
        ) as client:
            for attempt in range(attempts):
                try:
                    response = await client.post(url, data=payload)
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    last_error = exc
                else:
                    if response.status_code not in {429, 500, 502, 503, 504}:
                        return response
                    if attempt == attempts - 1:
                        return response

                if attempt < attempts - 1:
                    await asyncio.sleep(0.25 * (2**attempt))

        if isinstance(last_error, httpx.TimeoutException):
            raise ProviderError(
                "provider_timeout", "Historical provider timed out", 504
            ) from last_error
        raise ProviderError(
            "provider_unavailable", "Historical provider is unavailable", 502
        ) from last_error

    def _validate_configuration(self) -> None:
        """校验 provider 配置：API Key 是否存在、URL 是否合法、HTTP 是否允许。"""
        if not self.settings.hist_api_key:
            raise ProviderError(
                "provider_not_configured",
                "Historical provider key is not configured",
                503,
            )
        parsed = urlparse(self.settings.hist_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ProviderError(
                "provider_bad_url", "Historical provider URL is invalid", 500
            )
        if parsed.scheme == "http" and not self.settings.insecure_http_allowed:
            raise ProviderError(
                "insecure_provider_transport",
                "Refusing to send provider credentials over HTTP",
                503,
            )

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        """根据 HTTP 响应状态码抛出对应的 ProviderError。"""
        if 300 <= response.status_code < 400:
            raise ProviderError(
                "provider_redirected",
                "Historical provider redirected the request; configure its final HTTPS URL",
                502,
            )
        if response.status_code in {401, 403}:
            raise ProviderError(
                "provider_auth_failed", "Historical provider rejected credentials", 502
            )
        if response.status_code == 429:
            raise ProviderError(
                "provider_rate_limited", "Historical provider rate limit exceeded", 503
            )
        if response.status_code >= 500:
            raise ProviderError(
                "provider_unavailable", "Historical provider is unavailable", 502
            )
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", {})
            except (ValueError, AttributeError):
                detail = {}
            if detail.get("case") in {"data_end_after_available_end", "data_start_after_available_end"}:
                raise ProviderError(
                    "provider_data_not_ready",
                    "Historical provider has not published this time range yet",
                    409,
                    details=[{
                        "case": detail.get("case"),
                        "available_end": (detail.get("payload") or {}).get("available_end"),
                        "requested_end": (detail.get("payload") or {}).get("end"),
                        "requested_start": (detail.get("payload") or {}).get("start"),
                    }],
                )
            raise ProviderError(
                "provider_request_rejected",
                "Historical provider rejected the query",
                422,
            )

    @staticmethod
    def _decode_records(response: httpx.Response) -> list[dict[str, Any]]:
        """解析 HTTP 响应体为记录字典列表，支持 JSON 数组、NDJSON 和嵌套结构。"""
        text = response.text.strip()
        if not text:
            return []

        try:
            decoded = response.json()
        except json.JSONDecodeError:
            try:
                decoded = [json.loads(line) for line in text.splitlines() if line.strip()]
            except json.JSONDecodeError as exc:
                raise ProviderError(
                    "provider_invalid_data",
                    "Historical provider returned invalid JSON",
                    502,
                ) from exc

        if isinstance(decoded, list):
            records = decoded
        elif isinstance(decoded, dict):
            nested = next(
                (
                    decoded[key]
                    for key in ("data", "records", "result")
                    if isinstance(decoded.get(key), list)
                ),
                None,
            )
            records = nested if nested is not None else [decoded]
        else:
            raise ProviderError(
                "provider_invalid_data",
                "Historical provider returned an unsupported JSON shape",
                502,
            )

        if not all(isinstance(record, dict) for record in records):
            raise ProviderError(
                "provider_invalid_data",
                "Historical provider records must be JSON objects",
                502,
            )
        return records

    def _normalize_records(self, records: list[dict[str, Any]]) -> list[Bar]:
        """将原始记录字典列表标准化为 Bar 列表，校验时间戳唯一性和数据有效性。"""
        bars: list[Bar] = []
        errors: list[dict[str, Any]] = []
        for index, record in enumerate(records):
            try:
                bars.append(
                    Bar(
                        timestamp=self._parse_timestamp(record),
                        open=self._parse_price(record["open"]),
                        high=self._parse_price(record["high"]),
                        low=self._parse_price(record["low"]),
                        close=self._parse_price(record["close"]),
                        volume=record.get("volume"),
                    )
                )
            except (KeyError, TypeError, ValueError, ValidationError) as exc:
                errors.append({"record": index, "message": str(exc)})

        if errors:
            raise ProviderError(
                "provider_invalid_data",
                "Historical provider returned invalid OHLC records",
                502,
                details=errors[:20],
            )

        bars.sort(key=lambda bar: bar.timestamp)
        timestamps = [bar.timestamp for bar in bars]
        if len(timestamps) != len(set(timestamps)):
            raise ProviderError(
                "provider_invalid_data",
                "Historical provider returned duplicate timestamps",
                502,
            )
        return bars

    @staticmethod
    def _parse_timestamp(record: dict[str, Any]) -> datetime:
        """从记录中提取时间戳，支持多种字段名和 epoch/ISO-8601 格式。"""
        raw = next((record[field] for field in TIMESTAMP_FIELDS if field in record), None)
        if raw is None and isinstance(record.get("hd"), dict):
            header = record["hd"]
            raw = next(
                (header[field] for field in TIMESTAMP_FIELDS if field in header), None
            )
        if raw is None:
            raise ValueError("timestamp field is missing")
        if isinstance(raw, (int, float)):
            return MassiveHistoricalProvider._timestamp_from_epoch(raw)
        value = str(raw).strip()
        try:
            numeric_value = float(value)
        except ValueError:
            pass
        else:
            return MassiveHistoricalProvider._timestamp_from_epoch(numeric_value)
        value = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    @staticmethod
    def _timestamp_from_epoch(raw: int | float) -> datetime:
        """将 epoch 数值（自动判断纳秒/微秒/毫秒/秒精度）转换为 UTC datetime。"""
        magnitude = abs(raw)
        if magnitude >= 100_000_000_000_000_000:
            divisor = 1_000_000_000
        elif magnitude >= 100_000_000_000_000:
            divisor = 1_000_000
        elif magnitude >= 100_000_000_000:
            divisor = 1_000
        else:
            divisor = 1
        return datetime.fromtimestamp(raw / divisor, tz=timezone.utc)

    @staticmethod
    def _parse_price(raw: Any) -> float:
        """解析价格为 float，自动处理可能的纳秒级精度缩放。"""
        value = float(raw)
        if abs(value) >= 1_000_000_000:
            return value / 1_000_000_000
        return value
