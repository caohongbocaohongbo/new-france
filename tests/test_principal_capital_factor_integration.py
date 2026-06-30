import asyncio
import importlib
import unittest
from unittest.mock import patch

import pandas as pd


class PrincipalCapitalFactorIntegrationTest(unittest.TestCase):
    def test_plugin_skill_registered_in_all_skills(self):
        """plugin 存在时 PrincipalCapitalSkill 必须在 ALL_SKILLS 中。"""
        import backend.agents.layer2_signal_engine.skills as skills_module

        skills_module = importlib.reload(skills_module)
        ALL_SKILLS = skills_module.ALL_SKILLS

        keys = [s().key for s in ALL_SKILLS]
        self.assertIn("principal_capital", keys)

    def test_plugin_skill_scores_with_high_ratio(self):
        """ratio=60 应返回高分(passed=True)。"""
        from backend.plugins.principal_capital.skill import PrincipalCapitalSkill

        s = PrincipalCapitalSkill()
        r = s.score({"main_inflow_ratio": 60})
        self.assertGreaterEqual(r.score, 9.0)
        self.assertTrue(r.passed)

    def test_plugin_skill_scores_with_outflow(self):
        """ratio=-50 应返回低分(passed=False)。"""
        from backend.plugins.principal_capital.skill import PrincipalCapitalSkill

        s = PrincipalCapitalSkill()
        r = s.score({"main_inflow_ratio": -50})
        self.assertLessEqual(r.score, 1.0)
        self.assertFalse(r.passed)

    def test_plugin_skill_handles_missing_ratio(self):
        """ctx 缺 main_inflow_ratio 时给中性分 5.0。"""
        from backend.plugins.principal_capital.skill import PrincipalCapitalSkill

        s = PrincipalCapitalSkill()
        r = s.score({})
        self.assertEqual(r.score, 5.0)
        self.assertTrue(r.passed)

    def test_screening_uses_cached_fund_flow_when_fresh(self):
        """screening 命中 plugin 新鲜缓存时不走 fetch_resilient。"""
        from backend.services import screening_service as svc

        df = pd.DataFrame([{"code": "600001", "main_inflow_ratio": 55.0}])
        with patch.object(svc, "_pc_get_cached", return_value=(df, 120)) as mock_cached, patch.object(
            svc, "_pc_fetch_resilient"
        ) as mock_fetch:
            result = asyncio.run(svc._fetch_principal_capital_map(target_date=None))

        self.assertEqual(result.get("600001"), 55.0)
        mock_cached.assert_called_once()
        mock_fetch.assert_not_called()

    def test_screening_falls_back_to_fetch_when_cache_stale(self):
        """缓存过期时 fetch_resilient 被调用。"""
        from backend.services import screening_service as svc

        empty = pd.DataFrame()
        fresh = pd.DataFrame([{"code": "600002", "main_inflow_ratio": 70.0}])
        with patch.object(svc, "_pc_get_cached", return_value=(empty, None)), patch.object(
            svc, "_pc_fetch_resilient", return_value=(fresh, {"active_source": "eastmoney"})
        ):
            result = asyncio.run(svc._fetch_principal_capital_map(target_date=None))

        self.assertEqual(result.get("600002"), 70.0)

    def test_screening_returns_empty_when_plugin_missing(self):
        """plugin 不可加载时返回空 dict, 不抛异常。"""
        from backend.services import screening_service as svc

        with patch.object(svc, "_HAS_PC_PLUGIN", False):
            result = asyncio.run(svc._fetch_principal_capital_map(target_date=None))

        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
