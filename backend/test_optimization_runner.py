import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from services.configuration_optimizer import GroqConfigurationProposalClient
from services import environment_orchestrator as workflow
from services.optimization_policy import (
    OptimizationConstraints,
    evaluate_constraints,
    extract_features,
)
from services.optimization_runner import OptimizationRunner, OptimizationSettings


RESOURCE = {
    "apiVersion": "apps/v1",
    "kind": "Deployment",
    "metadata": {"name": "adservice", "namespace": "default"},
    "spec": {"replicas": 2, "template": {"spec": {"containers": [{"name": "adservice", "image": "example/ad:v1"}]}}},
}

APPLICATION = {
    "kind": "Deployment",
    "name": "adservice",
    "namespace": "default",
    "replicas": 2,
    "labels": {},
    "selector": {},
    "containers": [{
        "name": "adservice",
        "image": "example/ad:v1",
        "resources": {"requests": {"cpu": "500m", "memory": "512Mi"}, "limits": {"cpu": "1000m", "memory": "1Gi"}},
    }],
    "resource_override": RESOURCE,
}


def passing_result(configuration, iteration):
    return {
        "status": "COMPLETED",
        "baseline": {"metrics": {"p95_latency_ms": 100, "p99_latency_ms": 150, "error_rate_percent": 0, "pod_kill_recovery_seconds": 5, "available_replicas": configuration["replicas"], "oom_killed": False}},
        "experiments": [],
    }


class Proposal:
    def propose(self, **kwargs):
        return {"replicas": 1, "cpu_request": "250m", "cpu_limit": "500m", "memory_request": "256Mi", "memory_limit": "512Mi"}


class SequenceProposal:
    def __init__(self, configurations):
        self.configurations = iter(configurations)
        self.calls = []

    def propose(self, **kwargs):
        self.calls.append(kwargs)
        return next(self.configurations)


class GroqFakeCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = type("Message", (), {"content": '{"proposed_configuration":{"replicas":2}}'})()
        return type("Response", (), {"choices": [type("Choice", (), {"message": message})()]})()


def failing_result(application, p95=250, recovery=35):
    replicas = application["replicas"]
    return {
        "status": "COMPLETED",
        "baseline": {"metrics": {
            "p95_latency_ms": p95,
            "p99_latency_ms": 150,
            "error_rate_percent": 0,
            "pod_kill_recovery_seconds": recovery,
            "available_replicas": replicas,
            "oom_killed": False,
        }},
        "experiments": [],
    }


def larger_configuration(**overrides):
    return {
        "replicas": 3,
        "cpu_request": "600m",
        "cpu_limit": "1200m",
        "memory_request": "600Mi",
        "memory_limit": "1200Mi",
        **overrides,
    }


class RunnerTests(unittest.TestCase):
    def test_existing_below_minimum_resources_are_used_for_iteration_zero(self):
        application = {
            **APPLICATION,
            "containers": [{
                **APPLICATION["containers"][0],
                "resources": {
                    "requests": {"cpu": "50m", "memory": "64Mi"},
                    "limits": {"cpu": "100m", "memory": "128Mi"},
                },
            }],
        }
        executed = []
        with tempfile.TemporaryDirectory() as directory:
            result = OptimizationRunner(
                SequenceProposal([]),
                experiment_runner=lambda app, *_args: executed.append(app) or passing_result({"replicas": app["replicas"]}, 0),
                output_directory=directory,
                logger=lambda _: None,
            ).run(
                application,
                available_cpu="4",
                available_memory="8Gi",
                vus=1,
                duration="1s",
                settings=OptimizationSettings(max_iterations=1),
            )
        self.assertEqual(executed[0]["containers"][0]["resources"]["requests"]["memory"], "64Mi")
        self.assertEqual(result["initial_configuration"]["memory_request"], "64Mi")

    def test_passing_initial_configuration_enters_groq_guided_minimization(self):
        calls = []

        def execute(application, *_args):
            calls.append(application["replicas"])
            configuration = {
                "replicas": application["replicas"],
                "cpu_request": application["containers"][0]["resources"]["requests"]["cpu"],
                "cpu_limit": application["containers"][0]["resources"]["limits"]["cpu"],
                "memory_request": application["containers"][0]["resources"]["requests"]["memory"],
                "memory_limit": application["containers"][0]["resources"]["limits"]["memory"],
            }
            return passing_result(configuration, len(calls))

        with tempfile.TemporaryDirectory() as directory:
            lower = {
                "replicas": 1,
                "cpu_request": "250m",
                "cpu_limit": "500m",
                "memory_request": "256Mi",
                "memory_limit": "512Mi",
            }
            proposal = SequenceProposal([lower, lower])
            result = OptimizationRunner(proposal, experiment_runner=execute, output_directory=directory, logger=lambda _: None).run(
                APPLICATION,
                available_cpu="4",
                available_memory="8Gi",
                vus=1,
                duration="1s",
                settings=OptimizationSettings(constraints=OptimizationConstraints(max_p99_latency_ms=None), max_iterations=3),
            )
            self.assertEqual(result["final_constraints_passed"], True)
            self.assertEqual(result["final_configuration"]["replicas"], 1)
            self.assertEqual(result["optimization_stop_reason"], "REPEATED_CONFIGURATION")
            self.assertEqual(len(result["iterations"]), 2)
            self.assertEqual(len(proposal.calls), 2)
            self.assertEqual(proposal.calls[0]["proposal_goal"], "MINIMIZE_RESOURCES")
            self.assertEqual(proposal.calls[1]["proposal_goal"], "MINIMIZE_RESOURCES")
            self.assertTrue(Path(directory, "adservice-optimization-result.json").exists())
        self.assertEqual(calls, [2, 1])

    def test_equal_score_request_reduction_is_tested_and_selected_when_passing(self):
        lower_requests = {
            "replicas": 2,
            "cpu_request": "400m",
            "cpu_limit": "1000m",
            "memory_request": "256Mi",
            "memory_limit": "1Gi",
        }
        tested = []

        def execute(app, *_args):
            tested.append(app)
            return passing_result({"replicas": app["replicas"]}, len(tested))

        with tempfile.TemporaryDirectory() as directory:
            result = OptimizationRunner(
                SequenceProposal([lower_requests]),
                experiment_runner=execute,
                output_directory=directory,
                logger=lambda _: None,
            ).run(
                APPLICATION,
                available_cpu="4",
                available_memory="8Gi",
                vus=1,
                duration="1s",
                settings=OptimizationSettings(max_iterations=2),
            )
            artifact_path = Path(result["iterations"][0]["result_file"])
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))

        self.assertEqual(len(tested), 2)
        self.assertEqual(result["iterations"][0]["resource_score"], result["iterations"][1]["resource_score"])
        self.assertEqual(result["final_configuration"], lower_requests)
        self.assertEqual(result["iterations"][0]["proposed_configuration"], lower_requests)
        self.assertEqual(result["iterations"][0]["proposal_decision"]["status"], "ACCEPTED_FOR_TESTING")
        self.assertEqual(artifact["optimization"]["proposed_configuration"], lower_requests)
        self.assertEqual(artifact["optimization"]["proposal_decision"]["status"], "ACCEPTED_FOR_TESTING")

    def test_lower_limit_and_replica_proposal_with_lower_score_is_tested(self):
        lower = {
            "replicas": 1,
            "cpu_request": "250m",
            "cpu_limit": "500m",
            "memory_request": "256Mi",
            "memory_limit": "512Mi",
        }
        tested = []
        with tempfile.TemporaryDirectory() as directory:
            result = OptimizationRunner(
                SequenceProposal([lower]),
                experiment_runner=lambda app, *_args: (
                    tested.append(app)
                    or passing_result({"replicas": app["replicas"]}, len(tested))
                ),
                output_directory=directory,
                logger=lambda _: None,
            ).run(
                APPLICATION,
                available_cpu="4",
                available_memory="8Gi",
                vus=1,
                duration="1s",
                settings=OptimizationSettings(max_iterations=2),
            )
        self.assertEqual(len(tested), 2)
        self.assertEqual(result["iterations"][1]["configuration"], lower)
        self.assertLess(result["iterations"][1]["resource_score"], result["iterations"][0]["resource_score"])

    def test_minimization_rejects_any_resource_increase(self):
        increase = {
            "replicas": 2,
            "cpu_request": "400m",
            "cpu_limit": "1200m",
            "memory_request": "512Mi",
            "memory_limit": "1Gi",
        }
        tested = []
        with tempfile.TemporaryDirectory() as directory:
            result = OptimizationRunner(
                SequenceProposal([increase]),
                experiment_runner=lambda app, *_args: (
                    tested.append(app)
                    or passing_result({"replicas": app["replicas"]}, len(tested))
                ),
                output_directory=directory,
                logger=lambda _: None,
            ).run(APPLICATION, available_cpu="4", available_memory="8Gi", vus=1, duration="1s")
        self.assertEqual(len(tested), 1)
        self.assertEqual(result["optimization_stop_reason"], "NO_BETTER_CANDIDATE")
        self.assertEqual(result["iterations"][0]["proposal_decision"]["reason"], "resource_increase")

    def test_minimization_rejects_unchanged_configuration(self):
        unchanged = {
            "replicas": 2,
            "cpu_request": "500m",
            "cpu_limit": "1000m",
            "memory_request": "512Mi",
            "memory_limit": "1Gi",
        }
        tested = []
        with tempfile.TemporaryDirectory() as directory:
            result = OptimizationRunner(
                SequenceProposal([unchanged]),
                experiment_runner=lambda app, *_args: (
                    tested.append(app)
                    or passing_result({"replicas": app["replicas"]}, len(tested))
                ),
                output_directory=directory,
                logger=lambda _: None,
            ).run(APPLICATION, available_cpu="4", available_memory="8Gi", vus=1, duration="1s")
        self.assertEqual(len(tested), 1)
        self.assertEqual(result["optimization_stop_reason"], "REPEATED_CONFIGURATION")
        self.assertEqual(result["iterations"][0]["proposal_decision"]["reason"], "repeated_configuration")

    def test_failing_lower_resource_candidate_retains_previous_passing_candidate(self):
        lower = {
            "replicas": 1,
            "cpu_request": "250m",
            "cpu_limit": "500m",
            "memory_request": "256Mi",
            "memory_limit": "512Mi",
        }
        proposal = SequenceProposal([lower])
        executions = []

        def execute(app, *_args):
            executions.append(app)
            if len(executions) == 1:
                return passing_result({"replicas": app["replicas"]}, 0)
            return failing_result(app)

        with tempfile.TemporaryDirectory() as directory:
            result = OptimizationRunner(
                proposal,
                experiment_runner=execute,
                output_directory=directory,
                logger=lambda _: None,
            ).run(
                APPLICATION,
                available_cpu="4",
                available_memory="8Gi",
                vus=1,
                duration="1s",
                settings=OptimizationSettings(max_iterations=3),
            )
        self.assertEqual(len(executions), 2)
        self.assertEqual(result["final_configuration"], result["initial_configuration"])
        self.assertTrue(result["final_constraints_passed"])
        self.assertEqual(result["optimization_stop_reason"], "NO_LOWER_RESOURCE_PASSING_CANDIDATE")

    def test_passing_configuration_at_search_lower_bounds_stops_without_proposal(self):
        application = {
            **APPLICATION,
            "replicas": 1,
            "containers": [{
                **APPLICATION["containers"][0],
                "resources": {
                    "requests": {"cpu": "50m", "memory": "32Mi"},
                    "limits": {"cpu": "100m", "memory": "64Mi"},
                },
            }],
        }
        proposal = SequenceProposal([])
        with tempfile.TemporaryDirectory() as directory:
            result = OptimizationRunner(
                proposal,
                experiment_runner=lambda app, *_args: passing_result({"replicas": app["replicas"]}, 0),
                output_directory=directory,
                logger=lambda _: None,
            ).run(
                application,
                available_cpu="4",
                available_memory="8Gi",
                vus=1,
                duration="1s",
                settings=OptimizationSettings(max_iterations=3),
            )
        self.assertTrue(result["final_constraints_passed"])
        self.assertEqual(result["optimization_stop_reason"], "LOWEST_RESOURCE_BOUNDS_REACHED")
        self.assertEqual(proposal.calls, [])

    def test_initial_below_proposal_minimum_enters_minimization_with_lower_search_bounds(self):
        application = {
            **APPLICATION,
            "replicas": 1,
            "containers": [{
                **APPLICATION["containers"][0],
                "resources": {
                    "requests": {"cpu": "100m", "memory": "64Mi"},
                    "limits": {"cpu": "200m", "memory": "128Mi"},
                },
            }],
        }
        lower = {
            "replicas": 1,
            "cpu_request": "75m",
            "cpu_limit": "150m",
            "memory_request": "48Mi",
            "memory_limit": "96Mi",
        }
        proposal = SequenceProposal([lower, lower])
        with tempfile.TemporaryDirectory() as directory:
            result = OptimizationRunner(
                proposal,
                experiment_runner=lambda app, *_args: passing_result({"replicas": app["replicas"]}, 0),
                output_directory=directory,
                logger=lambda _: None,
            ).run(
                application,
                available_cpu="4",
                available_memory="8Gi",
                vus=1,
                duration="1s",
                settings=OptimizationSettings(max_iterations=3),
            )
        self.assertEqual(result["initial_configuration"]["memory_request"], "64Mi")
        self.assertEqual(proposal.calls[0]["proposal_goal"], "MINIMIZE_RESOURCES")
        self.assertEqual(proposal.calls[0]["bounds"]["min_cpu_request_m"], 50)
        self.assertEqual(proposal.calls[0]["bounds"]["min_cpu_limit_m"], 100)
        self.assertEqual(proposal.calls[0]["bounds"]["min_memory_request_bytes"], 32 * 1024**2)
        self.assertEqual(proposal.calls[0]["bounds"]["min_memory_limit_bytes"], 64 * 1024**2)
        self.assertEqual(len(result["iterations"]), 2)

    def test_minimizer_can_experimentally_test_each_new_search_floor(self):
        application = {
            **APPLICATION,
            "replicas": 1,
            "containers": [{
                **APPLICATION["containers"][0],
                "resources": {
                    "requests": {"cpu": "100m", "memory": "64Mi"},
                    "limits": {"cpu": "200m", "memory": "128Mi"},
                },
            }],
        }
        floor_candidate = {
            "replicas": 1,
            "cpu_request": "50m",
            "cpu_limit": "100m",
            "memory_request": "32Mi",
            "memory_limit": "64Mi",
        }
        tested = []
        proposal = SequenceProposal([floor_candidate])
        with tempfile.TemporaryDirectory() as directory:
            result = OptimizationRunner(
                proposal,
                experiment_runner=lambda app, *_args: (
                    tested.append(app)
                    or passing_result({"replicas": app["replicas"]}, len(tested))
                ),
                output_directory=directory,
                logger=lambda _: None,
            ).run(
                application,
                available_cpu="4",
                available_memory="8Gi",
                vus=1,
                duration="1s",
                settings=OptimizationSettings(max_iterations=3),
            )
        self.assertEqual(len(tested), 2)
        self.assertEqual(result["iterations"][1]["configuration"], floor_candidate)
        self.assertEqual(result["final_configuration"], floor_candidate)
        self.assertEqual(result["optimization_stop_reason"], "LOWEST_RESOURCE_BOUNDS_REACHED")

    def test_failed_constraints_are_recorded_and_passed_to_proposal(self):
        proposal = SequenceProposal([larger_configuration()])
        with tempfile.TemporaryDirectory() as directory:
            result = OptimizationRunner(
                proposal,
                experiment_runner=lambda app, *_args: failing_result(app),
                output_directory=directory,
                logger=lambda _: None,
            ).run(
                APPLICATION,
                available_cpu="4",
                available_memory="8Gi",
                vus=1,
                duration="1s",
                settings=OptimizationSettings(max_iterations=2),
            )
        self.assertEqual(
            result["iterations"][0]["failed_constraints"],
            [
                {"name": "p95_latency_ms", "observed": 250.0, "limit": 200.0},
                {"name": "pod_kill_recovery_seconds", "observed": 35.0, "limit": 30.0},
            ],
        )
        self.assertEqual(len(proposal.calls), 1)
        call = proposal.calls[0]
        self.assertEqual(call["proposal_goal"], "ADDRESS_FAILED_CONSTRAINTS")
        self.assertEqual(call["failed_constraints"], result["iterations"][0]["failed_constraints"])
        self.assertEqual(call["constraint_evaluation"], result["iterations"][0]["constraint_evaluation"])
        self.assertEqual(call["resource_score"], result["iterations"][0]["resource_score"])
        self.assertEqual(call["bounds"]["max_replicas"], 4)

    def test_groq_prompt_receives_evaluation_failed_constraints_history_score_and_bounds(self):
        completions = GroqFakeCompletions()
        client = GroqConfigurationProposalClient(
            client=type("Client", (), {"chat": type("Chat", (), {"completions": completions})()})(),
        )
        failed = [{"name": "p95_latency_ms", "observed": 250, "limit": 200}]
        client.propose(
            current_configuration={"replicas": 1},
            result={"status": "COMPLETED"},
            features={"max_p95_latency_ms": 250},
            constraints={"max_p95_latency_ms": 200},
            history=[{"configuration": {"replicas": 1}}],
            constraint_evaluation={"status": "FAIL", "checks": failed},
            failed_constraints=failed,
            resource_score=0.1,
            bounds={"max_replicas": 4},
            proposal_goal="MINIMIZE_RESOURCES",
        )
        prompt = completions.calls[0]["messages"][0]["content"]
        self.assertIn('"failed_constraints"', prompt)
        self.assertIn('"constraint_evaluation"', prompt)
        self.assertIn('"resource_score": 0.1', prompt)
        self.assertIn('"optimization_bounds"', prompt)
        self.assertIn('"optimization_history"', prompt)
        self.assertIn("never repeat", prompt)
        self.assertIn('"proposal_goal": "MINIMIZE_RESOURCES"', prompt)
        self.assertIn("strictly lower resource score", prompt)
        self.assertIn("kubectl commands", prompt)

    def test_repeated_proposal_is_rejected_before_another_experiment(self):
        proposal = SequenceProposal([{
            "replicas": 2,
            "cpu_request": "500m",
            "cpu_limit": "1000m",
            "memory_request": "512Mi",
            "memory_limit": "1Gi",
        }])
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            result = OptimizationRunner(
                proposal,
                experiment_runner=lambda app, *_args: (calls.append(app) or failing_result(app)),
                output_directory=directory,
                logger=lambda _: None,
            ).run(APPLICATION, available_cpu="4", available_memory="8Gi", vus=1, duration="1s")
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["optimization_stop_reason"], "REPEATED_CONFIGURATION")

    def test_higher_resource_candidate_without_failed_metric_improvement_stops(self):
        proposal = SequenceProposal([larger_configuration()])
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            result = OptimizationRunner(
                proposal,
                experiment_runner=lambda app, *_args: (calls.append(app) or failing_result(app)),
                output_directory=directory,
                logger=lambda _: None,
            ).run(APPLICATION, available_cpu="4", available_memory="8Gi", vus=1, duration="1s")
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["optimization_stop_reason"], "NON_IMPROVING_HIGHER_RESOURCE_CANDIDATE")

    def test_request_only_resource_increase_is_guarded(self):
        proposal = SequenceProposal([{
            "replicas": 2,
            "cpu_request": "600m",
            "cpu_limit": "1000m",
            "memory_request": "600Mi",
            "memory_limit": "1Gi",
        }])
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            result = OptimizationRunner(
                proposal,
                experiment_runner=lambda app, *_args: (calls.append(app) or failing_result(app)),
                output_directory=directory,
                logger=lambda _: None,
            ).run(APPLICATION, available_cpu="4", available_memory="8Gi", vus=1, duration="1s")
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["optimization_stop_reason"], "NON_IMPROVING_HIGHER_RESOURCE_CANDIDATE")

    def test_higher_resource_candidate_with_failed_metric_improvement_continues(self):
        proposal = SequenceProposal([larger_configuration(), {
            "replicas": 4,
            "cpu_request": "700m",
            "cpu_limit": "1400m",
            "memory_request": "700Mi",
            "memory_limit": "1400Mi",
        }])
        calls = []

        def execute(app, *_args):
            calls.append(app)
            return failing_result(app, p95=250 - (10 * len(calls)), recovery=35 - len(calls))

        with tempfile.TemporaryDirectory() as directory:
            result = OptimizationRunner(
                proposal,
                experiment_runner=execute,
                output_directory=directory,
                logger=lambda _: None,
            ).run(
                APPLICATION,
                available_cpu="4",
                available_memory="8Gi",
                vus=1,
                duration="1s",
                settings=OptimizationSettings(max_iterations=3),
            )
        self.assertEqual(len(calls), 3)
        self.assertEqual(result["optimization_stop_reason"], "MAX_ITERATIONS")

    def test_max_iterations_and_stagnation_defaults_are_unchanged(self):
        self.assertEqual(OptimizationSettings().max_iterations, 4)
        self.assertEqual(OptimizationSettings().stagnation_limit, 2)

    def test_max_iterations_stopping_remains_bounded(self):
        proposal = SequenceProposal([{
            "replicas": 1,
            "cpu_request": "250m",
            "cpu_limit": "500m",
            "memory_request": "256Mi",
            "memory_limit": "512Mi",
        }])
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            result = OptimizationRunner(
                proposal,
                experiment_runner=lambda app, *_args: (calls.append(app) or failing_result(app)),
                output_directory=directory,
                logger=lambda _: None,
            ).run(
                APPLICATION,
                available_cpu="4",
                available_memory="8Gi",
                vus=1,
                duration="1s",
                settings=OptimizationSettings(max_iterations=2),
            )
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["optimization_stop_reason"], "MAX_ITERATIONS")

    def test_stagnation_stopping_remains_unchanged(self):
        proposal = SequenceProposal([
            {"replicas": 1, "cpu_request": "250m", "cpu_limit": "800m", "memory_request": "256Mi", "memory_limit": "900Mi"},
            {"replicas": 1, "cpu_request": "250m", "cpu_limit": "700m", "memory_request": "256Mi", "memory_limit": "800Mi"},
        ])
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            result = OptimizationRunner(
                proposal,
                experiment_runner=lambda app, *_args: (calls.append(app) or failing_result(app)),
                output_directory=directory,
                logger=lambda _: None,
            ).run(APPLICATION, available_cpu="4", available_memory="8Gi", vus=1, duration="1s")
        self.assertEqual(len(calls), 3)
        self.assertEqual(result["optimization_stop_reason"], "NO_MEANINGFUL_IMPROVEMENT")

    def test_failed_hard_constraint_is_not_compensated_by_resource_score(self):
        proposal = SequenceProposal([])
        with tempfile.TemporaryDirectory() as directory:
            result = OptimizationRunner(
                proposal,
                experiment_runner=lambda app, *_args: failing_result(app, p95=100, recovery=35),
                output_directory=directory,
                logger=lambda _: None,
            ).run(
                APPLICATION,
                available_cpu="4",
                available_memory="8Gi",
                vus=1,
                duration="1s",
                settings=OptimizationSettings(max_iterations=1),
            )
        self.assertFalse(result["final_constraints_passed"])
        self.assertEqual(result["final_configuration"], result["initial_configuration"])

    def test_failed_experiment_is_never_recorded_as_passing_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            result = OptimizationRunner(
                SequenceProposal([]),
                experiment_runner=lambda *_args: {
                    **passing_result(APPLICATION, 0),
                    "status": "FAILED",
                },
                output_directory=directory,
                logger=lambda _: None,
            ).run(
                APPLICATION,
                available_cpu="4",
                available_memory="8Gi",
                vus=1,
                duration="1s",
                settings=OptimizationSettings(max_iterations=1),
            )
        self.assertFalse(result["final_constraints_passed"])
        self.assertFalse(result["iterations"][0]["constraints_passed"])


class PodKillRecoveryTimingTests(unittest.TestCase):
    application = {
        "name": "adservice",
        "namespace": "default",
        "replicas": 1,
        "labels": {"app": "adservice"},
    }
    pod = {"metadata": {"name": "adservice-old", "uid": "old-uid"}}

    def _execute_with_timestamps(self, timestamps, recovery):
        with (
            patch.object(workflow, "run_command", return_value=json.dumps({"items": [self.pod]})),
            patch.object(workflow, "apply_chaos_file"),
            patch.object(workflow, "wait_for_pod_kill_recovery", return_value=recovery),
            patch.object(workflow, "cleanup_chaos"),
            patch.object(workflow.time, "time", side_effect=timestamps),
        ):
            return workflow.execute_pod_kill(self.application, "pod-kill.yaml")

    def test_successful_recovery_reports_all_phases_from_their_boundaries(self):
        recovery = {
            "recovered": True,
            "pod_disappeared_at": 105,
            "replacement_pod_ready_at": 135,
        }
        result = self._execute_with_timestamps([100, 102, 140, 141], recovery)

        self.assertEqual(result["chaos_apply_seconds"], 2)
        self.assertEqual(result["pod_disappearance_seconds"], 3)
        self.assertEqual(result["replacement_pod_ready_seconds"], 30)
        self.assertEqual(result["pod_kill_recovery_seconds"], 40)
        self.assertEqual(result["total_recovery_seconds"], 40)
        self.assertEqual(result["status"], "completed")

    def test_slow_replacement_over_30_seconds_fails_existing_constraint(self):
        recovery = {
            "recovered": True,
            "pod_disappeared_at": 12,
            "replacement_pod_ready_at": 46,
        }
        result = self._execute_with_timestamps([10, 11, 47, 48], recovery)
        self.assertEqual(result["replacement_pod_ready_seconds"], 34)
        self.assertGreater(result["pod_kill_recovery_seconds"], 30)

        features = extract_features({
            "experiments": [{"name": "pod_kill", "metrics": result}],
        })
        evaluation = evaluate_constraints(features, OptimizationConstraints())
        recovery_check = next(
            check for check in evaluation["checks"]
            if check["name"] == "pod_kill_recovery_seconds"
        )
        self.assertEqual(recovery_check["status"], "FAIL")

    def test_recovery_timeout_when_replacement_is_missing_uses_existing_timeout_result(self):
        # The short timeout only keeps the mocked unit test fast; polling remains the
        # production three-second interval and the default timeout remains unchanged.
        clock = iter([0, 1, 2, 5, 7])
        with (
            patch.object(workflow, "run_command", return_value=json.dumps({"items": []})),
            patch.object(workflow.time, "time", side_effect=lambda: next(clock)),
            patch.object(workflow.time, "sleep") as sleep,
        ):
            result = workflow.wait_for_pod_kill_recovery(
                self.application,
                self.pod,
                timeout=6,
            )

        self.assertFalse(result["recovered"])
        self.assertEqual(result["reason"], "POD_REPLACEMENT_NOT_READY")
        self.assertIsNotNone(result["pod_disappeared_at"])
        self.assertIsNone(result["replacement_pod_ready_at"])
        self.assertEqual(result["poll_interval_seconds"], 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [3, 3])

    def test_execute_pod_kill_rejects_missing_initial_pod(self):
        with (
            patch.object(workflow, "run_command", return_value=json.dumps({"items": []})),
            patch.object(workflow, "apply_chaos_file") as apply_chaos,
        ):
            with self.assertRaisesRegex(RuntimeError, "no target pod exists"):
                workflow.execute_pod_kill(self.application, "pod-kill.yaml")
        apply_chaos.assert_not_called()

    def test_same_uid_container_restart_is_detected_without_waiting_for_pod_deletion(self):
        def pod(restart_count, ready):
            return {
                "metadata": {"name": "adservice-old", "uid": "old-uid"},
                "status": {
                    "phase": "Running",
                    "containerStatuses": [{
                        "name": "adservice",
                        "restartCount": restart_count,
                        "ready": ready,
                    }],
                },
            }

        clock = iter([0, 1, 2, 5, 6])
        responses = iter([
            json.dumps({"items": [pod(1, False)]}),
            json.dumps({"items": [pod(1, True)]}),
        ])
        with (
            patch.object(workflow, "run_command", side_effect=lambda _args: next(responses)),
            patch.object(workflow.time, "time", side_effect=lambda: next(clock)),
            patch.object(workflow.time, "sleep") as sleep,
        ):
            result = workflow.wait_for_pod_kill_recovery(
                self.application,
                {**self.pod, "status": {"containerStatuses": [{"name": "adservice", "restartCount": 0}]}},
                timeout=12,
            )

        self.assertTrue(result["recovered"])
        self.assertEqual(result["recovery_mode"], "container_restart")
        self.assertIsNone(result["pod_disappeared_at"])
        self.assertEqual(result["recovery_event_at"], 2)
        self.assertEqual(result["replacement_pod_ready_at"], 6)
        sleep.assert_called_once_with(3)

    def test_new_uid_ready_replacement_succeeds_while_old_pod_is_still_present(self):
        old_pod = {
            **self.pod,
            "metadata": {**self.pod["metadata"], "deletionTimestamp": "2026-10-05T00:00:00Z"},
            "status": {"phase": "Running", "containerStatuses": [{"name": "adservice", "ready": True, "restartCount": 0}]},
        }
        replacement = {
            "metadata": {"name": "adservice-new", "uid": "new-uid"},
            "status": {"phase": "Running", "containerStatuses": [{"name": "adservice", "ready": False, "restartCount": 0}]},
        }
        ready_replacement = {
            **replacement,
            "status": {"phase": "Running", "containerStatuses": [{"name": "adservice", "ready": True, "restartCount": 0}]},
        }
        clock = iter([0, 1, 2, 5, 6])
        responses = iter([
            json.dumps({"items": [old_pod, replacement]}),
            json.dumps({"items": [old_pod, ready_replacement]}),
        ])
        with (
            patch.object(workflow, "run_command", side_effect=lambda _args: next(responses)),
            patch.object(workflow.time, "time", side_effect=lambda: next(clock)),
            patch.object(workflow.time, "sleep") as sleep,
        ):
            result = workflow.wait_for_pod_kill_recovery(
                self.application,
                self.pod,
                timeout=12,
                pre_chaos_pods=[old_pod],
            )

        self.assertTrue(result["recovered"])
        self.assertEqual(result["recovery_mode"], "pod_replacement")
        self.assertIsNone(result["pod_disappeared_at"])
        self.assertEqual(result["recovery_event_at"], 2)
        self.assertEqual(result["replacement_pod_ready_at"], 6)
        sleep.assert_called_once_with(3)

    def test_replacement_is_matched_against_all_pre_chaos_pods(self):
        original_one = {
            "metadata": {"name": "adservice-one", "uid": "uid-one"},
            "status": {"phase": "Running", "containerStatuses": [{"name": "adservice", "ready": True, "restartCount": 0}]},
        }
        original_two = {
            "metadata": {"name": "adservice-two", "uid": "uid-two"},
            "status": {"phase": "Running", "containerStatuses": [{"name": "adservice", "ready": True, "restartCount": 0}]},
        }
        replacement = {
            "metadata": {"name": "adservice-three", "uid": "uid-three"},
            "status": {"phase": "Running", "containerStatuses": [{"name": "adservice", "ready": True, "restartCount": 0}]},
        }
        clock = iter([0, 1, 2, 3, 4])
        with (
            patch.object(workflow, "run_command", return_value=json.dumps({"items": [original_one, replacement]})),
            patch.object(workflow.time, "time", side_effect=lambda: next(clock)),
            patch.object(workflow.time, "sleep"),
        ):
            result = workflow.wait_for_pod_kill_recovery(
                {**self.application, "replicas": 2},
                original_one,
                pre_chaos_pods=[original_one, original_two],
            )

        self.assertTrue(result["recovered"])
        self.assertEqual(result["killed_pod"], "adservice-two")
        self.assertEqual(result["replacement_ready_replicas"], 2)

    def test_unrecovered_timeout_does_not_report_timeout_duration_as_recovery(self):
        result = self._execute_with_timestamps(
            [10, 11, 131, 132],
            {
                "recovered": False,
                "pod_disappeared_at": 12,
                "recovery_event_at": 12,
                "replacement_pod_ready_at": None,
            },
        )
        self.assertIsNone(result["pod_kill_recovery_seconds"])
        self.assertEqual(result["total_recovery_seconds"], 121)
        self.assertEqual(result["status"], "completed_without_confirmed_recovery")

    def test_oom_detection_checks_current_and_previous_container_termination(self):
        self.assertTrue(workflow.container_was_oom_killed({
            "state": {"terminated": {"reason": "OOMKilled"}},
        }))
        self.assertTrue(workflow.container_was_oom_killed({
            "lastState": {"terminated": {"reason": "OOMKilled"}},
        }))
        self.assertFalse(workflow.container_was_oom_killed({
            "state": {"running": {}},
            "lastState": {"terminated": {"reason": "Error"}},
        }))

    def test_oom_metrics_ignore_old_replica_set_pods_from_another_configuration(self):
        candidate_container = {
            "name": "adservice",
            "resources": {
                "requests": {"cpu": "500m", "memory": "512Mi"},
                "limits": {"cpu": "1000m", "memory": "1024Mi"},
            },
        }
        candidate = {
            **self.application,
            "containers": [candidate_container],
        }
        stale_oom_pod = {
            "spec": {"containers": [{
                "name": "adservice",
                "resources": {
                    "requests": {"cpu": "250m", "memory": "256Mi"},
                    "limits": {"cpu": "500m", "memory": "512Mi"},
                },
            }]},
            "status": {"containerStatuses": [{
                "name": "adservice",
                "restartCount": 1,
                "lastState": {"terminated": {"reason": "OOMKilled"}},
            }]},
        }
        current_pod = {
            "spec": {"containers": [{
                "name": "adservice",
                "resources": {
                    "requests": {"cpu": "500m", "memory": "512Mi"},
                    "limits": {"cpu": "1", "memory": "1Gi"},
                },
            }]},
            "status": {"containerStatuses": [{
                "name": "adservice",
                "restartCount": 0,
                "state": {"running": {}},
            }]},
        }

        def run_command(arguments):
            if arguments[1] == "get" and arguments[2] == "deployment":
                return json.dumps({"status": {"availableReplicas": 1}})
            if arguments[1] == "get" and arguments[2] == "pods":
                return json.dumps({"items": [stale_oom_pod, current_pod]})
            if arguments[1] == "top":
                raise RuntimeError("Metrics API not available")
            raise AssertionError(arguments)

        with (
            patch.object(workflow, "run_command", side_effect=run_command),
            patch.object(workflow, "get_application_pod", return_value=None),
        ):
            metrics = workflow.collect_result_metrics(candidate, None)

        self.assertFalse(metrics["oom_killed"])
        self.assertEqual(metrics["pod_restarts"], 0)


class CandidateDeploymentTests(unittest.TestCase):
    def test_deployment_applies_the_candidate_resource_override(self):
        candidate = dict(APPLICATION)
        candidate["resource_override"] = {
            **RESOURCE,
            "spec": {
                "replicas": 3,
                "template": {"spec": {"containers": [{
                    "name": "adservice",
                    "image": "example/ad:v1",
                    "resources": {
                        "requests": {"cpu": "600m", "memory": "600Mi"},
                        "limits": {"cpu": "1200m", "memory": "1200Mi"},
                    },
                }]}},
            },
        }
        applied = []

        def run_command(arguments):
            if arguments[1] == "apply":
                with open(arguments[-1], encoding="utf-8") as manifest:
                    applied.extend(yaml.safe_load_all(manifest))
            return ""

        with patch.object(workflow, "run_command", side_effect=run_command):
            workflow.deploy_application(candidate)

        self.assertEqual(applied[0]["spec"]["replicas"], 3)
        resources = applied[0]["spec"]["template"]["spec"]["containers"][0]["resources"]
        self.assertEqual(resources["requests"]["cpu"], "600m")
        self.assertEqual(resources["limits"]["memory"], "1200Mi")


if __name__ == "__main__":
    unittest.main()
