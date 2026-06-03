import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_optional_source_promotion.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_optional_source_promotion", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OptionalSourcePromotionTest(unittest.TestCase):
    def test_stable_source_enables_email_and_dashboard_in_generated_config(self):
        module = _load_module()
        config = {
            "version": 1,
            "sources": {
                "national_team": {
                    "label": "国家队动向",
                    "surfaces": {"email": False, "dashboard": False},
                }
            },
        }
        health = {
            "sources": {
                "national_team": {
                    "ok": True,
                    "stable": True,
                    "consecutive_successes": 3,
                    "required_successes": 3,
                    "data": {"record_count": 18},
                    "source_status": {
                        "source": "东方财富股东分析",
                        "source_url": "https://data.eastmoney.com/gdfx/HoldingAnalyse.html",
                        "fetched_at": "2026-06-03T15:10:00+08:00",
                    },
                }
            }
        }

        updated, promoted = module.build_promotion(config, health)

        self.assertEqual([item["key"] for item in promoted], ["national_team"])
        self.assertTrue(updated["sources"]["national_team"]["surfaces"]["email"])
        self.assertTrue(updated["sources"]["national_team"]["surfaces"]["dashboard"])

    def test_unstable_source_does_not_change_config(self):
        module = _load_module()
        config = {
            "version": 1,
            "sources": {
                "national_team": {
                    "label": "国家队动向",
                    "surfaces": {"email": False, "dashboard": False},
                }
            },
        }
        health = {
            "sources": {
                "national_team": {
                    "ok": True,
                    "stable": False,
                    "consecutive_successes": 1,
                    "required_successes": 3,
                    "data": {"record_count": 18},
                }
            }
        }

        updated, promoted = module.build_promotion(config, health)

        self.assertEqual(promoted, [])
        self.assertFalse(updated["sources"]["national_team"]["surfaces"]["email"])
        self.assertFalse(updated["sources"]["national_team"]["surfaces"]["dashboard"])

    def test_stable_source_without_traceable_url_does_not_change_config(self):
        module = _load_module()
        config = {
            "version": 1,
            "sources": {
                "national_team": {
                    "label": "国家队动向",
                    "surfaces": {"email": False, "dashboard": False},
                }
            },
        }
        health = {
            "sources": {
                "national_team": {
                    "ok": True,
                    "stable": True,
                    "consecutive_successes": 3,
                    "required_successes": 3,
                    "data": {"record_count": 18},
                    "source_status": {
                        "source": "东方财富股东分析",
                        "source_url": "",
                        "fetched_at": "2026-06-03T15:10:00+08:00",
                    },
                }
            }
        }

        updated, promoted = module.build_promotion(config, health)

        self.assertEqual(promoted, [])
        self.assertFalse(updated["sources"]["national_team"]["surfaces"]["email"])
        self.assertFalse(updated["sources"]["national_team"]["surfaces"]["dashboard"])


if __name__ == "__main__":
    unittest.main()
