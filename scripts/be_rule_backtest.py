"""BE-rule backtest (Issue #18): did the BE rules help or hurt?

MEASURE ONLY. Reuses loss_study.py reconstruction. Four variants:
  A = current rules (+$75 BE, +$80 -> lock +$50)
  B = no BE (original SL/TP only)
  C = BE only (+$75 -> entry, no lock)
  D = pre-Oct-7 (+$75 BE, +$90 -> lock +$50)
"""
import os, sys, json, math
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import loss_study as ls

# BE/lock thresholds in dollars of unrealized profit
VARIANTS = {
    "A": {"be_trigger": 75, "lock_trigger": 80, "lock_amount": 50},
    "B": {"be_trigger": None, "lock_trigger": None, "lock_amount": 0},
    "C": {"be_trigger": 75, "lock_trigger": None, "lock_amount": 0},
    "D": {"be_trigger": 75, "lock_trigger": 90, "lock_amount": 50},
}


def simulate_exit(path_bars, entry_price, entry_side, sl_price, tp_price,
                  qty, point_val, variant):
    """Walk price path; return (exit_price, exit_reason, be_triggered, locked).

    path_bars: list of (high, low) tuples in chronological order.
    exit_reason in {tp, sl, be, lock, flatten, open}.
    """
    v = VARIANTS[variant]
    be_trigger = v["be_trigger"]
    lock_trigger = v["lock_trigger"]
    lock_amount = v["lock_amount"]

    is_buy = entry_side == "buy"
    current_sl = sl_price
    be_triggered = False
    locked = False
    max_profit = 0.0

    for hi, lo in path_bars:
        # unrealized profit in dollars at this bar's extreme
        if is_buy:
            best = (hi - entry_price) * qty * point_val
            worst = (lo - entry_price) * qty * point_val
        else:
            best = (entry_price - lo) * qty * point_val
            worst = (entry_price - hi) * qty * point_val

        if best > max_profit:
            max_profit = best

        # check TP
        if is_buy and hi >= tp_price:
            return tp_price, "tp", be_triggered, locked
        if not is_buy and lo <= tp_price:
            return tp_price, "tp", be_triggered, locked

        # check SL
        if is_buy and lo <= current_sl:
            if be_triggered and not locked:
                return current_sl, "be", be_triggered, locked
            if locked:
                return current_sl, "lock", be_triggered, locked
            return current_sl, "sl", be_triggered, locked
        if not is_buy and hi >= current_sl:
            if be_triggered and not locked:
                return current_sl, "be", be_triggered, locked
            if locked:
                return current_sl, "lock", be_triggered, locked
            return current_sl, "sl", be_triggered, locked

        # BE trigger
        if be_trigger is not None and max_profit >= be_trigger and not be_triggered:
            current_sl = entry_price
            be_triggered = True

        # lock trigger
        if lock_trigger is not None and max_profit >= lock_trigger and not locked and be_triggered:
            # move SL to entry + lock_amount worth of price
            lock_price_move = lock_amount / (qty * point_val) if (qty * point_val) else 0
            if is_buy:
                current_sl = entry_price + lock_price_move
            else:
                current_sl = entry_price - lock_price_move
            locked = True

    # if path exhausted without exit, use last close (approximate)
    if path_bars:
        last_hi, last_lo = path_bars[-1]
        last_close = (last_hi + last_lo) / 2.0
        return last_close, "open", be_triggered, locked
    return None, "open", be_triggered, locked


def pnl_for(exit_price, entry_price, entry_side, qty, point_val):
    if exit_price is None:
        return 0.0
    if entry_side == "buy":
        return (exit_price - entry_price) * qty * point_val
    return (entry_price - exit_price) * qty * point_val


# --- tests for simulate_exit ---
def _test_simulate():
    # basic: TP hit
    bars = [(1.0, 0.5), (2.0, 1.5)]
    ep, reason, be, locked = simulate_exit(bars, 0, "buy", -1, 2, 1, 1, "A")
    assert reason == "tp", reason
    # BE trigger moves SL to entry
    bars = [(0.5, -0.1), (0.1, -0.5)]  # profit 0.5 then reversal
    ep, reason, be, locked = simulate_exit(bars, 0, "buy", -1, 2, 1, 1, "A")
    # max profit 0.5 < 75, no BE trigger, path exhausted -> open
    assert reason == "open", reason
    # variant B never moves SL
    bars = [(80, 70), (75, 60)]
    ep, reason, be, locked = simulate_exit(bars, 0, "buy", -10, 100, 1, 1, "B")
    assert not be and not locked
    print("simulate_exit tests passed")


if __name__ == "__main__":
    _test_simulate()
    print("BE backtest script ready (run on server for full replay)")
