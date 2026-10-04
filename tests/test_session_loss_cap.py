import importlib
from types import SimpleNamespace

import pandas as pd
import pytest


@pytest.fixture
def bot(monkeypatch):
    monkeypatch.setenv("AI_TIMEOUT_SECONDS", "15")
    monkeypatch.setenv("AI_MAX_PREDICT", "96")
    monkeypatch.setenv("AI_MAX_CONCURRENCY", "2")
    monkeypatch.setenv("AI_QUEUE_TIMEOUT_SECONDS", "30")
    import tradelocker
    monkeypatch.setattr(tradelocker, "TLAPI", lambda **kwargs: SimpleNamespace())
    module = importlib.import_module("scripts.main_cfd_5m")
    monkeypatch.setattr(module, "get_instrument_id", lambda symbol: {
        name: index + 1 for index, name in enumerate(module.TOP_SYMBOLS)
    }[symbol])
    monkeypatch.setattr(module, "get_point_value", lambda symbol: 100.0)
    module._ALL_FILLED_ORDERS_CACHE = None
    module._ALL_FILLED_ORDERS_FETCHED_AT = 0.0
    return module


def _orders(robot_symbol_module):
    """Two full-SL stop-outs on UKOIL.R joined to their entry fills by positionId."""
    instrument_id = robot_symbol_module.get_instrument_id("UKOIL.R")
    return pd.DataFrame([
        {"id": 1, "positionId": 101, "tradableInstrumentId": instrument_id,
         "type": "market", "status": "Filled", "side": "buy", "qty": 1.0,
         "filledQty": 1.0, "avgPrice": 100.0, "price": 100.0,
         "createdDate": 1_790_000_000_000, "lastModified": 1_790_000_000_000},
        {"id": 2, "positionId": 101, "tradableInstrumentId": instrument_id,
         "type": "stop", "status": "Filled", "side": "sell", "qty": 1.0,
         "avgPrice": 99.0, "price": 99.0,
         "createdDate": 1_790_000_000_000, "lastModified": 1_790_000_100_000},
        {"id": 3, "positionId": 102, "tradableInstrumentId": instrument_id,
         "type": "market", "status": "Filled", "side": "buy", "qty": 1.0,
         "filledQty": 1.0, "avgPrice": 100.0, "price": 100.0,
         "createdDate": 1_790_000_000_000, "lastModified": 1_790_000_000_000},
        {"id": 4, "positionId": 102, "tradableInstrumentId": instrument_id,
         "type": "stop", "status": "Filled", "side": "sell", "qty": 1.0,
         "avgPrice": 99.0, "price": 99.0,
         "createdDate": 1_790_000_000_000, "lastModified": 1_790_000_200_000},
    ])


def test_two_full_losses_in_session_block_entry(bot, monkeypatch):
    monkeypatch.setattr(bot, "_cached_all_filled_orders", lambda: _orders(bot))
    monkeypatch.setattr(bot, "current_session_start_et", lambda cfg: pd.Timestamp(1_790_000_000, unit="s", tz="UTC").tz_convert("US/Eastern"))
    blocked, detail = bot.session_loss_cap_blocks_entry("UKOIL.R")
    assert blocked
    assert "full-SL stop-outs" in detail


def test_breakeven_scratches_never_count(bot, monkeypatch):
    orders = _orders(bot)
    # Move both stop fills to breakeven (fill at entry price).
    orders.loc[orders["type"] == "stop", "avgPrice"] = 100.0
    monkeypatch.setattr(bot, "_cached_all_filled_orders", lambda: orders)
    monkeypatch.setattr(bot, "current_session_start_et", lambda cfg: pd.Timestamp(1_790_000_000, unit="s", tz="UTC").tz_convert("US/Eastern"))
    blocked, _ = bot.session_loss_cap_blocks_entry("UKOIL.R")
    assert not blocked


def test_cap_resets_at_session_boundary(bot, monkeypatch):
    orders = _orders(bot)
    monkeypatch.setattr(bot, "_cached_all_filled_orders", lambda: orders)
    # Session starts after both stop fills; nothing counts.
    monkeypatch.setattr(bot, "current_session_start_et", lambda cfg: pd.Timestamp(1_790_000_300, unit="s", tz="UTC").tz_convert("US/Eastern"))
    blocked, _ = bot.session_loss_cap_blocks_entry("UKOIL.R")
    assert not blocked


def test_history_fetch_failure_fails_open_with_loud_log(bot, monkeypatch, capsys):
    monkeypatch.setattr(bot, "current_session_start_et", lambda cfg: pd.Timestamp(1_790_000_000, unit="s", tz="UTC").tz_convert("US/Eastern"))
    def boom():
        raise RuntimeError("order history unavailable")
    monkeypatch.setattr(bot, "_cached_all_filled_orders", boom)
    blocked, detail = bot.session_loss_cap_blocks_entry("UKOIL.R")
    assert not blocked
    assert detail is None
    out = capsys.readouterr().out
    assert "[SESSION LOSS CAP]" in out
    assert "failing open" in out


def test_gate_disabled_never_blocks(bot, monkeypatch):
    monkeypatch.setattr(bot, "ENABLE_SESSION_LOSS_CAP", False)
    monkeypatch.setattr(bot, "_cached_all_filled_orders", lambda: _orders(bot))
    blocked, detail = bot.session_loss_cap_blocks_entry("UKOIL.R")
    assert not blocked
    assert detail is None
