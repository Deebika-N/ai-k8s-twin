import json
import tempfile
import unittest
from pathlib import Path

from services.configuration_validator import (
    ConfigurationValidationError,
    validate_workload_configuration,
)
from services.optimization_models import ConstraintRequirements, OptimizationRunInput
from services.result_store import ResultStore


VALID_APPLICATION = {
    "kind": "Deployment",
    "name": "paymentservice",
    "namespace": "simulation",
    "replicas": 3,
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


class ConfigurationValidationTests(unittest.TestCase):
    def test_valid_configuration_is_returned(self):
        result = validate_workload_configuration(VALID_APPLICATION)
        self.assertEqual(result["name"], "paymentservice")

    def test_request_cannot_exceed_limit(self):
        invalid = {**VALID_APPLICATION, "containers": [{
            **VALID_APPLICATION["containers"][0],
            "resources": {
                "requests": {"cpu": "750m", "memory": "256Mi"},
                "limits": {"cpu": "500m", "memory": "512Mi"},
            },
        }]}
        with self.assertRaises(ConfigurationValidationError):
            validate_workload_configuration(invalid)

    def test_immutable_identity_cannot_change(self):
        invalid = {**VALID_APPLICATION, "name": "other-service"}
        with self.assertRaisesRegex(ConfigurationValidationError, "immutable field changed: name"):
            validate_workload_configuration(invalid, immutable=VALID_APPLICATION)


class OptimizationContractTests(unittest.TestCase):
    def test_run_input_is_serializable(self):
        run_input = OptimizationRunInput(
            repository_url="https://github.com/example/repo",
            available_cpu="4",
            available_memory="8Gi",
            vus=10,
            duration="60s",
            selected_environments=("CPU Stress",),
            requirements=ConstraintRequirements(expected_replicas=3),
        )
        self.assertEqual(run_input.as_mapping()["vus"], 10)

    def test_result_store_keeps_iterations_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ResultStore(directory)
            run_id, run_directory = store.create_run()
            store.write_iteration(run_directory, 0, {"status": "FAIL"})
            store.write_iteration(run_directory, 1, {"status": "PASS"})
            first = json.loads((Path(run_directory) / "iterations/000/iteration.json").read_text())
            second = json.loads((Path(run_directory) / "iterations/001/iteration.json").read_text())
            self.assertTrue(run_id)
            self.assertEqual(first["status"], "FAIL")
            self.assertEqual(second["status"], "PASS")


if __name__ == "__main__":
    unittest.main()