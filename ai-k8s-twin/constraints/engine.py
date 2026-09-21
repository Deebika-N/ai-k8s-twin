"""Evaluate hard requirements without involving an LLM."""

from dataclasses import asdict, dataclass
from typing import Any

from metrics.features import MetricsFeatures


@dataclass(frozen=True)
class ConstraintRequirements:
    expected_replicas: int
    min_availability_percent: float = 99.0
    max_p95_latency_ms: float | None = 200.0
    max_p99_latency_ms: float | None = None
    max_error_rate_percent: float | None = 1.0
    max_recovery_time_seconds: float | None = 30.0
    max_pod_restarts: float | None = None
    max_oom_killed: float = 0.0

    def __post_init__(self) -> None:
        if self.expected_replicas < 1:
            raise ValueError("expected_replicas must be at least 1")
        if not 0 <= self.min_availability_percent <= 100:
            raise ValueError("min_availability_percent must be between 0 and 100")
        for field_name in (
            "max_p95_latency_ms",
            "max_p99_latency_ms",
            "max_error_rate_percent",
            "max_recovery_time_seconds",
            "max_pod_restarts",
            "max_oom_killed",
        ):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ValueError(f"{field_name} cannot be negative")


@dataclass(frozen=True)
class ConstraintCheck:
    name: str
    status: str
    measured: float | None
    limit: float | None
    reason: str

    def as_mapping(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ConstraintEvaluation:
    status: str
    checks: tuple[ConstraintCheck, ...]

    def as_mapping(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "checks": [check.as_mapping() for check in self.checks],
        }


def _check(
    name: str,
    measured: float | None,
    limit: float | None,
    comparison: str,
) -> ConstraintCheck:
    if measured is None:
        return ConstraintCheck(name, "UNKNOWN", None, limit, "metric is unavailable")
    passed = measured <= limit if comparison == "max" else measured >= limit
    status = "PASS" if passed else "FAIL"
    operator = "<=" if comparison == "max" else ">="
    reason = f"{measured:g} {operator} {limit:g}" if passed else f"{measured:g} exceeds {operator} limit {limit:g}"
    return ConstraintCheck(name, status, measured, limit, reason)


def evaluate_constraints(
    metrics: MetricsFeatures,
    requirements: ConstraintRequirements,
) -> ConstraintEvaluation:
    availability = 100.0 * metrics.available_replicas / requirements.expected_replicas
    checks = [
        _check("availability_percent", availability, requirements.min_availability_percent, "min"),
        _check("p95_latency_ms", metrics.p95_latency_ms, requirements.max_p95_latency_ms, "max")
        if requirements.max_p95_latency_ms is not None
        else ConstraintCheck("p95_latency_ms", "SKIPPED", metrics.p95_latency_ms, None, "no limit configured"),
        _check("p99_latency_ms", metrics.p99_latency_ms, requirements.max_p99_latency_ms, "max")
        if requirements.max_p99_latency_ms is not None
        else ConstraintCheck("p99_latency_ms", "SKIPPED", metrics.p99_latency_ms, None, "no limit configured"),
        _check("error_rate_percent", metrics.error_rate, requirements.max_error_rate_percent, "max")
        if requirements.max_error_rate_percent is not None
        else ConstraintCheck("error_rate_percent", "SKIPPED", metrics.error_rate, None, "no limit configured"),
        _check("recovery_time_seconds", metrics.recovery_time_seconds, requirements.max_recovery_time_seconds, "max")
        if requirements.max_recovery_time_seconds is not None
        else ConstraintCheck("recovery_time_seconds", "SKIPPED", metrics.recovery_time_seconds, None, "no limit configured"),
        _check("pod_restarts", metrics.pod_restarts, requirements.max_pod_restarts, "max")
        if requirements.max_pod_restarts is not None
        else ConstraintCheck("pod_restarts", "SKIPPED", metrics.pod_restarts, None, "no limit configured"),
        _check("oom_killed", metrics.oom_killed, requirements.max_oom_killed, "max"),
    ]
    status = "PASS" if all(check.status in {"PASS", "SKIPPED"} for check in checks) else "FAIL"
    return ConstraintEvaluation(status, tuple(checks))