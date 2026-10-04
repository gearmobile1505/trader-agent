# AGENTS.md — working in trader-agent

## Who's who

- **Owner** (gearmobile1505): sets risk and strategy direction. Does not approve each merge/deploy — see Workflow.
- **Zon** (the owner's external AI reviewer): reads this bot's closed trades from TradeLocker history daily, runs a weekly tune-up review, and reads every commit on `main`. When Zon finds a problem, it opens a vetting brief as a PR. Zon does not place trades, touch the live server, or change risk.
- **Repo agent** (you): implements briefs, extends the tests, posts the replay/test numbers in the PR, then merges and deploys.

## Workflow

1. Zon opens a PR on a `vetting/...` branch containing a brief file (the exact spec) plus an agent-instruction comment listing the items.
2. You implement on that branch, add the tests the brief names, and **post the replay/test numbers as a PR comment**.
3. **Merge to `main` and deploy: pull `main` on the server and restart the bot process so the change goes live. Do this right after posting the numbers — do not wait for owner approval.** (Policy changed 2026-10-04: per-merge approvals cost the owner tokens, so the approval gate is removed. The numbers must still be posted in the PR before merging.)
4. Zon verifies your commits line-by-line against the brief after merge and comments on anything missing or divergent. If Zon flags a real problem, fix it on a new branch/PR promptly — revert first if it's breaking.

## Standing rules (never violate)

- **Risk is frozen:** $100 TARGET_DOLLAR_RISK, $125 TP1 + trail-after-TP1, $75 breakeven trigger, $400 MAX_DAILY_LOSS amount, the sizing formula and lot caps. Only entry vetting, state persistence, and observability ever change. (Note: PR #8 proposes TP $125 → $100 on owner instruction — update this line when it merges.)
- **Main is the branch that ships — always push changes to `main`.** `main` is the source of truth the server runs. Implement on the brief branch, post the numbers, then merge to `main` and deploy promptly. Never leave finished work sitting on a side branch, and never push work to `main` that isn't covered by a brief or an explicit owner instruction.
- The `bar_time` freshness gate at 10 minutes max age is a **deliberate buffer**, not a bug. Do not "fix" it to `trigger_time`.
- SESSIONS_ET windows and each symbol's enabled sessions change only when a brief explicitly says so. The current map came from a 60-day 5m backtest (commit `8daf1ec`).
- If a brief item looks already implemented, unnecessary, or wrong, say so in the PR **with file/line evidence** instead of silently skipping or re-doing it. Every claim gets checked against the code — that's how we caught that the round-2 hardening never landed despite being reported done.
- Keep the `cdfcb56` ordering: the live-price fetch + ATR SL calc stay ahead of Gate 1. Moving them back reintroduces the crash that silently stopped all bot entries for ~19 hours on 2026-09-30 → 10-01.

## First brief

PR #1 (`vetting/round3-fixes`), spec in `docs/trader-agent-vetting-prompt-round3.md`: daily-loss gate counting realized + unrealized and failing closed, restart-persistent 60-min cooldown, XPDUSD.R slippage guard failing closed, startup session-map telemetry, `process_tradingview_alert()` integration tests, and crash records in the alert log.

## Decisions

The owner sets risk and strategy. Zon reviews and specifies. You implement, merge, and deploy. If we disagree on an item, put the evidence in the PR and the owner breaks the tie.
