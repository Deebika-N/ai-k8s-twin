import unittest

from config import EnvironmentConfig
from generate_k8s import render_manifests


VALID_VALUES = {
    "service": "paymentservice",
    "image": "gcr.io/google-samples/microservices-demo/paymentservice:v0.10.6",
    "replicas": 3,
    "cpu_request": "250m",
    "cpu_limit": "500m",
    "memory_request": "256Mi",
    "memory_limit": "512Mi",
    "application_port": 50051,
}


class EnvironmentConfigTests(unittest.TestCase):
    def test_render_includes_namespace_and_consistent_selectors(self):
        manifest = render_manifests(EnvironmentConfig.from_mapping(VALID_VALUES))

        self.assertIn("kind: Namespace", manifest)
        self.assertIn("name: simulation-paymentservice", manifest)
        self.assertEqual(manifest.count("app: simulation-paymentservice"), 3)
        self.assertIn("containerPort: 50051", manifest)

    def test_rejects_request_larger_than_limit(self):
        invalid_values = {**VALID_VALUES, "cpu_request": "750m"}

        with self.assertRaisesRegex(ValueError, "cpu_request"):
            EnvironmentConfig.from_mapping(invalid_values)

    def test_rejects_unsafe_service_name(self):
        invalid_values = {**VALID_VALUES, "service": "payment service"}

        with self.assertRaisesRegex(ValueError, "service"):
            EnvironmentConfig.from_mapping(invalid_values)


if __name__ == "__main__":
    unittest.main()