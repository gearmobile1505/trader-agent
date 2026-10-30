"""BE-rule backtest replay (Issue #18): full run over Sep 24 - Oct 23.

Reuses loss_study helpers for broker reconstruction, then runs 4 BE variants.
"""
import os, sys, json, statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import loss_study as ls
import be_rule_backtest as brb
import pandas as pd

ALERT_PATHS = [
    Path("/opt/trader-agent/scripts/alerts_log.jsonl"),
]

def tp_for_date(dt):
    return 125.0 if dt < datetime(2026, 10, 4, tzinfo=timezone.utc) else 100.0

def run():
    import time
    time.sleep(5)
    tl = ls._build_client()
    alerts, load_stats = ls.load_successful_alerts(ALERT_PATHS)
    print(f"Loaded {len(alerts)} alerts")
    # Build sl lookup from raw alert log
    sl_lookup = {}
    for line in open(ALERT_PATHS[0]):
        try:
            d = json.loads(line)
            a = d.get('alert', {})
            r = d.get('result', {})
            if (r.get('status') in ('filled','success','executed','placed')
                    or 'position' in str(r).lower() or r.get('reason','').startswith('placed')):
                if a.get('suggested_sl') is not None:
                    ts = d.get('timestamp','')[:19].replace('T',' ')
                    sym = a.get('ticker','')
                    if not sym.endswith('.R') and sym in ('EURUSD','GBPUSD','USDCHF','AUDUSD','USDCAD','NZDUSD','EURJPY','EURGBP','AUDJPY','USDJPY','XAUUSD','XAGUSD','XPDUSD','US30','NAS100','SPX500','UKOIL'):
                        sym = sym + '.R'
                    key = (ts, sym)
                    sl_lookup[key] = a['suggested_sl']
        except Exception:
            pass

    start_ts = int(min(a.timestamp for a in alerts).timestamp() * 1000)
    end_ts = int(datetime.now(timezone.utc).timestamp() * 1000)
    orders = tl.get_all_orders(start_timestamp=start_ts, end_timestamp=end_ts, history=True)
    instruments = tl.get_all_instruments()
    instrument_ids = dict(zip(instruments["name"].astype(str), instruments["tradableInstrumentId"].astype(int)))
    linked, unmatched, matched_by_time = ls.map_alerts_to_positions(alerts, orders, instrument_ids)
    print(f"Linked {len(linked)} positions")

    trades_out = []
    missing = 0
    import time as _t2
    for position_id, alert in linked.items():
        _t2.sleep(2)
        try:
            os_ = ls.position_summary(
                tl.get_all_executions(), position_id
            )
            if os_ is None:
                missing += 1
                continue
            inst_id = instrument_ids.get(alert.symbol)
            if inst_id is None:
                missing += 1
                continue
            details = tl.get_instrument_details(int(inst_id))
            import time as _t
            for _attempt in range(5):
                try:
                    bars, resolution = ls._fetch_history(
                        tl, inst_id, os_["entry_time"], os_["exit_time"]
                    )
                    break
                except Exception as _e:
                    if "429" in str(_e) or "Too Many" in str(_e):
                        _t.sleep(10 * (_attempt + 1))
                    else:
                        raise
            else:
                raise RuntimeError("rate limited")
            normalized = ls.normalize_bars(bars)
            # filter bars inside position lifetime
            normalized["bar_close_time"] = normalized["time"] + pd.to_timedelta(
                {"1m": 60, "5m": 300, "15m": 900}[resolution], unit="s"
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
            key = (str(alert.timestamp)[:19].replace('T',' '), alert.symbol)
            sl_price = sl_lookup.get(key)
            if sl_price is None:
                # fallback: approximate from $100 risk
                risk_pts = 100.0 / (os_["quantity"] * pv) if (os_["quantity"] * pv) else 10
                if entry_side == "buy":
                    sl_price = entry_price - risk_pts
                else:
                    sl_price = entry_price + risk_pts

            qty = os_["quantity"]
            pv = ls.usd_per_price_unit(alert.symbol)
            if alert.symbol.endswith('.R') and 'EUR' in alert.symbol:
                pv *= 1.0
            if alert.symbol.endswith(".R") and "EUR" in alert.symbol:
                pass

            dt = os_["entry_time"]
            if hasattr(dt, "to_pydatetime"):
                dt = dt.to_pydatetime()
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            tp_price = tp_for_date(dt)

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
                    "actual_pnl": round(os_.get("realized_pnl", 0), 2),
                })
        except Exception as e:
            missing += 1
            print(f"  skip {alert.symbol}: {e}")

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
        print(f"\nVariant {variant}: n={len(subset)}, net=${net:.2f}, "
              f"WR={wr:.1f}%, exp=${exp:.2f}, BE-triggered={be_count}, locked={lock_count}")

    # Variant A reconciliation
    a_trades = [t for t in trades_out if t["variant"] == "A"]
    if a_trades:
        sim_net = sum(t["pnl"] for t in a_trades)
        actual_net = sum(t["actual_pnl"] for t in a_trades)
        print(f"\nVariant A reconciliation: sim=${sim_net:.2f} vs actual=${actual_net:.2f}, "
              f"gap=${sim_net-actual_net:.2f}")

    with open("/tmp/be_replay_full.json", "w") as f:
        json.dump(trades_out, f, indent=2, default=str)
    print(f"\nTotal trades: {len(trades_out)//4}, missing: {missing}")
    print("Results saved to /tmp/be_replay_full.json")


if __name__ == "__main__":
    run()
