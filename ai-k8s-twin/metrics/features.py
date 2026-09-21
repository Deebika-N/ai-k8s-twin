"""Convert Prometheus measurements into the experiment result contract."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


NAMESPACE = "simulation"
WORKLOAD = "simulation-paymentservice"


@dataclass(frozen=True)
class MetricsFeatures:
    avg_cpu_cores: float
    max_cpu_cores: float
    avg_memory_bytes: float
    max_memory_bytes: float
    request_rate: float | None
    p95_latency_ms: float | None
    p99_latency_ms: float | None
    error_rate: float | None
    pod_restarts: float
    available_replicas: float
    oom_killed: float
    recovery_time_seconds: float | None = None

    def as_mapping(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExperimentResult:
    run_id: str
    configuration: dict[str, Any]
    experiment: str
    stress_level: str
    started_at: str
    finished_at: str
    metrics: MetricsFeatures
    constraints: dict[str, Any]
    result: str

    def as_mapping(self) -> dict[str, Any]:
        value = asdict(self)
        value["metrics"] = self.metrics.as_mapping()
        return value


def _query(client: Any, expression: str, optional: bool = False) -> float | None:
    try:
        return client.scalar(expression, default=None if optional else 0.0)
    except Exception:
        if optional:
            return None
        raise


def collect_features(client: Any, pod_selector: str = 'pod=~"simulation-paymentservice-.+"') -> MetricsFeatures:
    cpu_now = f'sum(rate(container_cpu_usage_seconds_total{{namespace="{NAMESPACE}",container="paymentservice",{pod_selector}}}[5m]))'
    cpu_avg = f'avg_over_time(({cpu_now})[5m:])'
    cpu_max = f'max_over_time(({cpu_now})[5m:])'
    memory_now = f'sum(container_memory_working_set_bytes{{namespace="{NAMESPACE}",container="paymentservice",{pod_selector}}})'
    memory_avg = f'avg_over_time(({memory_now})[5m:])'
    memory_max = f'max_over_time(({memory_now})[5m:])'
    restarts = f'sum(kube_pod_container_status_restarts_total{{namespace="{NAMESPACE}",container="paymentservice",{pod_selector}}})'
    available = f'kube_deployment_status_replicas_available{{namespace="{NAMESPACE}",deployment="{WORKLOAD}"}}'
    oom = f'sum(kube_pod_container_status_last_terminated_reason{{namespace="{NAMESPACE}",container="paymentservice",reason="OOMKilled",{pod_selector}}})'

    return MetricsFeatures(
        avg_cpu_cores=client.scalar(cpu_avg),
        max_cpu_cores=client.scalar(cpu_max),
        avg_memory_bytes=client.scalar(memory_avg),
        max_memory_bytes=client.scalar(memory_max),
        request_rate=_query(client, "paymentservice_request_rate", optional=True),
        p95_latency_ms=_query(client, "paymentservice_p95_latency_ms", optional=True),
        p99_latency_ms=_query(client, "paymentservice_p99_latency_ms", optional=True),
        error_rate=_query(client, "paymentservice_error_rate", optional=True),
        pod_restarts=client.scalar(restarts),
        available_replicas=client.scalar(available),
        oom_killed=client.scalar(oom),
    )


def baseline_result(run_id: str, configuration: dict[str, Any], features: MetricsFeatures) -> ExperimentResult:
    now = datetime.now(timezone.utc).isoformat()
    return ExperimentResult(
        run_id=run_id,
        configuration=configuration,
        experiment="baseline",
        stress_level="none",
        started_at=now,
        finished_at=now,
        metrics=features,
        constraints={},
        result="MEASURED",
    )