from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class GitHubWorkflowTest(unittest.TestCase):
    def test_daily_screening_can_push_generated_data(self):
        workflow = (ROOT / ".github/workflows/daily-screening.yml").read_text(encoding="utf-8")

        self.assertIn("permissions:", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("git push origin main", workflow)


if __name__ == "__main__":
    unittest.main()
