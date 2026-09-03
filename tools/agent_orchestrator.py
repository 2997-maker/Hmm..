#!/usr/bin/env python3
"""Approval-gated local Codex SDK + Prime Agent orchestration."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = ROOT / ".agent"
RUNTIME_DIR = AGENT_DIR / "runtime"
STATE_FILE = RUNTIME_DIR / "state.json"
REPORT_FILE = RUNTIME_DIR / "final-report.md"
CONFIG_FILE = AGENT_DIR / "config.json"
VENV_PYTHON = AGENT_DIR / "venv" / "bin" / "python"
SENSITIVE_ENV_NAMES = {
    "GITHUB_TOKEN", "GH_TOKEN", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID",
    "CF_API_TOKEN", "CF_API_KEY", "CF_ACCOUNT_ID",
}
HERMES_PROFILE = "orchestrator-advisor"
HERMES_PROVIDER = "openai-codex"
HERMES_MODEL = "gpt-5.6-sol"
HERMES_REASONING = "high"
HERMES_TOOLSETS = "todo"
HERMES_MAX_TURNS = 1
HERMES_RUN_BUDGET_SECONDS = 120
HERMES_MAX_REVIEWS = 2
HERMES_MAX_REPLANS = 1
HERMES_FORBIDDEN_FLAGS = {"--yolo", "--safe-mode", "--ignore-rules", "--gateway", "gateway", "setup", "update", "auth", "login", "logout", "codex"}
SENSITIVE_CHANGE_PATTERNS = (
    re.compile(r"\b(database|migration|schema migration)\b", re.I),
    re.compile(r"\b(authentication|auth provider|oauth|sso)\b", re.I),
    re.compile(r"\b(production config|production setting|major (?:package|dependency) upgrade)\b", re.I),
)
PLANNER_DECISIONS = {"PROCEED", "NO_CHANGES", "STOP_REQUIRED"}
TASK_SIZES = {"SMALL", "MEDIUM", "LARGE"}
PROTECTED_BRANCHES = {"main", "master"}
FIXED_AUTONOMOUS_GATE = "git diff --check"
EXECUTION_BUDGET_LIMITS = {
    "timeout_seconds": 1800,
    "max_fix_attempts": 3,
    "max_continuations": 3,
    "max_turns": 12,
    "max_tokens": 80_000,
}
# These trusted constants are the only source for Prime Agent limits and gates.
EXECUTION_PROFILES: dict[str, dict[str, Any]] = {
    "SMALL": {
        "timeout_seconds": 600, "max_fix_attempts": 1, "autonomous": False,
        "implementation_instruction": "Handle the task directly and do not use recursive subagents.",
    },
    "MEDIUM": {
        "timeout_seconds": 900, "max_fix_attempts": 2, "autonomous": True,
        "max_continuations": 2, "max_turns": 8, "max_tokens": 40_000,
        "implementation_instruction": (
            "Use subagents sparingly, only for subtasks worth investigating independently."
        ),
    },
    "LARGE": {
        "timeout_seconds": 1800, "max_fix_attempts": 3, "autonomous": True,
        "max_continuations": 3, "max_turns": 12, "max_tokens": 80_000,
        "implementation_instruction": (
            "Before implementation, break the task into small steps; for independent investigations, "
            "prefer using `rlm` subagents."
        ),
    },
}


class OrchestratorError(RuntimeError):
    pass


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def new_state(task: str, source: str, dry_run: bool) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "status": "running",
        "stage": "initialized",
        "task": task,
        "source": source,
        "dry_run": dry_run,
        "created_at": now(),
        "updated_at": now(),
        "completed_steps": [],
        "events": [],
        "checks": {},
        "fix_attempts": 0,
    }


def save_state(state: dict[str, Any], stage: str | None = None, message: str | None = None) -> None:
    if stage:
        state["stage"] = stage
    state["updated_at"] = now()
    if message:
        state.setdefault("events", []).append({"at": now(), "stage": state["stage"], "message": message})
    atomic_json(STATE_FILE, state)


def complete_step(state: dict[str, Any], step: str, message: str) -> None:
    if step not in state["completed_steps"]:
        state["completed_steps"].append(step)
    save_state(state, step, message)
    print(f"[완료] {message}")


def fail(state: dict[str, Any], message: str, recovery: str) -> None:
    state["status"] = "failed"
    state["error"] = message
    state["recovery"] = recovery
    save_state(state, "failed", message)
    raise OrchestratorError(f"{message}\n복구 방법: {recovery}")


def command_text(args: Sequence[str]) -> str:
    return shlex.join(str(item) for item in args)


def executable_name(value: str) -> str:
    return Path(value).name.lower()


def resolved_path(value: str) -> Path | None:
    candidate = Path(value)
    located = shutil.which(value) if not candidate.is_absolute() else value
    return Path(located).resolve() if located else None


def prime_profile_options(profile: dict[str, Any]) -> list[str]:
    """Build trusted Prime options; planner text never contributes an option or gate."""
    options = ["--print"]
    if profile["autonomous"]:
        options.extend([
            "--autonomous", "--autonomous-gate", FIXED_AUTONOMOUS_GATE,
            "--autonomous-max-continuations", str(profile["max_continuations"]),
            "--autonomous-max-turns", str(profile["max_turns"]),
            "--autonomous-max-tokens", str(profile["max_tokens"]),
            "--autonomous-timeout-ms", str(profile["timeout_seconds"] * 1000),
        ])
    return options


def prime_argv(command: str, profile: dict[str, Any], worktree: Path, session_dir: Path, prompt: str) -> list[str]:
    return [command, *prime_profile_options(profile), "--cwd", str(worktree), "--session-dir", str(session_dir), "--", prompt]


def assert_prime_command_allowed(args: Sequence[str], approved_worktree: Path) -> None:
    """Validate Prime Agent's trusted profile argv, treating its prompt as data."""
    argv = [str(item) for item in args]
    configured = str(load_config()["prime_agent"]["command"])
    allowed_executable = resolved_path(configured)
    actual_executable = resolved_path(argv[0]) if argv else None
    if allowed_executable is None or actual_executable != allowed_executable:
        raise OrchestratorError("허용되지 않은 Prime Agent 실행 파일입니다.")
    try:
        separator = argv.index("--", 1)
    except ValueError as exc:
        raise OrchestratorError("Prime Agent 명령에 prompt 구분자 `--`가 없습니다.") from exc
    options, prompt = argv[1:separator], argv[separator + 1:]
    if len(prompt) != 1:
        raise OrchestratorError("Prime Agent implementer prompt는 단일 데이터 인자여야 합니다.")
    try:
        cwd_index, session_index = options.index("--cwd"), options.index("--session-dir")
        if cwd_index + 1 >= len(options) or session_index + 1 >= len(options):
            raise ValueError
        values = {"--cwd": options[cwd_index + 1], "--session-dir": options[session_index + 1]}
        profile_options = options[:cwd_index]
        if options[cwd_index:session_index] != ["--cwd", values["--cwd"]] or options[session_index:] != ["--session-dir", values["--session-dir"]]:
            raise ValueError
    except ValueError as exc:
        raise OrchestratorError("허용되지 않은 Prime Agent 옵션입니다.") from exc
    allowed_options = [prime_profile_options(execution_profile(size)) for size in TASK_SIZES]
    if profile_options not in allowed_options:
        raise OrchestratorError("Prime Agent 옵션은 신뢰된 실행 프로필과 정확히 일치해야 합니다.")
    expected_worktree = approved_worktree.resolve()
    if expected_worktree == ROOT.resolve() or ROOT.resolve() in expected_worktree.parents:
        raise OrchestratorError("Prime Agent --cwd는 저장소 외부의 feature worktree여야 합니다.")
    if resolved_path(values["--cwd"]) != expected_worktree:
        raise OrchestratorError("Prime Agent --cwd가 승인된 feature worktree와 다릅니다.")
    session = resolved_path(values["--session-dir"])
    runtime = RUNTIME_DIR.resolve()
    if session is None or session == runtime or runtime not in session.parents:
        raise OrchestratorError("Prime Agent --session-dir은 .agent/runtime 아래여야 합니다.")


def verify_prime_cli(profile: dict[str, Any]) -> dict[str, str]:
    """Check the locally installed CLI without updating or falling back."""
    command = str(load_config()["prime_agent"]["command"])
    results: dict[str, str] = {}
    for flag, label in (("--version", "version"), ("--help", "help")):
        try:
            result = subprocess.run([command, flag], check=False, text=True, capture_output=True, timeout=30)
        except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired) as exc:
            raise OrchestratorError(f"Prime Agent {flag} 확인을 실행할 수 없습니다.") from exc
        if result.returncode != 0:
            raise OrchestratorError(f"Prime Agent {flag} 확인에 실패했습니다.")
        results[label] = (result.stdout or result.stderr).strip()
    if profile["autonomous"]:
        required = {"--autonomous", "--autonomous-gate", "--autonomous-max-continuations", "--autonomous-max-turns", "--autonomous-max-tokens", "--autonomous-timeout-ms"}
        if not required <= set(re.findall(r"--[a-z-]+", results["help"])):
            raise OrchestratorError("설치된 Prime Agent가 필요한 autonomous 옵션을 지원하지 않습니다. 업데이트하거나 다른 방식으로 전환하지 않고 중단합니다.")
    return {"version": results["version"].splitlines()[0] if results["version"] else "(unknown)", "autonomous_supported": str(profile["autonomous"]).lower()}


def assert_command_allowed(
    args: Sequence[str], *, approved_worktree: Path | None = None,
    approved_hermes_executable: str | None = None,
) -> None:
    """Inspect executable and argv structure without scanning ordinary data arguments."""
    argv = [str(item) for item in args]
    if not argv:
        raise OrchestratorError("빈 명령은 실행할 수 없습니다.")
    name = executable_name(argv[0])
    configured_prime = str(load_config()["prime_agent"]["command"])
    if name == "hermes":
        assert_hermes_command_allowed(argv, approved_hermes_executable)
        return
    if name == executable_name(configured_prime):
        if approved_worktree is None:
            raise OrchestratorError("Prime Agent 실행에는 승인된 feature worktree가 필요합니다.")
        assert_prime_command_allowed(argv, approved_worktree)
        return

    tail = [item.lower() for item in argv[1:]]
    forbidden = (
        (name == "git" and bool(tail) and tail[0] in {"push", "merge"})
        or (name == "gh" and tail[:2] in (["pr", "create"], ["pr", "merge"], ["pr", "ready"]))
        or (name == "wrangler" and (tail[:1] == ["deploy"] or tail[:2] == ["pages", "deploy"]))
    )
    if forbidden:
        raise OrchestratorError(f"금지된 명령을 차단했습니다: {command_text(argv)}")
    if name in {"bash", "sh"} and tail[:1] == ["-c"] and len(argv) >= 3:
        try:
            nested = shlex.split(argv[2])
        except ValueError as exc:
            raise OrchestratorError("셸 -c 명령을 안전하게 분석할 수 없습니다.") from exc
        assert_command_allowed(nested)


def run_cmd(
    args: Sequence[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None,
    check: bool = True, timeout: int | None = 120, input_text: str | None = None,
    approved_prime_worktree: Path | None = None,
    approved_hermes_executable: str | None = None,
) -> subprocess.CompletedProcess[str]:
    assert_command_allowed(
        args, approved_worktree=approved_prime_worktree,
        approved_hermes_executable=approved_hermes_executable,
    )
    try:
        return subprocess.run(
            [str(item) for item in args], cwd=cwd, env=env, check=check, text=True,
            capture_output=True, timeout=timeout, input=input_text,
        )
    except subprocess.TimeoutExpired as exc:
        raise OrchestratorError(f"명령 시간이 초과되었습니다: {command_text(args)}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "출력 없음").strip()
        raise OrchestratorError(f"명령 실패: {command_text(args)}\n{detail}") from exc


def assert_approved_external_state(state: dict[str, Any], worktree: Path) -> str:
    """Validate the state-owned feature branch before an approved external action."""
    if "external_approval" not in state.get("completed_steps", []):
        raise OrchestratorError("사용자 최종 승인 전에는 외부 작업을 실행할 수 없습니다.")
    branch = state.get("branch")
    if (
        not isinstance(branch, str) or not branch.startswith("agent/")
        or branch in PROTECTED_BRANCHES or not re.fullmatch(r"[A-Za-z0-9._/-]+", branch)
    ):
        raise OrchestratorError("승인된 상태의 feature branch가 아닙니다.")
    if not state.get("worktree_owned") or Path(state.get("worktree", "")).resolve() != worktree.resolve():
        raise OrchestratorError("오케스트레이터 소유로 검증되지 않은 worktree입니다.")
    if git_branch(worktree) != branch:
        raise OrchestratorError("현재 branch가 실행 상태의 feature branch와 다릅니다.")
    if git_status(worktree).strip():
        raise OrchestratorError("외부 작업 전 worktree가 깨끗해야 합니다.")
    commit = state.get("commit")
    head = run_cmd(["git", "rev-parse", "HEAD"], cwd=worktree).stdout.strip()
    if not isinstance(commit, str) or not commit or head != commit:
        raise OrchestratorError("현재 HEAD가 실행 상태에 저장된 commit과 다릅니다.")
    if not run_cmd(["git", "remote", "get-url", "origin"], cwd=worktree).stdout.strip():
        raise OrchestratorError("origin remote가 구성되어 있지 않습니다.")
    return branch


def approved_feature_push(state: dict[str, Any], worktree: Path) -> None:
    """Push only the state-owned feature branch to origin with one fixed refspec."""
    branch = assert_approved_external_state(state, worktree)
    args = ["git", "push", "--set-upstream", "origin", branch]
    try:
        subprocess.run(args, cwd=worktree, check=True, text=True, capture_output=True, timeout=300)
    except subprocess.TimeoutExpired as exc:
        raise OrchestratorError(f"명령 시간이 초과되었습니다: {command_text(args)}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "출력 없음").strip()
        raise OrchestratorError(f"명령 실패: {command_text(args)}\n{detail}") from exc


def approved_draft_pr_create(state: dict[str, Any], worktree: Path) -> subprocess.CompletedProcess[str]:
    """Create only a Draft PR from the state branch to main."""
    branch = assert_approved_external_state(state, worktree)
    args = ["gh", "pr", "create", "--draft", "--base", "main", "--head", branch, "--fill"]
    try:
        return subprocess.run(
            args, cwd=worktree, check=True, text=True, capture_output=True, timeout=300,
        )
    except subprocess.TimeoutExpired as exc:
        raise OrchestratorError(f"명령 시간이 초과되었습니다: {command_text(args)}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "출력 없음").strip()
        raise OrchestratorError(f"명령 실패: {command_text(args)}\n{detail}") from exc


def existing_draft_pr(state: dict[str, Any], worktree: Path) -> dict[str, Any] | None:
    """Return an exactly matching existing Draft PR, or stop on any inconsistency."""
    branch = assert_approved_external_state(state, worktree)
    result = run_cmd([
        "gh", "pr", "list", "--state", "open", "--head", branch,
        "--json", "number,url,isDraft,baseRefName,headRefName",
    ], cwd=worktree, timeout=300)
    try:
        prs = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise OrchestratorError("GitHub CLI PR 조회 결과를 해석할 수 없습니다.") from exc
    if not isinstance(prs, list):
        raise OrchestratorError("GitHub CLI PR 조회 결과 형식이 올바르지 않습니다.")
    if not prs:
        return None
    if len(prs) != 1:
        raise OrchestratorError("일치하는 기존 PR이 여러 개여서 자동 재사용하지 않습니다.")
    pr = prs[0]
    if not isinstance(pr, dict) or (
        pr.get("baseRefName") != "main" or pr.get("headRefName") != branch or not pr.get("isDraft")
    ):
        raise OrchestratorError("기존 PR이 정확히 일치하는 Draft PR이어서만 재사용할 수 있습니다.")
    return pr


def binary(name: str) -> str | None:
    return shutil.which(name)


def git_branch(cwd: Path = ROOT) -> str:
    return run_cmd(["git", "branch", "--show-current"], cwd=cwd).stdout.strip()


def git_status(cwd: Path = ROOT) -> str:
    return run_cmd(["git", "status", "--porcelain"], cwd=cwd).stdout


def git_tracked_status(cwd: Path = ROOT) -> str:
    """Return tracked changes only; user-owned untracked files are out of scope."""
    return run_cmd(["git", "status", "--porcelain", "--untracked-files=no"], cwd=cwd).stdout


def assert_feature_worktree(cwd: Path) -> None:
    """Require write-capable stages to use an external, non-protected worktree."""
    resolved = cwd.resolve()
    if resolved == ROOT.resolve():
        raise OrchestratorError("main/master 기준 작업공간에서는 직접 파일 수정 단계를 실행할 수 없습니다.")
    if ROOT.resolve() in resolved.parents:
        raise OrchestratorError("구현 worktree는 저장소 외부에 있어야 합니다.")
    if git_branch(resolved) in PROTECTED_BRANCHES:
        raise OrchestratorError("main/master 보호 브랜치에서는 파일 수정 단계를 실행할 수 없습니다.")


def load_config() -> dict[str, Any]:
    config = load_json(CONFIG_FILE)
    root = Path(config["worktree_root"])
    if not root.is_absolute() or root == Path("/tmp") or str(root).startswith("/tmp/"):
        raise OrchestratorError("worktree_root는 /tmp가 아닌 절대경로여야 합니다.")
    if root == ROOT or ROOT in root.parents:
        raise OrchestratorError("worktree_root는 저장소 외부여야 합니다.")
    return config


def ensure_venv_reexec() -> None:
    expected_prefix = (AGENT_DIR / "venv").resolve()
    if VENV_PYTHON.exists() and Path(sys.prefix).resolve() != expected_prefix:
        os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])


def doctor_rows() -> tuple[list[tuple[str, bool, str]], bool]:
    rows: list[tuple[str, bool, str]] = []
    rows.append(("Python >= 3.10", sys.version_info >= (3, 10), sys.version.split()[0]))
    rows.append(("Git", binary("git") is not None, binary("git") or "찾을 수 없음"))
    rows.append(("Codex CLI", binary("codex") is not None, binary("codex") or "찾을 수 없음"))
    rows.append(("Prime Agent", binary("prime-agent") is not None, binary("prime-agent") or "찾을 수 없음"))
    rows.append(("GitHub CLI", binary("gh") is not None, binary("gh") or "찾을 수 없음"))
    try:
        import openai_codex  # noqa: F401
        sdk_ok, sdk_detail = True, "openai-codex import 성공"
    except ImportError:
        sdk_ok, sdk_detail = False, f"{VENV_PYTHON} 환경에서 설치 필요"
    rows.append(("Codex SDK", sdk_ok, sdk_detail))
    branch = git_branch() if binary("git") else "unknown"
    if branch == "main":
        branch_detail = "main은 읽기 전용 기준 브랜치"
    elif branch == "master":
        branch_detail = "master는 읽기 전용 기준 브랜치"
    else:
        branch_detail = f"feature 브랜치: {branch}"
    rows.append(("보호 브랜치 정책", True, branch_detail))
    gh_ok = False
    if binary("gh"):
        result = run_cmd(["gh", "auth", "status"], check=False)
        gh_ok = result.returncode == 0
    rows.append(("GitHub 인증", gh_ok, "정상" if gh_ok else "실패: 토큰을 건드리지 말고 `gh auth login` 등 승인된 방식으로 직접 복구"))
    config_ok = CONFIG_FILE.exists()
    rows.append(("설정 파일", config_ok, str(CONFIG_FILE)))
    critical_ok = all(ok for name, ok, _ in rows if name != "GitHub 인증")
    return rows, critical_ok and gh_ok


def cmd_doctor(_: argparse.Namespace) -> int:
    rows, all_ok = doctor_rows()
    for name, ok, detail in rows:
        print(f"[{'OK' if ok else 'FAIL'}] {name}: {detail}")
    gh_ok = next(ok for name, ok, _detail in rows if name == "GitHub 인증")
    if not gh_ok:
        print("GitHub 인증 실패는 --issue 실행을 막지만 --text --dry-run에는 영향을 주지 않습니다.")
    return 0 if all_ok else 1


def read_task(args: argparse.Namespace) -> tuple[str, str]:
    if args.issue is not None:
        if not binary("gh"):
            raise OrchestratorError("GitHub CLI가 없습니다.")
        result = run_cmd([
            "gh", "issue", "view", str(args.issue), "--json", "number,title,body,url",
        ])
        issue = json.loads(result.stdout)
        return (
            f"GitHub Issue #{issue['number']}: {issue['title']}\nURL: {issue['url']}\n\n{issue.get('body') or ''}",
            f"issue:{args.issue}",
        )
    return args.text.strip(), "text"


def codex_turn(role: str, task: str, cwd: Path) -> tuple[str, str]:
    from openai_codex import Codex, Sandbox

    config = load_config()[role]
    prompt_file = AGENT_DIR / "prompts" / f"{role}.md"
    prompt = f"{prompt_file.read_text(encoding='utf-8')}\n\nTASK:\n{task}"
    try:
        with Codex() as codex:
            thread = codex.thread_start(
                cwd=str(cwd), model=config["model"], sandbox=Sandbox.read_only, ephemeral=False,
            )
            result = thread.run(
                prompt, cwd=str(cwd), model=config["model"], effort=config["reasoning_effort"],
                sandbox=Sandbox.read_only,
            )
            return thread.id, result.final_response
    except Exception as exc:
        text = str(exc)
        if re.search(r"api.?key|not logged in|authentication|unauthorized", text, re.I):
            raise OrchestratorError(
                "Codex SDK 인증에 별도 API 키 또는 로그인이 필요합니다. 키를 입력하거나 우회하지 않고 중단합니다."
            ) from exc
        raise OrchestratorError(f"Codex SDK 읽기 전용 작업 실패: {text}") from exc


def cmd_verify_codex(_: argparse.Namespace) -> int:
    thread_id, response = codex_turn(
        "planner", "읽기 전용 연결 검사입니다. 파일을 수정하지 말고 저장소 루트 파일 이름만 간단히 확인하세요.", ROOT,
    )
    print(f"Codex 읽기 전용 연결 성공 (thread {thread_id})")
    print(response)
    return 0


def approval(prompt: str) -> bool:
    if not sys.stdin.isatty():
        return False
    answer = input(f"{prompt} [yes/NO]: ").strip().lower()
    return answer in {"yes", "y"}


def validate_execution_profiles() -> None:
    """Reject malformed trusted profile constants before they can form an argv."""
    required = {"timeout_seconds", "max_fix_attempts", "autonomous"}
    if set(EXECUTION_PROFILES) != TASK_SIZES:
        raise OrchestratorError("Prime 실행 프로필 정의가 올바르지 않습니다.")
    for size, profile in EXECUTION_PROFILES.items():
        if size not in TASK_SIZES or set(profile) - {
            "timeout_seconds", "max_fix_attempts", "autonomous", "implementation_instruction",
            "max_continuations", "max_turns", "max_tokens",
        }:
            raise OrchestratorError("Prime 실행 프로필 정의가 올바르지 않습니다.")
        if (
            not required <= set(profile)
            or not isinstance(profile["autonomous"], bool)
            or not isinstance(profile.get("implementation_instruction"), str)
            or not profile["implementation_instruction"].strip()
        ):
            raise OrchestratorError("Prime 실행 프로필 정의가 올바르지 않습니다.")
        numeric = ["timeout_seconds", "max_fix_attempts"]
        numeric += ["max_continuations", "max_turns", "max_tokens"] if profile["autonomous"] else []
        if any(
            not isinstance(profile[key], int)
            or isinstance(profile[key], bool)
            or profile[key] <= 0
            or profile[key] > EXECUTION_BUDGET_LIMITS[key]
            for key in numeric
        ):
            raise OrchestratorError("Prime 실행 프로필 예산은 허용 상한 이내의 양의 정수여야 합니다.")
        autonomous_keys = {"max_continuations", "max_turns", "max_tokens"}
        if profile["autonomous"] != (autonomous_keys <= set(profile)) or (not profile["autonomous"] and autonomous_keys & set(profile)):
            raise OrchestratorError("Prime autonomous 프로필 정의가 올바르지 않습니다.")


def execution_profile(size: str) -> dict[str, Any]:
    validate_execution_profiles()
    if size not in TASK_SIZES:
        raise OrchestratorError("허용되지 않은 TASK_SIZE입니다.")
    return {"task_size": size, **EXECUTION_PROFILES[size]}


def planner_task_size(text: str) -> tuple[str, str]:
    """Parse required size fields only from planner lines two and three."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 3:
        raise OrchestratorError("PROCEED 계획의 두 번째와 세 번째 비어 있지 않은 줄에는 TASK_SIZE와 TASK_SIZE_REASON이 필요합니다.")
    size_match = re.fullmatch(r"TASK_SIZE: (SMALL|MEDIUM|LARGE)", lines[1])
    reason_match = re.fullmatch(r"TASK_SIZE_REASON: (.+)", lines[2])
    later_fields = any(
        line.startswith("TASK_SIZE:") or line.startswith("TASK_SIZE_REASON:")
        for line in lines[3:]
    )
    if not size_match or not reason_match or later_fields:
        raise OrchestratorError("TASK_SIZE와 TASK_SIZE_REASON은 각각 두 번째와 세 번째 비어 있지 않은 줄에 한 번만, 올바른 형식으로 있어야 합니다.")
    return size_match.group(1), reason_match.group(1)


def hermes_config() -> dict[str, Any]:
    """The advisor boundary is fixed in code, not task/config supplied."""
    return {"profile": HERMES_PROFILE, "provider": HERMES_PROVIDER, "model": HERMES_MODEL,
            "reasoning": HERMES_REASONING, "toolsets": HERMES_TOOLSETS,
            "max_turns": HERMES_MAX_TURNS, "run_budget_seconds": HERMES_RUN_BUDGET_SECONDS,
            "one_shot": True, "source": "tool", "repository_access": False}


def validate_hermes_config() -> None:
    if hermes_config() != {"profile": "orchestrator-advisor", "provider": "openai-codex", "model": "gpt-5.6-sol", "reasoning": "high", "toolsets": "todo", "max_turns": 1, "run_budget_seconds": 120, "one_shot": True, "source": "tool", "repository_access": False}:
        raise OrchestratorError("Hermes advisor 신뢰 설정이 올바르지 않습니다.")


def hermes_argv(command: str = "hermes") -> list[str]:
    validate_hermes_config()
    return [command, "chat", "--profile", HERMES_PROFILE, "--query-file", "-", "--oneshot", "--quiet",
            "--provider", HERMES_PROVIDER, "--model", HERMES_MODEL, "--reasoning", HERMES_REASONING,
            "--toolsets", HERMES_TOOLSETS, "--max-turns", str(HERMES_MAX_TURNS),
            "--run-budget", str(HERMES_RUN_BUDGET_SECONDS), "--source", "tool"]


def assert_hermes_command_allowed(args: Sequence[str], approved_executable: str | None = None) -> None:
    argv = [str(value) for value in args]
    # The advisor command is a single trusted argv, including its executable.
    # Do not accept another binary merely because it happens to be named hermes.
    expected_executable = approved_executable or "hermes"
    if argv != hermes_argv(expected_executable):
        raise OrchestratorError("허용되지 않은 Hermes advisor 명령입니다.")
    if approved_executable is not None and not Path(approved_executable).is_absolute():
        raise OrchestratorError("Hermes advisor의 검증된 실행 경로는 절대 경로여야 합니다.")
    if any(value.lower() in HERMES_FORBIDDEN_FLAGS for value in argv[1:]):
        raise OrchestratorError("허용되지 않은 Hermes advisor 옵션입니다.")


def hermes_env(profile_home: str | None = None) -> dict[str, str]:
    """Give Hermes only runtime basics; credentials remain in its managed auth store."""
    env = {name: value for name, value in os.environ.items() if name in {"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TERM"}}
    if profile_home:
        env["HERMES_HOME"] = profile_home
    return env


def hermes_decision(text: str) -> str:
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    match = re.fullmatch(r"DECISION: (PASS|REVISE|STOP)", first)
    if not match:
        raise OrchestratorError("Hermes 응답 형식 오류: 첫 번째 비어 있지 않은 줄은 DECISION: PASS, DECISION: REVISE, DECISION: STOP 중 하나여야 합니다.")
    return match.group(1)


def hermes_query(state: dict[str, Any]) -> str:
    return ("You are a read-only plan advisor. Do not access repository files or request tools. "
            "Reply with exactly DECISION: PASS, DECISION: REVISE, or DECISION: STOP as your first non-empty line, then concise sanitized advice.\n\n"
            f"TASK:\n{state['task']}\n\nCODEX PLAN:\n{state['plan']}")


class HermesPreflightError(OrchestratorError):
    """A sanitized, durable classification for Hermes preflight failures."""

    def __init__(self, failure_type: str, message: str):
        super().__init__(message)
        self.failure_type = failure_type


def verify_hermes() -> tuple[str, str]:
    """Check Hermes from an empty directory with no inherited credentials."""
    validate_hermes_config()
    located = binary("hermes")
    command = str(Path(located).resolve()) if located else ""
    if not command:
        raise HermesPreflightError("executable_missing", "Hermes 실행 파일을 확인할 수 없습니다.")
    # Preflight has the same remoteness and credential boundary as chat.
    with tempfile.TemporaryDirectory(prefix="hermes-advisor-preflight-") as directory:
        kwargs = {"text": True, "capture_output": True, "timeout": 30,
                  "cwd": Path(directory), "env": hermes_env()}
        try:
            version = subprocess.run([command, "--version"], **kwargs)
            profile = subprocess.run([command, "profile", "show", HERMES_PROFILE], **kwargs)
            auth = subprocess.run([command, "-p", HERMES_PROFILE, "auth", "status", HERMES_PROVIDER], **kwargs)
        except (OSError, subprocess.TimeoutExpired) as exc:
            failure_type = "executable_missing" if isinstance(exc, (FileNotFoundError, PermissionError)) else "preflight_unavailable"
            raise HermesPreflightError(failure_type, "Hermes 사전 점검을 실행할 수 없습니다.") from exc
    if version.returncode or "v0.21.0" not in (version.stdout + version.stderr):
        raise HermesPreflightError("executable_unavailable", "Hermes 실행 파일 호환성을 확인할 수 없습니다.")
    if profile.returncode:
        raise HermesPreflightError("profile_missing", "Hermes advisor 프로필을 확인할 수 없습니다.")
    if auth.returncode or re.search(r"logged out|not logged", auth.stdout + auth.stderr, re.I):
        raise HermesPreflightError("authentication_unavailable", "Hermes provider 인증을 확인할 수 없습니다.")
    match = re.search(r"^Path:\s+(.+)$", profile.stdout + profile.stderr, re.M)
    if not match:
        raise HermesPreflightError("profile_missing", "Hermes advisor 프로필 경로를 확인할 수 없습니다.")
    return command, match.group(1).strip()


def sanitize_hermes_advice(stdout: str) -> str:
    """Keep useful prose, never credential-shaped or auth-file output."""
    lines = [line for line in stdout.splitlines() if line.strip()]
    advice: list[str] = []
    unsafe = re.compile(
        r"(?i)auth\.json|bearer\s+|oauth|token\s*[=:]|(?:secret|password|credential|authorization|api[_-]?key)\s*[=:]|"
        r'["\'](?:access_token|refresh_token|id_token|client_secret|password|credentials?)["\']\s*:'
    )
    for line in lines[1:]:
        if not unsafe.search(line):
            advice.append(line)
    return redact_secrets("\n".join(advice))[-8000:] or "(no sanitized advice)"


def hermes_failure_type(exc: BaseException) -> str:
    cause = exc.__cause__
    if isinstance(exc, subprocess.TimeoutExpired) or isinstance(cause, subprocess.TimeoutExpired):
        return "timeout"
    if isinstance(exc, PermissionError) or isinstance(cause, PermissionError):
        return "permission_error"
    if isinstance(exc, (FileNotFoundError, OSError)) or isinstance(cause, (FileNotFoundError, OSError)):
        return "command_launch_failure"
    return "execution_error"


def run_hermes_review(state: dict[str, Any]) -> str:
    """Run once, atomically persist its result, then let the caller consume it."""
    state["hermes_config"] = hermes_config()
    state.setdefault("hermes_review_count", 0)
    state["hermes_preflight_phase"] = "started"
    save_state(state, "hermes_preflight_started", "Hermes advisor 고정 설정 및 사전 점검 시작 상태 저장")
    try:
        command, profile_home = verify_hermes()
    except HermesPreflightError as exc:
        state.setdefault("hermes_reviews", []).append({
            "at": now(), "decision": "ERROR", "failure_type": exc.failure_type,
            "config": hermes_config(), "credentials_stripped": True,
        })
        state["hermes_last_decision"] = "ERROR"
        state["hermes_failure_type"] = exc.failure_type
        state["hermes_preflight_phase"] = "failed"
        state["hermes_review_phase"] = "preflight_failed"
        save_state(state, "hermes_preflight_failed", f"Hermes advisor 사전 점검 실패 저장: {exc.failure_type}")
        raise OrchestratorError(f"Hermes advisor 사전 점검이 실패했습니다 ({exc.failure_type}).") from exc
    state["hermes_preflight_phase"] = "completed"
    child_env = hermes_env(profile_home)
    state["hermes_review_count"] = state.get("hermes_review_count", 0) + 1
    state["hermes_review_phase"] = "started"
    save_state(state, "hermes_review_started", "Hermes advisor 검토 시작 상태 저장")
    try:
        with tempfile.TemporaryDirectory(prefix="hermes-advisor-") as directory:
            result = run_cmd(
                hermes_argv(command), cwd=Path(directory), env=child_env, check=False,
                timeout=HERMES_RUN_BUDGET_SECONDS + 10, input_text=hermes_query(state),
                approved_hermes_executable=command,
            )
    except (OrchestratorError, OSError, subprocess.TimeoutExpired) as exc:
        failure_type = hermes_failure_type(exc)
        state.setdefault("hermes_reviews", []).append({
            "at": now(), "decision": "ERROR", "failure_type": failure_type,
            "config": hermes_config(), "credentials_stripped": True,
        })
        state["hermes_config"] = hermes_config()
        state["hermes_last_decision"] = "ERROR"
        state["hermes_failure_type"] = failure_type
        state["hermes_review_phase"] = "result_ready"
        save_state(state, "hermes_review_result", f"Hermes advisor 실패 저장: {failure_type}")
        raise OrchestratorError(f"Hermes advisor 실행이 실패했습니다 ({failure_type}).") from exc
    raw_output = result.stdout or ""
    # Parse all raw stdout. A tail must never choose a different decision.
    parse_error: OrchestratorError | None = None
    if result.returncode != 0:
        decision = "ERROR"
    else:
        try:
            decision = hermes_decision(raw_output)
        except OrchestratorError as exc:
            decision, parse_error = "FORMAT_ERROR", exc
    failure_type = "nonzero_exit" if result.returncode != 0 else ("invalid_response" if parse_error else None)
    record = {"at": now(), "returncode": result.returncode, "decision": decision,
              "config": hermes_config(), "credentials_stripped": True}
    if not failure_type:
        record["advice"] = sanitize_hermes_advice(raw_output)
    if failure_type:
        record["failure_type"] = failure_type
    state.setdefault("hermes_reviews", []).append(record)
    state["hermes_config"] = hermes_config()
    state["hermes_last_decision"] = decision
    if failure_type:
        state["hermes_failure_type"] = failure_type
    state["hermes_review_phase"] = "result_ready"
    # This save is intentionally before the next state transition, for resume idempotence.
    save_state(state, "hermes_review_result", f"Hermes advisor 결과 저장: {decision}")
    if result.returncode != 0:
        raise OrchestratorError("Hermes advisor 실행이 실패했습니다.")
    if parse_error:
        raise parse_error
    return decision


def planner_decision(text: str) -> str:
    """Return the exact decision from the planner's first non-empty line."""
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    match = re.fullmatch(r"DECISION: (PROCEED|NO_CHANGES|STOP_REQUIRED)", first_line)
    if not match:
        raise OrchestratorError(
            "planner 응답 형식 오류: 첫 번째 비어 있지 않은 줄은 "
            "DECISION: PROCEED, DECISION: NO_CHANGES, DECISION: STOP_REQUIRED 중 하나여야 합니다."
        )
    return match.group(1)


def terminal_planner_report(state: dict[str, Any], outcome: str, message: str) -> None:
    state["status"] = "complete"
    state["outcome"] = outcome
    report = (
        f"# Agent Orchestrator Report\n\nRun: `{state['run_id']}`\n\n"
        f"Status: `complete`\n\nOutcome: `{outcome}`\n\n"
        f"## Execution profile\n\n```json\n{json.dumps(state.get('execution_profile', {}), ensure_ascii=False, indent=2)}\n```\n\n"
        f"## Result\n\n{message}\n\n"
        f"## Hermes advisor\n\n```json\n{json.dumps(state.get('hermes_config', {}), ensure_ascii=False, indent=2)}\n```\n\n"
        f"Reviews: `{state.get('hermes_review_count', 0)}`; last decision: `{state.get('hermes_last_decision', '(not run)')}`\n\n"
        f"Failure type: `{state.get('hermes_failure_type', '(none)')}`\n\n"
        f"Advice:\n\n{state.get('hermes_reviews', [])[-1].get('advice', '(not run)') if state.get('hermes_reviews') else '(not run)'}\n\n"
        f"## Planner response\n\n{state['plan']}\n"
    )
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(report, encoding="utf-8")
    state["report"] = str(REPORT_FILE)
    complete_step(state, "report_written", f"최종 보고서 작성: {REPORT_FILE}")
    save_state(state, "complete", message)


def slug_for_state(state: dict[str, Any]) -> str:
    source = state["source"]
    if source.startswith("issue:"):
        return source.split(":", 1)[1]
    return state["run_id"].lower()


def create_worktree(state: dict[str, Any]) -> None:
    config = load_config()
    root = Path(config["worktree_root"])
    root.mkdir(parents=True, exist_ok=True)
    slug = slug_for_state(state)
    branch = f"{config['feature_branch_prefix']}{slug}"
    path = root / f"run-{state['run_id']}-{slug}"
    if branch in {"main", "master"} or not re.fullmatch(r"[A-Za-z0-9._/-]+", branch):
        fail(state, "안전하지 않은 feature branch 이름입니다.", "config의 feature_branch_prefix를 확인하세요.")
    if path.exists():
        fail(state, f"worktree 경로가 이미 존재합니다: {path}", "cleanup 또는 config 경로를 확인하세요.")
    run_cmd(["git", "worktree", "add", "-b", branch, str(path), "HEAD"])
    state.update({"branch": branch, "worktree": str(path), "worktree_owned": True, "pushed": False})
    complete_step(state, "worktree_created", f"전용 branch/worktree 생성: {branch} / {path}")


def discover_checks(cwd: Path = ROOT) -> dict[str, list[str] | None]:
    config = load_config()
    found: dict[str, list[str] | None] = {}
    configured = config.get("checks", {})
    package = cwd / "package.json"
    scripts = load_json(package).get("scripts", {}) if package.exists() else {}
    aliases = {"lint": ["lint"], "typecheck": ["typecheck", "type-check"], "test": ["test"], "build": ["build"]}
    for name, candidates in aliases.items():
        explicit = configured.get(name)
        if explicit:
            found[name] = shlex.split(explicit) if isinstance(explicit, str) else list(explicit)
            continue
        script = next((candidate for candidate in candidates if candidate in scripts), None)
        found[name] = ["npm", "run", script] if script else None
    return found


def run_checks(state: dict[str, Any], cwd: Path, label: str) -> bool:
    results: dict[str, Any] = {}
    all_ok = True
    for name, command in discover_checks(cwd).items():
        if not command:
            results[name] = {"status": "SKIPPED", "reason": "repository command not configured"}
            continue
        result = run_cmd(command, cwd=cwd, check=False, timeout=600)
        results[name] = {
            "status": "PASS" if result.returncode == 0 else "FAIL", "command": command,
            "returncode": result.returncode, "stdout_tail": redact_secrets(result.stdout[-4000:]),
            "stderr_tail": redact_secrets(result.stderr[-4000:]),
        }
        all_ok = all_ok and result.returncode == 0
    state.setdefault("checks", {})[label] = results
    save_state(state, f"checks_{label}", f"검사 세트 {label} 완료")
    return all_ok


def prime_env() -> dict[str, str]:
    env = os.environ.copy()
    for name in list(env):
        upper = name.upper()
        if name in SENSITIVE_ENV_NAMES or upper.startswith("GITHUB_") or upper.startswith("CLOUDFLARE_"):
            env.pop(name, None)
    return env


def redact_secrets(text: str, env: dict[str, str] | None = None) -> str:
    """Remove known secrets and credential-shaped values before persistence."""
    redacted = text
    # A stripped child environment cannot identify credentials withheld by its parent.
    source = dict(os.environ)
    if env:
        source.update(env)
    for name, value in source.items():
        upper = name.upper()
        if value and len(value) >= 8 and any(marker in upper for marker in ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIAL", "OAUTH", "AUTH")):
            redacted = redacted.replace(value, "[REDACTED]")
    token_value = r"(?:[A-Za-z0-9._~+/=-]{8,}|eyJ[A-Za-z0-9._-]+)"
    sensitive_field = r"(?:access_token|refresh_token|id_token|oauth_token|client_secret|client_password|api[_-]?key|secret|password|passphrase|credential(?:s)?|authorization|auth(?:entication)?(?:[_-]?(?:token|secret|key|password))?)"
    # This handles auth.json/OAuth objects as well as shell and URL key-value output.
    json_field = r"(?ix)([\"']" + sensitive_field + r"[\"']\s*:\s*)[\"'](?:[^\"'\\]|\\.)*[\"']"
    redacted = re.sub(json_field, r'\1"[REDACTED]"', redacted)
    redacted = re.sub(r"(?ix)(?:\b|[?&])" + sensitive_field + r"\s*[=:]\s*[\"']?" + token_value, "[REDACTED]", redacted)
    redacted = re.sub(r"(?i)(bearer\s+)" + token_value, r"\1[REDACTED]", redacted)
    redacted = re.sub(r"(?i)\b(?:sk|rk|pk|gh[opsru])-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", redacted)
    return redacted


def run_prime(state: dict[str, Any], correction: str | None = None) -> None:
    config = load_config()
    worktree = Path(state["worktree"])
    try:
        assert_feature_worktree(worktree)
    except OrchestratorError as exc:
        fail(state, str(exc), "저장소 외부의 전용 feature branch/worktree를 생성한 뒤 다시 실행하세요.")
    session_dir = RUNTIME_DIR / "prime-sessions" / state["run_id"]
    session_dir.mkdir(parents=True, exist_ok=True)
    base = (AGENT_DIR / "prompts" / "implementer.md").read_text(encoding="utf-8")
    profile = execution_profile(state["task_size"])
    state["execution_profile"] = profile
    implementation_instruction = profile["implementation_instruction"]
    task_prompt = correction or f"{base}\n\nAPPROVED PLAN:\n{state['plan']}\n\nTASK:\n{state['task']}"
    prompt = f"{task_prompt}\n\nTRUSTED IMPLEMENTATION INSTRUCTION:\n{implementation_instruction}"
    command = prime_argv(config["prime_agent"]["command"], profile, worktree, session_dir, prompt)
    child_env = prime_env()
    result = run_cmd(
        command, cwd=worktree, env=child_env, check=False,
        timeout=int(profile["timeout_seconds"]), approved_prime_worktree=worktree,
    )
    state.setdefault("prime_runs", []).append({
        "at": now(), "returncode": result.returncode, "stdout_tail": redact_secrets(result.stdout[-8000:], child_env),
        "stderr_tail": redact_secrets(result.stderr[-8000:], child_env), "credentials_stripped": True,
    })
    if result.returncode != 0:
        fail(state, "Prime Agent 실행이 실패했습니다.", "status로 로그를 확인한 뒤 resume 하세요.")
    complete_step(state, "prime_fixed" if correction else "prime_implemented", "Prime Agent 작업 완료")


def review_snapshot(worktree: Path) -> str:
    status = run_cmd(["git", "status", "--short"], cwd=worktree).stdout
    diff = run_cmd(["git", "diff", "--no-ext-diff"], cwd=worktree).stdout
    untracked = run_cmd(["git", "ls-files", "--others", "--exclude-standard"], cwd=worktree).stdout.splitlines()
    additions: list[str] = []
    for relative in untracked:
        path = worktree / relative
        if not path.is_file():
            continue
        if path.stat().st_size > 200_000:
            additions.append(f"UNTRACKED {relative}: omitted because file is larger than 200 KB")
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            additions.append(f"UNTRACKED {relative}: binary content omitted")
        else:
            additions.append(f"UNTRACKED FILE: {relative}\n{content}")
    return f"GIT STATUS:\n{status}\n\nTRACKED DIFF:\n{diff}\n\n" + "\n\n".join(additions)


def final_report(state: dict[str, Any]) -> None:
    worktree = Path(state["worktree"])
    changed = run_cmd(["git", "status", "--short"], cwd=worktree).stdout.strip() or "(없음)"
    diffstat = run_cmd(["git", "diff", "--stat"], cwd=worktree).stdout.strip() or "(없음)"
    report = (
        f"# Agent Orchestrator Report\n\nRun: `{state['run_id']}`\n\n"
        f"Status: `{state['status']}`\n\nBranch: `{state.get('branch', '-')}`\n\n"
        f"## Execution profile\n\n```json\n{json.dumps(state.get('execution_profile', {}), ensure_ascii=False, indent=2)}\n```\n\n"
        f"## Changed files\n\n```text\n{changed}\n```\n\n"
        f"## Diff summary\n\n```text\n{diffstat}\n```\n\n"
        f"## Hermes advisor\n\n```json\n{json.dumps(state.get('hermes_config', {}), ensure_ascii=False, indent=2)}\n```\n\n"
        f"Reviews: `{state.get('hermes_review_count', 0)}`; last decision: `{state.get('hermes_last_decision', '(not run)')}`\n\n"
        f"Failure type: `{state.get('hermes_failure_type', '(none)')}`\n\n"
        f"Advice:\n\n{state.get('hermes_reviews', [])[-1].get('advice', '(not run)') if state.get('hermes_reviews') else '(not run)'}\n\n"
        f"## Review\n\n{state.get('review', '(not run)')}\n\n"
        f"## Checks\n\n```json\n{json.dumps(state.get('checks', {}), ensure_ascii=False, indent=2)}\n```\n\n"
        "## Remaining risks\n\nPrime Agent command filtering is defense in depth, not a complete sandbox. "
        "Review every change before approving push or Draft PR creation.\n"
    )
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(report, encoding="utf-8")
    state["report"] = str(REPORT_FILE)
    complete_step(state, "report_written", f"최종 보고서 작성: {REPORT_FILE}")


def invalidate_stale_fix_review(state: dict[str, Any]) -> None:
    """Migrate a pre-review-status FIX resume to a fresh review and downstream output."""
    if "review_status" in state or "reviewed" not in state["completed_steps"]:
        return
    verdict = re.search(r"VERDICT:\s*(PASS|FIX|STOP)", state.get("review", ""), re.I)
    if not verdict or verdict.group(1).upper() != "FIX":
        return
    externally_advanced = bool(
        {"external_approval", "commit_created", "branch_pushed", "draft_pr_created"}
        & set(state["completed_steps"])
        or state.get("commit")
        or state.get("pushed")
        or state.get("draft_pr")
    )
    if externally_advanced:
        fail(
            state,
            "외부 승인 또는 commit/push/Draft PR 이후 stale FIX 검토를 발견해 안전하게 중단했습니다.",
            "외부 상태와 변경사항을 사람이 확인한 뒤 새 실행 여부를 결정하세요.",
        )
    invalidated = {"reviewed", "checks_final", "report_written"}
    state["completed_steps"] = [step for step in state["completed_steps"] if step not in invalidated]
    state.get("checks", {}).pop("final", None)
    state.pop("report", None)
    state["review_status"] = "pending"
    save_state(state, "stale_review_invalidated", "stale FIX 검토와 최종 검사/보고서를 무효화")


def continue_run(state: dict[str, Any]) -> int:
    if state.get("dry_run"):
        complete_step(state, "dry_run_complete", "dry-run 완료: 외부 작업과 Prime Agent를 실행하지 않음")
        state["status"] = "complete"
        save_state(state, "complete", "dry-run 정상 종료")
        return 0
    invalidate_stale_fix_review(state)
    if "planned" not in state["completed_steps"]:
        thread_id, plan = codex_turn("planner", state["task"], ROOT)
        state["planner_thread_id"] = thread_id
        state["plan"] = plan
        complete_step(state, "planned", "독립 Codex planner가 읽기 전용 계획 작성")
        print("\n--- 계획 ---\n" + plan + "\n------------")
    try:
        decision = planner_decision(state["plan"])
    except OrchestratorError as exc:
        fail(state, str(exc), "planner 프롬프트와 응답의 DECISION 첫 줄 형식을 확인하세요.")
    state["planner_decision"] = decision
    if decision == "PROCEED":
        # Hermes is deliberately before both Prime preflight and the first approval/worktree.
        # Older persisted runs that already passed approval cannot be safely sent backward.
        while "plan_approved" not in state["completed_steps"] and "hermes_reviewed" not in state["completed_steps"]:
            if state.get("hermes_review_phase") == "started":
                failure_type = "interrupted_review"
                state.setdefault("hermes_reviews", []).append({
                    "at": now(), "decision": "ERROR", "failure_type": failure_type,
                    "config": state.get("hermes_config", hermes_config()), "credentials_stripped": True,
                })
                state["hermes_review_phase"] = "ambiguous"
                state["hermes_last_decision"] = "ERROR"
                state["hermes_failure_type"] = failure_type
                save_state(state, "hermes_review_ambiguous", "중단된 Hermes advisor 검토를 안전 중단 상태로 저장")
                message = "이전 Hermes 검토 호출이 중단되어 성공 여부가 불명확합니다. 재실행하지 않았으며 사용자가 상태를 수동 확인한 뒤 복구해야 합니다."
                terminal_planner_report(state, "stop_required", message)
                print(message)
                return 0
            if state.get("hermes_replan_pending"):
                if state.get("hermes_replan_phase") == "started":
                    message = "Hermes 재계획 호출 중 중단되어 성공 여부를 확인할 수 없으므로 수동 확인이 필요합니다."
                    terminal_planner_report(state, "stop_required", message)
                    print(message)
                    return 0
                feedback = state.get("hermes_reviews", [{}])[-1].get("advice", "Revise the plan to address Hermes advisor feedback.")
                try:
                    state["hermes_replan_phase"] = "started"
                    save_state(state, "hermes_replan_started", "Codex planner 재계획 호출 시작 상태 저장")
                    thread_id, plan = codex_turn("planner", f"{state['task']}\n\nHERMES ADVISOR FEEDBACK (revise the plan once):\n{feedback}", ROOT)
                    state["planner_thread_id"] = thread_id
                    state["plan"] = plan
                    state["planner_decision"] = planner_decision(plan)
                    if state["planner_decision"] != "PROCEED":
                        raise OrchestratorError("Hermes 재계획 뒤 planner가 PROCEED를 반환하지 않았습니다.")
                except OrchestratorError as exc:
                    message = f"Hermes 재계획 단계에서 안전하게 중단했습니다: {exc}"
                    terminal_planner_report(state, "stop_required", message)
                    print(message)
                    return 0
                state.pop("hermes_replan_pending", None)
                state["hermes_replan_phase"] = "completed"
                state.pop("hermes_review_phase", None)
                save_state(state, "hermes_replanned", "Codex planner가 Hermes 피드백으로 계획을 한 번 재작성")
                continue
            if state.get("hermes_review_count", 0) >= HERMES_MAX_REVIEWS:
                message = "Hermes advisor 재검토 한도에서 PASS가 아니어서 사용자 개입이 필요합니다."
                terminal_planner_report(state, "stop_required", message)
                print(message)
                return 0
            # A completed call is saved as result_ready before any transition.
            # Resume consumes that durable result rather than contacting Hermes again.
            if state.get("hermes_review_phase") == "result_ready":
                hermes_result = state.get("hermes_last_decision", "FORMAT_ERROR")
            else:
                try:
                    hermes_result = run_hermes_review(state)
                except OrchestratorError as exc:
                    message = f"Hermes advisor 단계에서 안전하게 중단했습니다: {exc}"
                    terminal_planner_report(state, "stop_required", message)
                    print(message)
                    return 0
            if hermes_result == "PASS":
                complete_step(state, "hermes_reviewed", "Hermes 읽기 전용 계획 검토 통과")
                break
            if hermes_result == "STOP":
                message = "Hermes advisor가 STOP을 반환하여 승인과 worktree 생성 전에 안전하게 중단했습니다."
                terminal_planner_report(state, "stop_required", message)
                print(message)
                return 0
            if hermes_result != "REVISE":
                message = f"Hermes advisor의 저장된 오류 결과({hermes_result})로 승인 전에 안전하게 중단했습니다."
                terminal_planner_report(state, "stop_required", message)
                print(message)
                return 0
            if state.get("hermes_replans", 0) >= HERMES_MAX_REPLANS or state.get("hermes_review_count", 0) >= HERMES_MAX_REVIEWS:
                message = "Hermes advisor 재검토 한도에서 PASS가 아니어서 사용자 개입이 필요합니다."
                terminal_planner_report(state, "stop_required", message)
                print(message)
                return 0
            state["hermes_replans"] = state.get("hermes_replans", 0) + 1
            state["hermes_replan_pending"] = True
            save_state(state, "hermes_replan_requested", "Hermes REVISE 피드백으로 Codex planner 재계획 요청")
        try:
            task_size, task_size_reason = planner_task_size(state["plan"])
            profile = execution_profile(task_size)
            state.update({"task_size": task_size, "task_size_reason": task_size_reason, "execution_profile": profile})
            state["prime_cli"] = verify_prime_cli(profile)
        except OrchestratorError as exc:
            message = f"Prime 실행 전 안전하게 중단했습니다: {exc}"
            terminal_planner_report(state, "stop_required", message)
            print(message)
            return 0
    save_state(state, "planned", f"planner 판정: {decision}")
    if decision == "NO_CHANGES":
        message = "변경할 필요가 없어 안전하게 종료했습니다"
        terminal_planner_report(state, "no_changes", message)
        print(message)
        return 0
    if decision == "STOP_REQUIRED":
        reason = "\n".join(line for line in state["plan"].splitlines()[1:] if line.strip()).strip()
        message = "민감 변경이 필요하여 안전하게 중단했습니다."
        if reason:
            message += f"\n중단 이유: {reason}"
        terminal_planner_report(state, "stop_required", message)
        print(message)
        return 0
    if "plan_approved" not in state["completed_steps"]:
        if not approval("이 계획을 승인하고 feature worktree에서 Prime Agent를 실행할까요?"):
            state["status"] = "awaiting_plan_approval"
            save_state(state, "awaiting_plan_approval", "계획 승인 대기")
            print("승인하지 않아 중단했습니다. 같은 터미널에서 `resume`을 실행할 수 있습니다.")
            return 2
        complete_step(state, "plan_approved", "사용자가 구현 계획 승인")
    if "worktree_created" not in state["completed_steps"]:
        create_worktree(state)
    if "prime_implemented" not in state["completed_steps"]:
        run_prime(state)
    worktree = Path(state["worktree"])
    if "checks_initial" not in state["completed_steps"]:
        if not run_checks(state, worktree, "initial"):
            fail(state, "초기 프로젝트 검사가 실패했습니다.", "검사 로그를 확인하고 resume 하세요.")
        complete_step(state, "checks_initial", "저장소에 실제 존재하는 초기 검사 완료")
    # A correction can be interrupted after its attempt was persisted.  Such a
    # resumed run must obtain a new review, not reuse the stale FIX verdict.
    if "review_status" not in state:
        old_verdict = re.search(r"VERDICT:\s*(PASS|FIX|STOP)", state.get("review", ""), re.I)
        state["review_status"] = "pass" if "reviewed" in state["completed_steps"] and old_verdict and old_verdict.group(1).upper() == "PASS" else "pending"
    if state["review_status"] != "pass":
        state["completed_steps"] = [step for step in state["completed_steps"] if step != "reviewed"]
        state["review_status"] = "pending"
    while True:
        if "reviewed" not in state["completed_steps"]:
            snapshot = review_snapshot(worktree)
            review_task = f"TASK:\n{state['task']}\n\nAPPROVED PLAN:\n{state['plan']}\n\nCHANGES:\n{snapshot}"
            thread_id, review = codex_turn("reviewer", review_task, worktree)
            state["reviewer_thread_id"] = thread_id
            state["review"] = review
            complete_step(state, "reviewed", "별도 Codex reviewer thread가 읽기 전용 검수 완료")
        verdict = re.search(r"VERDICT:\s*(PASS|FIX|STOP)", state["review"], re.I)
        verdict_text = verdict.group(1).upper() if verdict else "STOP"
        if verdict_text == "STOP":
            fail(state, "검수가 STOP이거나 판정 형식이 올바르지 않습니다.", "review 결과를 사람이 확인하세요.")
        if verdict_text == "PASS":
            state["review_status"] = "pass"
            save_state(state, "review_passed", "독립 검수 통과")
            break
        if state["fix_attempts"] >= state["execution_profile"]["max_fix_attempts"]:
            fail(state, "Prime Agent 재수정 한도를 초과했습니다.", "남은 문제를 사람이 수정하세요.")
        state["fix_attempts"] += 1
        state["review_status"] = "fix_requested"
        save_state(state, "fix_requested", f"Prime Agent 재수정 {state['fix_attempts']}회 요청")
        run_prime(state, f"다음 독립 검수 지적만 수정하세요. 외부 작업은 금지됩니다.\n\n{state['review']}")
        state["completed_steps"].remove("reviewed")
        state["review_status"] = "pending"
        save_state(state, "review_requested", "수정 후 독립 재검수 요청")
    if "checks_final" not in state["completed_steps"]:
        if not run_checks(state, worktree, "final"):
            fail(state, "최종 프로젝트 검사가 실패했습니다.", "검사 로그와 변경사항을 확인하세요.")
        complete_step(state, "checks_final", "최종 검사 완료")
    if "report_written" not in state["completed_steps"]:
        final_report(state)
    if "external_approval" not in state["completed_steps"]:
        if not approval("feature branch push와 Draft PR 생성을 승인할까요?"):
            state["status"] = "awaiting_external_approval"
            save_state(state, "awaiting_external_approval", "push/Draft PR 승인 대기")
            print("push와 PR을 실행하지 않았습니다. 필요할 때 `resume`을 실행하세요.")
            return 2
        complete_step(state, "external_approval", "사용자가 feature push 및 Draft PR 승인")
    if "commit_created" not in state["completed_steps"]:
        if not git_status(worktree).strip():
            fail(state, "커밋할 변경사항이 없어 push/PR을 중단했습니다.", "작업 결과와 Prime Agent 로그를 확인하세요.")
        run_cmd(["git", "add", "--all"], cwd=worktree)
        run_cmd(["git", "commit", "-m", f"agent: {state['source']}"], cwd=worktree)
        state["commit"] = run_cmd(["git", "rev-parse", "HEAD"], cwd=worktree).stdout.strip()
        complete_step(state, "commit_created", "승인 후 로컬 feature commit 생성")
    if not state.get("pushed"):
        state["pushed"] = False
        save_state(state, "push_pending", "승인된 feature branch push 시작")
        try:
            approved_feature_push(state, worktree)
        except OrchestratorError as exc:
            state["pushed"] = False
            fail(state, "승인된 feature branch push에 실패했습니다.", str(exc))
        state["pushed"] = True
        complete_step(state, "branch_pushed", "승인된 feature branch push 완료")
    if "draft_pr_created" not in state["completed_steps"]:
        existing = existing_draft_pr(state, worktree)
        if existing is not None:
            state["draft_pr"] = existing
            complete_step(state, "draft_pr_created", "기존의 정확히 일치하는 Draft PR 재사용")
        else:
            try:
                result = approved_draft_pr_create(state, worktree)
            except OrchestratorError as exc:
                fail(state, "Draft PR 생성에 실패했습니다.", str(exc))
            state["draft_pr"] = {"url": result.stdout.strip()} if result.stdout.strip() else {}
            complete_step(state, "draft_pr_created", "승인된 Draft PR 생성 완료; merge/deploy는 실행하지 않음")
    state["status"] = "complete"
    save_state(state, "complete", "워크플로 완료")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    if STATE_FILE.exists():
        old = load_json(STATE_FILE)
        if old.get("status") not in {"complete", "cleaned"}:
            raise OrchestratorError("미완료 실행이 있습니다. `status`, `resume`, 또는 안전한 `cleanup`을 사용하세요.")
    task, source = read_task(args)
    if not task:
        raise OrchestratorError("작업 내용이 비어 있습니다.")
    state = new_state(task, source, args.dry_run)
    save_state(state, "initialized", "새 실행 상태 저장")
    branch = git_branch()
    if branch in PROTECTED_BRANCHES and git_tracked_status(ROOT).strip():
        fail(
            state, f"{branch} 기준 작업공간에 추적된 수정사항이 남아 있습니다.",
            "수정사항을 직접 검토해 보존하거나 정리한 뒤 다시 실행하세요. 미추적 파일은 건드리지 않습니다.",
        )
    rows, _ = doctor_rows()
    blockers = [
        name for name, ok, _detail in rows
        if not ok and (name != "GitHub 인증" or args.issue is not None)
    ]
    if blockers:
        fail(
            state, f"사전 환경 점검 실패: {', '.join(blockers)}",
            "`python tools/agent_orchestrator.py doctor` 결과를 확인하고 문제를 직접 해결하세요.",
        )
    checks = discover_checks(ROOT)
    state["discovered_checks"] = {name: command or "SKIPPED" for name, command in checks.items()}
    complete_step(state, "preflight", "사전 환경 및 저장소 검사 완료")
    if args.dry_run:
        print(json.dumps({"task": task, "checks": state["discovered_checks"], "would_run_prime": False}, ensure_ascii=False, indent=2))
    return continue_run(state)


def cmd_status(_: argparse.Namespace) -> int:
    if not STATE_FILE.exists():
        print("저장된 실행 상태가 없습니다.")
        return 0
    print(json.dumps(load_json(STATE_FILE), ensure_ascii=False, indent=2))
    return 0


def cmd_resume(_: argparse.Namespace) -> int:
    if not STATE_FILE.exists():
        raise OrchestratorError("재개할 상태가 없습니다.")
    state = load_json(STATE_FILE)
    if state.get("status") in {"complete", "cleaned"}:
        print(f"이미 종료된 실행입니다: {state['status']}")
        return 0
    state["status"] = "running"
    save_state(state, state["stage"], "resume 시작")
    return continue_run(state)


def cleanup_state(state: dict[str, Any], simulate: bool = False) -> None:
    path_text = state.get("worktree")
    if not path_text:
        state["status"] = "cleaned"
        save_state(state, "cleaned", "정리할 worktree가 없음")
        return
    path = Path(path_text)
    root = Path(load_config()["worktree_root"])
    if root not in path.parents or not state.get("worktree_owned"):
        raise OrchestratorError("오케스트레이터 소유로 검증되지 않은 worktree는 정리하지 않습니다.")
    if path.exists() and git_status(path).strip():
        raise OrchestratorError("변경사항이 남은 worktree는 자동 삭제하지 않습니다. 직접 검토하세요.")
    if simulate:
        print(f"[모의검사] 안전하게 정리 가능한 worktree: {path}")
        return
    if path.exists():
        run_cmd(["git", "worktree", "remove", str(path)])
    branch = state.get("branch")
    if branch and not state.get("pushed") and branch not in {"main", "master"}:
        result = run_cmd(["git", "branch", "--list", branch])
        if result.stdout.strip():
            run_cmd(["git", "branch", "-d", branch])
    state["status"] = "cleaned"
    complete_step(state, "cleaned", "오케스트레이터 소유의 깨끗한 로컬 리소스 정리 완료")


def cmd_cleanup(args: argparse.Namespace) -> int:
    if not STATE_FILE.exists():
        print("정리할 저장 상태가 없습니다.")
        return 0
    cleanup_state(load_json(STATE_FILE), simulate=args.simulate)
    return 0


def cmd_self_test(_: argparse.Namespace) -> int:
    blocked = [
        ["git", "push", "origin", "main"], ["git", "push", "origin", "master"],
        ["wrangler", "deploy"], ["wrangler", "pages", "deploy"],
    ]
    for command in blocked:
        try:
            assert_command_allowed(command)
        except OrchestratorError:
            print(f"[PASS] 차단: {command_text(command)}")
        else:
            raise OrchestratorError(f"금지 명령 차단 실패: {command_text(command)}")
    env = prime_env()
    leaks = [name for name in env if name in SENSITIVE_ENV_NAMES or name.upper().startswith(("GITHUB_", "CLOUDFLARE_"))]
    if leaks:
        raise OrchestratorError("Prime Agent 환경 자격증명 제거 검사 실패")
    print("[PASS] Prime Agent 자격증명 환경 제거")
    return 0


def cmd_self_test_lifecycle(_: argparse.Namespace) -> int:
    interrupted = new_state("resume validation", "text", True)
    complete_step(interrupted, "preflight", "검증용 중단 직전 상태 저장")
    reloaded = load_json(STATE_FILE)
    if reloaded["run_id"] != interrupted["run_id"] or reloaded["status"] != "running":
        raise OrchestratorError("중단 상태 재로딩 검사 실패")
    print("[PASS] 중단 상태를 디스크에서 복구")
    cmd_resume(argparse.Namespace())
    resumed = load_json(STATE_FILE)
    if resumed["status"] != "complete" or "dry_run_complete" not in resumed["completed_steps"]:
        raise OrchestratorError("resume 완료 상태 검사 실패")
    print("[PASS] resume가 완료된 단계를 건너뛰고 이어서 완료")

    worktree_state = new_state("worktree cleanup validation", "text", False)
    save_state(worktree_state, "initialized", "검증용 worktree 상태 저장")
    create_worktree(worktree_state)
    worktree = Path(worktree_state["worktree"])
    if not worktree.exists() or git_branch(worktree) in {"main", "master"}:
        raise OrchestratorError("전용 worktree 생성 검사 실패")
    print(f"[PASS] 저장소 외부 feature worktree 생성: {worktree}")
    cleanup_state(worktree_state, simulate=True)
    if not worktree.exists():
        raise OrchestratorError("cleanup 모의검사가 worktree를 실제 삭제했습니다.")
    print("[PASS] cleanup 모의검사는 파일을 삭제하지 않음")
    cleanup_state(worktree_state, simulate=False)
    if worktree.exists():
        raise OrchestratorError("검증용 깨끗한 worktree 정리 실패")
    print("[PASS] 검증용 깨끗한 worktree만 안전하게 정리")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor").set_defaults(func=cmd_doctor)
    run_parser = sub.add_parser("run")
    source = run_parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--issue", type=int)
    source.add_argument("--text")
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.set_defaults(func=cmd_run)
    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("resume").set_defaults(func=cmd_resume)
    cleanup_parser = sub.add_parser("cleanup")
    cleanup_parser.add_argument("--simulate", action="store_true", help=argparse.SUPPRESS)
    cleanup_parser.set_defaults(func=cmd_cleanup)
    sub.add_parser("verify-codex", help=argparse.SUPPRESS).set_defaults(func=cmd_verify_codex)
    sub.add_parser("self-test", help=argparse.SUPPRESS).set_defaults(func=cmd_self_test)
    sub.add_parser("self-test-lifecycle", help=argparse.SUPPRESS).set_defaults(func=cmd_self_test_lifecycle)
    return result


def main() -> int:
    ensure_venv_reexec()
    try:
        args = parser().parse_args()
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\n사용자가 중단했습니다. 상태가 저장되어 있으면 `resume`으로 이어갈 수 있습니다.", file=sys.stderr)
        return 130
    except (OrchestratorError, json.JSONDecodeError, OSError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
