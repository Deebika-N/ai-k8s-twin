"""CLI entry point for bounded Kubernetes configuration optimization."""

import argparse
import json
import sys
from typing import Any

from services import environment_orchestrator as workflow
from services.optimization_policy import OptimizationConstraints, ResourceBounds
from services.optimization_runner import OptimizationRunner, OptimizationSettings
from services.configuration_optimizer import GroqConfigurationProposalClient


def _constraints(path: str | None) -> OptimizationConstraints:
    values: dict[str, Any] = {}
    if path:
        with open(path, "r", encoding="utf-8") as file:
            values.update(json.load(file))
    return OptimizationConstraints(**values)


def main() -> int:
    parser = argparse.ArgumentParser(description="Optimize a Kubernetes workload against Chaos requirements")
    parser.add_argument("--repository-url", required=True)
    parser.add_argument("--available-cpu", required=True)
    parser.add_argument("--available-memory", required=True)
    parser.add_argument("--vus", type=int, default=10)
    parser.add_argument("--duration", default="60s")
    parser.add_argument("--max-iterations", type=int, default=4)
    parser.add_argument("--stagnation-limit", type=int, default=2)
    parser.add_argument("--workload", help="Workload name; defaults to the first discovered workload")
    parser.add_argument("--requirements-json", help="JSON file containing optimization constraint thresholds")
    args = parser.parse_args()

    project_path = workflow.clone_repository(args.repository_url)
    resources = []
    for yaml_file in workflow.find_yaml_files(project_path):
        resources.extend(workflow.parse_yaml_file(yaml_file))
    workloads = workflow.get_workloads(workflow.analyze_resources(resources))
    application = next((item for item in workloads if item["name"] == args.workload), None) if args.workload else workloads[0]
    if application is None:
        raise ValueError(f"workload not found: {args.workload}")

    result = OptimizationRunner(
        GroqConfigurationProposalClient(),
        output_directory="backend/generated",
    ).run(
        application,
        available_cpu=args.available_cpu,
        available_memory=args.available_memory,
        vus=args.vus,
        duration=args.duration,
        settings=OptimizationSettings(
            constraints=_constraints(args.requirements_json),
            bounds=ResourceBounds(),
            max_iterations=args.max_iterations,
            stagnation_limit=args.stagnation_limit,
        ),
    )
    print(json.dumps(result, indent=2))
    return 0 if result["final_constraints_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())