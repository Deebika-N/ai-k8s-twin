import unittest

from fastapi.testclient import TestClient

from backend.app import app


class BackendApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_health_and_default_contracts(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
        self.assertEqual(self.client.get("/config").status_code, 200)
        self.assertEqual(self.client.get("/requirements").status_code, 200)

    def test_experiment_is_queued_with_run_id(self):
        response = self.client.post("/experiments?experiment=cpu-stress")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "QUEUED")
        self.assertTrue(response.json()["run_id"])

    def test_invalid_experiment_is_rejected(self):
        response = self.client.post("/experiments?experiment=unknown")

        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()