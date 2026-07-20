import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.plugins.overnight_arbitrage.router import router


class OvernightArbitrageRouterTest(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router, prefix="/api/v1/overnight-arbitrage")
        self.client = TestClient(app)

    def test_scheduled_run_rejects_invalid_token(self):
        with patch.dict("os.environ", {"OA_TRIGGER_TOKEN": "expected-token"}, clear=False):
            response = self.client.post(
                "/api/v1/overnight-arbitrage/scheduled-run",
                headers={"X-OA-Trigger-Token": "wrong-token"},
            )

        self.assertEqual(response.status_code, 401)

    def test_scheduled_run_starts_official_pipeline_with_valid_token(self):
        with patch.dict("os.environ", {"OA_TRIGGER_TOKEN": "expected-token"}, clear=False), \
                patch("backend.plugins.overnight_arbitrage.router._write_overnight_cache"), \
                patch("backend.plugins.overnight_arbitrage.router._run_overnight_task") as task:
            response = self.client.post(
                "/api/v1/overnight-arbitrage/scheduled-run",
                headers={"X-OA-Trigger-Token": "expected-token"},
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["trigger"], "external_scheduler")
        task.assert_called_once_with(False)

    def test_manual_run_defaults_to_dry_run(self):
        with patch("backend.plugins.overnight_arbitrage.router._write_overnight_cache"), \
                patch("backend.plugins.overnight_arbitrage.router._run_overnight_task") as task:
            response = self.client.post("/api/v1/overnight-arbitrage/run")

        self.assertEqual(response.status_code, 200)
        task.assert_called_once_with(True)


if __name__ == "__main__":
    unittest.main()
