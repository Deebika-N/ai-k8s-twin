import unittest

from services.configuration_optimizer import parse_proposal
from services.configuration_validator import ConfigurationValidationError
from services.optimization_policy import (
    OptimizationConstraints,
    ResourceBounds,
    evaluate_constraints,
    extract_features,
    resource_score,
    validate_candidate,
)


CONFIGURATION = {
    "replicas": 2,
    "cpu_request": "500m",
    "cpu_limit": "1000m",
    "memory_request": "512Mi",
    "memory_limit": "1Gi",
}


class OptimizationPolicyTests(unittest.TestCase):
    def test_extracts_worst_case_features_from_each_scenario(self):
        result = {
            "baseline": {"metrics": {"p95_latency_ms": 100, "available_replicas": 2, "oom_killed": False}},
            "experiments": [{"name": "cpu_stress", "metrics": {"p95_latency_ms": 300, "available_replicas": 1, "oom_killed": False}}],
        }
        features = extract_features(result)
        self.assertEqual(features["max_p95_latency_ms"], 300)
        self.assertEqual(features["min_available_replicas"], 1)

    def test_recovery_feature_uses_only_pod_kill_metric(self):
        result = {
            "baseline": {"metrics": {"recovery_time_seconds": 99}},
            "experiments": [
                {"name": "pod_kill", "metrics": {"pod_kill_recovery_seconds": 12}},
                {"name": "cpu_stress", "metrics": {"recovery_time_seconds": 88, "cpu_readiness_recovery_seconds": 4}},
            ],
        }
        features = extract_features(result)
        self.assertEqual(features["pod_kill_recovery_seconds"], 12)
        self.assertEqual(features["max_recovery_time_seconds"], 12)

    def test_constraint_evaluation_is_deterministic(self):
        evaluation = evaluate_constraints(
            {"max_p95_latency_ms": 300, "max_p99_latency_ms": None, "max_error_rate_percent": 0, "pod_kill_recovery_seconds": 5, "min_available_replicas": 2, "oom_killed": False},
            OptimizationConstraints(max_p95_latency_ms=200, max_p99_latency_ms=None, min_available_replicas=2),
        )
        self.assertEqual(evaluation["status"], "FAIL")

    def test_lower_resource_configuration_scores_lower(self):
        bounds = ResourceBounds()
        lower = {**CONFIGURATION, "replicas": 1, "cpu_limit": "500m", "memory_limit": "512Mi"}
        self.assertLess(resource_score(lower, bounds), resource_score(CONFIGURATION, bounds))

    def test_bounds_and_request_limit_are_enforced(self):
        with self.assertRaises(ConfigurationValidationError):
            validate_candidate({**CONFIGURATION, "cpu_request": "1500m", "cpu_limit": "1000m"}, ResourceBounds())

    def test_groq_parser_ignores_extra_top_level_fields(self):
        candidate = parse_proposal(
            '{"proposed_configuration":{"replicas":1,"unexpected":"ignored"},"reasoning_summary":"x"}',
            CONFIGURATION,
        )
        self.assertEqual(candidate["replicas"], 1)
        self.assertNotIn("unexpected", candidate)


if __name__ == "__main__":
    unittest.main()