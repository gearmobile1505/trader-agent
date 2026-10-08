# Brief: enforce stop-out cooldown on the alert entry path (not only via the background monitor)

## What happened (real trades, TradeLocker DEMO GATESFX #2458648)

- 2026-10-01 12:12 ET: UKOIL.R BUY 0.10 (Order 216172782136186127 / Position 216172782117670033, entry 100.066) closed by **stop loss set at entry** — a breakeven scratch, net -$3.10.
- 2026-10-01 12:30 ET: UKOIL.R SELL 0.16 (Order 216172782136247607 / Position 216172782117677568, entry 99.980) opened — **18 minutes later, opposite direction, same symbol**.
- 2026-10-01 12:50 ET: the SELL stopped out for the full initial risk, net **-$111.68**.

The 60-minute post-stop-out cooldown (`ENABLE_COOLDOWN_GATE = True`, `COOLDOWN_AFTER_STOP_MINUTES = 60`, Gate 4 in `process_tradingview_alert`) should have rejected the 12:30 alert. It did not. This is the same whipsaw re-entry pattern as 2026-09-30 (UKOIL.R BUY stopped 03:16 ET → SELL entered 03:20 ET, both stopped, -$217.23 combined) that the cooldown was built to prevent.

## Likely root cause

`record_stop_out()` is only ever called from the background breakeven-monitor loop (`be_check_loop`), which detects closes by polling and then matching filled `stop`/`stop_limit` orders in order history. Failure modes:

1. **Race:** if a new alert arrives before the monitor's next poll records the close, the entry path sees no cooldown.
2. **Detection miss:** a breakeven exit is a stop fill *at the entry price*. If TradeLocker reports the close of a position whose SL was moved to entry under a different order type (or the history window doesn't cover it), the `stop_fills` filter never matches and no cooldown is recorded — silently.
3. The entry path (`is_in_cooldown`, Gate 4) trusts the in-memory/persisted dict as its *only* source of truth.

## Required changes

1. **Entry-path direct check (the core fix).** In `process_tradingview_alert`, at Gate 4, before accepting an alert for a symbol, query recent order/position history directly for that symbol: if any stop-exit (full SL *or* breakeven stop) occurred within the last `COOLDOWN_AFTER_STOP_MINUTES`, reject the alert with `gate: "cooldown"` and log the blocking close (order/position id, exit time, type). Do not depend solely on the monitor having recorded the event first.
2. **Make the monitor's detection observable.** Log every detected close with its classification: `[COOLDOWN] recorded for {sym} (stop fill {order_id})` vs `[COOLDOWN] close detected but NOT a stop fill — type={type}, no cooldown recorded`. Verify against the live platform what order type a breakeven-stop close reports as, and make sure BE fills count as stop-outs for cooldown purposes.
3. **Keep the persisted cooldown dict as a backup layer**, not the only enforcement. Both layers must agree; on conflict, the more restrictive (longer remaining) wins.
4. **History-unavailable behavior:** if the entry-path history check itself fails (fetch error), fail OPEN (do not block the alert) — consistent with the standing `AI_FAIL_OPEN` rule — but log `[COOLDOWN] history check failed, failing open` so it is visible.

## Tests (handler-level, through `process_tradingview_alert`)

- Alert 18 min after a breakeven stop-exit on the same symbol → rejected, `gate == "cooldown"`.
- Alert 18 min after a full-SL stop-out → rejected (existing behavior, keep green).
- Alert 61 min after a stop-exit → accepted (cooldown expired).
- Alert on a *different* symbol 18 min after the stop-exit → accepted (per-symbol scoping).
- History-fetch failure on the entry path → alert still processed (fail-open), warning logged.
- Monitor detection: simulated BE-stop close produces a `[COOLDOWN] recorded` log line and a persisted entry.

## Expected effect

The 2026-10-01 12:30 ET UKOIL.R SELL would have been rejected → ~$111.68 of the day's loss avoided. No change to entries that respect the cooldown.

## Standing rules (do not change)

Risk parameters are frozen: TARGET_DOLLAR_RISK ($100), TP_CONFIG ($125 TP1), TRAILING_SL_BE_PROFIT ($75), TRAILING_SL_LOCK_TRIGGER ($80) / TRAILING_SL_LOCK_DOLLARS ($50), MAX_DAILY_LOSS ($400), sizing formula, lot caps, session windows, flattening behavior, AI_FAIL_OPEN. This brief changes only *which alerts become trades*, never position sizing or risk.

**Show the replay numbers before merge. No deploy until the owner approves.**
