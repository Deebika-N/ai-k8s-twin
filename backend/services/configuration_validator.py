"""Validation for discovered and Groq-proposed Kubernetes workload settings."""

import re
from typing import Any, Mapping


class ConfigurationValidationError(ValueError):
    """Raised when a workload configuration cannot be safely deployed."""


_NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


def _cpu_cores(value: Any, field_name: str) -> float:
    if not isinstance(value, (int, float, str)) or isinstance(value, bool):
        raise ConfigurationValidationError(f"{field_name} must be a CPU quantity")
    text = str(value).strip()
    try:
        result = float(text[:-1]) / 1000 if text.endswith("m") else float(text)
    except ValueError as error:
        raise ConfigurationValidationError(f"{field_name} has an invalid CPU quantity") from error
    if result <= 0:
        raise ConfigurationValidationError(f"{field_name} must be greater than zero")
    return result


def _memory_bytes(value: Any, field_name: str) -> float:
    if not isinstance(value, (int, float, str)) or isinstance(value, bool):
        raise ConfigurationValidationError(f"{field_name} must be a memory quantity")
    text = str(value).strip()
    units = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "K": 1000, "M": 1000**2, "G": 1000**3}
    for suffix, multiplier in sorted(units.items(), key=lambda item: len(item[0]), reverse=True):
        if text.endswith(suffix):
            try:
                result = float(text[:-len(suffix)]) * multiplier
            except ValueError as error:
                raise ConfigurationValidationError(f"{field_name} has an invalid memory quantity") from error
            break
    else:
        try:
            result = float(text)
        except ValueError as error:
            raise ConfigurationValidationError(f"{field_name} has an invalid memory quantity") from error
    if result <= 0:
        raise ConfigurationValidationError(f"{field_name} must be greater than zero")
    return result


def _resource_limits(application: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    containers = application.get("containers")
    if not isinstance(containers, list) or not containers or not isinstance(containers[0], Mapping):
        raise ConfigurationValidationError("workload must contain at least one container")
    resources = containers[0].get("resources", {})
    if not isinstance(resources, Mapping):
        resources = {}
    requests = resources.get("requests", {})
    limits = resources.get("limits", {})
    return (
        dict(requests) if isinstance(requests, Mapping) else {},
        dict(limits) if isinstance(limits, Mapping) else {},
    )


def validate_workload_configuration(
    application: Mapping[str, Any],
    *,
    available_cpu: Any | None = None,
    available_memory: Any | None = None,
    immutable: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and return a copy of an analyzer-shaped workload configuration."""
    candidate = dict(application)
    kind = candidate.get("kind")
    if kind not in {"Deployment", "StatefulSet", "DaemonSet"}:
        raise ConfigurationValidationError(f"unsupported workload kind: {kind}")

    for field_name in ("name", "namespace"):
        value = candidate.get(field_name)
        if not isinstance(value, str) or not _NAME_PATTERN.fullmatch(value):
            raise ConfigurationValidationError(f"{field_name} must be a valid Kubernetes name")

    replicas = candidate.get("replicas", 1)
    if isinstance(replicas, bool) or not isinstance(replicas, int) or not 1 <= replicas <= 100:
        raise ConfigurationValidationError("replicas must be an integer between 1 and 100")

    containers = candidate.get("containers")
    if not isinstance(containers, list) or not containers:
        raise ConfigurationValidationError("workload must contain at least one container")
    for container in containers:
        if not isinstance(container, Mapping) or not container.get("name") or not container.get("image"):
            raise ConfigurationValidationError("every container requires a name and image")

    requests, limits = _resource_limits(candidate)
    for resource_name, parser in (("cpu", _cpu_cores), ("memory", _memory_bytes)):
        request = requests.get(resource_name)
        limit = limits.get(resource_name)
        if request is None or limit is None:
            raise ConfigurationValidationError(f"cpu and memory {resource_name} requests and limits are required")
        request_value = parser(request, f"{resource_name} request")
        limit_value = parser(limit, f"{resource_name} limit")
        if request_value > limit_value:
            raise ConfigurationValidationError(f"{resource_name} request cannot exceed limit")
        if resource_name == "cpu" and available_cpu is not None and limit_value > _cpu_cores(available_cpu, "available_cpu"):
            raise ConfigurationValidationError("CPU limit exceeds available CPU")
        if resource_name == "memory" and available_memory is not None and limit_value > _memory_bytes(available_memory, "available_memory"):
            raise ConfigurationValidationError("memory limit exceeds available memory")

    labels = candidate.get("labels", {})
    selector = candidate.get("selector", {})
    match_labels = selector.get("matchLabels", {}) if isinstance(selector, Mapping) else {}
    if isinstance(labels, Mapping) and isinstance(match_labels, Mapping):
        for key, value in match_labels.items():
            if labels.get(key) != value:
                raise ConfigurationValidationError("workload selector does not match pod labels")

    if immutable:
        for field_name in ("kind", "name", "namespace", "labels"):
            if field_name in immutable and candidate.get(field_name) != immutable.get(field_name):
                raise ConfigurationValidationError(f"immutable field changed: {field_name}")
        original_containers = immutable.get("containers", [])
        for index, original in enumerate(original_containers):
            if index >= len(containers):
                raise ConfigurationValidationError("container identity changed")
            if containers[index].get("name") != original.get("name") or containers[index].get("image") != original.get("image"):
                raise ConfigurationValidationError("container identity changed")

    return candidate