# HTF Trend-Alignment Filter Replay — Work Status
# Branch: vetting/htf-trend-alignment-eval
# Date: 2026-10-14
# Owner: gearmobile1505
# PR: #10

## What We Know

### The Task
Replay the bot's trade history through two HTF (higher timeframe) trend-alignment
filter variants and post the numbers as a PR comment. This is evaluation ONLY —
no live trading logic changes.

### Data Sources (all available on the live server at 138.197.8.50)
- **alerts_log.jsonl** at `/opt/trader-agent/scripts/alerts_log.jsonl` — 454 lines,
  88 successful bot trades, date range 2026-09-29 to 2026-10-13
- **TradeLocker order history** via `tl.get_all_orders(lookback_period="90D", history=True)` —
  548 filled orders, 275 positions
- **TradeLocker 4H price history** via `tl.get_price_history(instrument_id, resolution="4H", ...)`
- **TradeLocker 1H price history** for USDJPY rate conversion

### Server Credentials (already decrypted to /tmp/decrypted_env)
- TL_ENV=https://demo.tradelocker.com
- TL_USER=gearmobile1505@gmail.com
- TL_PASS=ve8bb0!S
- TL_SERVER=GATESFX
- USDJPY instrument id: 13440

### How to Run Replay Script
```bash
ssh root@138.197.8.50
cd /opt/trader-agent
SOPS_AGE_KEY_FILE=/etc/sops/age/keys.txt sops -d .env > /tmp/decrypted_env
set -a && source /tmp/decrypted_env && set +a
/opt/trader-agent/venv/bin/python3 scripts/replay_htf_eval.py 2>&1
```

### Key Findings So Far

#### Alert-to-Order Matching (DEBUGGED, WORKING)
- 88 successful bot trades in alerts_log.jsonl
- All 88 have broker_response order_ids (16-digit numbers like 216172782135535984)
- All 88 order_ids ARE in the TradeLocker order history (verified)
- BUT: `match_trade_to_position()` returns 0 matches — the bug is in the position
  reconstruction logic, NOT the order_id lookup

#### TradeLocker Order Structure
- Each position has 2 filled orders: entry (market) + exit (market/stop/limit)
- Some positions have isOpen=True (still open, skip)
- Some positions have 3+ filled orders (trailing stop cases)
- Entry order: first by fill time (lastModified)
- Exit order: last by fill time (lastModified)
- Price data: use `avgPrice` column for actual fill price

#### P&L Calculation
- Non-JPY pairs: `(exit_price - entry_price) * direction * point_value * qty`
- JPY pairs (GBPJPY, USDJPY): need USDJPY rate conversion
- EUR pairs (LVMH, SIEMENS): multiply by EUR_USD_RATE (1.08)

#### HTF Trend Computation (IMPLEMENTED, NOT YET TESTED)
- Fetch 4H bars per symbol covering the full replay window
- Compute swing-structure trend at each entry time:
  - Compare first half vs second half of 20-bar lookback
  - Higher highs + higher lows = uptrend
  - Lower highs + lower lows = downtrend
  - Otherwise = no_clear_trend
- Also compute EMA21 and EMA50 for Variant B
- **Variant A**: reject if trade direction opposes 4H trend; allow on no_clear_trend
- **Variant B**: Variant A + require entry within 2% of 4H EMA21

#### Filter Variants to Evaluate
- **Variant A** (direction gate): reject entries that oppose 4H trend
- **Variant B** (retracement + continuation): Variant A + entry near 4H EMA21

#### Required Reporting
1. Per variant: trades blocked, trades taken, net P&L vs baseline, win-rate delta,
   expectancy delta, per-symbol breakdown
2. Whether owner's manual EURUSD.R (+$493) and EURJPY.R (+$369) trades survive
3. HTF data availability: fail-open vs fail-closed choice, failure rate
4. Trade frequency impact: expected entries per week

### Current State
- `scripts/replay_htf_eval.py` is written and on the server
- It correctly loads 88 bot trades and matches all 88 order_ids
- But `match_trade_to_position()` returns 0 — needs debugging of position reconstruction
- The HTF trend computation logic is implemented but not yet tested end-to-end
- No results have been generated yet

### What Needs to Be Done
1. Fix `match_trade_to_position()` to correctly reconstruct positions from TradeLocker orders
2. Run the replay to generate numbers for both variants
3. Post the results as a PR comment on PR #10