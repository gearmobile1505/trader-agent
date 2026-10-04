"""Position sizing invariants.

The bug these lock down rejected trades that were safe to take. max_lot is a
ceiling on size, not a target: the placed quantity is
min(TARGET_DOLLAR_RISK / risk_per_lot, max_lot), so realized risk is
TARGET_DOLLAR_RISK when the size fits and strictly less when it does not.
"""


from scripts import main_cfd_5m as m
from scripts.main_cfd_5m import (
    TARGET_DOLLAR_RISK,
    TOP_SYMBOLS,
    TP_CONFIG,
    calculate_position_size,
)


def test_every_symbol_tp1_is_100_dollars():
    """PR #8: every configured take-profit target is $100; no pair left at $125."""
    assert TP_CONFIG, "TP_CONFIG must cover every tradable symbol"
    for symbol, cfg in TP_CONFIG.items():
        assert cfg["tp1_dollars"] == 100, f"{symbol} tp1 is {cfg['tp1_dollars']}, expected 100"
    # Every approved symbol must have a TP entry; a missing key falls back to
    # the generic default in the order path, which should also be $100.
    for symbol in TOP_SYMBOLS:
        assert symbol in TP_CONFIG, f"{symbol} missing from TP_CONFIG"


def test_usdjpy_max_lot_raised_to_point_four():
    assert TOP_SYMBOLS["USDJPY.R"]["max_lot"] == 0.40


def test_sizing_never_exceeds_target_risk():
    """Across the tradeable SL range, placed risk stays at or under $100.

    Very wide stops are excluded: below min_lot the broker cannot express a
    smaller position, so risk floors at min_lot * risk_per_lot. That is a
    pre-existing property bounded separately by validate_sl_distance against
    the $200 MAX_SL_OVERSHOOT cap, not something max_lot governs.
    """
    pv = 635.6
    for sl_distance in (0.47, 0.5, 0.6, 0.8, 1.0, 2.0, 5.0):
        qty = calculate_position_size("USDJPY", 157.37, 157.37 + sl_distance)
        assert qty is not None, f"SL {sl_distance} should be tradeable, not rejected"
        assert qty <= TOP_SYMBOLS["USDJPY.R"]["max_lot"] + 1e-9
        realized = qty * sl_distance * pv
        # Rounding to 0.01 lots can add a fraction of a point of risk.
        assert realized <= TARGET_DOLLAR_RISK * 1.02, (
            f"SL {sl_distance}: risk ${realized:.2f} exceeds target"
        )


def test_the_rejected_alert_now_settles_at_target_risk():
    """Replay of the 2026-09-29 01:00:23 USDJPY sell that was rejected.

    Live price 157.371, ATR(10)*3 gave a 0.1665 stop, the MIN_SL guard widened
    it to 0.47, and sizing needed 0.335 lots against the old 0.30 max_lot. The
    trade was rejected as "Position size exceeds max_lot".
    """
    sl_distance = 0.47
    qty = calculate_position_size("USDJPY", 157.371, 157.371 + sl_distance)

    assert qty is not None, "this trade must no longer be rejected"
    # 100 / (0.47 * 635.6) = 0.3347, rounded to 0.33.
    assert qty == 0.33, f"expected 0.33 lots, got {qty}"

    realized = qty * sl_distance * 635.6
    assert realized <= TARGET_DOLLAR_RISK * 1.02, (
        f"risk ${realized:.2f} must not exceed the $100 target"
    )


def test_max_lot_is_a_ceiling_not_a_target():
    """max_lot never inflates risk. It only caps size when the stop is tight.

    A size at the cap implies risk_per_lot <= TARGET/max_lot, so max_lot lots
    risk at most TARGET. This is why raising max_lot to 0.40 needs no change to
    TARGET_DOLLAR_RISK or MAX_DAILY_LOSS.
    """
    pv = 635.6
    max_lot = TOP_SYMBOLS["USDJPY.R"]["max_lot"]
    # The real invariant: for any tradeable SL, placed risk <= target.
    for sl_distance in (0.47, 1.0, 3.0, 5.0):
        qty = calculate_position_size("USDJPY", 157.0, 157.0 + sl_distance)
        assert qty is not None
        assert qty <= max_lot + 1e-9
        assert qty * sl_distance * pv <= TARGET_DOLLAR_RISK * 1.02


def test_stops_tighter_than_the_cap_are_still_rejected():
    """Documents the known remaining gap, which the max_lot bump does not fix.

    A tighter stop needs MORE lots. Below the SL distance where the $100 size
    fits under 0.40 (~0.393 pts on a 157 handle) the trade is still rejected
    outright rather than clamped to a smaller, safe size. Raising max_lot only
    moves this threshold down; it does not fix the inverted rejection.
    """
    pv = 635.6
    threshold_sl = TARGET_DOLLAR_RISK / (TOP_SYMBOLS["USDJPY.R"]["max_lot"] * pv)
    # Just under the threshold the size fits and trades normally.
    assert calculate_position_size("USDJPY", 157.0, 157.0 + threshold_sl * 1.02) is not None
    # Materially tighter than the cap it is rejected, not clamped.
    assert calculate_position_size("USDJPY", 157.0, 157.0 + threshold_sl * 0.8) is None


def test_wider_stops_produce_smaller_size():
    """Monotonicity: a wider stop must never produce a larger position."""
    prev = None
    for sl_distance in (0.1, 0.2, 0.47, 1.0, 2.0, 5.0, 10.0):
        qty = calculate_position_size("USDJPY", 157.0, 157.0 + sl_distance)
        if prev is not None:
            assert qty <= prev, f"size grew at SL {sl_distance}: {prev} -> {qty}"
        prev = qty


def test_zero_stop_distance_returns_min_lot():
    qty = calculate_position_size("USDJPY", 157.0, 157.0)
    assert qty == TOP_SYMBOLS["USDJPY.R"]["min_lot"]


def test_gbpjpy_unchanged_max_lot():
    """Only USDJPY was in scope for this change."""
    assert TOP_SYMBOLS["GBPJPY.R"]["max_lot"] == 0.30
