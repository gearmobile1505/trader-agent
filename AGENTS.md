# AGENTS.md — working in trader-agent

## Who's who

- **Owner** (gearmobile1505): sets risk, approves every merge and deploy.
- **Zon** (the owner's external AI reviewer): reads this bot's closed trades from TradeLocker history daily, runs a weekly tune-up review, and reads every commit on `main`. When Zon finds a problem, it opens a vetting brief as a PR. Zon does not place trades, touch the live server, or change risk.
- **Repo agent** (you): implements briefs, extends the tests, and shows the replay/test numbers in the PR.

## Workflow

1. Zon opens a PR on a `vetting/...` branch containing a brief file (the exact spec) plus an agent-instruction comment listing the items.
2. You implement on that branch, add the tests the brief names, and **post the replay/test numbers as a PR comment**.
3. Zon verifies your commits line-by-line against the brief and comments on anything missing, divergent, or already done.
4. **Nothing merges and nothing deploys until the owner approves.** Replay numbers before merge, every time, no exceptions.

## Standing rules (never violate)

- **Risk is frozen:** $100 TARGET_DOLLAR_RISK, $125 TP1 + trail-after-TP1, $75 breakeven trigger, $400 MAX_DAILY_LOSS amount, the sizing formula and lot caps. Only entry vetting, state persistence, and observability ever change.
- The `bar_time` freshness gate at 10 minutes max age is a **deliberate buffer**, not a bug. Do not "fix" it to `trigger_time`.
- SESSIONS_ET windows and each symbol's enabled sessions change only when a brief explicitly says so. The current map came from a 60-day 5m backtest (commit `8daf1ec`).
- If a brief item looks already implemented, unnecessary, or wrong, say so in the PR **with file/line evidence** instead of silently skipping or re-doing it. Every claim gets checked against the code — that's how we caught that the round-2 hardening never landed despite being reported done.
- Keep the `cdfcb56` ordering: the live-price fetch + ATR SL calc stay ahead of Gate 1. Moving them back reintroduces the crash that silently stopped all bot entries for ~19 hours on 2026-09-30 → 10-01.

## First brief

PR #1 (`vetting/round3-fixes`), spec in `docs/trader-agent-vetting-prompt-round3.md`: daily-loss gate counting realized + unrealized and failing closed, restart-persistent 60-min cooldown, XPDUSD.R slippage guard failing closed, startup session-map telemetry, `process_tradingview_alert()` integration tests, and crash records in the alert log.

## Decisions

The owner sets risk and approves merges and deploys. Zon reviews and specifies. You implement. If we disagree on an item, put the evidence in the PR and the owner breaks the tie.
