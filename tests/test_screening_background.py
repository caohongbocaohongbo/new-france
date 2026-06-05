import inspect
import importlib
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from backend.api import router_screening
from backend.api import router_overnight_arbitrage
from backend.main import get_app


class ScreeningBackgroundTaskTest(unittest.TestCase):
    def test_background_screening_task_runs_as_sync_threadpool_task(self):
        self.assertFalse(
            inspect.iscoroutinefunction(router_screening._run_screening_task),
            "筛选流水线包含同步行情请求，BackgroundTasks 必须用同步函数进入线程池，避免阻塞轮询接口",
        )

    def test_background_screening_task_writes_completed_payload(self):
        params = {
            "drop_min": 5.0,
            "drop_max": 10.0,
            "vol_min": 1.0,
            "vol_max": 5.0,
            "turnover_min": 5.0,
            "turnover_max": 10.0,
            "mc_min": 50.0,
            "mc_max": 200.0,
            "pe_max": 50.0,
        }
        result = {
            "strong_buy": 1,
            "buy": 2,
            "watch": 3,
            "results": [],
            "total_scored": 6,
        }

        with patch.object(router_screening, "_execute_screening_pipeline", return_value=result), \
                patch.object(router_screening, "_write_json_cache") as write_json_cache:
            router_screening._run_screening_task(params)

        payload = write_json_cache.call_args.args[0]
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["strong_buy"], 1)
        self.assertEqual(payload["buy"], 2)

    def test_data_collector_import_does_not_require_thread_event_loop(self):
        def load_agent_name():
            sys.modules.pop("backend.agents.layer1_data_collector.agent", None)
            module = importlib.import_module("backend.agents.layer1_data_collector.agent")
            return module.DataCollectorAgent.__name__

        with ThreadPoolExecutor(max_workers=1) as executor:
            self.assertEqual(executor.submit(load_agent_name).result(timeout=3), "DataCollectorAgent")

    def test_overnight_arbitrage_task_runs_as_sync_threadpool_task(self):
        self.assertFalse(
            inspect.iscoroutinefunction(router_overnight_arbitrage._run_overnight_task),
            "尾盘套利包含同步行情请求，BackgroundTasks 必须用同步函数进入线程池，避免阻塞轮询接口",
        )

    def test_overnight_arbitrage_task_writes_completed_payload(self):
        result = {
            "strategy": "overnight_arbitrage",
            "buy_count": 1,
            "watch_count": 2,
            "results": [],
        }

        with patch.object(router_overnight_arbitrage, "_execute_overnight_pipeline", return_value=result), \
                patch.object(router_overnight_arbitrage, "_write_overnight_cache") as write_cache:
            router_overnight_arbitrage._run_overnight_task(dry_run=True)

        payload = write_cache.call_args.args[0]
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["strategy"], "overnight_arbitrage")
        self.assertEqual(payload["buy_count"], 1)

    def test_app_mounts_overnight_arbitrage_routes(self):
        routes = {getattr(route, "path", "") for route in get_app().routes}
        self.assertIn("/api/v1/overnight-arbitrage/run", routes)
        self.assertIn("/api/v1/overnight-arbitrage/latest", routes)


if __name__ == "__main__":
    unittest.main()
