# Brief: same-symbol per-session loss cap (new vetting gate)

Source: weekly bot-vetting review 2026-10-04 (week 2026-09-28 → 2026-10-04, journal rows 09-28→09-30 only; journal stale 10-01→10-04).

## Problem

Stop clusters: 5 instances in the review week of 2+ full-SL losses on the same symbol in one session. The entry-path 60-min post-stop cooldown (PR #4) fires only on stop-exits within 60 minutes; nothing stops the 3rd+ entry after a symbol has already burned through two full stops in the session.

## Evidence (TradeLocker demo, trade journal)

- UKOIL.R 2026-09-30 asia: BUY 0.14 entered 03:00 ET (Order 216172782135831090), SL hit 03:16, net -$116.48; SELL 0.13 entered 03:20 ET (Order 216172782135836366), SL hit 03:44, net -$100.75 — two full-SL losses in 45 min, then a third entry (BUY 04:20, +$123.31).
- XAUUSD.R 2026-09-30 asia: SELL 0.05 entered 20:50 ET 9/29 (Order 216172782135758334), SL hit 03:08 ET, net -$106.90; SELL 0.05 entered 01:15 ET (Order 216172782135811458), SL hit 02:53, net -$106.45 — stacked pair, combined -$213.35.
- USDJPY.R 2026-09-29 asia: SELL 0.33 entered 01:45 ET (Order 216172782135535985), breakeven exit 02:30, net -$3.36; SELL 0.34 entered 03:30 ET (Order 216172782135556903), SL hit 04:15, net -$34.99.
- XPDUSD.R 2026-09-29 asia: -$22.12 (BUY) + -$541.53 (SELL stop-through) — the stop-through is already covered by PR #6's guards; the cap below is about the second entry after the first full stop, not the stop-through itself.
- XAUUSD.R 2026-09-29 ny: -$3.50 (BUY, BE) + -$35.50 (SELL, BE) — both breakeven scratches; must NOT count toward the cap.

## Required behavior

After **2 realized full-SL stop-outs** (net loss ≥ $50 each — breakeven scratches never count) on the **same symbol within the same session** (use the existing TOP_SYMBOLS/SESSIONS_ET boundaries; sessions themselves are frozen), reject further alerts for that symbol until the session ends. One session's cap must not carry into the next session.

## Implementation notes

- Derive the count from the same stop-out tracking the entry-path cooldown uses (`find_recent_stop_exit` / `record_stop_outs_from_history`); if history can't be fetched, fail open with a loud log, consistent with the cooldown entry check's deliberate AI_FAIL_OPEN behavior — the persisted dict still applies.
- BE-scratch exits (net loss < $50) never contribute to the count.
- Add tests:
  - two full losses same symbol/session → third alert rejected;
  - BE scratches don't trigger the cap;
  - cap resets at session boundary;
  - history-fetch failure → fail open with loud log.

## Expected effect

Stops same-symbol churn after the symbol proves adverse for the session.

Honest note: on this week's data the cap would have blocked the UKOIL 04:20 ET BUY (+$123.31 winner) — so run a full replay over `alerts_log.jsonl` and report blocked-entry count and net P&L delta vs. baseline before claiming improvement.

## Frozen

Risk parameters are frozen: no change to $100 risk, $125 TP, $75 breakeven, the +$90 → +$50 profit lock, sizing formula, lot caps, or sessions.

show the replay numbers before merge, no deploy until approved.
