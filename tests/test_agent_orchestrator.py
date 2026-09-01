import argparse
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "agent_orchestrator.py"
SPEC = importlib.util.spec_from_file_location("agent_orchestrator", MODULE_PATH)
orchestrator = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(orchestrator)


class PlannerDecisionTests(unittest.TestCase):
    def run_plan(self, response):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            state_file = runtime / "state.json"
            report_file = runtime / "final-report.md"
            state = orchestrator.new_state("test task", "text", False)
            with (
                mock.patch.object(orchestrator, "STATE_FILE", state_file),
                mock.patch.object(orchestrator, "REPORT_FILE", report_file),
                mock.patch.object(orchestrator, "codex_turn", return_value=("thread", response)),
                mock.patch.object(orchestrator, "approval", return_value=False) as approval,
                mock.patch.object(orchestrator, "create_worktree") as create_worktree,
                mock.patch.object(orchestrator, "run_prime") as run_prime,
            ):
                result = orchestrator.continue_run(state)
                saved = orchestrator.load_json(state_file)
                report_exists = report_file.exists()
            return result, saved, report_exists, approval, create_worktree, run_prime

    def test_no_changes_ignores_stop_required_words_in_body(self):
        result, state, report, approval, worktree, prime = self.run_plan(
            "DECISION: NO_CHANGES\nSTOP_REQUIRED 해당 없음"
        )
        self.assertEqual(result, 0)
        self.assertEqual(state["status"], "complete")
        self.assertEqual(state["outcome"], "no_changes")
        self.assertTrue(report)
        approval.assert_not_called()
        worktree.assert_not_called()
        prime.assert_not_called()

    def test_no_changes_ignores_database_words_in_body(self):
        result, state, _, approval, worktree, prime = self.run_plan(
            "DECISION: NO_CHANGES\n데이터베이스 변경 불필요"
        )
        self.assertEqual(result, 0)
        self.assertEqual(state["status"], "complete")
        approval.assert_not_called()
        worktree.assert_not_called()
        prime.assert_not_called()

    def test_proceed_moves_to_approval(self):
        result, state, _, approval, worktree, prime = self.run_plan(
            "DECISION: PROCEED\nSTOP_REQUIRED, 데이터베이스, 인증, 배포는 모두 불필요"
        )
        self.assertEqual(result, 2)
        self.assertEqual(state["status"], "awaiting_plan_approval")
        approval.assert_called_once()
        worktree.assert_not_called()
        prime.assert_not_called()

    def test_stop_required_stops_safely(self):
        result, state, report, approval, worktree, prime = self.run_plan(
            "DECISION: STOP_REQUIRED\n인증 변경이 필요합니다"
        )
        self.assertEqual(result, 0)
        self.assertEqual(state["outcome"], "stop_required")
        self.assertTrue(report)
        approval.assert_not_called()
        worktree.assert_not_called()
        prime.assert_not_called()

    def test_missing_decision_is_format_error(self):
        with self.assertRaises(orchestrator.OrchestratorError):
            self.run_plan("변경 계획입니다")

    def test_invalid_decision_is_format_error(self):
        with self.assertRaises(orchestrator.OrchestratorError):
            self.run_plan("DECISION: MAYBE\n변경 계획입니다")


class CommandSafetyTests(unittest.TestCase):
    def setUp(self):
        self.worktree = Path("/tmp/agent-test-worktree")
        self.session = orchestrator.RUNTIME_DIR / "prime-sessions" / "test-run"
        self.prime = "/opt/prime/bin/prime-agent"
        self.config = {"prime_agent": {"command": self.prime}}

    def prime_command(self, prompt, cwd=None, session=None):
        return [
            self.prime, "--print", "--cwd", str(cwd or self.worktree),
            "--session-dir", str(session or self.session), "--", prompt,
        ]

    def assert_prime_allowed(self, prompt):
        with (
            mock.patch.object(orchestrator, "load_config", return_value=self.config),
            mock.patch.object(orchestrator, "resolved_path", side_effect=lambda value: Path(value).resolve()),
        ):
            orchestrator.assert_command_allowed(
                self.prime_command(prompt), approved_worktree=self.worktree,
            )

    def test_prime_prompt_git_push_is_data(self):
        self.assert_prime_allowed("Never run git push")

    def test_prime_prompt_wrangler_deploy_is_data(self):
        self.assert_prime_allowed("wrangler deploy 금지")

    def test_git_push_is_blocked(self):
        with self.assertRaises(orchestrator.OrchestratorError):
            orchestrator.assert_command_allowed(["git", "push", "origin", "main"])

    def test_git_merge_is_blocked(self):
        with self.assertRaises(orchestrator.OrchestratorError):
            orchestrator.assert_command_allowed(["git", "merge", "feature"])

    def test_wrangler_pages_deploy_is_blocked(self):
        with self.assertRaises(orchestrator.OrchestratorError):
            orchestrator.assert_command_allowed(["wrangler", "pages", "deploy", "."])

    def test_gh_pr_create_is_blocked(self):
        with self.assertRaises(orchestrator.OrchestratorError):
            orchestrator.assert_command_allowed(["gh", "pr", "create"])

    def test_bash_c_git_push_is_blocked(self):
        with self.assertRaises(orchestrator.OrchestratorError):
            orchestrator.assert_command_allowed(["bash", "-c", "git push origin main"])

    def test_sh_c_wrangler_deploy_is_blocked(self):
        with self.assertRaises(orchestrator.OrchestratorError):
            orchestrator.assert_command_allowed(["sh", "-c", "wrangler deploy"])

    def test_prime_cwd_outside_approved_worktree_is_blocked(self):
        with (
            mock.patch.object(orchestrator, "load_config", return_value=self.config),
            mock.patch.object(orchestrator, "resolved_path", side_effect=lambda value: Path(value).resolve()),
            self.assertRaises(orchestrator.OrchestratorError),
        ):
            orchestrator.assert_command_allowed(
                self.prime_command("implement", cwd=Path("/tmp/other-worktree")),
                approved_worktree=self.worktree,
            )

    def test_prime_session_outside_runtime_is_blocked(self):
        with (
            mock.patch.object(orchestrator, "load_config", return_value=self.config),
            mock.patch.object(orchestrator, "resolved_path", side_effect=lambda value: Path(value).resolve()),
            self.assertRaises(orchestrator.OrchestratorError),
        ):
            orchestrator.assert_command_allowed(
                self.prime_command("implement", session=Path("/tmp/prime-session")),
                approved_worktree=self.worktree,
            )


if __name__ == "__main__":
    unittest.main()
