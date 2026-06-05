from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/commit_screening_data.sh"


def run_git(args, cwd, **kwargs):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
        **kwargs,
    )


class GitHubWorkflowTest(unittest.TestCase):
    def test_generated_runtime_outputs_are_ignored_on_main(self):
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

        self.assertIn("data/france.md", gitignore)
        self.assertIn("data/snapshot_manifest.json", gitignore)
        self.assertIn("reports/*.json", gitignore)

    def test_daily_screening_pushes_generated_data_to_snapshot_branch(self):
        workflow = (ROOT / ".github/workflows/daily-screening.yml").read_text(encoding="utf-8")

        self.assertIn("permissions:", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("concurrency:", workflow)
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("DATA_BRANCH: data-snapshots", workflow)
        self.assertIn("bash scripts/restore_screening_data.sh", workflow)
        self.assertIn("bash scripts/commit_screening_data.sh", workflow)
        self.assertNotIn("git push origin main", workflow)

    def test_daily_screening_snapshot_script_has_stale_sha_and_retry_guards(self):
        script = SCRIPT.read_text(encoding="utf-8")
        restore_script = (ROOT / "scripts/restore_screening_data.sh").read_text(encoding="utf-8")

        self.assertIn("DATA_BRANCH=\"${DATA_BRANCH:-data-snapshots}\"", script)
        self.assertIn("origin/${BASE_BRANCH}", script)
        self.assertIn("SKIP_DATA_SNAPSHOT_COMMIT", script)
        self.assertIn("for attempt in 1 2 3", script)
        self.assertIn("git pull --rebase origin \"${DATA_BRANCH}\"", script)
        self.assertIn("git push origin \"HEAD:${DATA_BRANCH}\"", script)
        self.assertIn("git add -f", script)
        self.assertIn("data/source_health.json", script)
        self.assertNotIn("git push origin main", script)
        self.assertIn("DATA_BRANCH=\"${DATA_BRANCH:-data-snapshots}\"", restore_script)
        self.assertIn("git show \"origin/${DATA_BRANCH}:${file}\"", restore_script)
        self.assertIn('restore_file "data/france.md"', restore_script)
        self.assertIn('restore_file "reports/latest.json"', restore_script)

    def test_daily_screening_snapshot_script_handles_generated_changes_without_branch_checkout_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            remote = base / "remote.git"
            repo = base / "repo"

            run_git(["init", "--bare", remote.as_posix()], cwd=base)
            run_git(["clone", remote.as_posix(), repo.as_posix()], cwd=base)
            run_git(["config", "user.name", "Test User"], cwd=repo)
            run_git(["config", "user.email", "test@example.com"], cwd=repo)

            (repo / "data").mkdir()
            (repo / "reports").mkdir()
            (repo / "data/france.md").write_text("old main data\n", encoding="utf-8")
            (repo / "reports/latest.json").write_text('{"old": true}\n', encoding="utf-8")
            run_git(["add", "data/france.md", "reports/latest.json"], cwd=repo)
            run_git(["commit", "-m", "initial main data"], cwd=repo)
            run_git(["branch", "-M", "main"], cwd=repo)
            run_git(["push", "-u", "origin", "main"], cwd=repo)

            run_git(["checkout", "--orphan", "data-snapshots"], cwd=repo)
            run_git(["rm", "-rf", "."], cwd=repo)
            (repo / "data").mkdir()
            (repo / "reports").mkdir()
            (repo / "data/france.md").write_text("old snapshot data\n", encoding="utf-8")
            (repo / "reports/latest.json").write_text('{"snapshot": true}\n', encoding="utf-8")
            run_git(["add", "data/france.md", "reports/latest.json"], cwd=repo)
            run_git(["commit", "-m", "initial snapshot data"], cwd=repo)
            run_git(["push", "-u", "origin", "data-snapshots"], cwd=repo)

            run_git(["checkout", "main"], cwd=repo)
            script_path = repo / "scripts/commit_screening_data.sh"
            script_path.parent.mkdir()
            shutil.copy2(SCRIPT, script_path)

            (repo / "data/france.md").write_text("generated main data\n", encoding="utf-8")
            (repo / "reports/latest.json").write_text('{"generated": true}\n', encoding="utf-8")

            env = os.environ.copy()
            env.update(
                {
                    "BASE_BRANCH": "main",
                    "DATA_BRANCH": "data-snapshots",
                    "GIT_AUTHOR_NAME": "github-actions[bot]",
                    "GIT_AUTHOR_EMAIL": "github-actions[bot]@users.noreply.github.com",
                    "GIT_COMMITTER_NAME": "github-actions[bot]",
                    "GIT_COMMITTER_EMAIL": "github-actions[bot]@users.noreply.github.com",
                }
            )
            result = subprocess.run(
                ["bash", script_path.as_posix()],
                cwd=repo,
                env=env,
                text=True,
                capture_output=True,
            )

            self.assertEqual(
                result.returncode,
                0,
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
            )
            self.assertEqual(run_git(["branch", "--show-current"], cwd=repo).stdout.strip(), "main")
            self.assertEqual((repo / "data/france.md").read_text(encoding="utf-8"), "generated main data\n")

            verify = base / "verify"
            run_git(["clone", "--branch", "data-snapshots", remote.as_posix(), verify.as_posix()], cwd=base)
            self.assertEqual((verify / "data/france.md").read_text(encoding="utf-8"), "generated main data\n")
            self.assertEqual((verify / "reports/latest.json").read_text(encoding="utf-8"), '{"generated": true}\n')
            self.assertTrue((verify / "data/snapshot_manifest.json").exists())

    def test_optional_source_promotion_workflow_opens_manual_review_pr(self):
        workflow = (ROOT / ".github/workflows/optional-source-promotion.yml").read_text(encoding="utf-8")

        self.assertIn("Optional Source Promotion", workflow)
        self.assertIn("pull-requests: write", workflow)
        self.assertIn("data-snapshots", workflow)
        self.assertIn("scripts/check_optional_source_promotion.py", workflow)
        self.assertIn("git add -f config/optional_sources.json reports/optional-source-promotion.md", workflow)
        self.assertIn("gh pr create", workflow)

    def test_overnight_arbitrage_workflow_runs_before_tail_window_and_uses_snapshot_branch(self):
        workflow = (ROOT / ".github/workflows/overnight-arbitrage.yml").read_text(encoding="utf-8")

        self.assertIn("Overnight Arbitrage Decision", workflow)
        self.assertIn("43 6 * * 1-5", workflow)
        self.assertIn("DATA_BRANCH: data-snapshots", workflow)
        self.assertIn("bash scripts/restore_screening_data.sh", workflow)
        self.assertIn("python -m backend.main --run-overnight-arbitrage", workflow)
        self.assertIn("bash scripts/commit_screening_data.sh", workflow)
        self.assertNotIn("python -m backend.main\\n", workflow)
        self.assertNotIn("git push origin main", workflow)

    def test_render_blueprint_has_independent_overnight_arbitrage_cron(self):
        blueprint = (ROOT / "render.yaml").read_text(encoding="utf-8")

        self.assertIn("new-france-overnight-arbitrage", blueprint)
        self.assertIn('schedule: "43 6 * * 1-5"', blueprint)
        self.assertIn("python -m backend.main --run-overnight-arbitrage", blueprint)


if __name__ == "__main__":
    unittest.main()
