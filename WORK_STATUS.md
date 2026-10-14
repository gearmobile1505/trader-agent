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
  91 successful bot trades, date range 2026-09-29 to 2026-10-13
- **TradeLocker order history** via `tl.get_all_orders(lookback_period="90D", history=True)` —
  553 filled orders
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

## RESULTS (Generated 2026-10-14)

### Baseline (all bot trades)
- 88 trades, 41 wins, 46 losses
- Net P&L: $-1,143.14
- Win rate: 46.6%
- Expectancy: $-12.99

### Variant A (direction gate)
- 55 trades taken, 33 blocked
- Net P&L: $-1,050.47 (delta: +$92.67 vs baseline)
- Win rate: 43.6% (delta: -3.0pp)
- Expectancy: $-19.10 (delta: -$6.11)

### Variant B (retracement + continuation)
- 49 trades taken, 39 blocked
- Net P&L: $-860.98 (delta: +$282.16 vs baseline)
- Win rate: 44.9% (delta: -1.7pp)
- Expectancy: $-17.57 (delta: -$4.58)

### Manual Trade Survival Check
Both owner's manual trades would PASS both variants:
- EURUSD.R (+$493.00, SELL): 4H downtrend → PASS Variant A and B
- EURJPY.R (+$369.24, SELL): 4H downtrend → PASS Variant A and B

### HTF Data Availability
- 0% failure rate (all 88 trades had sufficient 4H bars)
- Choice: FAIL-OPEN (when 4H candles can't be fetched, the trade is allowed)

### Trade Frequency Impact
- Baseline: 41.1 trades/week
- Variant A: 25.7 trades/week
- Variant B: 22.9 trades/week

### Per-Symbol Breakdown (Variant B)
| Symbol | Trades | Net P&L | Win Rate |
|---|---|---|---|
| GBPJPY.R | 8 | $61.33 | 50.0% |
| NAS100.R | 3 | $-99.74 | 33.3% |
| UKOIL.R | 5 | $-311.71 | 40.0% |
| US30.R | 3 | $-173.44 | 33.3% |
| USDJPY.R | 7 | $-99.64 | 28.6% |
| XAUUSD.R | 20 | $-120.97 | 55.0% |
| XPDUSD.R | 3 | $-116.81 | 33.3% |

## What Needs to Be Done
1. **Post the replay numbers as a PR comment** on PR #10
2. The script is working — results are in `/tmp/htf_replay_results.csv` and `/tmp/htf_replay_summary.json`
3. No further code changes needed unless the numbers need adjustment