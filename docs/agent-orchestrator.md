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
- Prime Agent runs only in a task-specific feature worktree outside the repository, under the configured
  `/workspaces` worktree root.
- GitHub and Cloudflare credential variables are removed from the Prime Agent child-process environment.
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

The orchestrator discovers scripts that actually exist in `package.json`. Missing lint, typecheck, test, or
build commands are recorded as `SKIPPED`, never invented. Commands may also be explicitly configured in
`.agent/config.json`.

## GitHub authentication

`doctor` reports GitHub CLI authentication failures but never reads, prints, deletes, or replaces token
values. Fix authentication manually with `gh auth login` or your organization-approved method, then rerun
`doctor`. Text-based dry runs do not require GitHub authentication.

## Recovery and removal

Run `status` to see the current state and `resume` after an interruption. `cleanup` removes only clean,
unpublished worktrees recorded as owned by the orchestrator. It refuses destructive cleanup when a worktree
contains changes or when ownership cannot be proven.

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
