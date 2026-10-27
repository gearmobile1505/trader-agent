"""BE-slip measurement (Issue #17, Part B).

Reports over the last 30 days of closed trades: count of BE-stop fills,
slippage distribution (points and dollars beyond the BE level), and total
leak dollars. Measurement only -- no trading-code changes.
"""
import os
import sys
import json
from datetime import datetime, timedelta, timezone
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main_cfd_5m as bot

BE_SLIP_MIN_LOSS = 0.01   # a fill counts as BE-slip if realized loss > this
BE_SLIP_MAX_LOSS = 20.0   # a BE-slip is a small slippage loss, not a full stop-out


def _safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _order_exit_epoch(row):
    for col in ("filledAt", "createdAt", "updatedAt"):
        v = row.get(col)
        if v is None:
            continue
        try:
            ts = float(v)
            if ts > 1e12:
                ts /= 1000.0
            return ts
        except (TypeError, ValueError):
            continue
    return None


def collect_be_slips(days=30):
    orders = bot._cached_all_filled_orders()
    if orders is None or len(orders) == 0:
        return []

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    results = []

    for _, row in orders.iterrows():
        if str(row.get("type", "")).lower() not in bot.COOLDOWN_STOP_EXIT_ORDER_TYPES:
            continue
        exited_at = _order_exit_epoch(row)
        if exited_at is None or exited_at < cutoff:
            continue
        tl_symbol = None
        inst_id = row.get("tradableInstrumentId")
        for sym, info in bot.TOP_SYMBOLS.items():
            if str(info.get("instrument_id")) == str(inst_id):
                tl_symbol = sym
                break
        if tl_symbol is None:
            continue

        pid = bot.normalize_id_static(row.get("positionId"))
        # find entry
        entries = {}
        for _, er in orders.iterrows():
            if str(er.get("tradableInstrumentId")) != str(inst_id):
                continue
            if str(er.get("type", "")).lower() not in ("market", "limit"):
                continue
            ep = bot.normalize_id_static(er.get("positionId"))
            if ep == pid and ep not in entries:
                entries[ep] = er

        entry_row = entries.get(pid)
        if entry_row is None:
            continue

        entry_price = _safe_float(entry_row.get("avgPrice") or entry_row.get("price"))
        fill_price = _safe_float(row.get("avgPrice"))
        qty = _safe_float(entry_row.get("filledQty") or entry_row.get("qty"))
        side = str(entry_row.get("side", "")).lower()
        if entry_price is None or fill_price is None or qty is None:
            continue

        point_val = bot.get_point_value(tl_symbol)
        if bot.TOP_SYMBOLS.get(tl_symbol, {}).get("currency") == "EUR":
            point_val *= bot.EUR_USD_RATE

        if side == "buy":
            realized = (entry_price - fill_price) * qty * point_val
        else:
            realized = (fill_price - entry_price) * qty * point_val

        # BE-slip: stop was at entry but filled with a small loss (slippage).
        # Full stop-outs (loss >= BE_SLIP_MAX_LOSS) are excluded.
        if BE_SLIP_MIN_LOSS < realized < BE_SLIP_MAX_LOSS:
            slip_points = abs(fill_price - entry_price)
            slip_dollars = realized
            results.append({
                "symbol": tl_symbol,
                "side": side,
                "entry_price": entry_price,
                "fill_price": fill_price,
                "qty": qty,
                "realized_loss": round(realized, 2),
                "slip_points": round(slip_points, 4),
                "slip_dollars": round(slip_dollars, 2),
                "exited_at": datetime.fromtimestamp(exited_at, tz=timezone.utc).isoformat(),
            })

    return results


def main():
    slips = collect_be_slips(days=30)
    total_leak = sum(s["slip_dollars"] for s in slips)
    by_symbol = defaultdict(lambda: {"count": 0, "dollars": 0.0})
    for s in slips:
        by_symbol[s["symbol"]]["count"] += 1
        by_symbol[s["symbol"]]["dollars"] += s["slip_dollars"]

    print(f"BE-slip fills (30d): {len(slips)}")
    print(f"Total leak: ${total_leak:.2f}")
    print()
    print("By symbol:")
    for sym, d in sorted(by_symbol.items()):
        print(f"  {sym}: {d['count']} fills, ${d['dollars']:.2f}")
    print()
    print("Slippage distribution (dollars):")
    if slips:
        amounts = sorted(s["slip_dollars"] for s in slips)
        print(f"  min: ${amounts[0]:.2f}")
        print(f"  max: ${amounts[-1]:.2f}")
        print(f"  median: ${amounts[len(amounts)//2]:.2f}")
        print(f"  mean: ${sum(amounts)/len(amounts):.2f}")
    print()
    print("Detail:")
    for s in slips:
        print(json.dumps(s))


if __name__ == "__main__":
    main()
