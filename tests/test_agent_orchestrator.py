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

    def test_gh_pr_merge_and_ready_are_blocked(self):
        for command in (["gh", "pr", "merge", "5"], ["gh", "pr", "ready", "5"]):
            with self.assertRaises(orchestrator.OrchestratorError):
                orchestrator.assert_command_allowed(command)

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


class ExternalApprovalTests(unittest.TestCase):
    def external_state(self):
        state = orchestrator.new_state("test", "text", False)
        state.update({
            "branch": "agent/issue-5", "worktree": "/tmp/agent-test-worktree",
            "worktree_owned": True, "pushed": False, "commit": "abc123",
            "completed_steps": ["external_approval"],
        })
        return state

    def test_push_requires_external_approval(self):
        state = self.external_state()
        state["completed_steps"] = []
        with self.assertRaises(orchestrator.OrchestratorError):
            orchestrator.approved_feature_push(state, Path(state["worktree"]))

    def test_exact_approved_push_is_the_only_push_command(self):
        state = self.external_state()
        completed = mock.Mock()
        with (
            mock.patch.object(orchestrator, "assert_approved_external_state", return_value=state["branch"]),
            mock.patch.object(orchestrator.subprocess, "run", return_value=completed) as execute,
        ):
            orchestrator.approved_feature_push(state, Path(state["worktree"]))
        execute.assert_called_once_with(
            ["git", "push", "--set-upstream", "origin", "agent/issue-5"],
            cwd=Path(state["worktree"]), check=True, text=True, capture_output=True, timeout=300,
        )

    def test_no_arbitrary_external_command_runner_exists(self):
        self.assertFalse(hasattr(orchestrator, "_run_approved_external"))
        self.assertEqual(
            list(__import__("inspect").signature(orchestrator.approved_feature_push).parameters),
            ["state", "worktree"],
        )

    def test_non_agent_branch_is_blocked(self):
        state = self.external_state()
        state["branch"] = "feature/issue-5"
        with (
            mock.patch.object(orchestrator, "git_branch") as branch,
            mock.patch.object(orchestrator.subprocess, "run") as execute,
            self.assertRaises(orchestrator.OrchestratorError),
        ):
            orchestrator.approved_feature_push(state, Path(state["worktree"]))
        branch.assert_not_called()
        execute.assert_not_called()

    def test_dirty_worktree_is_blocked(self):
        state = self.external_state()
        with (
            mock.patch.object(orchestrator, "git_branch", return_value=state["branch"]),
            mock.patch.object(orchestrator, "git_status", return_value=" M unsafe.txt\n"),
            mock.patch.object(orchestrator.subprocess, "run") as execute,
            self.assertRaises(orchestrator.OrchestratorError),
        ):
            orchestrator.approved_feature_push(state, Path(state["worktree"]))
        execute.assert_not_called()

    def test_head_commit_mismatch_is_blocked(self):
        state = self.external_state()
        with (
            mock.patch.object(orchestrator, "git_branch", return_value=state["branch"]),
            mock.patch.object(orchestrator, "git_status", return_value=""),
            mock.patch.object(orchestrator, "run_cmd", return_value=mock.Mock(stdout="different\n")),
            mock.patch.object(orchestrator.subprocess, "run") as execute,
            self.assertRaises(orchestrator.OrchestratorError),
        ):
            orchestrator.approved_feature_push(state, Path(state["worktree"]))
        execute.assert_not_called()

    def test_clean_matching_agent_branch_and_commit_are_approved(self):
        state = self.external_state()
        with (
            mock.patch.object(orchestrator, "git_branch", return_value=state["branch"]),
            mock.patch.object(orchestrator, "git_status", return_value=""),
            mock.patch.object(orchestrator, "run_cmd", side_effect=[
                mock.Mock(stdout=f"{state['commit']}\n"), mock.Mock(stdout="origin-url\n"),
            ]),
        ):
            self.assertEqual(
                orchestrator.assert_approved_external_state(state, Path(state["worktree"])),
                state["branch"],
            )

    def test_draft_pr_uses_explicit_main_base_and_state_head(self):
        state = self.external_state()
        with (
            mock.patch.object(orchestrator, "assert_approved_external_state", return_value=state["branch"]),
            mock.patch.object(orchestrator.subprocess, "run", return_value=mock.Mock(stdout="url\n")) as execute,
        ):
            orchestrator.approved_draft_pr_create(state, Path(state["worktree"]))
        execute.assert_called_once_with(
            ["gh", "pr", "create", "--draft", "--base", "main", "--head", "agent/issue-5", "--fill"],
            cwd=Path(state["worktree"]), check=True, text=True, capture_output=True, timeout=300,
        )

    def test_existing_pr_query_uses_head_without_base_and_stops_on_other_base(self):
        state = self.external_state()
        other_base_pr = {"baseRefName": "other", "headRefName": state["branch"], "isDraft": True}
        with (
            mock.patch.object(orchestrator, "assert_approved_external_state", return_value=state["branch"]),
            mock.patch.object(orchestrator, "run_cmd", return_value=
                              mock.Mock(stdout=__import__("json").dumps([other_base_pr]))) as run_cmd,
            self.assertRaises(orchestrator.OrchestratorError),
        ):
            orchestrator.existing_draft_pr(state, Path(state["worktree"]))
        self.assertEqual(
            run_cmd.call_args,
            mock.call(
                ["gh", "pr", "list", "--state", "open", "--head", state["branch"],
                 "--json", "number,url,isDraft,baseRefName,headRefName"],
                cwd=Path(state["worktree"]), timeout=300,
            ),
        )

    def test_existing_ready_or_mismatched_pr_stops(self):
        state = self.external_state()
        for pr in (
            {"baseRefName": "main", "headRefName": state["branch"], "isDraft": False},
            {"baseRefName": "other", "headRefName": state["branch"], "isDraft": True},
        ):
            with (
                mock.patch.object(orchestrator, "assert_approved_external_state", return_value=state["branch"]),
                mock.patch.object(orchestrator, "run_cmd", return_value=
                                  mock.Mock(stdout=__import__("json").dumps([pr]))),
                self.assertRaises(orchestrator.OrchestratorError),
            ):
                orchestrator.existing_draft_pr(state, Path(state["worktree"]))

    def test_push_failure_keeps_pushed_false(self):
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "state.json"
            state = self.external_state()
            state.update({
                "plan": "DECISION: PROCEED", "review": "VERDICT: PASS",
                "completed_steps": [
                    "planned", "plan_approved", "worktree_created", "prime_implemented", "checks_initial",
                    "reviewed", "checks_final", "report_written", "external_approval", "commit_created",
                ],
            })
            with (
                mock.patch.object(orchestrator, "STATE_FILE", state_file),
                mock.patch.object(orchestrator, "approved_feature_push", side_effect=orchestrator.OrchestratorError("push failed")),
                self.assertRaises(orchestrator.OrchestratorError),
            ):
                orchestrator.continue_run(state)
            self.assertFalse(orchestrator.load_json(state_file)["pushed"])

    def test_successful_push_sets_pushed_true(self):
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "state.json"
            state = self.external_state()
            state.update({
                "plan": "DECISION: PROCEED", "review": "VERDICT: PASS",
                "completed_steps": [
                    "planned", "plan_approved", "worktree_created", "prime_implemented", "checks_initial",
                    "reviewed", "checks_final", "report_written", "external_approval", "commit_created",
                ],
            })
            pr = {"number": 5, "url": "https://example.test/pr/5", "baseRefName": "main", "headRefName": state["branch"], "isDraft": True}
            with (
                mock.patch.object(orchestrator, "STATE_FILE", state_file),
                mock.patch.object(orchestrator, "approved_feature_push") as push,
                mock.patch.object(orchestrator, "existing_draft_pr", return_value=pr),
            ):
                self.assertEqual(orchestrator.continue_run(state), 0)
            push.assert_called_once()
            self.assertTrue(orchestrator.load_json(state_file)["pushed"])

    def test_resume_reuses_existing_draft_without_create(self):
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "state.json"
            state = self.external_state()
            state.update({
                "plan": "DECISION: PROCEED", "review": "VERDICT: PASS", "pushed": True,
                "completed_steps": [
                    "planned", "plan_approved", "worktree_created", "prime_implemented", "checks_initial",
                    "reviewed", "checks_final", "report_written", "external_approval", "commit_created", "branch_pushed",
                ],
            })
            pr = {"number": 5, "url": "https://example.test/pr/5", "baseRefName": "main", "headRefName": state["branch"], "isDraft": True}
            with (
                mock.patch.object(orchestrator, "STATE_FILE", state_file),
                mock.patch.object(orchestrator, "existing_draft_pr", return_value=pr),
                mock.patch.object(orchestrator, "approved_draft_pr_create") as create,
            ):
                self.assertEqual(orchestrator.continue_run(state), 0)
            create.assert_not_called()
            saved = orchestrator.load_json(state_file)
            self.assertEqual(saved["status"], "complete")
            self.assertEqual(saved["draft_pr"], pr)


if __name__ == "__main__":
    unittest.main()
