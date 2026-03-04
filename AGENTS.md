# AGENTS.md

## Mandatory Startup Context

Before doing analysis, code changes, backtests, or recommendations in this repository,
the agent must read:

1. `docs/PROJECT_MEMORY.md`
2. Any strategy doc relevant to the requested symbol in `docs/STRATEGY_*.md`
3. `docs/ONBOARDING.md` when the task is onboarding/backtesting workflow related

If there is a conflict, use this precedence:
- concrete code behavior > strategy docs > project memory summary

## Memory Persistence Policy

- Treat `docs/PROJECT_MEMORY.md` as the persistent project memory source.
- Whenever the project behavior changes meaningfully, update this file in the same task.
- Do not assume cross-session memory; re-read the file each new session.

## Update Rule

When adding or changing:
- strategy parameters,
- command workflows,
- exchange precision details,
- risk guardrails,

also update `docs/PROJECT_MEMORY.md` so future sessions keep the same context.

