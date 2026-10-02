# Brief: generalize stop-slippage protection beyond XPDUSD.R + tighten min-lot risk tolerance

## What happened (real trades, TradeLocker DEMO GATESFX #2458648, 2026-10-02)

1. **US30.R SELL 0.01** (Order 216172782136415624 / Position 216172782117694921): entered 06:25 ET @ 51,099.01, SL 51,251.49 (152.48 pts), TP 50,973.20. Price gapped through the stop in pre-market; filled @ 51,332.56 — **~81 pts beyond the stop**. Net **-$233.62** on a $100-risk design.
2. **XPDUSD.R BUY 0.10** (Order 216172782136416669): entered 06:30 ET, clean-ish stop, net -$128.20 on $117.60 planned risk (lot-step rounding).
3. **XAUUSD.R BUY 0.05**: textbook $100.95 full-risk loss, clean fill — system working as designed.

No session violations, no whipsaw, no stacking. The entries were legitimate under current rules. Two distinct gaps combined on trade 1:

## Gap A — min-lot risk tolerance is decorative

`validate_sl_distance()` runs on the entry path, but `MAX_SL_OVERSHOOT_PCT = 100` allows up to **$200** of risk at minimum lot. The US30 trade's planned risk was $152.48 (0.01 lots is the floor at $1/pt × 152.48-pt ATR stop) and passed without a murmur. A guard that waves through 152% of target is not guarding.

**Fix:** tighten `MAX_SL_OVERSHOOT_PCT` from 100 to **25** (cap $125 at min lot). The $100 `TARGET_DOLLAR_RISK` itself is unchanged — this only narrows how far a wide-stop/min-lot combination may overshoot it. The US30 entry ($152.48) would have been rejected; the XPDUSD entry ($117.60) still passes.

## Gap B — slippage guard is XPDUSD.R-only, and only screens entries

`validate_slippage_guard()` returns early for every symbol except XPDUSD.R. The 9/28 XPDUSD -$541 stop-through motivated that guard, but 2026-10-02 proves index CFDs fail the same way: US30 slipped ~81 pts through its stop at 08:30 ET. Note the XPD guard is also *pre-entry* (spread check) — it would not have prevented an exit-time gap-through either.

**Fixes:**
1. Generalize the pre-entry spread guard to all symbols using the `max_spread` already defined per symbol in `TOP_SYMBOLS` (US30.R: 2.0). Fail closed on unavailable spread data, same as XPDUSD.R today.
2. Investigate exit protection and report the trade-offs with data before implementing: (a) stop-limit orders with a bounded offset instead of stop-market for exits, vs (b) skipping/flagging thin-session index entries (the slip happened in NY_EARLY pre-market). A stop-limit that doesn't fill in a gap leaves the position open — potentially worse — so do not implement blindly; bring replay numbers for both options.
3. Add an exit-slippage tripwire: when a stop fill exceeds the SL by more than 2× the SL distance (mirroring `XPDUSD_SLIPPAGE_MULTIPLE`), log it prominently and record the event for the vetting log. Observability first, blocking later.

## Tests

- Entry with min-lot risk $152 on $100 target → rejected (overshoot guard).
- Entry with min-lot risk $117.60 → accepted.
- Spread above a symbol's `max_spread` → rejected; spread data unavailable → rejected (fail closed), for a non-XPD symbol.
- Existing XPDUSD.R guard behavior unchanged (keep its tests green).
- Replay: run the 2026-10-02 US30 trade and recent wide-stop entries through the new gates; report which would have been blocked and the net P&L delta.

## Standing rules (do not change)

$100 TARGET_DOLLAR_RISK, $125 TP1, +$75 breakeven trigger, +$90 → +$50 lock, $400 MAX_DAILY_LOSS amount, sizing formula and lot caps, session windows and the SESSIONS_ET map, flattening behavior, AI_FAIL_OPEN, and the 10-minute `bar_time` freshness buffer (deliberate, not a bug). This brief tightens *vetting thresholds only*.

**Show the replay numbers before merge. No merge and no deploy until the owner approves.**
