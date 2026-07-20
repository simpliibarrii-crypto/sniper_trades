# Codex Execution Handoff

Repository: `simpliibarrii-crypto/sniper_trades`

Start branch: `design/security-foundation`

Create implementation branch: `feature/security-foundation`

Primary design:
- `docs/superpowers/specs/2026-07-20-sniper-trades-security-foundation-design.md`

Implementation plan:
- `docs/superpowers/plans/2026-07-20-sniper-trades-security-foundation.md`

## Required workflow

1. Read the design and implementation plan completely before editing code.
2. Create and work only on `feature/security-foundation`; do not implement on `main`, `master`, or `design/security-foundation`.
3. Follow the implementation plan task-by-task using test-driven development.
4. Before each implementation step, write the specified failing test and run it to confirm the expected failure.
5. Make the smallest implementation that passes the current tests.
6. Run the task-specific verification commands and the full existing test suite before every task commit.
7. Keep commits small and use the commit messages specified in the plan.
8. Never add or commit secrets, API keys, wallet seed phrases, private keys, session tokens, CSRF tokens, or live confirmation phrases.
9. Preserve public read-only market intelligence while protecting private state, paid model calls, warm research sessions, and all mutations.
10. The fixed phrase `CONFIRM LIVE` alone must never authorize a real trade.
11. Live challenges must be single-use, session-bound, action-digest-bound, and consumed before the exchange adapter runs.
12. LAN mode over plain HTTP must remain documented as trusted-network convenience, not active-attacker protection.
13. Run Ruff, Bandit, pip-audit, secret scanning, compile checks, and the full pytest suite before opening a pull request.
14. Review the final diff for accidental route exposure, CORS widening, token logging, challenge replay, and any way paid Grok calls could occur anonymously.
15. Open a pull request from `feature/security-foundation` to the repository default branch with a security summary, migration notes, test evidence, and known limitations.

## Initial Codex command

Use this instruction in Codex:

> Implement the approved Sniper Trades security foundation. Read `CODEX_HANDOFF.md`, the design spec, and the implementation plan first. Create `feature/security-foundation` from `design/security-foundation`, then execute the plan exactly with TDD, small commits, full verification, and a final pull request. Do not weaken any security boundary or claim protections beyond the documented threat model.
