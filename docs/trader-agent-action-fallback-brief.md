# Brief: fail closed on unresolvable alert action (no default-to-buy)

**Found by reviewer (Zon), 2026-10-01**, from a live webhook processed at 14:40:06 UTC:

```json
{"action": "{{strategy.order.action}}", "ticker": "XPDUSD", "indicator_value": 1173.438,
 "trend": "Phantom Shift Buy", "bar_time": "2026-10-01T14:35:00Z", "trigger_time": "2026-10-01T14:40:00Z"}
```

That alert resolved correctly (trend text yielded `buy`, trend gate vetoed it — good). But it exposed a fail-open edge in `_process_tradingview_alert()` (`scripts/main_cfd_5m.py`, placeholder-action block right after `action = data.get("action")`):

- If `action` arrives as a literal `{{...}}` placeholder, the code falls back to `alert_name`, then to the `trend` text.
- **If neither yields a side, the final fallback is `action = "buy"`.** A payload whose direction cannot be determined silently becomes a BUY candidate and proceeds to the gates and AI decider.

## Required change

1. Keep the `alert_name` → `trend` fallback exactly as is (it works — see the live alert above).
2. Replace the default-to-buy with a fail-closed rejection: if the action is still not `buy`/`sell` after those fallbacks, return `{"status": "rejected", "reason": "Unresolvable action in alert payload", "gate": "action_parse"}` and log it via the normal `log_alert` path. No decider call, no order.
3. Apply the same rejection to any `action` value that is missing, empty, or not `buy`/`sell` after normalization (do not let an arbitrary string flow downstream as an order side).
4. Change nothing else: gates, sizing, risk, TP, sessions, cooldown, daily-loss, `AI_FAIL_OPEN` all frozen.

## Tests required

- Placeholder action + trend "Phantom Shift Buy"/"Phantom Shift Sell" → resolves to buy/sell (regression lock for today's live behavior).
- Placeholder action + trend carrying no side (e.g. missing/`"Unknown"`) → rejected at `action_parse`, and the stubbed decider / `create_order` are never called.
- Missing or garbage `action` string → same rejection.
- Existing handler/replay tests still pass; post the test output on the PR.

**No merge and no deploy until the owner approves.**
