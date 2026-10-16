# Brief: US30.R 125-point stop cap (owner decision 2026-10-15)

## Background
- Since 2026-10-02, `validate_sl_distance` (`MAX_SL_OVERSHOOT_PCT = 25`) vetoes any signal whose
  stop at minimum lot risks more than $125. For US30.R (min lot 0.01, point value 100 → $1/pt),
  that means every signal with a stop wider than **125 points** is rejected.
- US30.R stops (TradingView `suggested_sl`, else ATR(10)×3, else percentage fallback) routinely
  exceed 125 points. Result: effectively zero US30.R trades — none in the 2026-10-05→10-09 week.
- Code verified 2026-10-15: this is the gate working as designed, not a bug. Session gating
  (US30.R = ["NY"] only, 09:00–19:00 ET) is also confirmed correct.

## Owner decision (2026-10-15, explicit)
Cap US30.R's stop-loss distance at **125 points**. This is NOT a loosening of the risk guard:
the 25% / $125-at-min-lot framework stays untouched. Capping the stop keeps every US30.R trade
inside the existing risk box ($100–$125 risk vs $100 TP) while letting signals through.

## Work items
1. **Replay first (evidence bar).** From the alert log, pull US30.R alerts since 2026-10-02 that
   were vetoed by `validate_sl_distance`. Re-simulate each with the stop capped at 125 points
   (TP $100, BE +$75, profit lock +$80 → +$50, NY session only). Report: vetoed count,
   simulated net P&L, win rate, expectancy/trade, and how many vetoed signals would have been
   winners vs losers. This is the "what did the gate cost us" number.
2. **Implement the cap.** Add a per-symbol max stop distance for US30.R = 125 points, applied
   in the SL resolution path AFTER `suggested_sl` / ATR(10)×3 / fallback is resolved and BEFORE
   `validate_sl_distance` runs. If resolved SL distance ≤ 125, leave it untouched; if wider,
   clamp to exactly 125 points from entry. Keep `validate_sl_distance` as the backstop.
   No changes to: the 25% guard, TP ($100), BE (+$75), profit lock (+$80 → +$50), sessions,
   or any other symbol.
3. **Tests.** Cap applies to US30.R only; boundary behavior (125 → untouched, 126 → clamped
   to 125); other symbols' SL resolution unchanged; a previously-vetoed 150-pt US30 signal now
   passes validation with $125 max risk.
4. **Post replay + test numbers in the PR**, then merge to main and deploy (server pull +
   restart) per the standing workflow.

## Out of scope
- Changing `MAX_SL_OVERSHOOT_PCT`, `TARGET_DOLLAR_RISK`, TP, BE/lock levels, or sessions.
- Any other symbol. US30.R only.
