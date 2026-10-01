# Brief: second-stage stop rule — lock +$50 after +$90 profit

**Owner decision (2026-10-01):** add another stop-loss management rule. When an open position reaches **+$90 unrealized profit**, move its stop to the price that locks in **+$50 profit**. The existing breakeven rule stays unchanged: at **+$75**, stop moves to entry.

## Current behavior (verified on `main` @ `af7a3f2`)

- `scripts/main_cfd_5m.py` has `TRAILING_SL_BE_PROFIT = 75.0`.
- `check_and_apply_trailing_stops()` moves SL to `avgPrice` once per position when `unrealizedPl >= 75`, tracked by `_be_applied_positions`.
- There is no second-stage profit lock after breakeven.

## Required change

1. **Keep the existing $75 breakeven rule exactly as is.** Do not change `TRAILING_SL_BE_PROFIT`, target risk, TP config, sizing, sessions, cooldown, daily-loss amount, flattening, or `AI_FAIL_OPEN`.
2. Add a second stage with exact behavior:
   - `unrealizedPl >= 90` → stop target is the price that locks **+$50 profit** for that position.
   - `75 <= unrealizedPl < 90` → stop target remains entry/breakeven.
   - If profit jumps straight past +$90 between monitor ticks, go directly to the +$50 lock; do not stop at breakeven first.
3. **Price math:** compute the +$50 lock distance as `50 / (quantity * point_value)`, using the same point-value conversion as the existing TP calculation (including JPY dynamic point value and EUR conversion). For a buy: `avgPrice + distance`. For a sell: `avgPrice - distance`. Quantize to the symbol tick size. After quantization, clamp so a buy lock stop is never below entry and a sell lock stop is never above entry (rounding must not turn the +$50 lock into something worse than breakeven).
4. **No backwards moves.** Never move a stop to a less protective price. If the broker/position data exposes the current SL and it is already at or beyond the computed target, skip. Track the applied stage per `position_id` so that once +$50 is applied, a later profit dip back into the $75–$90 band does not move the stop back to entry. On restart, be conservative: do not downgrade an existing broker stop; explain the restart guard you implemented.
5. **Idempotent + logged.** Re-running the monitor must not spam duplicate modify calls for the same stage. Keep the existing BE log format and add a clear lock log, e.g. `[SL LOCK] Position {id}: SL moved to +$50 lock @{price}, P&L=${unrealized}`.
6. Do not modify take-profit placement or session-end flattening. If you find an interaction with session flatten/force-close logic, flag it in the PR rather than changing that behavior.

## Tests required

Add tests covering:

- Buy and sell +$50 lock price math for a normal USD symbol.
- EUR and JPY point-value conversions for the lock distance.
- Stage selection: +$80 → breakeven only; +$95 → +$50 lock; direct jump to +$95 → +$50 lock, not entry.
- Monotonic guard: after +$50 is applied, profit falling to +$80 does not move the stop back; an already-better broker SL is left alone.
- Monitor integration with a stubbed TradeLocker client: `check_and_apply_trailing_stops()` calls `modify_position` with the expected `stopLoss` once per stage.

Existing replay/handler tests must still pass. Post the test command output on the PR.

## Reporting before merge

Post the test output on the PR and state the expected effect plainly. If the available offline data cannot measure intrabar max-favorable-excursion (so the rule's P&L effect cannot be honestly replayed), say that rather than inventing replay numbers.

**No merge and no deploy until the owner approves.**
