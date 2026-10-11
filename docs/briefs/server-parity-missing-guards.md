# Repo-agent brief: server/main parity + implement missing guards

Paste to the repo agent. It implements on a branch, posts evidence in the PR, then merges + deploys per the standing workflow (no owner approval needed once evidence is posted).

## Step 0 — Server/main parity (do FIRST, before any code changes)

1. On the deployment server, in the trader-agent checkout, run:
   `git rev-parse HEAD`, `git status --porcelain`, and diff the working tree against `origin/main`.
2. Report: exact commit hash running on the server vs `origin/main` (currently `0ef241e`),
   any uncommitted/local changes, any commits on the server that are not on `main`.
3. If the server differs from `origin/main` in any way: sync it to `origin/main`
   (pull/reset as appropriate), restart the bot, and confirm the running commit hash.
   Report exactly what was done.
4. Goal: when this step is finished, the server runs the exact code on `origin/main` — zero drift.

## Step 1 — Check each guard against the SERVER code; implement what's missing

For each item below: first verify whether it exists in the deployed code.
If it exists and works, report that and move on. If missing, implement it on a
branch with replay + test evidence.

### (a) Breakeven stop-limit evaluation (remainder of the merged 2026-10-07 brief, Part A)

- Status on main (`0ef241e`): only the BE-slippage tripwire was merged (logging).
  Stop-limit BE orders are NOT implemented — BE/trailing stops go through the
  broker-native stop modification (stop-market). The code itself notes at
  `validate_slippage_guard`: "For production: could use stop-limit order with offset".
- Fresh evidence: 2026-10-08 XAUUSD.R BUY slipped 7.5 points through the BE stop
  (SL was at entry 4112.57, filled 4105.04) for -$38.00. Third confirmed case
  after the two on 2026-10-07.
- Task: replay-evaluate stop-limit BE orders (limit offset vs stop price).
  Implement only if replay shows the leak shrinking without material fill-rate loss.
  Keep the tripwire either way.

### (b) US30.R pre-market (NY_EARLY) restriction

- Status on main: `US30.R` sessions are still `["NY_EARLY", "NY"]`. There is no
  liquidity or time gate for the first 30 minutes of the session.
- Evidence: 2026-10-02 US30.R short lost -$233.62 with ~81 points of stop-through
  slippage in thin pre-market conditions.
- Task: replay US30.R with NY_EARLY vs NY-only (and/or a first-30-minutes filter).
  Implement whichever config the replay supports. Post the numbers.

### (c) Direction-aware whipsaw cooldown

- Status on main: the stop-out cooldown is keyed by symbol only
  (`_STOP_OUT_COOLDOWN[tl_symbol]`, 60 minutes). There is no direction awareness —
  an opposite-direction signal 61 minutes after a stop-out sails straight through.
- Fresh evidence (week of 2026-10-05):
  - 2026-10-05 UKOIL.R: BUY stopped (-$113.28), then SELL ~2.5h later stopped (-$108.63).
  - 2026-10-06 XPDUSD.R: BUY stopped (-$122.70), then SELL stopped (-$129.92).
- Task: replay with the opposite-direction block extended to 3–4 hours after a
  stop-out (same-direction entries keep the 60-minute cooldown). Implement only if
  it cuts whipsaw losses without killing winners. Post the numbers.

## Frozen constraints

- Risk parameters stay frozen: $100 risk, $100 TP, +$75 breakeven,
  +$80 trigger locking +$50, $400 max daily loss. Entry vetting only.
- Evidence bar unchanged: replay numbers + green tests posted in the PR before merge.
- NOT in scope: removing XPDUSD.R (parked — awaiting the owner's explicit go-ahead).
  Do not implement it in this PR.

## Deliverable

A PR containing: the server parity report (commit hash before/after, what was synced),
per-item check results (exists vs missing on the server), replay numbers for each
implemented change, and test results. Then merge + deploy (pull + restart).
