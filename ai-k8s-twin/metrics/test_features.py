import unittest

from metrics.features import baseline_result, collect_features
from metrics.prometheus_client import StaticPrometheusClient


class FeatureCollectionTests(unittest.TestCase):
    def test_collects_required_resource_features(self):
        client = StaticPrometheusClient({
            'avg_over_time((sum(rate(container_cpu_usage_seconds_total{namespace="simulation",container="paymentservice",pod=~"simulation-paymentservice-.+"}[5m])))[5m:])': 0.42,
            'max_over_time((sum(rate(container_cpu_usage_seconds_total{namespace="simulation",container="paymentservice",pod=~"simulation-paymentservice-.+"}[5m])))[5m:])': 0.58,
            'avg_over_time((sum(container_memory_working_set_bytes{namespace="simulation",container="paymentservice",pod=~"simulation-paymentservice-.+"}))[5m:])': 268435456,
            'max_over_time((sum(container_memory_working_set_bytes{namespace="simulation",container="paymentservice",pod=~"simulation-paymentservice-.+"}))[5m:])': 300000000,
            'sum(kube_pod_container_status_restarts_total{namespace="simulation",container="paymentservice",pod=~"simulation-paymentservice-.+"})': 1,
            'kube_deployment_status_replicas_available{namespace="simulation",deployment="simulation-paymentservice"}': 3,
            'sum(kube_pod_container_status_last_terminated_reason{namespace="simulation",container="paymentservice",reason="OOMKilled",pod=~"simulation-paymentservice-.+"})': 0,
        })

        features = collect_features(client)

        self.assertEqual(features.available_replicas, 3)
        self.assertEqual(features.avg_cpu_cores, 0.42)
        self.assertEqual(features.max_cpu_cores, 0.58)
        self.assertIsNone(features.p95_latency_ms)

    def test_result_has_standard_experiment_shape(self):
        client = StaticPrometheusClient({})
        result = baseline_result("run-1", {"replicas": 3}, collect_features(client))

        value = result.as_mapping()

        self.assertEqual(value["experiment"], "baseline")
        self.assertEqual(value["result"], "MEASURED")
        self.assertIn("metrics", value)
        self.assertIn("available_replicas", value["metrics"])


if __name__ == "__main__":
    unittest.main()