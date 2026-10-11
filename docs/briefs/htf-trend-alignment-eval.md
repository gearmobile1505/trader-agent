# Repo-agent brief: HTF trend-alignment filter — replay evaluation (TESTING ONLY)

## Purpose

Testing / evaluation ONLY. Do NOT change any live trading logic in this PR.
The deliverable is replay evidence + a recommendation, so the owner can decide
whether to implement anything.

## Background

The bot's Gate 2 trend gate (`validate_trend_gate`, `ENABLE_TREND_GATE`) reads
trend off the **5-minute** timeframe: TradingView's tech summary plus internal
scoring (EMA9/21, SMA50, RSI, MACD, ADX, Williams) on 5m candles. A 5m buy
signal can therefore fire while the 1H/4H trend points the other way. The owner
wants to test whether requiring higher-timeframe alignment improves the strategy.

## HTF trend definition (pick one, state it in the PR)

Suggested: reuse the swing-structure classifier from the 4H trend radar —
higher highs + higher lows = uptrend, lower highs + lower lows = downtrend,
otherwise no-clear-trend — computed on 1H and 4H candles at signal time.
Alternative: 4H EMA stack (price > EMA21 > EMA50 = up, reverse = down).
Whichever is chosen, document the exact rule in the PR.

## Variants to replay (60 days of alert/trade history)

- **Variant A (direction gate):** reject any 5m entry whose direction opposes the
  4H trend. When the 4H reads "no clear trend", allow the trade (do not filter
  range days in this variant) — or test both treatments and report separately.
- **Variant B (retracement + continuation):** Variant A, PLUS require the entry
  to occur on a HTF pullback — e.g. price within a stated tolerance of the 4H
  EMA21, or inside a Fib 0.382–0.618 retracement of the current HTF leg.
  State the exact pullback rule used.

## Required reporting

1. Per variant: trades blocked, trades taken, net P&L vs baseline, win-rate
   delta, expectancy delta, per-symbol breakdown. **Baseline is the 38-trade
   bot-only history** (see correction below) — not the 40-trade mixed history.
2. CORRECTION (owner 2026-10-11): the 2026-10-07 EURUSD.R (+$493.00) and EURJPY.R
   (+$369.24) trades were the owner's MANUAL trades using his 4H price-action
   BOS-retracement strategy — not bot trades. EXCLUDE them from the bot baseline
   (true bot week 10/5→10/9: 38 trades, net -$600.16). Instead, use the owner's
   manual approach as the reference concept: report whether each variant would
   have TAKEN setups like those two manual trades (4H break-of-structure +
   retracement entries, EURUSD/EURJPY entered 10/6 afternoon ET, held overnight)
   — i.e., does the filter select for the same kind of trade the owner's manual
   strategy takes? The filter must not silently exclude the strategy's best
   manual-style opportunities.
3. HTF data availability: state the fail-open vs fail-closed choice for when
   1H/4H candles can't be fetched at signal time, and measure how often that
   path triggers across the replay window.
4. Trade-frequency impact: expected entries per week under each variant.

## Constraints

- No live trading-logic changes in this PR. Evaluation only.
- Risk parameters stay frozen for any eventual implementation discussion:
  $100 risk, $100 TP, +$75 breakeven, +$80 trigger locking +$50,
  $400 max daily loss. Entry vetting only.

## Deliverable

Replay numbers for both variants posted in the PR, plus a clear recommendation
(implement A, implement B, or don't implement) backed by those numbers.
No merge of trading-logic changes and no deploy from this PR.
