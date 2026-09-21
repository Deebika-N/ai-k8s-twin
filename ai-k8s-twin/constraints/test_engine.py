import unittest

from constraints.engine import ConstraintRequirements, evaluate_constraints
from metrics.features import MetricsFeatures


def metrics(**overrides):
    values = {
        "avg_cpu_cores": 0.2,
        "max_cpu_cores": 0.4,
        "avg_memory_bytes": 100,
        "max_memory_bytes": 200,
        "request_rate": None,
        "p95_latency_ms": 150,
        "p99_latency_ms": 190,
        "error_rate": 0.2,
        "pod_restarts": 0,
        "available_replicas": 3,
        "oom_killed": 0,
        "recovery_time_seconds": 10,
    }
    values.update(overrides)
    return MetricsFeatures(**values)


class ConstraintEngineTests(unittest.TestCase):
    def test_passing_metrics_pass_all_hard_constraints(self):
        evaluation = evaluate_constraints(metrics(), ConstraintRequirements(expected_replicas=3))

        self.assertEqual(evaluation.status, "PASS")
        self.assertTrue(all(check.status in {"PASS", "SKIPPED"} for check in evaluation.checks))

    def test_failed_metric_reports_reason(self):
        evaluation = evaluate_constraints(
            metrics(p95_latency_ms=450),
            ConstraintRequirements(expected_replicas=3, max_p95_latency_ms=200),
        )

        latency_check = next(check for check in evaluation.checks if check.name == "p95_latency_ms")
        self.assertEqual(evaluation.status, "FAIL")
        self.assertEqual(latency_check.status, "FAIL")
        self.assertIn("exceeds", latency_check.reason)

    def test_missing_required_metric_is_not_a_pass(self):
        evaluation = evaluate_constraints(
            metrics(p95_latency_ms=None),
            ConstraintRequirements(expected_replicas=3),
        )

        latency_check = next(check for check in evaluation.checks if check.name == "p95_latency_ms")
        self.assertEqual(evaluation.status, "FAIL")
        self.assertEqual(latency_check.status, "UNKNOWN")

    def test_availability_is_calculated_from_expected_replicas(self):
        evaluation = evaluate_constraints(
            metrics(available_replicas=2),
            ConstraintRequirements(expected_replicas=3, min_availability_percent=99),
        )

        availability_check = next(check for check in evaluation.checks if check.name == "availability_percent")
        self.assertEqual(availability_check.status, "FAIL")
        self.assertAlmostEqual(availability_check.measured, 66.666666, places=4)


if __name__ == "__main__":
    unittest.main()