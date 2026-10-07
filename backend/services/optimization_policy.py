"""Deterministic features, constraints, bounds, and resource scoring."""

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from services.configuration_validator import ConfigurationValidationError


def cpu_millicores(value: Any) -> float:
    text = str(value).strip()
    return float(text[:-1]) if text.endswith("m") else float(text) * 1000


def memory_bytes(value: Any) -> float:
    text = str(value).strip()
    units = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "K": 1000, "M": 1000**2, "G": 1000**3}
    for suffix, multiplier in sorted(units.items(), key=lambda item: len(item[0]), reverse=True):
        if text.endswith(suffix):
            return float(text[:-len(suffix)]) * multiplier
    return float(text)


@dataclass(frozen=True)
class ResourceBounds:
    min_replicas: int = 1
    max_replicas: int = 4
    min_cpu_request_m: float = 100
    max_cpu_request_m: float = 2000
    min_cpu_limit_m: float = 200
    max_cpu_limit_m: float = 4000
    min_memory_request_bytes: float = 128 * 1024**2
    max_memory_request_bytes: float = 2 * 1024**3
    min_memory_limit_bytes: float = 256 * 1024**2
    max_memory_limit_bytes: float = 4 * 1024**3


@dataclass(frozen=True)
class ResourceSearchFloors:
    """Lower test limits, set to half the smallest checked-in workload resources."""
    min_replicas: int = 1
    min_cpu_request_m: float = 50
    min_cpu_limit_m: float = 100
    min_memory_request_bytes: float = 32 * 1024**2
    min_memory_limit_bytes: float = 64 * 1024**2


@dataclass(frozen=True)
class OptimizationConstraints:
    max_p95_latency_ms: float | None = 200.0
    max_p99_latency_ms: float | None = None
    max_error_rate_percent: float | None = 1.0
    max_recovery_time_seconds: float | None = 30.0
    min_available_replicas: int = 1
    oom_killed_allowed: bool = False

    def as_mapping(self) -> dict[str, Any]:
        return asdict(self)


def configuration_from_application(application: Mapping[str, Any]) -> dict[str, Any]:
    containers = application.get("containers", [])
    if not containers:
        raise ConfigurationValidationError("selected workload has no containers")
    container = containers[0]
    resources = container.get("resources", {})
    requests = resources.get("requests", {})
    limits = resources.get("limits", {})
    return {
        "replicas": application.get("replicas", 1),
        "cpu_request": requests.get("cpu"),
        "cpu_limit": limits.get("cpu"),
        "memory_request": requests.get("memory"),
        "memory_limit": limits.get("memory"),
    }


def validate_candidate(
    configuration: Mapping[str, Any],
    bounds: ResourceBounds,
    available_cpu: Any | None = None,
    available_memory: Any | None = None,
    *,
    allow_below_minimum_resources: bool = False,
) -> dict[str, Any]:
    required = {"replicas", "cpu_request", "cpu_limit", "memory_request", "memory_limit"}
    missing = required - set(configuration)
    if missing:
        raise ConfigurationValidationError(f"candidate is missing fields: {sorted(missing)}")
    replicas = configuration["replicas"]
    if isinstance(replicas, bool) or not isinstance(replicas, int) or not bounds.min_replicas <= replicas <= bounds.max_replicas:
        raise ConfigurationValidationError("replicas are outside configured bounds")
    cpu_request = cpu_millicores(configuration["cpu_request"])
    cpu_limit = cpu_millicores(configuration["cpu_limit"])
    memory_request = memory_bytes(configuration["memory_request"])
    memory_limit = memory_bytes(configuration["memory_limit"])
    if cpu_request > bounds.max_cpu_request_m or (
        not allow_below_minimum_resources and cpu_request < bounds.min_cpu_request_m
    ):
        raise ConfigurationValidationError("CPU request is outside configured bounds")
    if cpu_limit > bounds.max_cpu_limit_m or (
        not allow_below_minimum_resources and cpu_limit < bounds.min_cpu_limit_m
    ):
        raise ConfigurationValidationError("CPU limit is outside configured bounds")
    if memory_request > bounds.max_memory_request_bytes or (
        not allow_below_minimum_resources and memory_request < bounds.min_memory_request_bytes
    ):
        raise ConfigurationValidationError("memory request is outside configured bounds")
    if memory_limit > bounds.max_memory_limit_bytes or (
        not allow_below_minimum_resources and memory_limit < bounds.min_memory_limit_bytes
    ):
        raise ConfigurationValidationError("memory limit is outside configured bounds")
    if cpu_request > cpu_limit:
        raise ConfigurationValidationError("CPU request cannot exceed CPU limit")
    if memory_request > memory_limit:
        raise ConfigurationValidationError("memory request cannot exceed memory limit")
    if available_cpu is not None and cpu_limit > cpu_millicores(available_cpu):
        raise ConfigurationValidationError("CPU limit exceeds available CPU")
    if available_memory is not None and memory_limit > memory_bytes(available_memory):
        raise ConfigurationValidationError("memory limit exceeds available memory")
    return dict(configuration)


def extract_features(result: Mapping[str, Any]) -> dict[str, Any]:
    metric_sets = []
    baseline = result.get("baseline", {})
    if isinstance(baseline, Mapping) and isinstance(baseline.get("metrics"), Mapping):
        metric_sets.append(baseline["metrics"])
    for experiment in result.get("experiments", []):
        if isinstance(experiment, Mapping) and isinstance(experiment.get("metrics"), Mapping):
            metric_sets.append(experiment["metrics"])

    def values(name: str, aliases: tuple[str, ...] = ()) -> list[float]:
        names = (name,) + aliases
        collected = []
        for metrics in metric_sets:
            value = next((metrics.get(key) for key in names if metrics.get(key) is not None), None)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                collected.append(float(value))
        return collected

    available = values("available_replicas", ("ready_replicas",))
    oom_values = [metrics.get("oom_killed") for metrics in metric_sets if metrics.get("oom_killed") is not None]
    pod_kill_recovery_states = [
        metrics.get("pod_kill_recovered")
        for metrics in metric_sets
        if isinstance(metrics.get("pod_kill_recovered"), bool)
    ]
    pod_kill_recovery = max(values("pod_kill_recovery_seconds"), default=None)
    return {
        "max_p95_latency_ms": max(values("p95_latency_ms", ("p95_ms",)), default=None),
        "max_p99_latency_ms": max(values("p99_latency_ms", ("p99_ms",)), default=None),
        "max_error_rate_percent": max(values("error_rate_percent", ("error_rate",)), default=None),
        "pod_kill_recovery_seconds": pod_kill_recovery,
        "pod_kill_recovered": (
            False if False in pod_kill_recovery_states
            else True if pod_kill_recovery_states
            else None
        ),
        "max_recovery_time_seconds": pod_kill_recovery,
        "min_available_replicas": min(available, default=None),
        "oom_killed": any(bool(value) for value in oom_values) if oom_values else None,
        "metrics_observations": len(metric_sets),
    }


def evaluate_constraints(features: Mapping[str, Any], constraints: OptimizationConstraints) -> dict[str, Any]:
    checks = []

    def add(name: str, measured: Any, limit: Any, operator: str, skipped: bool = False) -> None:
        if skipped:
            checks.append({"name": name, "status": "SKIPPED", "measured": measured, "limit": None})
            return
        if measured is None:
            checks.append({"name": name, "status": "UNKNOWN", "measured": None, "limit": limit})
            return
        passed = measured <= limit if operator == "<=" else measured >= limit
        checks.append({"name": name, "status": "PASS" if passed else "FAIL", "measured": measured, "limit": limit})

    add("p95_latency_ms", features.get("max_p95_latency_ms"), constraints.max_p95_latency_ms, "<=", constraints.max_p95_latency_ms is None)
    add("p99_latency_ms", features.get("max_p99_latency_ms"), constraints.max_p99_latency_ms, "<=", constraints.max_p99_latency_ms is None)
    add("error_rate_percent", features.get("max_error_rate_percent"), constraints.max_error_rate_percent, "<=", constraints.max_error_rate_percent is None)
    recovery_value = features.get("pod_kill_recovery_seconds")
    if constraints.max_recovery_time_seconds is None:
        add("pod_kill_recovery_seconds", recovery_value, None, "<=", True)
    elif recovery_value is None and features.get("pod_kill_recovered") is False:
        checks.append({
            "name": "pod_kill_recovery_seconds",
            "status": "FAIL",
            "measured": None,
            "limit": constraints.max_recovery_time_seconds,
            "reason": "recovery_not_observed_before_timeout",
            "recovered": False,
        })
    else:
        add("pod_kill_recovery_seconds", recovery_value, constraints.max_recovery_time_seconds, "<=")
        checks[-1]["recovered"] = features.get("pod_kill_recovered")
    add("available_replicas", features.get("min_available_replicas"), constraints.min_available_replicas, ">=")
    add("oom_killed", features.get("oom_killed"), False, "<=", constraints.oom_killed_allowed)
    return {"status": "PASS" if all(item["status"] in {"PASS", "SKIPPED"} for item in checks) else "FAIL", "checks": checks}


def resource_score(configuration: Mapping[str, Any], bounds: ResourceBounds, weights: Mapping[str, float] | None = None) -> float:
    weights = weights or {"cpu": 0.4, "memory": 0.4, "replicas": 0.2}
    cpu = cpu_millicores(configuration["cpu_limit"]) / bounds.max_cpu_limit_m
    memory = memory_bytes(configuration["memory_limit"]) / bounds.max_memory_limit_bytes
    replicas = configuration["replicas"] / bounds.max_replicas
    return weights["cpu"] * cpu + weights["memory"] * memory + weights["replicas"] * replicas


def failed_constraint_count(evaluation: Mapping[str, Any]) -> int:
    return sum(check.get("status") in {"FAIL", "UNKNOWN"} for check in evaluation.get("checks", []))
