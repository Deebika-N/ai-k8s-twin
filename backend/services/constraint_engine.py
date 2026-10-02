"""Deterministic evaluation of experiment requirements."""

from typing import Any, Mapping

from services.optimization_models import ConstraintRequirements


def _check(name: str, measured: Any, limit: Any, comparison: str) -> dict[str, Any]:
    if measured is None:
        return {"name": name, "status": "UNKNOWN", "measured": None, "limit": limit, "reason": "metric is unavailable"}
    try:
        value = float(measured)
        threshold = float(limit)
    except (TypeError, ValueError):
        return {"name": name, "status": "UNKNOWN", "measured": measured, "limit": limit, "reason": "metric is not numeric"}
    passed = value >= threshold if comparison == "min" else value <= threshold
    operator = ">=" if comparison == "min" else "<="
    status = "PASS" if passed else "FAIL"
    reason = f"{value:g} {operator} {threshold:g}" if passed else f"{value:g} exceeds {operator} limit {threshold:g}"
    return {"name": name, "status": status, "measured": value, "limit": threshold, "reason": reason}


def evaluate_constraints(metrics: Mapping[str, Any], requirements: ConstraintRequirements) -> dict[str, Any]:
    expected = requirements.expected_replicas
    available = metrics.get("available_replicas")
    availability = None if available is None else 100.0 * float(available) / expected
    checks = [_check("availability_percent", availability, requirements.min_availability_percent, "min")]
    optional_checks = (
        ("p95_latency_ms", metrics.get("p95_latency_ms"), requirements.max_p95_latency_ms),
        ("p99_latency_ms", metrics.get("p99_latency_ms"), requirements.max_p99_latency_ms),
        ("error_rate_percent", metrics.get("error_rate_percent"), requirements.max_error_rate_percent),
        ("recovery_time_seconds", metrics.get("recovery_time_seconds"), requirements.max_recovery_time_seconds),
        ("pod_restarts", metrics.get("pod_restarts"), requirements.max_pod_restarts),
        ("oom_killed", metrics.get("oom_killed"), requirements.max_oom_killed),
    )
    for name, measured, limit in optional_checks:
        checks.append(
            {"name": name, "status": "SKIPPED", "measured": measured, "limit": None, "reason": "no limit configured"}
            if limit is None
            else _check(name, measured, limit, "max")
        )
    status = "PASS" if all(check["status"] in {"PASS", "SKIPPED"} for check in checks) else "FAIL"
    return {"status": status, "checks": checks}