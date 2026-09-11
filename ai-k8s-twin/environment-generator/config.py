"""Validated configuration model for the paymentservice experiment target."""

from dataclasses import dataclass
import re
from typing import Any, Mapping


_NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
_QUANTITY_PATTERN = re.compile(r"^(?:[1-9][0-9]*m|[1-9][0-9]*|[1-9][0-9]*(?:Mi|Gi))$")


def _required_string(values: Mapping[str, Any], field_name: str) -> str:
    value = values.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _quantity(values: Mapping[str, Any], field_name: str) -> str:
    value = _required_string(values, field_name)
    if not _QUANTITY_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} contains an unsupported resource quantity")
    return value


@dataclass(frozen=True)
class EnvironmentConfig:
    service: str
    image: str
    replicas: int
    cpu_request: str
    cpu_limit: str
    memory_request: str
    memory_limit: str
    application_port: int = 50051

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "EnvironmentConfig":
        service = _required_string(values, "service")
        image = _required_string(values, "image")
        if not _NAME_PATTERN.fullmatch(service):
            raise ValueError("service must be a lowercase Kubernetes name")
        if "/" not in image or any(character in image for character in "\r\n $'"):
            raise ValueError("image must be a valid controlled image reference")

        replicas = values.get("replicas")
        if isinstance(replicas, bool) or not isinstance(replicas, int) or not 1 <= replicas <= 20:
            raise ValueError("replicas must be an integer between 1 and 20")

        application_port = values.get("application_port", 50051)
        if isinstance(application_port, bool) or not isinstance(application_port, int) or not 1 <= application_port <= 65535:
            raise ValueError("application_port must be between 1 and 65535")

        config = cls(
            service=service,
            image=image,
            replicas=replicas,
            cpu_request=_quantity(values, "cpu_request"),
            cpu_limit=_quantity(values, "cpu_limit"),
            memory_request=_quantity(values, "memory_request"),
            memory_limit=_quantity(values, "memory_limit"),
            application_port=application_port,
        )
        if config.cpu_request.endswith("m") and config.cpu_limit.endswith("m"):
            if int(config.cpu_request[:-1]) > int(config.cpu_limit[:-1]):
                raise ValueError("cpu_request cannot exceed cpu_limit")
        if config.memory_request.endswith(("Mi", "Gi")) and config.memory_limit.endswith(("Mi", "Gi")):
            request_unit = config.memory_request[-2:]
            limit_unit = config.memory_limit[-2:]
            request_value = int(config.memory_request[:-2]) * (1024 if request_unit == "Gi" else 1)
            limit_value = int(config.memory_limit[:-2]) * (1024 if limit_unit == "Gi" else 1)
            if request_value > limit_value:
                raise ValueError("memory_request cannot exceed memory_limit")
        return config

    def as_mapping(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "image": self.image,
            "replicas": self.replicas,
            "cpu_request": self.cpu_request,
            "cpu_limit": self.cpu_limit,
            "memory_request": self.memory_request,
            "memory_limit": self.memory_limit,
            "application_port": self.application_port,
        }