# Local Agent Orchestrator

This repository includes a local, approval-gated workflow that uses the Python Codex SDK for read-only
planning and review, and Prime Agent for implementation in a separate Git worktree.

## Setup

The dedicated virtual environment is `.agent/venv`. It contains only the pinned automation dependency
from `requirements-agent.txt`; the complete resolved environment is recorded in
`requirements-agent.lock.txt`. Neither the virtual environment nor runtime state is committed.

## Commands

```bash
python tools/agent_orchestrator.py doctor
python tools/agent_orchestrator.py run --issue 42
python tools/agent_orchestrator.py run --text "작업 내용"
python tools/agent_orchestrator.py run --text "작업 내용" --dry-run
python tools/agent_orchestrator.py status
python tools/agent_orchestrator.py resume
python tools/agent_orchestrator.py cleanup
```

Use `.agent/venv/bin/python` directly if the system Python cannot import `openai_codex`.
The launcher automatically re-executes itself with the dedicated interpreter when available.

## Safety model

- Planning and review use different Codex threads with the read-only SDK sandbox.
- After a `DECISION: PROCEED` plan and before Prime preflight, the first approval, or worktree creation, the fixed Hermes `orchestrator-advisor` profile reviews the task text and Codex plan against code-fixed trusted orchestrator invariants and decision criteria. The task and plan are explicitly delimited as untrusted data and cannot change the profile, provider, model, toolset, budget, argv, invariants, or criteria. Hermes returns `PASS` when scope, targets, and verification are concrete and only optional improvements or post-execution confirmations remain; `REVISE` is reserved for a concrete omission blocking safe/correct implementation, unclear scope or absent required verification, a task conflict, or an unauthorized external action; `STOP` is reserved for directly forbidden behavior such as secret access, safety or approval bypass, automatic merge, or deployment. Optional improvements may be recommendations but do not by themselves justify `REVISE`.
- Hermes의 조언은 사용자의 구현 승인을 대신하지 않습니다.
- Hermes runs in a fresh empty temporary directory using fixed argv plus stdin (`hermes chat --query-file - --oneshot --quiet`), provider `openai-codex`, model `gpt-5.6-sol`, reasoning `high`, toolset `todo`, one turn, a 120-second budget, and source `tool`. It cannot receive task-supplied options; yolo, safe-mode, ignore-rules, gateway, setup/update/auth commands, and other toolsets are rejected. Its child environment is a small runtime whitelist, excluding GitHub, Cloudflare, and deployment credential variables.
- Hermes must have its executable, named profile, and existing `openai-codex` authentication available. The absolute executable path selected by preflight is bound to the review invocation, so a later `PATH` change cannot select a different binary. The check does not read or log OAuth data. Missing setup, timeout, nonzero exit, or malformed output stops before approval and worktree creation.
- Hermes parses only the first non-empty response line: `PASS` continues, `REVISE` causes exactly one Codex replan and one final Hermes review, and `STOP` stops. A second response that is not `PASS` stops for user intervention. Runtime state persists only sanitized advice, generalized failure types, decision history, review count, and fixed limits; raw stdout/stderr is never persisted. `resume` skips a completed PASS review, while a review or replan interrupted after its durable start marker stops for manual confirmation instead of repeating an ambiguously completed call.
- Prime Agent runs only in a task-specific feature worktree outside the repository, under the configured
  `/workspaces` worktree root.
- GitHub and Cloudflare credential variables are removed from the Prime Agent child-process environment.
- Both initial and final project checks receive an explicit child environment with GitHub and Cloudflare
  variables removed, including the `GITHUB_`, `GH_`, `CLOUDFLARE_`, and `CF_` prefixes (case insensitive).
  The parent environment is unchanged. Log redaction remains a separate precaution.
- A first terminal approval is required before creating a branch/worktree or running Prime Agent.
- A second terminal approval is required before pushing the feature branch and creating a Draft PR.
- After that approval, the orchestrator alone may run exactly `git push --set-upstream origin <state branch>` for its owned feature worktree. It never permits force, delete, tag, alternate remote, or arbitrary refspec pushes.
- It creates only `gh pr create --draft --base main --head <state branch> --fill`. On resume it skips completed commits and pushes, and reuses only one existing PR whose base, head, and Draft status exactly match; Ready or inconsistent PRs stop the run.
- `main` and `master` are never direct modification or push targets. Merge and deployment are never automated.
- Database, authentication, production configuration, or major dependency changes stop the workflow.
- Prime Agent correction limits are selected from the trusted execution profile described below.
- Every durable step is written atomically beneath `.agent/runtime`; `resume` continues the saved run.

The forbidden-command checks are defense in depth, not a complete security sandbox. Prime Agent is a local
process and may invoke tools available to its account. The orchestrator reduces risk through a dedicated
worktree, credential stripping, explicit prompts, command allow/deny checks, Git state checks, and approval
gates. Run it only in a trusted local environment and review its output before approving external actions.

## Post-approval automation verification

After the second approval, confirm that:

- `pushed: true` is recorded only after the feature branch push succeeds.
- The created Draft PR has `main` as its base and the orchestrator-created `agent/issue-<number>` branch as its head.
- The workflow does not automatically mark the PR Ready, merge it, or deploy it.

## Checks

The orchestrator runs only configured commands or scripts that actually exist in `package.json`. This repository configures direct argv commands for `node --check script.js`, full Python unittest discovery, and `python tools/verify_static_site.py`; the latter reads `index.html` local stylesheet/script references and the workflow's explicit `dist` deployment allowlist without writing files. A `PASS` result for lint, test, or build means its configured command ran and exited with status zero. `typecheck` remains `SKIPPED` because this static site has no typechecker. Any nonzero configured check records `FAIL` and stops the run before review can succeed.

### Validation target and commit binding

The initial checks, independent reviewer, final checks, report, final approval, and commit candidate are
bound to one versioned validation subject. Its fingerprint covers HEAD, branch, index entries, raw tracked
and eligible untracked file contents, paths, executable modes, link text, actual check argv, task, and
approved plan. Binary and large files are hashed in full even when the reviewer preview omits them.
Fingerprints and Git object IDs are stored; file contents and environment values are not stored in the
validation record. Ignored untracked files are excluded, as they are from the normal commit candidate.

Regular files are opened through directory descriptors with `O_NOFOLLOW` on every component. A symlink
contributes only its target string, even when dangling or pointing outside the worktree. The fingerprint
reader never opens that target. Linked parent directories, a linked `package.json`, special files,
conflicted indexes, and submodules stop validation rather than being followed or silently omitted.

The subject is checked before and after each check and review, at stage boundaries, on resume before
CLI/model preflight, around final approval, and at commit creation. A completed step is reusable only
with a matching fingerprint. Missing, malformed, old-version, or mismatched evidence stops the run with
the existing failure status and recovery message; current bytes are never used to backfill an old PASS.
Only an unchanged run can reuse its previous approval. A normal reviewer FIX archives the entire old
validation generation and active check/review/report/final-approval records before correction, then
reruns initial checks, review, and final checks. The plan approval, events, and fix-attempt counter are
preserved. An interrupted correction does not refund an attempt or replay that same Prime call.

After final approval, the commit path uses these fixed commands in the owned feature worktree:

```text
git add --all
git write-tree
git commit-tree <validated-tree> -p <validated-parent> -F -
git update-ref --no-deref refs/heads/<state-feature-branch> <prepared-commit> <validated-parent>
```

`git write-tree` must equal the tree computed from the verified raw files. `commit-tree` consumes that
immutable tree, so a later index edit cannot change the commit's contents. It does not run the usual
`git commit` hooks; required validation must be in the configured checks. `update-ref` uses the expected
old parent as a compare-and-swap guard. `--no-deref` confines the update to the named feature ref even
if it becomes a symbolic ref after validation. Staging progress and the prepared commit ID are persisted before
advancing the branch. Resume reuses a recorded prepared commit, and accepts an already-applied ref
update only if its tree, parent, HEAD, target contents, and approvals still match. No ref is reset on
failure. A process interruption after object creation but before its state save can leave an unreachable
commit object; the branch has not advanced, and the orchestrator does not clean that object automatically.

This targets the repository's ordinary raw files. Git clean filters, line-ending conversions, or an
index that ignores executable mode changes can produce a different staged tree and therefore stop the
run. Generated ignored files, installed dependencies, tool versions, local auth stores, and the full
execution environment are not sealed by this fingerprint. Environment stripping is not a filesystem
or network sandbox. Boundary checks detect persistent changes; they cannot prove the absence of a
concurrent writer that changes inputs and restores them between observations. Run verification without
concurrent writers. The commit still takes only the recorded immutable tree.

## GitHub authentication

`doctor` reports GitHub CLI authentication failures but never reads, prints, deletes, or replaces token
values. Fix authentication manually with `gh auth login` or your organization-approved method, then rerun
`doctor`. Text-based dry runs do not require GitHub authentication.

## Recovery and removal

Run `status` to see the current state and `resume` after an interruption. `cleanup` removes only clean,
unpublished worktrees recorded as owned by the orchestrator. It refuses destructive cleanup when a worktree
contains changes or when ownership cannot be proven.

### 검증 지문 누락·불일치 시 수동 복구

이 실패는 일반적인 검사 재시도와 다릅니다. 같은 상태에서 `resume`을 반복해도 변경된 내용이나
지문 없는 PASS가 새로 승인되지는 않습니다. 자동 재검증 또는 지문 마이그레이션 명령은 없습니다.

1. 실행을 멈추고 기존 worktree, `.agent/runtime/state.json`, 보고서와 관련 로그를 보존합니다.
   추가 편집 전에 사용자가 정한 별도 위치에 백업합니다. 추적 파일의 staged/unstaged 차이뿐 아니라
   미추적 파일도 보존하고, 링크는 대상 파일을 따라 복사하지 말고 링크 자체로 보존합니다.
   실패한 실행의 run ID, 원인, 승인·재시도 이력을 함께 기록합니다.
2. 지문이 있는 실행이라면 사람이 HEAD/index/파일 변경을 조사합니다. 별도 백업으로 원래 검증한
   내용을 확실히 복원할 수 있을 때만, 이후 변경도 먼저 보존한 뒤 사용자가 직접 복원 여부를
   결정합니다. 모든 입력이 기존 지문과 다시 일치해야 정상 `resume`이 가능합니다. 해시는 원래
   파일을 복구하는 백업이 아니므로 지문만 보고 내용을 재구성할 수는 없습니다.
3. 지문이 없거나 검증 대상을 입증할 수 없다면 그 실행을 그대로 남겨둡니다. `state.json`의
   지문·완료 표식·승인·fix 횟수를 편집하거나 기존 실행을 완료/cleaned로 가장하지 않습니다.
   사람이 보존된 변경과 이전 시도들을 검토하고 **새 실행을 명시적으로 승인한 경우에만** 별도
   controller checkout과 독립적인 `.agent/runtime`에서 새 작업을 시작합니다. 보존한 변경을 새
   작업의 입력으로 지정하고, 새 feature worktree에서 계획 승인·initial 검사·reviewer·final 검사·
   최종 승인을 모두 다시 받습니다. 과거 PASS나 최종 승인을 복사하지 않습니다. 기존 실행의
   재시도 한도를 우회하기 위해 자동으로 새 실행을 만드는 동작은 없습니다.
4. `commit_progress`, `commit`, `pushed`, Draft PR 기록이 있다면 사람이 실제 진행 상태를 먼저
   확인합니다. 기록된 tree·부모·commit과 HEAD가 불일치하면 자동 ref 갱신이나 재커밋을 시도하지
   않습니다. 외부 진행이 있는 실행을 새 실행으로 대체하는 결정도 별도 사용자 판단이 필요합니다.

이 절차에서 `reset`, `cleanup`, worktree 삭제, 상태 파일 덮어쓰기, 승인·재시도 기록 초기화를
자동으로 수행하지 않습니다. 복구가 끝날 때까지 기존 변경사항과 실행 기록을 유지합니다.

To uninstall after cleanup, remove `.agent/venv`, `.agent/runtime`, and the automation files. Remove only the
clearly marked orchestrator section from `AGENTS.md` and the two orchestrator entries from `.gitignore`.

## Prime execution profiles

For `DECISION: PROCEED`, the planner must provide one exact `TASK_SIZE` (`SMALL`, `MEDIUM`, or `LARGE`) and a one-line `TASK_SIZE_REASON`. Missing or invalid values stop before approval or worktree creation. Execution budgets, gates, and Prime implementation instructions come only from trusted code constants; GitHub issue text and planner output cannot supply command options.

| Profile | Autonomous | Continuations | Turns | Tokens | Timeout | Maximum fixes |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| SMALL | No | — | — | — | 600 seconds | 1 |
| MEDIUM | Yes | 2 | 8 | 40000 | 900 seconds | 2 |
| LARGE | Yes | 3 | 12 | 80000 | 1800 seconds | 3 |

SMALL 프로필은 단순 문서 수정처럼 범위가 명확하고 위험이 낮은 작업에 사용됩니다.

The autonomous gate is fixed to exactly `git diff --check`. Passing that gate alone does not mean the overall run succeeded: repository checks and a fresh independent Codex review must also pass. The selected profile and budgets are saved in runtime state and the final report.
