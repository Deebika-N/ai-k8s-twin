"""Bounded orchestration loop for Kubernetes configuration optimization."""

from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol

from services.configuration_validator import validate_workload_configuration
from services.constraint_engine import evaluate_constraints
from services.optimization_models import ConstraintRequirements, OptimizationRunInput


class CandidateExecutor(Protocol):
    def run_candidate(self, configuration: Mapping[str, Any], run_input: OptimizationRunInput, iteration: int) -> Mapping[str, Any]: ...


class Optimizer(Protocol):
    def propose(self, **kwargs: Any) -> dict[str, Any]: ...


class RunStore(Protocol):
    def create_run(self) -> tuple[str, Any]: ...
    def write_input(self, run_directory: Any, value: Any) -> None: ...
    def write_iteration(self, run_directory: Any, iteration: int, value: Any) -> Any: ...
    def write_final(self, run_directory: Any, value: Any) -> None: ...


def _configuration_key(configuration: Mapping[str, Any]) -> str:
    import json

    return json.dumps(dict(configuration), sort_keys=True, separators=(",", ":"))


class OptimizationController:
    def __init__(
        self,
        executor: CandidateExecutor,
        optimizer: Optimizer,
        store: RunStore,
        logger: Callable[[str], None] = print,
    ) -> None:
        self.executor = executor
        self.optimizer = optimizer
        self.store = store
        self.logger = logger

    def run(
        self,
        run_input: OptimizationRunInput,
        initial_configuration: Mapping[str, Any],
    ) -> dict[str, Any]:
        run_id, run_directory = self.store.create_run()
        self.store.write_input(run_directory, run_input.as_mapping())
        self.logger(f"[run] started: {run_id}")

        initial = validate_workload_configuration(
            initial_configuration,
            available_cpu=run_input.available_cpu,
            available_memory=run_input.available_memory,
        )
        current = initial
        immutable = initial
        seen: set[str] = set()
        final: dict[str, Any] = {"run_id": run_id, "status": "FAILED", "iterations": []}

        for iteration in range(run_input.max_iterations):
            started = datetime.now(timezone.utc).isoformat()
            self.logger(f"[iteration {iteration}] validating candidate")
            validate_workload_configuration(
                current,
                available_cpu=run_input.available_cpu,
                available_memory=run_input.available_memory,
                immutable=immutable,
            )
            key = _configuration_key(current)
            if key in seen:
                final["status"] = "NO_PROGRESS"
                break
            seen.add(key)

            self.logger(f"[iteration {iteration}] executing candidate")
            execution = dict(self.executor.run_candidate(current, run_input, iteration))
            metrics = dict(execution.get("metrics", {}))
            evaluation = evaluate_constraints(metrics, run_input.requirements)
            record = {
                "iteration": iteration,
                "configuration": current,
                "requirements": run_input.requirements.__dict__,
                "chaos_parameters": execution.get("chaos_parameters", {}),
                "deployment": execution.get("deployment", {}),
                "chaos_results": execution.get("chaos_results", {}),
                "workload_results": execution.get("workload_results", {}),
                "metrics": metrics,
                "constraint_evaluation": evaluation,
                "started_at": started,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
            self.store.write_iteration(run_directory, iteration, record)
            final["iterations"].append(record)
            self.logger(f"[iteration {iteration}] constraint status: {evaluation['status']}")

            if evaluation["status"] == "PASS":
                final["status"] = "SUCCESS"
                final["configuration"] = current
                self.logger("[run] optimal configuration found")
                break

            if iteration == run_input.max_iterations - 1:
                final["status"] = "MAX_ITERATIONS_REACHED"
                break

            self.logger(f"[iteration {iteration}] asking Groq for an optimized configuration")
            current = self.optimizer.propose(
                current_configuration=current,
                initial_configuration=initial,
                requirements=run_input.requirements.__dict__,
                selected_environments=run_input.selected_environments,
                chaos_parameters=execution.get("chaos_parameters", {}),
                metrics=metrics,
                evaluation=evaluation,
                previous_configurations=[item["configuration"] for item in final["iterations"]],
            )
            validate_workload_configuration(
                current,
                available_cpu=run_input.available_cpu,
                available_memory=run_input.available_memory,
                immutable=immutable,
            )

        self.store.write_final(run_directory, final)
        return final