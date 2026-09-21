"""API request and response models."""

from typing import Any

from pydantic import BaseModel, Field


class ConfigurationRequest(BaseModel):
    service: str = "paymentservice"
    image: str = "us-central1-docker.pkg.dev/online-boutique-ci/microservices-demo/paymentservice:v0.10.6"
    replicas: int = Field(default=3, ge=1, le=20)
    cpu_request: str = "250m"
    cpu_limit: str = "500m"
    memory_request: str = "256Mi"
    memory_limit: str = "512Mi"
    application_port: int = Field(default=50051, ge=1, le=65535)


class RequirementsRequest(BaseModel):
    expected_replicas: int = Field(default=3, ge=1)
    min_availability_percent: float = Field(default=99, ge=0, le=100)
    max_p95_latency_ms: float | None = Field(default=200, ge=0)
    max_p99_latency_ms: float | None = Field(default=None, ge=0)
    max_error_rate_percent: float | None = Field(default=1, ge=0)
    max_recovery_time_seconds: float | None = Field(default=30, ge=0)
    max_pod_restarts: float | None = Field(default=None, ge=0)
    max_oom_killed: float = Field(default=0, ge=0)


class ExperimentRecord(BaseModel):
    run_id: str
    experiment: str
    status: str
    result: dict[str, Any] | None = None