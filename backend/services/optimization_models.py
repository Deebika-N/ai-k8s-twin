"""Data contracts for iterative configuration optimization runs."""

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ConstraintRequirements:
    expected_replicas: int = 1
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
        for name in (
            "max_p95_latency_ms",
            "max_p99_latency_ms",
            "max_error_rate_percent",
            "max_recovery_time_seconds",
            "max_pod_restarts",
            "max_oom_killed",
        ):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} cannot be negative")


@dataclass(frozen=True)
class OptimizationRunInput:
    repository_url: str
    available_cpu: str
    available_memory: str
    vus: int
    duration: str
    selected_environments: tuple[str, ...]
    requirements: ConstraintRequirements = field(default_factory=ConstraintRequirements)
    max_iterations: int = 5

    def __post_init__(self) -> None:
        if not self.repository_url.strip():
            raise ValueError("repository_url is required")
        if self.vus < 1:
            raise ValueError("vus must be at least 1")
        if not self.duration.strip():
            raise ValueError("duration is required")
        if not self.selected_environments:
            raise ValueError("at least one Chaos environment is required")
        if self.max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")

    def as_mapping(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IterationRecord:
    iteration: int
    configuration: dict[str, Any]
    chaos_parameters: dict[str, Any]
    metrics: dict[str, Any]
    constraint_evaluation: dict[str, Any]
    status: str
    started_at: str
    finished_at: str
    deployment: dict[str, Any] = field(default_factory=dict)
    chaos_results: dict[str, Any] = field(default_factory=dict)
    workload_results: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def as_mapping(self) -> dict[str, Any]:
        return asdict(self)