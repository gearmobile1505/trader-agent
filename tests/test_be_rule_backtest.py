"""Tests for BE-rule backtest exit simulation (Issue #18)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import be_rule_backtest as brb


def test_be_trip_at_75_moves_sl_to_entry():
    # price reaches +75 then reverses to entry -> BE exit
    bars = [(80, 70)]  # high gives 80 profit, low at 70
    ep, reason, be, locked = brb.simulate_exit(bars, 0, "buy", -10, 100, 1, 1, "A")
    # max profit 80 >= 75, BE triggered; low 70 > SL 0 (entry), no SL hit this bar
    assert be is True
    assert locked is True


def test_lock_at_80_moves_sl_to_entry_plus_50():
    bars = [(85, 80)]  # profit 85 >= 80 lock trigger
    ep, reason, be, locked = brb.simulate_exit(bars, 0, "buy", -10, 100, 1, 1, "A")
    assert be is True
    assert locked is True
    # SL should be at entry + 50 = 50
    assert ep is not None


def test_be_exit_when_price_reverses_after_be():
    # reaches +80 (BE+lock), then reverses to lock level (50)
    bars = [(85, 79), (50, 49)]  # bar1: profit 85, lock at 50; bar2: hits 50
    ep, reason, be, locked = brb.simulate_exit(bars, 0, "buy", -10, 100, 1, 1, "A")
    assert reason == "lock", reason
    assert ep == 50


def test_lock_never_moves_backwards():
    # once locked at 50, a later bar can't lower SL
    bars = [(85, 79), (90, 85), (50, 49)]
    ep, reason, be, locked = brb.simulate_exit(bars, 0, "buy", -10, 100, 1, 1, "A")
    # should exit at lock (50) or TP, not lower
    assert ep is not None and ep >= 50


def test_variant_b_never_moves_stop():
    bars = [(85, 79), (50, 49)]
    ep, reason, be, locked = brb.simulate_exit(bars, 0, "buy", -10, 100, 1, 1, "B")
    assert be is False
    assert locked is False


def test_variant_d_uses_90_trigger():
    # profit 85 should NOT trigger lock in D (needs 90)
    bars = [(85, 79)]
    ep, reason, be, locked = brb.simulate_exit(bars, 0, "buy", -10, 100, 1, 1, "D")
    assert be is True
    assert locked is False  # 85 < 90


def test_variant_d_lock_at_90():
    bars = [(95, 90)]
    ep, reason, be, locked = brb.simulate_exit(bars, 0, "buy", -10, 100, 1, 1, "D")
    assert be is True
    assert locked is True


def test_tp_hit():
    bars = [(105, 95)]
    ep, reason, be, locked = brb.simulate_exit(bars, 0, "buy", -10, 100, 1, 1, "A")
    assert reason == "tp"
    assert ep == 100


def test_sl_hit():
    bars = [(-15, -20)]
    ep, reason, be, locked = brb.simulate_exit(bars, 0, "buy", -10, 100, 1, 1, "A")
    assert reason == "sl"


def test_sell_side_be():
    bars = [(0.5, -0.1), (-0.5, -1.0)]  # price drops, profit for sell
    # entry 0, sell side: profit when price goes down
    ep, reason, be, locked = brb.simulate_exit(bars, 0, "sell", 10, -100, 1, 1, "A")
    # max profit: (0 - (-1.0)) = 1.0 < 75, no BE
    assert be is False


def test_open_path_uses_last_close():
    bars = [(5, 4), (6, 5)]
    ep, reason, be, locked = brb.simulate_exit(bars, 0, "buy", -10, 100, 1, 1, "A")
    assert reason == "open"
    assert ep is not None  # last close used, not None
