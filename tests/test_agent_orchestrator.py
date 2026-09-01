import argparse
import contextlib
import importlib.util
import io
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

    def test_main_workspace_as_prime_cwd_is_blocked(self):
        with (
            mock.patch.object(orchestrator, "load_config", return_value=self.config),
            mock.patch.object(orchestrator, "resolved_path", side_effect=lambda value: Path(value).resolve()),
            self.assertRaises(orchestrator.OrchestratorError),
        ):
            orchestrator.assert_command_allowed(
                self.prime_command("implement", cwd=orchestrator.ROOT),
                approved_worktree=orchestrator.ROOT,
            )


class BranchPolicyTests(unittest.TestCase):
    def doctor_rows_for(self, branch, gh_returncode=0):
        completed = mock.Mock(returncode=gh_returncode)
        with (
            mock.patch.object(orchestrator, "binary", side_effect=lambda name: f"/usr/bin/{name}"),
            mock.patch.object(orchestrator, "git_branch", return_value=branch),
            mock.patch.object(orchestrator, "run_cmd", return_value=completed),
            mock.patch.dict("sys.modules", {"openai_codex": mock.Mock()}),
        ):
            return orchestrator.doctor_rows()

    def test_main_doctor_passes_protected_branch_policy(self):
        rows, _ = self.doctor_rows_for("main")
        self.assertIn(("보호 브랜치 정책", True, "main은 읽기 전용 기준 브랜치"), rows)

    def test_master_doctor_passes_protected_branch_policy(self):
        rows, _ = self.doctor_rows_for("master")
        self.assertIn(("보호 브랜치 정책", True, "master는 읽기 전용 기준 브랜치"), rows)

    def test_feature_branch_doctor_passes_and_names_branch(self):
        rows, _ = self.doctor_rows_for("feature/safe")
        self.assertIn(("보호 브랜치 정책", True, "feature 브랜치: feature/safe"), rows)

    def test_planner_on_main_is_read_only(self):
        codex = mock.MagicMock()
        codex.thread_start.return_value.run.return_value.final_response = "plan"
        codex.thread_start.return_value.id = "thread"
        codex_context = mock.MagicMock()
        codex_context.__enter__.return_value = codex
        sandbox = mock.Mock(read_only="READ_ONLY")
        module = mock.Mock(Codex=mock.Mock(return_value=codex_context), Sandbox=sandbox)
        with (
            mock.patch.dict("sys.modules", {"openai_codex": module}),
            mock.patch.object(orchestrator, "load_config", return_value={"planner": {"model": "model", "reasoning_effort": "medium"}}),
        ):
            orchestrator.codex_turn("planner", "inspect", orchestrator.ROOT)
        self.assertEqual(codex.thread_start.call_args.kwargs["sandbox"], "READ_ONLY")
        self.assertEqual(codex.thread_start.return_value.run.call_args.kwargs["sandbox"], "READ_ONLY")

    def test_direct_file_modification_stage_on_main_is_blocked(self):
        with self.assertRaises(orchestrator.OrchestratorError):
            orchestrator.assert_feature_worktree(orchestrator.ROOT)

    def test_prime_allowed_after_external_feature_worktree_creation(self):
        worktree = Path("/workspaces/agent-orchestrator-worktrees/test-feature")
        with mock.patch.object(orchestrator, "git_branch", return_value="feature/test"):
            orchestrator.assert_feature_worktree(worktree)

    def test_github_auth_success_has_no_failure_guidance(self):
        rows, all_ok = self.doctor_rows_for("main", 0)
        with (
            mock.patch.object(orchestrator, "doctor_rows", return_value=(rows, all_ok)),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            orchestrator.cmd_doctor(argparse.Namespace())
        self.assertNotIn("GitHub 인증 실패는", output.getvalue())

    def test_github_auth_failure_prints_issue_guidance(self):
        rows, all_ok = self.doctor_rows_for("main", 1)
        with (
            mock.patch.object(orchestrator, "doctor_rows", return_value=(rows, all_ok)),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            orchestrator.cmd_doctor(argparse.Namespace())
        self.assertIn("GitHub 인증 실패는 --issue 실행을 막지만", output.getvalue())


if __name__ == "__main__":
    unittest.main()
