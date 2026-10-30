"""BE-rule backtest replay (Issue #18): full run over Sep 24 - Oct 23.

Reuses loss_study helpers for broker reconstruction, then runs 4 BE variants.
Caches executions and bars to disk for resume support.
"""
import os, sys, json, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import loss_study as ls
import be_rule_backtest as brb
import pandas as pd

ALERT_PATHS = [Path("/opt/trader-agent/scripts/alerts_log.jsonl")]
CACHE_DIR = Path("/opt/trader-agent/be_replay_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def tp_for_date(dt):
    return 125.0 if dt < datetime(2026, 10, 4, tzinfo=timezone.utc) else 100.0


def _load_cache(name):
    p = CACHE_DIR / name
    if p.exists():
        return json.loads(p.read_text())
    return None


def _save_cache(name, data):
    (CACHE_DIR / name).write_text(json.dumps(data, default=str))


def run():
    tl = ls._build_client()
    time.sleep(3)
    alerts, load_stats = ls.load_successful_alerts(ALERT_PATHS)
    print(f"Loaded {len(alerts)} alerts")

    start_ts = int(min(a.timestamp for a in alerts).timestamp() * 1000)
    end_ts = int(datetime.now(timezone.utc).timestamp() * 1000)

    # Cache orders
    orders_cache = _load_cache("orders.json")
    if orders_cache is None:
        orders = tl.get_all_orders(start_timestamp=start_ts, end_timestamp=end_ts, history=True)
        _save_cache("orders.json", orders.to_dict("records") if hasattr(orders, "to_dict") else [])
        time.sleep(3)
    else:
        orders = pd.DataFrame(orders_cache)
    print(f"Orders: {len(orders)}")

    instruments = tl.get_all_instruments()
    instrument_ids = dict(zip(instruments["name"].astype(str), instruments["tradableInstrumentId"].astype(int)))

    linked, unmatched, matched_by_time = ls.map_alerts_to_positions(alerts, orders, instrument_ids)
    print(f"Linked {len(linked)} positions")

    # Cache executions (hoisted outside loop)
    exec_cache = _load_cache("executions.json")
    if exec_cache is None:
        executions = tl.get_all_executions()
        _save_cache("executions.json", executions.to_dict("records") if hasattr(executions, "to_dict") else [])
        time.sleep(3)
    else:
        executions = pd.DataFrame(exec_cache)
    print(f"Executions: {len(executions)}")

    # sl lookup
    sl_lookup = {}
    for line in open(ALERT_PATHS[0]):
        try:
            d = json.loads(line)
            a = d.get('alert', {})
            if a.get('suggested_sl') is not None:
                ts = d.get('timestamp', '')[:19].replace('T', ' ')
                sym = a.get('ticker', '')
                if not sym.endswith('.R'):
                    sym = sym + '.R'
                sl_lookup[(ts, sym)] = a['suggested_sl']
        except Exception:
            pass

    # Resume support
    results_cache = _load_cache("results.json")
    trades_out = results_cache if results_cache else []
    done_keys = set((t.get("symbol"), t.get("entry_time")) for t in trades_out if t.get("variant") == "A")
    print(f"Resuming: {len(done_keys)} trades already done")

    missing = 0
    for position_id, alert in linked.items():
        key = (alert.symbol, None)
        try:
            os_ = ls.position_summary(executions, position_id)
            if os_ is None:
                missing += 1
                continue
            inst_id = instrument_ids.get(alert.symbol)
            if inst_id is None:
                missing += 1
                continue

            entry_time_str = str(os_["entry_time"])[:19]
            if (alert.symbol, entry_time_str) in done_keys:
                continue

            details = tl.get_instrument_details(int(inst_id))
            time.sleep(2)
            bars, resolution = ls._fetch_history(tl, inst_id, os_["entry_time"], os_["exit_time"])
            time.sleep(2)
            normalized = ls.normalize_bars(bars)
            normalized["bar_close_time"] = normalized["time"] + pd.to_timedelta(
                {"1m": 60, "5m": 300, "15m": 900}.get(resolution, 60), unit="s"
            )
            in_life = normalized[
                (normalized["time"] >= os_["entry_time"])
                & (normalized["bar_close_time"] <= os_["exit_time"])
            ]
            if in_life.empty:
                missing += 1
                continue

            entry_price = os_["entry_price"]
            entry_side = os_["side"]
            qty = os_["quantity"]
            pv = ls.usd_per_price_unit(alert.symbol)
            if alert.symbol.endswith('.R') and 'EUR' in alert.symbol:
                pv *= 1.0

            lk = (str(alert.timestamp)[:19].replace('T', ' '), alert.symbol)
            sl_price = sl_lookup.get(lk)
            if sl_price is None:
                risk_pts = 100.0 / (qty * pv) if (qty * pv) else 10
                if entry_side == "buy":
                    sl_price = entry_price - risk_pts
                else:
                    sl_price = entry_price + risk_pts

            dt = os_["entry_time"]
            if hasattr(dt, "to_pydatetime"):
                dt = dt.to_pydatetime()
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            tp_dollars = tp_for_date(dt)
            if entry_side == "buy":
                tp_price = entry_price + tp_dollars / (qty * pv) if (qty * pv) else entry_price + 1
            else:
                tp_price = entry_price - tp_dollars / (qty * pv) if (qty * pv) else entry_price - 1

            bar_list = [(float(r["high"]), float(r["low"])) for _, r in in_life.iterrows()]

            for variant in brb.VARIANTS:
                ep, reason, be, locked = brb.simulate_exit(
                    bar_list, entry_price, entry_side, sl_price, tp_price,
                    qty, pv, variant
                )
                pnl = brb.pnl_for(ep, entry_price, entry_side, qty, pv)
                trades_out.append({
                    "symbol": alert.symbol,
                    "entry_time": str(dt),
                    "week": dt.strftime("%Y-W%W"),
                    "variant": variant,
                    "exit_reason": reason,
                    "pnl": round(pnl, 2),
                    "be_triggered": be,
                    "locked": locked,
                    "actual_pnl": round(os_.get("gross_pnl_price_units", 0) * pv, 2),
                })

            done_keys.add((alert.symbol, entry_time_str))
            _save_cache("results.json", trades_out)
            print(f"  done: {alert.symbol} {entry_time_str} ({len(trades_out)//4} trades total)")
        except Exception as e:
            missing += 1
            print(f"  skip {alert.symbol}: {str(e)[:80]}")
            time.sleep(5)

    # Report
    for variant in brb.VARIANTS:
        subset = [t for t in trades_out if t["variant"] == variant]
        pnls = [t["pnl"] for t in subset]
        net = sum(pnls)
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / len(pnls) * 100 if pnls else 0
        exp = net / len(pnls) if pnls else 0
        be_count = sum(1 for t in subset if t["be_triggered"])
        lock_count = sum(1 for t in subset if t["locked"])
        tp_count = sum(1 for t in subset if t["exit_reason"] == "tp")
        sl_count = sum(1 for t in subset if t["exit_reason"] == "sl")
        print(f"\nVariant {variant}: n={len(subset)}, net=${net:.2f}, "
              f"WR={wr:.1f}%, exp=${exp:.2f}, BE={be_count}, locked={lock_count}, TP={tp_count}, SL={sl_count}")

    a_trades = [t for t in trades_out if t["variant"] == "A"]
    if a_trades:
        sim_net = sum(t["pnl"] for t in a_trades)
        actual_net = sum(t["actual_pnl"] for t in a_trades)
        print(f"\nVariant A reconciliation: sim=${sim_net:.2f} vs actual=${actual_net:.2f}, gap=${sim_net-actual_net:.2f}")

    print(f"\nTotal trades: {len(trades_out)//4}, missing: {missing}")
    _save_cache("results.json", trades_out)
    print("Results saved to cache")


if __name__ == "__main__":
    run()
