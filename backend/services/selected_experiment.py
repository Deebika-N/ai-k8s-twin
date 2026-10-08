"""Reliable single-workload experiment lifecycle."""

import json
import os
import time
from typing import Any, Mapping

from services import environment_orchestrator as workflow


ACTIVE_EXPERIMENTS = ("Pod Kill", "CPU Stress", "Memory Stress", "Traffic Stress")
CHAOS_ENVIRONMENTS = ("Pod Kill", "CPU Stress", "Memory Stress")


def _result(application: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "application": application.get("name"),
        "environment": {},
        "deployment": {
            "status": "NOT_STARTED",
            "replicas": application.get("replicas"),
            "ready_replicas": None,
            "failure_reason": None,
        },
        "baseline": {"status": "SKIPPED", "metrics": {}},
        "experiments": [],
        "metrics_status": "UNAVAILABLE",
        "cleanup_status": "NOT_STARTED",
    }


def _write_chaos_file(application: Mapping[str, Any], environment: str, parameters: Mapping[str, Any]) -> str:
    os.makedirs(workflow.GENERATED_DIRECTORY, exist_ok=True)
    path = os.path.join(
        workflow.GENERATED_DIRECTORY,
        f"{application['name']}-{environment.lower().replace(' ', '-')}.yaml",
    )
    with open(path, "w", encoding="utf-8") as file:
        file.write(workflow.generate_chaos_yaml(environment, parameters))
    return path


def _run_k6(application: Mapping[str, Any], vus: int, duration: str) -> dict[str, Any]:
    grpc_names = {"currencyservice", "productcatalogservice", "adservice", "shippingservice"}
    if application.get("name") in grpc_names:
        return workflow.run_k6_grpc(application, vus, duration)
    return workflow.run_k6_http(application, vus, duration)


def _collect(application: Mapping[str, Any], recovery: Mapping[str, Any] | None, workload: Mapping[str, Any] | None) -> dict[str, Any]:
    metrics = workflow.collect_result_metrics(application, recovery, workload)
    metrics["metrics_available"] = not bool(metrics.get("collection_errors"))
    if not metrics["metrics_available"]:
        metrics["reason"] = "METRICS_PARTIALLY_UNAVAILABLE"
    return metrics


def _diagnose_deployment(application: Mapping[str, Any]) -> dict[str, Any]:
    namespace = application.get("namespace", "default")
    name = application.get("name")
    diagnostics: dict[str, Any] = {}
    commands = [
        ("pods", ["kubectl", "get", "pods", "-n", namespace, "-o", "json"]),
        ("deployment", ["kubectl", "get", "deployment", name, "-n", namespace, "-o", "json"]),
        ("describe_deployment", ["kubectl", "describe", "deployment", name, "-n", namespace]),
        ("events", ["kubectl", "get", "events", "-n", namespace, "--sort-by=.lastTimestamp"]),
    ]
    for label, command in commands:
        try:
            diagnostics[label] = workflow.run_command(command)
        except RuntimeError as error:
            diagnostics[label] = {"error": str(error)}
    return diagnostics


def _classify_failure(diagnostics: Mapping[str, Any]) -> str:
    text = json.dumps(diagnostics).lower()
    for marker, reason in (
        ("imagepullbackoff", "IMAGE_PULL_BACKOFF"),
        ("errimagepull", "IMAGE_PULL_ERROR"),
        ("crashloopbackoff", "CRASH_LOOP_BACKOFF"),
        ("oomkilled", "OOM_KILLED"),
        ("insufficient cpu", "INSUFFICIENT_CPU"),
        ("insufficient memory", "INSUFFICIENT_MEMORY"),
        ("readiness probe failed", "READINESS_PROBE_FAILURE"),
    ):
        if marker in text:
            return reason
    return "DEPLOYMENT_TIMEOUT"


def run_selected_application(
    application: Mapping[str, Any],
    available_cpu: str,
    available_memory: str,
    vus: int,
    duration: str,
    selected_experiments: tuple[str, ...] = ACTIVE_EXPERIMENTS,
) -> dict[str, Any]:
    """Run only one workload and always return a structured result."""
    result = _result(application)
    result["environment"] = {
        "available_cpu": available_cpu,
        "available_memory": available_memory,
        "vus": vus,
        "duration": duration,
    }
    deployed = False
    try:
        try:
            deployment = workflow.deploy_application(dict(application))
            deployed = True
            result["deployment"].update(deployment, status="READY", ready_replicas=application.get("replicas"))
        except (RuntimeError, OSError) as error:
            diagnostics = _diagnose_deployment(application)
            result["deployment"].update(
                status="FAILED",
                failure_reason=_classify_failure(diagnostics),
                error=str(error),
                diagnostics=diagnostics,
            )
            result["status"] = "FAILED"
            result["baseline"] = {"status": "SKIPPED"}
            result["metrics_status"] = "UNAVAILABLE"
            return result

        baseline_workload = _run_k6(application, vus, duration)
        baseline_metrics = _collect(application, None, baseline_workload)
        result["baseline"] = {"status": "COMPLETED", "metrics": baseline_metrics}
        result["metrics_status"] = "AVAILABLE" if baseline_metrics.get("metrics_available") else "UNAVAILABLE"

        parameters = workflow.generate_environment_parameters(
            workflow.prepare_llm_application(application),
            list(CHAOS_ENVIRONMENTS),
            available_cpu,
            available_memory,
            vus,
            duration,
        )
        valid, message = workflow.validate_environment_parameters(
            parameters, application, list(CHAOS_ENVIRONMENTS),
        )
        if not valid:
            raise RuntimeError(f"Chaos parameter validation failed: {message}")
        chaos_parameters = workflow.build_chaos_parameters(
            application, list(CHAOS_ENVIRONMENTS), parameters, duration,
        )

        for environment in ("Pod Kill", "CPU Stress", "Memory Stress"):
            if environment not in selected_experiments:
                continue
            started = time.time()
            experiment = {"name": environment.lower().replace(" ", "_"), "status": "FAILED", "parameters": chaos_parameters[environment]}
            try:
                path = _write_chaos_file(application, environment, chaos_parameters[environment])
                workflow.run_command(["kubectl", "apply", "--dry-run=server", "-f", path])
                if environment == "Pod Kill":
                    recovery = workflow.execute_pod_kill(application, path)
                    workload = {}
                else:
                    duration_seconds = workflow.parse_duration(duration)
                    workflow.apply_chaos_file(path)
                    stress_started = time.time()
                    workload = _run_k6(application, vus, duration)
                    remaining_seconds = duration_seconds - (time.time() - stress_started)
                    if remaining_seconds > 0:
                        time.sleep(remaining_seconds)
                    stress_duration = time.time() - stress_started
                    cleanup_started = time.time()
                    workflow.cleanup_chaos(application.get("namespace", "default"), ["stresschaos"])
                    cleanup_duration = time.time() - cleanup_started
                    readiness_started = time.time()
                    recovered = workflow.wait_for_application_recovery(application)
                    recovery = {
                        "environment": environment,
                        "recovered": recovered,
                        "stress_duration_seconds": stress_duration,
                        "cleanup_duration_seconds": cleanup_duration,
                        "readiness_recovery_seconds": time.time() - readiness_started,
                    }
                experiment.update(status="COMPLETED", metrics=_collect(application, recovery, workload), recovery=recovery)
            except (RuntimeError, OSError) as error:
                experiment.update(status="FAILED", failure_reason=str(error))
                workflow.cleanup_chaos(application.get("namespace", "default"), ["podchaos", "stresschaos"])
            result["experiments"].append(experiment)

        traffic = {"name": "traffic_stress", "status": "FAILED", "parameters": {"vus": vus, "duration": duration}}
        if "Traffic Stress" in selected_experiments:
            try:
                workload = _run_k6(application, vus, duration)
                traffic.update(status="COMPLETED", metrics=_collect(application, None, workload))
            except (RuntimeError, OSError) as error:
                traffic["failure_reason"] = str(error)
        result["experiments"].append(traffic)
        result["status"] = "COMPLETED" if all(item["status"] == "COMPLETED" for item in result["experiments"]) else "FAILED"
        return result
    finally:
        try:
            workflow.cleanup_chaos(application.get("namespace", "default"), ["podchaos", "stresschaos"])
            if deployed:
                workflow.cleanup_application(dict(application))
            result["cleanup_status"] = "COMPLETED"
        except RuntimeError as error:
            result["cleanup_status"] = "FAILED"
            result["cleanup_error"] = str(error)