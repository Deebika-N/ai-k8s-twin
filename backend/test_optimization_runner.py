import tempfile
import unittest
from pathlib import Path

from services.optimization_policy import OptimizationConstraints
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


class RunnerTests(unittest.TestCase):
    def test_selects_lower_resource_passing_configuration(self):
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
            result = OptimizationRunner(Proposal(), experiment_runner=execute, output_directory=directory, logger=lambda _: None).run(
                APPLICATION,
                available_cpu="4",
                available_memory="8Gi",
                vus=1,
                duration="1s",
                settings=OptimizationSettings(constraints=OptimizationConstraints(max_p99_latency_ms=None), max_iterations=3),
            )
            self.assertEqual(result["final_constraints_passed"], True)
            self.assertEqual(result["final_configuration"]["replicas"], 1)
            self.assertTrue(Path(directory, "adservice-optimization-result.json").exists())
        self.assertEqual(calls, [2, 1])


if __name__ == "__main__":
    unittest.main()