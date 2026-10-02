import unittest

from services.optimizer import GroqConfigurationOptimizer, OptimizerError


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeResponse:
    def __init__(self, content):
        self.choices = [type("Choice", (), {"message": FakeMessage(content)})()]


class FakeCompletions:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse(self.content)


class FakeClient:
    def __init__(self, content):
        self.chat = type("Chat", (), {"completions": FakeCompletions(content)})()


CONFIGURATION = {
    "kind": "Deployment",
    "name": "paymentservice",
    "namespace": "simulation",
    "replicas": 1,
    "containers": [{"name": "paymentservice", "image": "example/paymentservice:v1"}],
}


class OptimizerTests(unittest.TestCase):
    def test_merges_only_allowed_fields(self):
        optimizer = GroqConfigurationOptimizer(FakeClient('{"replicas": 2, "cpu_limit": "1"}'))
        result = optimizer.propose(
            current_configuration=CONFIGURATION,
            initial_configuration=CONFIGURATION,
            requirements={},
            selected_environments=["CPU Stress"],
            chaos_parameters={},
            metrics={},
            evaluation={},
            previous_configurations=[],
        )
        self.assertEqual(result["replicas"], 2)
        self.assertEqual(result["name"], "paymentservice")

    def test_rejects_identity_fields(self):
        optimizer = GroqConfigurationOptimizer(FakeClient('{"namespace": "other"}'))
        with self.assertRaisesRegex(OptimizerError, "unsupported fields"):
            optimizer.propose(
                current_configuration=CONFIGURATION,
                initial_configuration=CONFIGURATION,
                requirements={},
                selected_environments=[],
                chaos_parameters={},
                metrics={},
                evaluation={},
                previous_configurations=[],
            )

    def test_rejects_invalid_json(self):
        optimizer = GroqConfigurationOptimizer(FakeClient("not-json"))
        with self.assertRaises(OptimizerError):
            optimizer.propose(
                current_configuration=CONFIGURATION,
                initial_configuration=CONFIGURATION,
                requirements={},
                selected_environments=[],
                chaos_parameters={},
                metrics={},
                evaluation={},
                previous_configurations=[],
            )


if __name__ == "__main__":
    unittest.main()