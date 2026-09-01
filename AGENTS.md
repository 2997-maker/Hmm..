# Project Working Agreement

## Persistent project memory

- Read `PROJECT_CONTEXT.md` before starting any non-trivial task in this repository.
- Treat `PROJECT_CONTEXT.md` as the durable handoff between conversations. Conversation history may be unavailable or incomplete.
- Update `PROJECT_CONTEXT.md` near the end of a task whenever any durable fact changes, including:
  - project goals, scope, or audience;
  - deployment URLs, external design files, important node/frame IDs, or integration status;
  - visual direction, technical architecture, or other meaningful decisions;
  - completed work, current work, blockers, or the recommended next step.
- Do not update the context file for transient debugging details, routine commands, or facts already recorded accurately.
- Keep the context concise, factual, dated where useful, and understandable without the original conversation.
- Never store secrets, API tokens, credentials, private authentication data, or expiring asset URLs in project memory.
- When a task materially changes the design direction, also update the design section in `PROJECT_CONTEXT.md`.
- Before handing off completed work, check whether the memory file needs an update. Mention material context updates in the final response.

## Repository safety

- Preserve unrelated user changes.
- The site is deployed from `main` to the existing Cloudflare Pages project. Do not deploy, push, or change external resources unless the user requests it or it is clearly part of the requested workflow.

## Agent orchestrator safety rules

The following rules apply to work performed through `tools/agent_orchestrator.py`:

- Never modify `main` or `master` directly.
- Use a task-specific feature branch and a separate Git worktree.
- Never push, merge, or deploy without explicit user approval.
- Never print or commit secrets, tokens, credentials, or private environment values.
- Obtain approval before changing databases, authentication, or production configuration.
- Before completion, run only the lint, typecheck, test, and build commands that actually exist in this repository.
- Report changed files, check results, and remaining risks.
