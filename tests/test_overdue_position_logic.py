from datetime import datetime, timezone

import pandas as pd
import pytest

from scripts import main_cfd_5m as m
from scripts.main_cfd_5m import (
    MAX_HOLD_TIME_MINUTES,
    audit_recent_stop_fills,
    classify_stop_fill_slippage,
    parse_position_open_time,
)


def _now_ms():
    return int(datetime.now(timezone.utc).timestamp() * 1000)


# Row shape captured verbatim from the live TradeLocker positions endpoint.
# The column is openDate in epoch milliseconds. An earlier version of the
# overdue check read "openTime", which the API never returns, so the max-hold
# rule silently matched nothing and never closed a position.
def test_parse_open_date_from_tradelocker_row():
    opened = _now_ms() - (60 * 60 * 1000)
    row = {
        "id": 216172782117583349,
        "tradableInstrumentId": 13674,
        "routeId": 1482858,
        "side": "sell",
        "qty": 0.09,
        "avgPrice": 1222.19,
        "stopLossId": 216172782135434833,
        "takeProfitId": 216172782135434835,
        "openDate": opened,
        "unrealizedPl": -540.9,
        "strategyId": "key-undefined",
    }

    parsed = parse_position_open_time(pd.Series(row))

    assert parsed is not None, "openDate must be parsed, not skipped"
    assert parsed.tzinfo is not None
    age_minutes = (datetime.now(timezone.utc) - parsed).total_seconds() / 60
    assert 59 < age_minutes < 61


def test_overdue_row_would_trigger_max_hold():
    """The historical loss: held 4h36m against a 45m limit, never closed."""
    opened = _now_ms() - (int(4.61 * 60) * 60 * 1000)
    row = {"id": 1, "openDate": opened, "tradableInstrumentId": 13674, "qty": 0.09}

    parsed = parse_position_open_time(pd.Series(row))
    age_minutes = (datetime.now(timezone.utc) - parsed).total_seconds() / 60

    assert age_minutes > MAX_HOLD_TIME_MINUTES, "this row must be overdue"


def test_missing_open_date_returns_none():
    row = {"id": 1, "tradableInstrumentId": 13674, "qty": 0.09}
    assert parse_position_open_time(pd.Series(row)) is None


def test_nan_open_date_falls_back_and_returns_none():
    row = {"id": 1, "openDate": float("nan"), "qty": 0.09}
    assert parse_position_open_time(pd.Series(row)) is None


def test_legacy_open_time_key_still_supported():
    opened = _now_ms() - (30 * 60 * 1000)
    row = {"id": 1, "openTime": opened}
    parsed = parse_position_open_time(pd.Series(row))
    assert parsed is not None


def test_epoch_seconds_not_mistaken_for_milliseconds():
    seconds = int(datetime.now(timezone.utc).timestamp()) - 600
    row = {"id": 1, "openDate": seconds}
    parsed = parse_position_open_time(pd.Series(row))
    assert parsed is not None
    age = (datetime.now(timezone.utc) - parsed).total_seconds() / 60
    assert 9 < age < 11, "seconds-scale value must not be divided by 1000 twice"


def test_iso_string_open_date_supported():
    iso = (datetime.now(timezone.utc) - pd.Timedelta(hours=2)).isoformat()
    parsed = parse_position_open_time(pd.Series({"id": 1, "openDate": iso}))
    assert parsed is not None
    age = (datetime.now(timezone.utc) - parsed).total_seconds() / 60
    assert 118 < age < 122


def test_garbage_string_returns_none_rather_than_raising():
    assert parse_position_open_time(pd.Series({"id": 1, "openDate": "unknown"})) is None
    assert parse_position_open_time(pd.Series({"id": 1, "openDate": "not-a-date"})) is None


def _positions_df(age_minutes):
    """A DataFrame shaped exactly like the broker's positions response."""
    return pd.DataFrame([{
        "id": 216172782117583349,
        "tradableInstrumentId": 13674,
        "routeId": 1482858,
        "side": "sell",
        "qty": 0.09,
        "avgPrice": 1222.19,
        "stopLossId": 216172782135434833,
        "takeProfitId": 216172782135434835,
        "openDate": _now_ms() - int(age_minutes * 60 * 1000),
        "unrealizedPl": -540.9,
        "strategyId": "key-undefined",
    }])


@pytest.mark.asyncio
async def test_overdue_position_is_actually_closed(monkeypatch):
    """Regression: the max-hold rule never fired because it read a column the
    API does not return. This drives the real async function end to end."""
    modified, closed = [], []

    class FakeTL:
        def modify_position(self, position_id, params):
            modified.append((position_id, params))
            return True

        def close_position(self, position_id=None, **kwargs):
            closed.append(position_id)
            return True

    monkeypatch.setattr(m, "tl", FakeTL())
    monkeypatch.setattr(m, "resolve_symbol_for_instrument", lambda i: "XPDUSD.R")

    # 4h36m old, exactly the historical loss, against a 45m limit.
    await m.check_and_close_overdue_positions(_positions_df(4.61 * 60))

    assert closed == [216172782117583349], "overdue position must be closed"
    assert modified, "breakeven stop must be pinned before closing"
    assert modified[0][1]["stopLoss"] == 1222.19
    assert m._POSITIONS_CACHE["expires_at"] == 0.0, "cache must be invalidated"


@pytest.mark.asyncio
async def test_fresh_position_is_left_alone(monkeypatch):
    closed = []

    class FakeTL:
        def close_position(self, position_id=None, **kwargs):
            closed.append(position_id)
            return True

    monkeypatch.setattr(m, "tl", FakeTL())
    monkeypatch.setattr(m, "resolve_symbol_for_instrument", lambda i: "XPDUSD.R")

    await m.check_and_close_overdue_positions(_positions_df(5))

    assert closed == [], "a 5m-old position is well inside the 45m limit"


@pytest.mark.asyncio
async def test_unreadable_open_date_is_reported_not_silently_skipped(
    monkeypatch, capsys
):
    """Fail loudly: a silent skip is what hid this for weeks."""
    closed = []

    class FakeTL:
        def close_position(self, position_id=None, **kwargs):
            closed.append(position_id)
            return True

    monkeypatch.setattr(m, "tl", FakeTL())

    df = pd.DataFrame([{
        "id": 42, "tradableInstrumentId": 13674, "side": "sell",
        "qty": 0.09, "avgPrice": 1222.19, "unrealizedPl": -1.0,
    }])

    await m.check_and_close_overdue_positions(df)

    out = capsys.readouterr().out
    assert "cannot determine open time" in out, "must warn about the skipped row"
    assert closed == []


# --- Stop-fill slippage -------------------------------------------------
# Real order from the loss: buy stop @1231.81 filled @1282.29, entry 1222.19.
def test_classify_flags_real_gapped_stop_fill():
    result = classify_stop_fill_slippage(entry=1222.19, stop=1231.81, fill=1282.29)

    assert result is not None, "a 5.2x overshoot must be flagged"
    assert round(result["overshoot"], 2) == 50.48
    assert result["multiple"] > 2.0


def test_classify_ignores_ordinary_stop_fill():
    # Real UKOIL stop from ordersHistory: buy stop @99.090 filled @99.094.
    assert classify_stop_fill_slippage(entry=98.373, stop=99.090, fill=99.094) is None
    assert classify_stop_fill_slippage(entry=1222.19, stop=1231.81, fill=1231.90) is None


def test_classify_ignores_degenerate_input():
    assert classify_stop_fill_slippage(entry=100.0, stop=100.0, fill=105.0) is None
    assert classify_stop_fill_slippage(entry=None, stop=1.0, fill=2.0) is None


def test_audit_flags_gapped_stop_and_ignores_others(monkeypatch, capsys):
    monkeypatch.setattr(m, "resolve_symbol_for_instrument", lambda i: "XPDUSD.R")

    # Row shapes taken verbatim from the live ordersHistory response.
    orders = pd.DataFrame([
        {"id": 216172782135434833, "tradableInstrumentId": 13674, "type": "stop",
         "status": "Filled", "avgPrice": 1282.290, "price": 1231.810},
        {"id": 216172782135434832, "tradableInstrumentId": 13674, "type": "market",
         "status": "Filled", "avgPrice": 1222.190, "price": 1222.190},
        {"id": 216172782135434835, "tradableInstrumentId": 13674, "type": "limit",
         "status": "Cancelled", "avgPrice": 0.0, "price": 1203.560},
        {"id": 999, "tradableInstrumentId": 13119, "type": "stop",
         "status": "Filled", "avgPrice": 99.090, "price": 99.090},
    ])

    flagged = audit_recent_stop_fills(orders)

    assert flagged == 1, "only the gapped XPDUSD stop may be flagged"
    out = capsys.readouterr().out
    assert "216172782135434833" in out
    assert "3.94% past the trigger" in out


def test_audit_on_empty_history_is_noop():
    assert audit_recent_stop_fills(pd.DataFrame()) == 0
    assert audit_recent_stop_fills(None) == 0
