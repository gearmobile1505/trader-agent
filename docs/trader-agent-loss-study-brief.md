# Brief: data-driven loss study — MAE analysis + time-stop simulation (ANALYSIS ONLY, no live-logic change)

## Context

The owner asked for ways to minimize losses beyond the current guards (+$75 breakeven, +$90 → +$50 lock, $400 daily-loss gate, 60-min cooldown, trend veto). Two candidate directions came out of the trade review:

1. **Data-driven stop calibration** — find, from real trade data, the adverse-excursion level where recovery becomes rare, instead of picking a number by feel.
2. **Time stop** — scratch trades that don't get going, instead of letting them bleed to the full stop.

The owner's -$75 → -$90 tighter-stop idea is **explicitly rejected — do not implement it**. Tightening the stop inside the noise range (e.g. $15 of room left on a gold trade) raises the stop-out rate; ~$10 saved per full loser is wiped out by each winner-turned-loser ($125+ of upside gone plus the $90 loss). The study below replaces it with evidence.

## Deliverable of THIS PR: analysis, not a trading change

Do **not** change any live entry/exit logic in this PR. The deliverable is:

1. A reusable study script (e.g. `scripts/loss_study.py`) plus its full numeric output.
2. A PR comment posting the complete results: sample sizes, MAE/MFE distributions, recovery-probability table, time-stop simulation grid, and a clear recommendation (specific parameters, or "no change recommended").
3. Optional small observability addition (item 4 below).

Implementation of any new rule happens only in a follow-up brief after the owner reviews the numbers.

## Data: use as much as possible

- Primary source: the full `alerts_log.jsonl` (all history available, not a truncated window) — every accepted alert that became a position, with entry time, symbol, side, size.
- Reconstruct each trade's intra-trade P&L path from the broker's price history (`tl.get_price_history`, finest resolution available) from entry to exit; convert to dollars using the same point-value math as the live TP calc.
- Bot-attributed trades only. Exclude anything not traceable to an accepted alert in the log.
- Document the sample size for every bucket you report. If a symbol has too few trades for a stable estimate, say so rather than pooling blindly — XAUUSD/XPDUSD behave very differently from USDJPY/GBPJPY.

## Study 1 — MAE/MFE and recovery probability

For each trade compute:
- **MAE**: worst unrealized P&L reached while the trade was open.
- **MFE**: best unrealized P&L reached.
- Final outcome bucket: full-SL loss / breakeven scratch / small win / TP win / profit-lock exit.

Report:
- MAE distribution for eventual winners vs eventual losers (median, p25/p75).
- **Recovery table**: P(final outcome ≥ breakeven | MAE reached -$X) for X in {25, 50, 75} dollars, overall and per symbol.
- The adverse level where recovery probability collapses (e.g. below ~15–20%) — that is the evidence-based candidate for a tighter stop, *if* one is warranted at all.
- Sanity check against the current -$100 risk: what fraction of full-SL losers ever showed a meaningful recovery chance after -$75?

## Study 2 — time-stop simulation

Simulate: "if unrealized P&L < +$P at T minutes after entry, scratch at market (minus fees)". Grid over T in {15, 30, 45, 60} minutes and P in {0, 10, 25} dollars. For each cell report vs the no-time-stop baseline:
- Net P&L delta (after fees).
- Number of trades scratched; of those, how many would have been winners (and their foregone P&L) vs full losers (and their saved loss).
- Per-symbol breakdown.

## Item 4 — lightweight uPnL sampling (observability only)

Add a per-monitor-poll append to a `position_pnl_log.jsonl`: timestamp, position id, symbol, unrealized P&L. No trading-logic change. This makes future MAE studies exact instead of reconstructed from price history. Keep the write cheap and failure-safe (a logging failure must never affect trading).

## Standing rules (do not change)

Risk parameters are frozen: $100 TARGET_DOLLAR_RISK, $125 TP1, +$75 breakeven trigger, +$90 → +$50 lock, $400 MAX_DAILY_LOSS, sizing formula, lot caps, session windows and SESSIONS_ET map, flattening behavior, AI_FAIL_OPEN, and the 10-minute `bar_time` freshness buffer (deliberate, not a bug).

**Post the full numbers as a PR comment. No merge and no deploy until the owner approves. If the data says neither idea beats the current guards, the correct deliverable is "no change recommended" with the evidence.**
