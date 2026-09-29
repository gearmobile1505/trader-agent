import pandas as pd

from scripts.daily_pnl import summarize_position_group


def test_summarize_long_position_uses_matched_qty_and_direction():
    df = pd.DataFrame([
        {"positionId": 101, "side": "buy", "qty": 1.0, "price": 100.0, "time": pd.Timestamp("2026-09-15T09:30:00Z")},
        {"positionId": 101, "side": "sell", "qty": 1.0, "price": 105.0, "time": pd.Timestamp("2026-09-15T10:30:00Z")},
    ])

    result = summarize_position_group(df)

    assert result["direction"] == "long"
    assert result["status"] == "CLOSED"
    assert result["qty"] == 1.0
    assert result["pnl"] == 5.0
    assert result["entry"] == 100.0
    assert result["exit"] == 105.0


def test_summarize_short_position_uses_matched_qty_and_direction():
    df = pd.DataFrame([
        {"positionId": 202, "side": "sell", "qty": 2.0, "price": 110.0, "time": pd.Timestamp("2026-09-15T09:30:00Z")},
        {"positionId": 202, "side": "buy", "qty": 2.0, "price": 105.0, "time": pd.Timestamp("2026-09-15T10:30:00Z")},
    ])

    result = summarize_position_group(df)

    assert result["direction"] == "short"
    assert result["status"] == "CLOSED"
    assert result["qty"] == 2.0
    assert result["pnl"] == 10.0
    assert result["entry"] == 110.0
    assert result["exit"] == 105.0
