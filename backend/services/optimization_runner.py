"""Per-microservice, bounded, backend-controlled optimization loop."""

import copy
import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping

from services.configuration_optimizer import GroqConfigurationProposalClient
from services.configuration_validator import ConfigurationValidationError
from services.optimization_policy import (
    OptimizationConstraints,
    ResourceBounds,
    ResourceSearchFloors,
    configuration_from_application,
    cpu_millicores,
    evaluate_constraints,
    extract_features,
    failed_constraint_count,
    memory_bytes,
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
    search_floors: ResourceSearchFloors = field(default_factory=ResourceSearchFloors)
    max_iterations: int = 4
    stagnation_limit: int = 2
    score_weights: Mapping[str, float] = field(default_factory=lambda: {"cpu": 0.4, "memory": 0.4, "replicas": 0.2})


def _key(configuration: Mapping[str, Any]) -> str:
    return json.dumps(dict(configuration), sort_keys=True, separators=(",", ":"))


def _failed_constraints(evaluation: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Expose failed checks without duplicating the policy evaluator."""
    return [
        {
            "name": check["name"],
            "observed": check.get("measured"),
            "limit": check.get("limit"),
        }
        for check in evaluation.get("checks", [])
        if check.get("status") == "FAIL"
    ]


def _has_failed_constraint_improvement(
    previous: Mapping[str, Any], current: Mapping[str, Any],
) -> bool:
    """Return true when a currently failing measured check strictly improves."""
    previous_checks = {
        check.get("name"): check
        for check in previous.get("checks", [])
    }
    for check in current.get("checks", []):
        if check.get("status") != "FAIL":
            continue
        old = previous_checks.get(check.get("name"), {}).get("measured")
        new = check.get("measured")
        previous_check = previous_checks.get(check.get("name"), {})
        if (
            check.get("name") == "pod_kill_recovery_seconds"
            and previous_check.get("recovered") is False
            and check.get("recovered") is True
            and isinstance(new, (int, float))
        ):
            return True
        if not isinstance(old, (int, float)) or not isinstance(new, (int, float)):
            continue
        if check.get("name") == "available_replicas":
            if new > old:
                return True
        elif new < old:
            return True
    return False


def _has_resource_increase(
    previous: Mapping[str, Any], current: Mapping[str, Any],
) -> bool:
    """Detect an increase in any tuned field, including requests excluded from score."""
    return any((
        current["replicas"] > previous["replicas"],
        cpu_millicores(current["cpu_request"]) > cpu_millicores(previous["cpu_request"]),
        cpu_millicores(current["cpu_limit"]) > cpu_millicores(previous["cpu_limit"]),
        memory_bytes(current["memory_request"]) > memory_bytes(previous["memory_request"]),
        memory_bytes(current["memory_limit"]) > memory_bytes(previous["memory_limit"]),
    ))


def _is_genuine_resource_reduction(
    incumbent: Mapping[str, Any], candidate: Mapping[str, Any],
) -> bool:
    incumbent_values = (
        incumbent["replicas"],
        cpu_millicores(incumbent["cpu_request"]),
        cpu_millicores(incumbent["cpu_limit"]),
        memory_bytes(incumbent["memory_request"]),
        memory_bytes(incumbent["memory_limit"]),
    )
    candidate_values = (
        candidate["replicas"],
        cpu_millicores(candidate["cpu_request"]),
        cpu_millicores(candidate["cpu_limit"]),
        memory_bytes(candidate["memory_request"]),
        memory_bytes(candidate["memory_limit"]),
    )
    return (
        all(new <= old for old, new in zip(incumbent_values, candidate_values))
        and any(new < old for old, new in zip(incumbent_values, candidate_values))
    )


def _persist_iteration_artifact(
    result_file: Path,
    candidate_result: Mapping[str, Any],
    entry: Mapping[str, Any],
) -> None:
    artifact = copy.deepcopy(dict(candidate_result))
    artifact["optimization"] = {
        key: value for key, value in entry.items()
        if key != "result_file"
    }
    result_file.write_text(json.dumps(artifact, indent=2), encoding="utf-8")


def _at_search_lower_bounds(configuration: Mapping[str, Any], floors: ResourceSearchFloors) -> bool:
    return all((
        configuration["replicas"] == floors.min_replicas,
        cpu_millicores(configuration["cpu_request"]) == floors.min_cpu_request_m,
        cpu_millicores(configuration["cpu_limit"]) == floors.min_cpu_limit_m,
        memory_bytes(configuration["memory_request"]) == floors.min_memory_request_bytes,
        memory_bytes(configuration["memory_limit"]) == floors.min_memory_limit_bytes,
    ))


def _minimum_resource_score(
    bounds: ResourceBounds,
    floors: ResourceSearchFloors,
    weights: Mapping[str, float],
) -> float:
    return (
        weights["cpu"] * floors.min_cpu_limit_m / bounds.max_cpu_limit_m
        + weights["memory"] * floors.min_memory_limit_bytes / bounds.max_memory_limit_bytes
        + weights["replicas"] * floors.min_replicas / bounds.max_replicas
    )


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
    updated = False
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
            updated = True
    if not updated:
        raise ConfigurationValidationError(
            f"candidate container {target_name!r} was not found in its deployment resource"
        )
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
            allow_below_minimum_resources=True,
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
                "experiment_status": candidate_result.get("status", "UNKNOWN"),
                "constraints_passed": (
                    candidate_result.get("status") == "COMPLETED"
                    and evaluation["status"] == "PASS"
                ),
                "constraint_evaluation": evaluation,
                "failed_constraints": _failed_constraints(evaluation),
                "features": features,
                "resource_score": score,
                "result_file": str(result_file),
                "proposal_goal": None,
                "proposed_configuration": None,
                "proposal_decision": None,
            }
            history.append(entry)
            self.logger(f"[optimization {iteration}] constraints={evaluation['status']} score={score:.4f}")

            if (
                candidate_result.get("status") == "COMPLETED"
                and evaluation["status"] == "PASS"
                and (
                    best_score is None
                    or score < best_score
                    or (
                        score == best_score
                        and best is not None
                        and _is_genuine_resource_reduction(best, current)
                    )
                )
            ):
                best = current
                best_score = score

            if candidate_result.get("status") == "FAILED":
                stop_reason = "EXPERIMENT_FAILED"
                break
            if any(check["status"] == "UNKNOWN" for check in evaluation["checks"]):
                stop_reason = "REQUIRED_METRICS_UNAVAILABLE"
                break
            if evaluation["status"] == "PASS" and (
                _at_search_lower_bounds(current, settings.search_floors)
                or score <= _minimum_resource_score(
                    settings.bounds, settings.search_floors, settings.score_weights,
                )
            ):
                stop_reason = "LOWEST_RESOURCE_BOUNDS_REACHED"
                break
            if iteration == settings.max_iterations - 1:
                stop_reason = "MAX_ITERATIONS"
                break
            if (
                len(history) > 1
                and _has_resource_increase(history[-2]["configuration"], current)
                and evaluation["status"] != "PASS"
                and not _has_failed_constraint_improvement(
                    history[-2]["constraint_evaluation"], evaluation,
                )
            ):
                stop_reason = "NON_IMPROVING_HIGHER_RESOURCE_CANDIDATE"
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
                proposal_goal = (
                    "MINIMIZE_RESOURCES"
                    if evaluation["status"] == "PASS"
                    else "ADDRESS_FAILED_CONSTRAINTS"
                )
                proposal_bounds = settings.bounds
                if proposal_goal == "MINIMIZE_RESOURCES":
                    proposal_bounds = replace(
                        settings.bounds,
                        min_replicas=settings.search_floors.min_replicas,
                        min_cpu_request_m=settings.search_floors.min_cpu_request_m,
                        min_cpu_limit_m=settings.search_floors.min_cpu_limit_m,
                        min_memory_request_bytes=settings.search_floors.min_memory_request_bytes,
                        min_memory_limit_bytes=settings.search_floors.min_memory_limit_bytes,
                    )
                entry["proposal_goal"] = proposal_goal
                proposal = self.proposal_client.propose(
                    current_configuration=current,
                    result=candidate_result,
                    features=features,
                    constraints=settings.constraints.as_mapping(),
                    history=history,
                    constraint_evaluation=evaluation,
                    failed_constraints=entry["failed_constraints"],
                    resource_score=score,
                    bounds=asdict(proposal_bounds),
                    proposal_goal=proposal_goal,
                )
                entry["proposed_configuration"] = dict(proposal)
                proposed = validate_candidate(proposal, proposal_bounds, available_cpu, available_memory)
                if _key(proposed) in seen:
                    stop_reason = "REPEATED_CONFIGURATION"
                    entry["proposal_decision"] = {
                        "status": "REJECTED",
                        "reason": "repeated_configuration",
                    }
                    _persist_iteration_artifact(result_file, candidate_result, entry)
                    break
                if proposal_goal == "MINIMIZE_RESOURCES":
                    if _has_resource_increase(current, proposed):
                        stop_reason = "NO_BETTER_CANDIDATE"
                        entry["proposal_decision"] = {
                            "status": "REJECTED",
                            "reason": "resource_increase",
                        }
                        _persist_iteration_artifact(result_file, candidate_result, entry)
                        break
                    if not _is_genuine_resource_reduction(current, proposed):
                        stop_reason = "NO_BETTER_CANDIDATE"
                        entry["proposal_decision"] = {
                            "status": "REJECTED",
                            "reason": "no_resource_reduction",
                        }
                        _persist_iteration_artifact(result_file, candidate_result, entry)
                        break
                entry["proposal_decision"] = {
                    "status": "ACCEPTED_FOR_TESTING",
                    "reason": "bounded_genuine_resource_reduction"
                    if proposal_goal == "MINIMIZE_RESOURCES"
                    else "bounded_failed_constraint_proposal",
                }
                current = proposed
            except (ConfigurationValidationError, RuntimeError, ValueError) as error:
                self.logger(f"[optimization] proposal rejected: {error}")
                entry["proposal_decision"] = {
                    "status": "REJECTED",
                    "reason": str(error),
                }
                _persist_iteration_artifact(result_file, candidate_result, entry)
                stop_reason = "INVALID_OR_FAILED_PROPOSAL"
                break

            if (
                proposal_goal != "MINIMIZE_RESOURCES"
                and best is not None
                and resource_score(current, settings.bounds, settings.score_weights) >= best_score
            ):
                entry["proposal_decision"] = {
                    "status": "REJECTED",
                    "reason": "NO_BETTER_CANDIDATE",
                }
                _persist_iteration_artifact(result_file, candidate_result, entry)
                stop_reason = "NO_BETTER_CANDIDATE"
                break
            _persist_iteration_artifact(result_file, candidate_result, entry)

        final_configuration = best or current
        final_entry = next((
            item for item in reversed(history)
            if (
                item["configuration"] == final_configuration
                and item.get("experiment_status") == "COMPLETED"
            )
        ), None)
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
