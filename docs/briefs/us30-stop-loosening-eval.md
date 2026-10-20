# Brief: US30.R — diagnose the continued drought; evaluate loosening the stop cap

**Owner request (2026-10-20):** US30.R is still not trading — zero trades since the
125-pt stop cap (PR #12) merged on 2026-10-15. Owner has pre-authorized loosening
the US30 stop **if the diagnosis shows it's needed**, within the limits below.
Owner quote: "draft a brief so the bot can check if we need to loosen the stop,
thats fine."

## Why US30 matters

- Lifetime live record through 2026-09-26: **US30.R +$734.54, 27 trades, 59.3% win
  rate** — the #2 symbol on the account behind XAUUSD.R (+$1,611.08), and the only
  pair net-positive in both variants of the 2026-09-30 backtest.
- Zero US30.R trades since 2026-10-02.

## Background — what already changed, and what each piece does

1. **2026-10-02 (PR #6):** `MAX_SL_OVERSHOOT_PCT` tightened 100 → 25 after a US30.R
   stop-through lost -$233.62 on $152.48 of planned risk. Effect: any signal whose
   planned risk at minimum lot exceeds $125 is vetoed. US30.R (min lot 0.01,
   point_value 100 → $1/pt) therefore needs a stop ≤ 125 pts; its ATR(10)×3 stops
   routinely run wider. This guard is **global** (all symbols).
2. **2026-10-11 (PR #9):** US30.R sessions `["NY_EARLY","NY"]` → `["NY"]`.
   (NY_EARLY replay: 4 trades, -$307, PF 0.23.) **Not in scope for this brief —
   US30 stays NY-only.**
3. **2026-10-15 (PR #12, merge `139e62e`; PR #13 fix, main now `ffa91b9`):**
   per-symbol stop cap `MAX_SL_DISTANCE_POINTS = {"US30.R": 125.0}` clamps the
   resolved stop to 125 pts before `validate_sl_distance`, so capped US30 trades
   plan exactly $125 at min lot and pass the gate. Replay of the 27 signals vetoed
   since Oct 2: net +$500, 40.7% WR, +$18.52 expectancy (8 of 27 unresolved —
   treat as directional).
4. **Since the cap:** Fri 10/16, Mon 10/19, Tue 10/20 — still zero US30.R trades.
   The PR #12/#13 server deploy (pull + restart) was **never independently
   verified**.

## Task 1 — verify the deploy (do this first)

- Report the commit the server is actually running, the bot process start time,
  and `/health` status.
- If the server is not running `ffa91b9` (or later), pull + restart, and state
  plainly that the cap was never live. Then continue to Task 2 with the deploy
  fixed — the rest of the diagnosis still matters.

## Task 2 — log audit: every US30.R alert since 2026-10-16 00:00 ET

For each US30.R alert received, report: timestamp (ET), action, and disposition —
accepted (order placed) or rejected, with the rejecting gate and reason
(freshness, trend gate, sl-distance, spread guard, session, cooldown,
one-position-per-symbol, AI decider, other). Summarize counts per reason.

- For sl-distance rejections, report the resolved stop distance **before and
  after** the cap, and the risk figure the gate computed.
- For spread-guard rejections, report the observed spread vs `max_spread` 2.0.
- If **zero** US30.R alerts arrived in the window, say so explicitly — that
  points at the TradingView side, not the bot, and Tasks 3–4 change accordingly
  (use the full signal set since Oct 2 for the replay).

## Task 3 — replay the loosening options

Using all US30.R signals received since 2026-10-02, NY session only, with the
existing management rules ($100 TP, breakeven at +$75, profit lock +$80 → +$50):

| Variant | Stop treatment | Max planned risk at min lot |
|---|---|---|
| (a) Baseline | Cap 125 pts (current) | $125 |
| (b) | Cap 150 pts | $150 |
| (c) | Cap 175 pts | $175 |
| (d) | Cap 200 pts (= pre-Oct-2 guard level) | $200 |
| (e) Control | No cap, guard as-is | — (expect most vetoed) |

Per variant report: trades taken, W/L, net P&L, win rate, expectancy, worst
single-trade planned risk. State the unresolved count and the bar-data source,
as in the PR #12 replay.

## Task 4 — decision rule (owner pre-authorized)

- **If Task 1 found a stale deploy** and the cap is now live: no code change.
  Report and stop; the owner will watch the next sessions.
- **If alerts are arriving and still being vetoed on stop distance with the cap
  live:** implement the **smallest** cap increase from Task 3 that restores a
  reasonable trade flow with non-negative expectancy. Hard ceiling: **200 pts /
  $200 min-lot risk**. Post the replay numbers + tests, then merge + deploy per
  the standing workflow, and report the running commit after deploy.
- **If no variant shows non-negative expectancy:** do NOT loosen. Report the
  numbers and stop — the owner decides from there.
- **If the rejections are dominated by a different gate** (spread, trend,
  freshness): do not loosen the stop to compensate. Report that finding on its
  own.

## Implementation note (if loosening happens)

Raising `MAX_SL_DISTANCE_POINTS` alone does nothing: `validate_sl_distance`
would still veto any US30 trade planning more than $125 at min lot. The
loosening must be implemented **per-symbol for US30.R only** — e.g. a per-symbol
risk-overshoot allowance consulted by `validate_sl_distance` for symbols present
in `MAX_SL_DISTANCE_POINTS` — so the global 25% guard and every other symbol's
behavior stay exactly as they are. Add regression tests: (1) a US30 signal with
a 180-pt ATR stop is accepted at the chosen cap with correct stop side/price;
(2) a non-US30 symbol with the same overshoot is still vetoed; (3) the cap value
in force matches the replay winner from Task 3.

## Frozen — do not touch

- $100 TP, +$75 breakeven, +$80 → +$50 profit lock, $400 max daily loss.
- Sizing formula and lot caps.
- Sessions (US30.R stays NY-only; do not restore NY_EARLY).
- All other symbols' guards, caps (none), and session maps.
- `MAX_SL_OVERSHOOT_PCT = 25` stays as the global backstop.

## Evidence bar (standing)

Replay numbers + test results posted before merge; deploy confirmed by
reporting the server's running commit afterward.
