import importlib
import os
import sys

import pandas as pd
import pytest


class StubTradeLocker:
    def __init__(self):
        self.bid = 100.0
        self.ask = 100.0
        self.orders = pd.DataFrame()

    def get_latest_bid_price(self, instrument_id):
        return self.bid

    def get_latest_asking_price(self, instrument_id):
        return self.ask

    def get_all_orders(self, **kwargs):
        return self.orders


@pytest.fixture
def bot(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_TIMEOUT_SECONDS", "15")
    monkeypatch.setenv("AI_MAX_PREDICT", "96")
    monkeypatch.setenv("AI_MAX_CONCURRENCY", "2")
    monkeypatch.setenv("AI_QUEUE_TIMEOUT_SECONDS", "30")

    tradelocker = importlib.import_module("tradelocker")
    broker = StubTradeLocker()
    monkeypatch.setattr(tradelocker, "TLAPI", lambda **kwargs: broker)
    sys.modules.pop("scripts.main_cfd_5m", None)
    module = importlib.import_module("scripts.main_cfd_5m")
    monkeypatch.setattr(module, "tl", broker)
    monkeypatch.setattr(module, "STATE_FILE", str(tmp_path / "bot_state.json"))
    monkeypatch.setattr(module, "ALERT_LOG", str(tmp_path / "alerts.jsonl"))
    monkeypatch.setattr(module, "get_instrument_id", lambda symbol: {
        name: index + 1 for index, name in enumerate(module.TOP_SYMBOLS)
    }[symbol])
    return module, broker


def test_minimum_lot_risk_cap_rejects_us30_152_48(bot):
    module, _ = bot

    valid, reason = module.validate_sl_distance("US30.R", "sell", 51100.0, 50947.52)

    assert not valid
    assert "min lot risk=$152.48" in reason
    assert module.MAX_SL_OVERSHOOT_PCT == 25


def test_minimum_lot_risk_cap_keeps_xpd_117_60_eligible(bot):
    module, _ = bot

    valid, reason = module.validate_sl_distance("XPDUSD.R", "buy", 1200.0, 1188.24)

    assert valid, reason


def test_non_xpd_spread_over_symbol_limit_is_rejected(bot):
    module, broker = bot
    broker.bid = 100.0
    broker.ask = 102.01

    valid, reason = module.validate_spread("US30.R")

    assert not valid
    assert "exceeds max 2.0" in reason


@pytest.mark.parametrize("failure", ["zero", "exception"])
def test_non_xpd_unavailable_spread_fails_closed(bot, monkeypatch, failure):
    module, broker = bot
    if failure == "zero":
        broker.bid = 0.0
    else:
        monkeypatch.setattr(
            broker,
            "get_latest_bid_price",
            lambda instrument_id: (_ for _ in ()).throw(RuntimeError("quote timeout")),
        )

    valid, reason = module.validate_spread("US30.R")

    assert not valid
    assert "unavailable" in reason


def test_non_xpd_none_quotes_fail_closed(bot):
    module, broker = bot
    broker.bid = None

    valid, reason = module.validate_spread("US30.R")

    assert not valid
    assert "unavailable" in reason


def test_xpd_specific_slippage_guard_unchanged(bot):
    module, broker = bot
    broker.bid = 1200.0
    broker.ask = 1201.0

    valid, reason = module.validate_slippage_guard("XPDUSD.R", "buy", 1200.0, 1190.0)

    assert valid, reason
    assert module.XPDUSD_MAX_SPREAD_PCT == 0.005


def test_exit_slippage_tripwire_records_and_logs_stop_fill(bot, monkeypatch, capsys):
    module, broker = bot
    monkeypatch.setattr(module, "resolve_symbol_for_instrument", lambda instrument_id: "US30.R")
    cooldowns = []
    entries = []
    monkeypatch.setattr(module, "record_stop_out", lambda symbol, exit_epoch=None: cooldowns.append(symbol))
    monkeypatch.setattr(module, "log_alert", lambda alert, result: entries.append((alert, result)))
    broker.orders = pd.DataFrame([{
        "id": 789,
        "positionId": 456,
        "tradableInstrumentId": 1,
        "type": "stop",
        "status": "Filled",
        "price": 51100.0,
        "avgPrice": 51332.56,
        "lastModified": 1790950000000,
    }])

    module.record_stop_outs_from_history({
        456: {
            "instrument_id": 1,
            "entry_price": 51000.0,
            "qty": 0.01,
            "side": "sell",
        },
        457: {
            "instrument_id": 1,
            "entry_price": 51300.0,
            "qty": 0.5,
            "side": "buy",
        },
    })

    assert cooldowns == ["US30.R"]
    assert len(entries) == 1
    assert entries[0][0]["event"] == "stop_fill"
    assert entries[0][1]["gate"] == "exit_slippage"
    assert "risk_multiple" in entries[0][1]["triggered_by"]
    assert entries[0][1]["implied_loss"] == pytest.approx(332.56)
    assert "[EXIT SLIPPAGE]" in capsys.readouterr().out
