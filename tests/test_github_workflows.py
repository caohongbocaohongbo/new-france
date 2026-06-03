from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class GitHubWorkflowTest(unittest.TestCase):
    def test_daily_screening_pushes_generated_data_to_snapshot_branch(self):
        workflow = (ROOT / ".github/workflows/daily-screening.yml").read_text(encoding="utf-8")

        self.assertIn("permissions:", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("concurrency:", workflow)
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("DATA_BRANCH: data-snapshots", workflow)
        self.assertIn("bash scripts/commit_screening_data.sh", workflow)
        self.assertNotIn("git push origin main", workflow)

    def test_daily_screening_snapshot_script_has_stale_sha_and_retry_guards(self):
        script = (ROOT / "scripts/commit_screening_data.sh").read_text(encoding="utf-8")

        self.assertIn("DATA_BRANCH=\"${DATA_BRANCH:-data-snapshots}\"", script)
        self.assertIn("origin/${BASE_BRANCH}", script)
        self.assertIn("SKIP_DATA_SNAPSHOT_COMMIT", script)
        self.assertIn("for attempt in 1 2 3", script)
        self.assertIn("git pull --rebase origin \"${DATA_BRANCH}\"", script)
        self.assertIn("git push origin \"HEAD:${DATA_BRANCH}\"", script)
        self.assertIn("git add -f", script)
        self.assertIn("data/source_health.json", script)
        self.assertNotIn("git push origin main", script)

    def test_optional_source_promotion_workflow_opens_manual_review_pr(self):
        workflow = (ROOT / ".github/workflows/optional-source-promotion.yml").read_text(encoding="utf-8")

        self.assertIn("Optional Source Promotion", workflow)
        self.assertIn("pull-requests: write", workflow)
        self.assertIn("data-snapshots", workflow)
        self.assertIn("scripts/check_optional_source_promotion.py", workflow)
        self.assertIn("git add -f config/optional_sources.json reports/optional-source-promotion.md", workflow)
        self.assertIn("gh pr create", workflow)


if __name__ == "__main__":
    unittest.main()
