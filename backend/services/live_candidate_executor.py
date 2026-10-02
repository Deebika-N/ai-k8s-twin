"""Adapter that connects the optimization controller to the existing workflow."""

import os
from typing import Any, Mapping

from services import environment_orchestrator as workflow
from services.optimization_models import OptimizationRunInput


class LiveCandidateExecutor:
    """Run one candidate using the existing kubectl, Chaos, k6, and metrics code."""

    def __init__(self, application: Mapping[str, Any]) -> None:
        self.application = dict(application)
        self.deployed = False

    @staticmethod
    def _write_single_chaos_file(application: Mapping[str, Any], environment: str, parameters: Mapping[str, Any]) -> str:
        os.makedirs(workflow.GENERATED_DIRECTORY, exist_ok=True)
        filename = os.path.join(
            workflow.GENERATED_DIRECTORY,
            f"{application['name']}-{environment.lower().replace(' ', '-')}.yaml",
        )
        content = workflow.generate_chaos_yaml(environment, parameters[environment])
        with open(filename, "w", encoding="utf-8") as file:
            file.write(content)
        return filename

    @staticmethod
    def _write_compound_chaos_file(application: Mapping[str, Any], generated: list[dict[str, str]]) -> str:
        os.makedirs(workflow.GENERATED_DIRECTORY, exist_ok=True)
        filename = os.path.join(workflow.GENERATED_DIRECTORY, f"{application['name']}-optimization-chaos.yaml")
        workflow.write_yaml_file(filename, generated)
        return filename

    def _update_resources(self, configuration: Mapping[str, Any]) -> dict[str, Any]:
        name = configuration["name"]
        namespace = configuration["namespace"]
        kind = configuration["kind"].lower()
        container = configuration["containers"][0]
        resources = container["resources"]
        requests = resources["requests"]
        limits = resources["limits"]
        workflow.run_command([
            "kubectl", "scale", f"{kind}/{name}",
            "--replicas", str(configuration["replicas"]), "-n", namespace,
        ])
        workflow.run_command([
            "kubectl", "set", "resources", f"{kind}/{name}",
            "-c", container["name"],
            f"--requests=cpu={requests['cpu']},memory={requests['memory']}",
            f"--limits=cpu={limits['cpu']},memory={limits['memory']}",
            "-n", namespace,
        ])
        output = workflow.run_command([
            "kubectl", "rollout", "status", f"{kind}/{name}",
            "-n", namespace, "--timeout=180s",
        ])
        return {"status": "deployed_and_ready", "output": output}

    def _deploy(self, configuration: Mapping[str, Any]) -> dict[str, Any]:
        if not self.deployed:
            result = workflow.deploy_application(configuration)
            self.deployed = True
            return result
        return self._update_resources(configuration)

    def _execute_single_environment(
        self,
        application: Mapping[str, Any],
        environment: str,
        path: str,
        run_input: OptimizationRunInput,
    ) -> dict[str, Any]:
        import time

        started = time.time()
        workflow.apply_chaos_file(path)
        if application.get("name") in {"currencyservice", "productcatalogservice", "adservice", "shippingservice"}:
            workload = workflow.run_k6_grpc(application, run_input.vus, run_input.duration)
        else:
            workload = workflow.run_k6_http(application, run_input.vus, run_input.duration)
        time.sleep(workflow.parse_duration(run_input.duration))
        resource_type = "stresschaos" if environment in {"CPU Stress", "Memory Stress"} else "networkchaos"
        cleanup_started = time.time()
        workflow.cleanup_chaos(application["namespace"], [resource_type])
        cleanup_duration = time.time() - cleanup_started
        readiness_started = time.time()
        recovered = workflow.wait_for_application_recovery(application)
        return {
            "environment": environment,
            "status": "completed",
            "recovered": recovered,
            "stress_duration_seconds": time.time() - started,
            "cleanup_duration_seconds": cleanup_duration,
            "readiness_recovery_seconds": time.time() - readiness_started,
            "k6": workload,
        }

    def run_candidate(
        self,
        configuration: Mapping[str, Any],
        run_input: OptimizationRunInput,
        iteration: int,
    ) -> Mapping[str, Any]:
        deployment = self._deploy(configuration)
        application = dict(configuration)
        parameters = workflow.generate_environment_parameters(
            application=workflow.prepare_llm_application(application),
            environments=list(run_input.selected_environments),
            cpu=run_input.available_cpu,
            memory=run_input.available_memory,
            vus=run_input.vus,
            duration=run_input.duration,
        )
        valid, message = workflow.validate_environment_parameters(
            parameters, application, list(run_input.selected_environments),
        )
        if not valid:
            raise RuntimeError(f"Chaos parameter validation failed: {message}")
        chaos_parameters = workflow.build_chaos_parameters(
            application, list(run_input.selected_environments), parameters, run_input.duration,
        )
        chaos_results: dict[str, Any] = {}
        workload_results: dict[str, Any] = {}
        recovery_result: dict[str, Any] = {}

        if "Pod Kill" in run_input.selected_environments:
            path = self._write_single_chaos_file(application, "Pod Kill", chaos_parameters)
            chaos_results["Pod Kill"] = workflow.execute_pod_kill(application, path)
            recovery_result = chaos_results["Pod Kill"]

        compound = set(workflow.FIVE_CHAOS_ENVIRONMENTS)
        selected = set(run_input.selected_environments)
        if compound.issubset(selected):
            generated = workflow.generate_compound_chaos_yaml(
                workflow.FIVE_CHAOS_ENVIRONMENTS,
                {name: chaos_parameters[name] for name in workflow.FIVE_CHAOS_ENVIRONMENTS},
            )
            path = self._write_compound_chaos_file(application, generated)
            result = workflow.execute_five_chaos(application, path, run_input.duration, run_input.vus)
            chaos_results["compound"] = result
            workload_results = result.get("k6", {})
            recovery_result = result
        else:
            for environment in sorted(selected & compound):
                path = self._write_single_chaos_file(application, environment, chaos_parameters)
                result = self._execute_single_environment(application, environment, path, run_input)
                chaos_results[environment] = result
                workload_results = result.get("k6", {})
                recovery_result = result

        if "Node Failure" in run_input.selected_environments:
            path = self._write_single_chaos_file(application, "Node Failure", chaos_parameters)
            chaos_results["Node Failure"] = workflow.execute_node_failure(application, path, run_input.duration)

        metrics = workflow.collect_result_metrics(application, recovery_result, workload_results)
        return {
            "deployment": deployment,
            "chaos_parameters": chaos_parameters,
            "chaos_results": chaos_results,
            "workload_results": workload_results,
            "metrics": metrics,
            "iteration": iteration,
        }