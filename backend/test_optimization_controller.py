import tempfile
import unittest

from services.optimization_controller import OptimizationController
from services.optimization_models import ConstraintRequirements, OptimizationRunInput
from services.result_store import ResultStore


CONFIGURATION = {
    "kind": "Deployment",
    "name": "paymentservice",
    "namespace": "simulation",
    "replicas": 1,
    "labels": {"app": "paymentservice"},
    "selector": {"matchLabels": {"app": "paymentservice"}},
    "containers": [{
        "name": "paymentservice",
        "image": "example/paymentservice:v1",
        "resources": {
            "requests": {"cpu": "250m", "memory": "256Mi"},
            "limits": {"cpu": "500m", "memory": "512Mi"},
        },
    }],
}


class FakeExecutor:
    def __init__(self):
        self.calls = []

    def run_candidate(self, configuration, run_input, iteration):
        self.calls.append(configuration)
        return {"metrics": {
            "available_replicas": configuration["replicas"],
            "p95_latency_ms": 100 if iteration else 500,
            "error_rate_percent": 0,
            "recovery_time_seconds": 1,
            "oom_killed": 0,
        }}


class FakeOptimizer:
    def propose(self, **kwargs):
        configuration = dict(kwargs["current_configuration"])
        configuration["replicas"] = 2
        return configuration


class OptimizationControllerTests(unittest.TestCase):
    def test_failed_iteration_is_optimized_until_pass(self):
        run_input = OptimizationRunInput(
            repository_url="https://github.com/example/repo",
            available_cpu="4",
            available_memory="8Gi",
            vus=1,
            duration="1s",
            selected_environments=("CPU Stress",),
            requirements=ConstraintRequirements(expected_replicas=1, max_p95_latency_ms=200),
            max_iterations=3,
        )
        executor = FakeExecutor()
        with tempfile.TemporaryDirectory() as directory:
            result = OptimizationController(
                executor=executor,
                optimizer=FakeOptimizer(),
                store=ResultStore(directory),
                logger=lambda _: None,
            ).run(run_input, CONFIGURATION)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(len(executor.calls), 2)

    def test_max_iterations_is_reported(self):
        run_input = OptimizationRunInput(
            repository_url="https://github.com/example/repo",
            available_cpu="4",
            available_memory="8Gi",
            vus=1,
            duration="1s",
            selected_environments=("CPU Stress",),
            requirements=ConstraintRequirements(expected_replicas=1, max_p95_latency_ms=20),
            max_iterations=2,
        )
        with tempfile.TemporaryDirectory() as directory:
            result = OptimizationController(
                executor=FakeExecutor(),
                optimizer=FakeOptimizer(),
                store=ResultStore(directory),
                logger=lambda _: None,
            ).run(run_input, CONFIGURATION)
        self.assertEqual(result["status"], "MAX_ITERATIONS_REACHED")


if __name__ == "__main__":
    unittest.main()