# Repo-agent brief: stop-tightening MAE evaluation — find the adverse-excursion sweet spot (TESTING ONLY)

## Purpose

Testing / evaluation ONLY. Do NOT change any live trading logic in this PR.
The deliverable is replay evidence + a recommendation, so the owner can decide
whether a tighter stop is worth implementing. The owner is considering moving
the stop from -$100 to -$50 and wants data on whether that causes more damage
(winners killed) than good (losses saved).

## Background

The bot's initial stop is risk-based: position size is set so a full stop-out
loses approximately -$100 (`TARGET_DOLLAR_RISK = 100`). The owner wants to know
the "usual negative equity" on the bot's trades — i.e., at what adverse
excursion a trade is likely to fail — and whether a tighter stop ($50, or an
intermediate $75) would reduce losses on net.

Prior art: PR #5 (`vetting/loss-study`, merged 2026-10-03) built
`scripts/loss_study.py`, which reconstructs per-trade MAE/MFE from broker price
history and links alerts to positions. Its corrected run produced **0 valid
price paths** because the broker price-history API returned Cloudflare 1015
rate-limit responses. Do NOT rebuild the matching/reconstruction logic from
scratch — reuse `loss_study.py`. The new work in this PR is (a) a paced,
resumable re-run that gets past the 1015 problem, and (b) the stop-level
simulation below.

## The test

For every closed bot trade in the replay window (bot-attributed trades only —
see baseline note), reconstruct the intra-trade unrealized-P&L path in dollars
at the finest resolution the broker history API allows, excluding partial
entry/exit bars (per the PR #5 correction), with the same point-value /
currency math as the live TP calc.

### Study 1 — MAE distributions and recovery rates

- Per trade record MAE (maximum adverse excursion, $) and outcome bucket
  (full stop-out / BE scratch / small win / TP win / profit-lock exit).
- Report MAE distributions for winners vs losers (median, p75, p90, max).
- Recovery analysis: of trades whose MAE reached -$25 / -$50 / -$75, what %
  recovered to >= $0? What % went on to hit TP? This directly answers "at what
  negative equity is a trade likely to fail."

### Study 2 — stop-level simulation (-$50 / -$75 / -$100)

Replay each trade with a hard dollar stop at -$50, -$75, and -$100 (current
behavior reference):

- Walk the reconstructed P&L path; if unrealized touches the level before the
  trade's actual breakeven trigger fired (or if BE never triggered), count the
  trade as stopped at that level (apply a conservative slippage haircut and
  state it).
- If the BE/profit-lock trigger fired first in the actual trade, keep the
  actual outcome (the tighter initial stop never came into play).
- Report per level: winners killed (count + their actual P&L = damage),
  loser savings vs actual (benefit), net P&L delta vs baseline, expectancy
  delta, win-rate delta, per-symbol breakdown.
- Name the sweet spot explicitly: the level (if any) where savings clearly
  exceed damage. If no level beats the current -$100 on net, say so plainly —
  "don't implement" is an acceptable recommendation.

## Baseline

Bot-attributed closed trades from the full available `alerts_log.jsonl`
history. The owner's two manual EUR trades of 2026-10-07 (EURUSD.R +$493.00,
EURJPY.R +$369.24, his 4H BOS-retracement strategy) are NOT bot trades and must
be excluded from the baseline.

## Rate-limit handling (the PR #5 lesson)

- Run the price-history fetch off-hours, throttled (the 4H replay used 0.25s
  between calls without 1015s; size this fetch similarly or slower).
- Checkpoint after each symbol/day so an interrupted run resumes, not restarts.
- On the first Cloudflare 1015: stop fetching, keep what was collected, and
  report coverage (n trades with valid paths / n total) rather than retrying
  into the limit.

## Required reporting

1. Study 1: MAE distributions (winners vs losers), recovery rates at
   -$25/-$50/-$75, per-symbol sample sizes.
2. Study 2: per stop level (-$50/-$75/-$100) — winners killed, loser savings,
   net P&L delta, expectancy delta, win-rate delta, per-symbol breakdown.
3. The sweet-spot verdict: which level (if any) wins on net, and by how much.
4. Data-availability note: % of trades with valid reconstructed paths, and the
   fetch parameters that achieved it.

## Constraints

- No live trading-logic changes in this PR. Evaluation only.
- Risk parameters stay frozen for any eventual implementation discussion:
  $100 risk, $100 TP, +$75 breakeven, +$80 trigger locking +$50,
  $400 max daily loss.
- Do NOT implement the withdrawn 2026-10-02 idea (moving the stop tighter
  *after* negative equity, e.g. -$75 to -$90 mid-trade). This PR tests only the
  *initial* stop distance.

## Deliverable

Replay numbers for both studies posted in the PR, plus a clear recommendation
(tighten to $50, to $75, or don't tighten) backed by those numbers.
No merge of trading-logic changes and no deploy from this PR.
