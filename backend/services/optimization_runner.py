"""Per-microservice, bounded, backend-controlled optimization loop."""

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from services.configuration_optimizer import GroqConfigurationProposalClient
from services.configuration_validator import ConfigurationValidationError
from services.optimization_policy import (
    OptimizationConstraints,
    ResourceBounds,
    configuration_from_application,
    evaluate_constraints,
    extract_features,
    failed_constraint_count,
    resource_score,
    validate_candidate,
)


ACTIVE_EXPERIMENTS = ("Pod Kill", "CPU Stress", "Memory Stress", "Traffic Stress")


def _default_experiment_runner(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from services.selected_experiment import run_selected_application

    return run_selected_application(*args, **kwargs)


@dataclass(frozen=True)
class OptimizationSettings:
    constraints: OptimizationConstraints = field(default_factory=OptimizationConstraints)
    bounds: ResourceBounds = field(default_factory=ResourceBounds)
    max_iterations: int = 4
    stagnation_limit: int = 2
    score_weights: Mapping[str, float] = field(default_factory=lambda: {"cpu": 0.4, "memory": 0.4, "replicas": 0.2})


def _key(configuration: Mapping[str, Any]) -> str:
    return json.dumps(dict(configuration), sort_keys=True, separators=(",", ":"))


def _candidate_application(application: Mapping[str, Any], configuration: Mapping[str, Any]) -> dict[str, Any]:
    candidate = copy.deepcopy(dict(application))
    candidate["replicas"] = configuration["replicas"]
    containers = candidate.get("containers", [])
    containers[0]["resources"] = {
        "requests": {"cpu": configuration["cpu_request"], "memory": configuration["memory_request"]},
        "limits": {"cpu": configuration["cpu_limit"], "memory": configuration["memory_limit"]},
    }
    resource = copy.deepcopy(application.get("resource_override"))
    if resource is None:
        from services import environment_orchestrator as workflow

        resource = workflow.find_application_resource(application)
    resource = copy.deepcopy(resource)
    resource.setdefault("spec", {})["replicas"] = configuration["replicas"]
    resource_containers = resource["spec"]["template"]["spec"].get("containers", [])
    target_name = containers[0].get("name")
    for resource_container in resource_containers:
        if resource_container.get("name") == target_name:
            resource_container.setdefault("resources", {})["requests"] = {
                "cpu": configuration["cpu_request"],
                "memory": configuration["memory_request"],
            }
            resource_container.setdefault("resources", {})["limits"] = {
                "cpu": configuration["cpu_limit"],
                "memory": configuration["memory_limit"],
            }
    candidate["resource_override"] = resource
    return candidate


class OptimizationRunner:
    def __init__(
        self,
        proposal_client: Any,
        *,
        experiment_runner: Callable[..., dict[str, Any]] | None = None,
        output_directory: str | Path = "backend/generated",
        logger: Callable[[str], None] = print,
    ) -> None:
        self.proposal_client = proposal_client
        self.experiment_runner = experiment_runner or _default_experiment_runner
        self.output_directory = Path(output_directory)
        self.logger = logger

    def run(
        self,
        application: Mapping[str, Any],
        *,
        available_cpu: str,
        available_memory: str,
        vus: int,
        duration: str,
        settings: OptimizationSettings = OptimizationSettings(),
    ) -> dict[str, Any]:
        initial = validate_candidate(
            configuration_from_application(application),
            settings.bounds,
            available_cpu,
            available_memory,
        )
        current = initial
        history: list[dict[str, Any]] = []
        seen: set[str] = set()
        best: dict[str, Any] | None = None
        best_score: float | None = None
        previous_failures: int | None = None
        stagnation = 0
        stop_reason = "MAX_ITERATIONS"
        application_name = str(application.get("name"))

        for iteration in range(settings.max_iterations):
            current_key = _key(current)
            if current_key in seen:
                stop_reason = "REPEATED_CONFIGURATION"
                break
            seen.add(current_key)
            self.logger(f"[optimization {iteration}] testing {application_name}: {current}")
            candidate_result = self.experiment_runner(
                _candidate_application(application, current),
                available_cpu,
                available_memory,
                vus,
                duration,
                ACTIVE_EXPERIMENTS,
            )
            result_file = self.output_directory / f"{application_name}-optimization-iteration-{iteration}.json"
            result_file.parent.mkdir(parents=True, exist_ok=True)
            result_file.write_text(json.dumps(candidate_result, indent=2), encoding="utf-8")
            features = extract_features(candidate_result)
            evaluation = evaluate_constraints(features, settings.constraints)
            score = resource_score(current, settings.bounds, settings.score_weights)
            entry = {
                "iteration": iteration,
                "configuration": current,
                "constraints_passed": evaluation["status"] == "PASS",
                "constraint_evaluation": evaluation,
                "features": features,
                "resource_score": score,
                "result_file": str(result_file),
            }
            history.append(entry)
            self.logger(f"[optimization {iteration}] constraints={evaluation['status']} score={score:.4f}")

            if evaluation["status"] == "PASS" and (best_score is None or score < best_score):
                best = current
                best_score = score

            if candidate_result.get("status") == "FAILED":
                stop_reason = "EXPERIMENT_FAILED"
                break
            if any(check["status"] == "UNKNOWN" for check in evaluation["checks"]):
                stop_reason = "REQUIRED_METRICS_UNAVAILABLE"
                break
            if iteration == settings.max_iterations - 1:
                stop_reason = "MAX_ITERATIONS"
                break
            if best is not None and evaluation["status"] != "PASS":
                stop_reason = "NO_LOWER_RESOURCE_PASSING_CANDIDATE"
                break

            failures = failed_constraint_count(evaluation)
            if previous_failures is not None and failures >= previous_failures:
                stagnation += 1
            else:
                stagnation = 0
            previous_failures = failures
            if stagnation >= settings.stagnation_limit:
                stop_reason = "NO_MEANINGFUL_IMPROVEMENT"
                break

            try:
                proposal = self.proposal_client.propose(
                    current_configuration=current,
                    result=candidate_result,
                    features=features,
                    constraints=settings.constraints.as_mapping(),
                    history=history,
                )
                current = validate_candidate(proposal, settings.bounds, available_cpu, available_memory)
            except (ConfigurationValidationError, RuntimeError, ValueError) as error:
                self.logger(f"[optimization] proposal rejected: {error}")
                stop_reason = "INVALID_OR_FAILED_PROPOSAL"
                break

            if best is not None and resource_score(current, settings.bounds, settings.score_weights) >= best_score:
                stop_reason = "NO_BETTER_CANDIDATE"
                break

        final_configuration = best or current
        final_entry = next((item for item in reversed(history) if item["configuration"] == final_configuration), None)
        final_constraints = final_entry["constraint_evaluation"] if final_entry else {"status": "UNKNOWN", "checks": []}
        final = {
            "application": application_name,
            "status": "COMPLETED" if final_constraints.get("status") == "PASS" else "STOPPED",
            "iterations": history,
            "initial_configuration": initial,
            "final_configuration": final_configuration,
            "constraints": settings.constraints.as_mapping(),
            "final_constraints_passed": final_constraints.get("status") == "PASS",
            "optimization_stop_reason": stop_reason,
            "resource_score": resource_score(final_configuration, settings.bounds, settings.score_weights),
        }
        final_file = self.output_directory / f"{application_name}-optimization-result.json"
        final_file.write_text(json.dumps(final, indent=2), encoding="utf-8")
        self.logger(f"[optimization] stopped: {stop_reason}")
        self.logger(f"[optimization] final configuration: {json.dumps(final_configuration)}")
        return final