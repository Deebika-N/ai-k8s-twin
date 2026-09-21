"""Small dependency-free client for the Prometheus HTTP query API."""

import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class PrometheusError(RuntimeError):
    """Raised when Prometheus cannot execute or decode a query."""


@dataclass(frozen=True)
class PrometheusClient:
    base_url: str = "http://localhost:9090"
    timeout_seconds: float = 10.0
    opener: Callable[..., Any] = urlopen

    def query(self, expression: str) -> list[dict[str, Any]]:
        query_url = f"{self.base_url.rstrip('/')}/api/v1/query?{urlencode({'query': expression})}"
        request = Request(query_url, headers={"Accept": "application/json"})
        try:
            with self.opener(request, timeout=self.timeout_seconds) as response:
                payload = json.load(response)
        except (OSError, ValueError) as error:
            raise PrometheusError(f"Prometheus query failed: {error}") from error

        if payload.get("status") != "success":
            error_message = payload.get("error", "unknown Prometheus error")
            raise PrometheusError(str(error_message))
        data = payload.get("data", {})
        result = data.get("result")
        if not isinstance(result, list):
            raise PrometheusError("Prometheus response did not contain a result list")
        return result

    def scalar(self, expression: str, default: float | None = 0.0) -> float | None:
        result = self.query(expression)
        if not result:
            return default
        value = result[0].get("value")
        if not isinstance(value, list) or len(value) != 2:
            raise PrometheusError("Prometheus scalar result has an invalid value")
        try:
            return float(value[1])
        except (TypeError, ValueError) as error:
            raise PrometheusError("Prometheus scalar result is not numeric") from error


class StaticPrometheusClient:
    """Test double that returns predefined scalar values by query."""

    def __init__(self, values: dict[str, float]):
        self.values = values
        self.queries: list[str] = []

    def scalar(self, expression: str, default: float | None = 0.0) -> float | None:
        self.queries.append(expression)
        value = self.values.get(expression, default)
        return None if value is None else float(value)