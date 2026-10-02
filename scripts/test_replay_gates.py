#!/usr/bin/env python3
"""
Pytest tests for entry vetting gates replay.

Run: pytest scripts/test_replay_gates.py -v
"""

import pytest
import json
import importlib
import os
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

# Import replay functions
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.replay_gates import (
    parse_alert_line,
    check_trend_gate,
    check_one_per_symbol_gate,
    check_cooldown_gate,
    check_daily_loss_gate,
    check_alert_freshness,
    replay_log,
)


class TestGateFunctions:
    """Unit tests for individual gate functions."""
    
    def test_trend_gate_sell_into_buy(self):
        """SELL with bullish tech should be rejected."""
        ok, reason = check_trend_gate("sell", "Strong Buy")
        assert not ok
        assert "SELL rejected" in reason
    
    def test_trend_gate_buy_into_sell(self):
        """BUY with bearish tech should be rejected."""
        ok, reason = check_trend_gate("buy", "Strong Sell")
        assert not ok
        assert "BUY rejected" in reason
    
    def test_trend_gate_sell_neutral_passes(self):
        """SELL with neutral tech should pass."""
        ok, reason = check_trend_gate("sell", "Neutral")
        assert ok
        assert reason == "OK"
    
    def test_trend_gate_buy_neutral_passes(self):
        """BUY with neutral tech should pass."""
        ok, reason = check_trend_gate("buy", "Neutral")
        assert ok
        assert reason == "OK"
    
    def test_trend_gate_case_insensitive(self):
        """Tech summary matching should be case-insensitive."""
        ok, _ = check_trend_gate("SELL", "strong buy")
        assert not ok
        ok, _ = check_trend_gate("Buy", "SELL")
        assert not ok
    
    def test_one_per_symbol_same_direction_blocked(self):
        """Same direction on same symbol should be blocked."""
        open_positions = {"XAUUSD.R": {"side": "buy", "entry_time": datetime.now()}}
        alert = {
            "result": {"tl_symbol": "XAUUSD.R"},
            "alert": {"action": "buy"}
        }
        ok, reason = check_one_per_symbol_gate(alert, open_positions)
        assert not ok
        assert "already open" in reason
    
    def test_one_per_symbol_opposite_direction_passes(self):
        """Opposite direction on same symbol should pass (handled by opposing gate)."""
        open_positions = {"XAUUSD.R": {"side": "buy", "entry_time": datetime.now()}}
        alert = {
            "result": {"tl_symbol": "XAUUSD.R"},
            "alert": {"action": "sell"}
        }
        ok, reason = check_one_per_symbol_gate(alert, open_positions)
        assert ok
    
    def test_one_per_symbol_different_symbol_passes(self):
        """Different symbol should pass."""
        open_positions = {"XAUUSD.R": {"side": "buy", "entry_time": datetime.now()}}
        alert = {
            "result": {"tl_symbol": "XPDUSD.R"},
            "alert": {"action": "buy"}
        }
        ok, reason = check_one_per_symbol_gate(alert, open_positions)
        assert ok
    
    def test_cooldown_gate_active(self):
        """Symbol in cooldown should be blocked."""
        stop_outs = {"XAUUSD.R": datetime.now()}
        alert = {"result": {"tl_symbol": "XAUUSD.R"}}
        ok, reason = check_cooldown_gate(alert, stop_outs)
        assert not ok
        assert "cooldown" in reason.lower()
    
    def test_cooldown_gate_expired(self):
        """Symbol not in cooldown should pass."""
        stop_outs = {}
        alert = {"result": {"tl_symbol": "XAUUSD.R"}}
        ok, reason = check_cooldown_gate(alert, stop_outs)
        assert ok
    
    def test_daily_loss_gate_hit(self):
        """Daily loss <= -400 should block."""
        ok, reason = check_daily_loss_gate({}, -450.0)
        assert not ok
        assert "Daily loss" in reason
    
    def test_daily_loss_gate_not_hit(self):
        """Daily loss > -400 should pass."""
        ok, reason = check_daily_loss_gate({}, -300.0)
        assert ok
    
    def test_alert_freshness_missing_bar_time(self):
        """Missing bar_time should be rejected."""
        alert = {"alert": {"action": "buy", "ticker": "XAUUSD"}}
        ok, reason = check_alert_freshness(alert)
        assert not ok
        assert "Missing bar_time" in reason
    
    def test_alert_freshness_valid(self):
        """Valid bar_time should pass."""
        now = datetime.now().isoformat()
        alert = {"alert": {"action": "buy", "ticker": "XAUUSD", "bar_time": now}}
        ok, reason = check_alert_freshness(alert)
        assert ok


class TestReplayIntegration:
    """Integration tests using sample log data."""
    
    def create_sample_log(self, tmp_path: Path) -> Path:
        """Create a sample alerts_log.jsonl for testing."""
        log_file = tmp_path / "test_alerts_log.jsonl"
        
        base_time = datetime.now()
        # Use timestamps that will definitely be within the cutoff window
        t1 = base_time - timedelta(hours=10)
        t2 = base_time - timedelta(hours=9)
        t3 = base_time - timedelta(hours=8)
        t4 = base_time - timedelta(hours=7, minutes=56)
        t5 = base_time - timedelta(days=1)
        
        entries = [
            # Trade 1: XAUUSD.R SELL, tech=Strong Buy (should be blocked by trend gate)
            {
                "timestamp": t1.isoformat() + "Z",
                "alert": {"action": "sell", "ticker": "XAUUSD", "tl_symbol": "XAUUSD.R"},
                "result": {
                    "status": "success",
                    "tl_symbol": "XAUUSD.R",
                    "technical_summary": "Strong Buy",
                    "net_pnl": -106.90
                }
            },
            # Trade 2: XAUUSD.R SELL again (same direction, should be blocked by one-per-symbol)
            {
                "timestamp": t2.isoformat() + "Z",
                "alert": {"action": "sell", "ticker": "XAUUSD", "tl_symbol": "XAUUSD.R"},
                "result": {
                    "status": "success",
                    "tl_symbol": "XAUUSD.R",
                    "technical_summary": "Neutral",
                    "net_pnl": -106.45
                }
            },
            # Trade 3: UKOIL.R BUY (normal trade)
            {
                "timestamp": t3.isoformat() + "Z",
                "alert": {"action": "buy", "ticker": "UKOIL", "tl_symbol": "UKOIL.R"},
                "result": {
                    "status": "success",
                    "tl_symbol": "UKOIL.R",
                    "technical_summary": "Buy",
                    "net_pnl": -116.48
                }
            },
            # Trade 4: UKOIL.R SELL shortly after (whipsaw, same symbol opposite direction)
            {
                "timestamp": t4.isoformat() + "Z",
                "alert": {"action": "sell", "ticker": "UKOIL", "tl_symbol": "UKOIL.R"},
                "result": {
                    "status": "success",
                    "tl_symbol": "UKOIL.R",
                    "technical_summary": "Sell",
                    "net_pnl": -100.75
                }
            },
            # Trade 5: XPDUSD.R with bad slippage
            {
                "timestamp": t5.isoformat() + "Z",
                "alert": {"action": "sell", "ticker": "XPDUSD", "tl_symbol": "XPDUSD.R"},
                "result": {
                    "status": "success",
                    "tl_symbol": "XPDUSD.R",
                    "technical_summary": "Neutral",
                    "net_pnl": -541.53
                }
            },
        ]
        
        with open(log_file, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
        
        return log_file
    
    def test_replay_sample_log(self, tmp_path):
        """Test replay with sample log."""
        log_file = self.create_sample_log(tmp_path)
        
        # This will run the replay and print results
        # We just verify it runs without error
        gates = replay_log(log_file, days=2)
        
        # Verify all gates ran
        assert "trend_gate" in gates
        assert "cooldown_gate" in gates
        assert "one_per_symbol" in gates
        assert "daily_loss" in gates
        assert "freshness" in gates
        
        # Trend gate should have blocked trade 1 (SELL into Strong Buy)
        assert gates["trend_gate"]["blocked"] >= 1
        
        # One-per-symbol should have blocked trade 2 (same direction XAUUSD.R)
        assert gates["one_per_symbol"]["blocked"] >= 1
    
    def test_parse_alert_line(self):
        """Test parsing valid and invalid JSON lines."""
        valid = '{"timestamp": "2026-09-30T00:00:00Z", "alert": {"action": "buy"}}'
        parsed = parse_alert_line(valid)
        assert parsed is not None
        assert parsed["alert"]["action"] == "buy"
        
        invalid = 'not json'
        parsed = parse_alert_line(invalid)
        assert parsed is None


class StubTradeLocker:
    def __init__(self, bid=1999.9, ask=2000.1, executions=None):
        self.bid = bid
        self.ask = ask
        self.executions = pd.DataFrame() if executions is None else executions

    def get_latest_bid_price(self, instrument_id):
        return self.bid

    def get_latest_asking_price(self, instrument_id):
        return self.ask

    def get_all_executions(self):
        return self.executions

    def create_order(self, **kwargs):
        return {"id": "test-order"}


@pytest.fixture
def bot_module(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_TIMEOUT_SECONDS", "15")
    monkeypatch.setenv("AI_MAX_PREDICT", "96")
    monkeypatch.setenv("AI_MAX_CONCURRENCY", "2")
    monkeypatch.setenv("AI_QUEUE_TIMEOUT_SECONDS", "30")
    tradelocker = importlib.import_module("tradelocker")
    monkeypatch.setattr(tradelocker, "TLAPI", lambda **kwargs: StubTradeLocker())
    bot = importlib.import_module("scripts.main_cfd_5m")
    symbols = {symbol: index + 1 for index, symbol in enumerate(bot.TOP_SYMBOLS)}
    monkeypatch.setattr(bot, "get_instrument_id", lambda symbol: symbols[symbol])
    monkeypatch.setattr(bot, "get_point_value", lambda symbol: 100.0)
    monkeypatch.setattr(bot, "get_live_price", lambda symbol: 2000.0 if symbol != "XPDUSD.R" else 1000.0)
    monkeypatch.setattr(bot, "calculate_atr_sl", lambda *args: 0.0)
    monkeypatch.setattr(bot, "get_technical_summary", lambda symbol: "Neutral")
    monkeypatch.setattr(bot, "is_session_active", lambda config: True)
    monkeypatch.setattr(bot, "is_weekend_et", lambda: False)
    monkeypatch.setattr(bot, "validate_spread", lambda symbol: (True, "OK"))
    monkeypatch.setattr(bot, "get_cached_positions", lambda: pd.DataFrame())
    monkeypatch.setattr(bot, "tl", StubTradeLocker())
    monkeypatch.setattr(bot, "ALERT_LOG", str(tmp_path / "alerts.jsonl"))
    monkeypatch.setattr(bot, "STATE_FILE", str(tmp_path / "bot_state.json"))
    bot._POSITIONS_CACHE["last_error"] = None
    bot._STOP_OUT_COOLDOWN.clear()
    bot._trailing_stop_stages.clear()
    bot._be_applied_positions.clear()
    monkeypatch.setattr(
        bot.ai_decider,
        "decide",
        lambda prompt: {
            "decision": "DENY",
            "confidence": 1.0,
            "reason": "integration-test denial",
            "latency_ms": 0,
            "source": "test",
        },
    )
    return bot


def make_handler_alert(ticker="XAUUSD", action="buy", price=2000.0):
    return {
        "ticker": ticker,
        "action": action,
        "trend": "Neutral",
        "indicator_value": price,
        "suggested_sl": price - (10.0 if ticker == "XAUUSD" else 5.0),
        "bar_time": datetime.now(timezone.utc).isoformat(),
    }


class TestAlertHandlerIntegration:
    def test_clean_alert_runs_through_gates_and_returns_decision(self, bot_module):
        result = bot_module.process_tradingview_alert(make_handler_alert(), "test-clean")

        assert result["status"] == "blocked"
        assert result["tl_symbol"] == "XAUUSD.R"
        assert result["technical_summary"] == "Neutral"

    def test_trend_gate_veto_returns_decision(self, bot_module, monkeypatch):
        monkeypatch.setattr(bot_module, "get_technical_summary", lambda symbol: "Strong Buy")
        result = bot_module.process_tradingview_alert(
            make_handler_alert(action="sell"), "test-trend-veto"
        )

        assert result["status"] == "rejected"
        assert result["gate"] == "trend_gate"

    def test_outside_session_returns_decision_with_allowed_sessions(self, bot_module, monkeypatch):
        monkeypatch.setattr(bot_module, "is_session_active", lambda config: False)
        result = bot_module.process_tradingview_alert(make_handler_alert(), "test-session")

        assert result["status"] == "rejected"
        assert "allowed:" in result["reason"]
        assert result["allowed_sessions"] == bot_module.TOP_SYMBOLS["XAUUSD.R"]["sessions"]

    def test_xpdusd_missing_quotes_rejects_at_slippage_gate(self, bot_module, monkeypatch):
        monkeypatch.setattr(bot_module, "tl", StubTradeLocker(bid=0.0, ask=0.0))
        result = bot_module.process_tradingview_alert(
            make_handler_alert(ticker="XPDUSD", price=1000.0), "test-xpd-quotes"
        )

        assert result["status"] == "rejected"
        assert result["gate"] == "slippage_guard"
        assert "unavailable" in result["reason"]

    def test_xpdusd_quote_error_rejects_at_slippage_gate(self, bot_module, monkeypatch):
        broker = StubTradeLocker()
        monkeypatch.setattr(
            broker,
            "get_latest_bid_price",
            lambda instrument_id: (_ for _ in ()).throw(RuntimeError("quote timeout")),
        )
        monkeypatch.setattr(bot_module, "tl", broker)
        result = bot_module.process_tradingview_alert(
            make_handler_alert(ticker="XPDUSD", price=1000.0), "test-xpd-quote-error"
        )

        assert result["status"] == "rejected"
        assert result["gate"] == "slippage_guard"
        assert "quote timeout" in result["reason"]

    def test_closed_loss_plus_open_loss_blocks_new_entry(self, bot_module, monkeypatch):
        import pytz

        et = pytz.timezone("America/New_York")
        today_start = datetime.now(et).replace(hour=0, minute=1, second=0, microsecond=0)
        close_time = today_start + timedelta(minutes=1)
        instrument_id = bot_module.get_instrument_id("XAUUSD.R")
        executions = pd.DataFrame([
            {
                "positionId": 45,
                "tradableInstrumentId": instrument_id,
                "createdDate": int(today_start.timestamp() * 1000),
                "side": "buy",
                "qty": 1.0,
                "price": 100.0,
            },
            {
                "positionId": 45,
                "tradableInstrumentId": instrument_id,
                "createdDate": int(close_time.timestamp() * 1000),
                "side": "sell",
                "qty": 1.0,
                "price": 96.5,
            },
        ])
        open_positions = pd.DataFrame([
            {
                "id": 99,
                "tradableInstrumentId": bot_module.get_instrument_id("GE"),
                "side": "buy",
                "qty": 1.0,
                "avgPrice": 100.0,
                "unrealizedPl": -60.0,
            }
        ])
        monkeypatch.setattr(bot_module, "get_cached_positions", lambda: open_positions)
        monkeypatch.setattr(bot_module, "tl", StubTradeLocker(executions=executions))
        bot_module._POSITIONS_CACHE["last_error"] = None
        monkeypatch.setattr(bot_module.ai_decider, "decide", lambda prompt: pytest.fail("AI must not run"))

        assert bot_module.compute_daily_pnl() == pytest.approx(-410.0)
        result = bot_module.process_tradingview_alert(make_handler_alert(), "test-daily-loss")

        assert result["status"] == "rejected"
        assert result["gate"] == "daily_loss"
        assert result["reason"].startswith("Daily loss limit hit")

    def test_unavailable_realized_history_blocks_new_entry(self, bot_module, monkeypatch, capsys):
        broker = StubTradeLocker()
        monkeypatch.setattr(broker, "get_all_executions", lambda: None)
        monkeypatch.setattr(bot_module, "tl", broker)
        monkeypatch.setattr(bot_module.ai_decider, "decide", lambda prompt: pytest.fail("AI must not run"))

        result = bot_module.process_tradingview_alert(make_handler_alert(), "test-history-failure")

        assert result["status"] == "rejected"
        assert result["gate"] == "daily_loss"
        assert "unavailable" in result["reason"]
        output = capsys.readouterr().out
        assert "[DAILY P&L] realized=unavailable" in output
        assert "failing closed" in output

    def test_unexpected_handler_crash_is_logged_before_reraise(self, bot_module, monkeypatch):
        monkeypatch.setattr(
            bot_module,
            "get_technical_summary",
            lambda symbol: (_ for _ in ()).throw(RuntimeError("synthetic handler failure")),
        )

        with pytest.raises(RuntimeError, match="synthetic handler failure"):
            bot_module.process_tradingview_alert(make_handler_alert(), "test-crash")

        logged = json.loads(Path(bot_module.ALERT_LOG).read_text().splitlines()[-1])
        assert logged["result"]["status"] == "error"
        assert logged["result"]["exception_message"] == "synthetic handler failure"


PLACEHOLDER_ACTION = "{{strategy.order.action}}"


def make_placeholder_alert(trend="Phantom Shift Buy", ticker="XAUUSD"):
    alert = make_handler_alert(ticker=ticker)
    alert["action"] = PLACEHOLDER_ACTION
    alert["trend"] = trend
    return alert


@pytest.fixture
def no_order_or_decider(bot_module, monkeypatch):
    """Make the decider and broker order path fail loudly if the gate lets an alert through."""
    calls = []

    monkeypatch.setattr(
        bot_module.ai_decider, "decide", lambda prompt: calls.append("decide")
    )
    monkeypatch.setattr(
        bot_module.tl,
        "create_order",
        lambda **kwargs: calls.append("create_order"),
    )
    return calls


@pytest.fixture
def captured_action(bot_module, monkeypatch):
    """Record the action that clears action parsing, then stop at the trend gate.

    The trend gate is the first consumer of the resolved side, so vetoing there proves the
    alert was parsed into buy/sell rather than rejected earlier.
    """
    seen = {}

    def fake_trend_gate(action, tech_summary, tl_symbol):
        seen["action"] = action
        return False, f"captured action={action}"

    monkeypatch.setattr(bot_module, "validate_trend_gate", fake_trend_gate)
    return seen


class TestActionParseGate:
    """The action must resolve to buy/sell or the alert is rejected (fail closed)."""

    def test_placeholder_action_resolves_buy_from_trend(self, bot_module, captured_action):
        result = bot_module.process_tradingview_alert(
            make_placeholder_alert(trend="Phantom Shift Buy"), "test-placeholder-buy"
        )

        assert result["gate"] == "trend_gate"
        assert captured_action["action"] == "buy"

    def test_placeholder_action_resolves_sell_from_trend(self, bot_module, captured_action):
        result = bot_module.process_tradingview_alert(
            make_placeholder_alert(trend="Phantom Shift Sell"), "test-placeholder-sell"
        )

        assert result["gate"] == "trend_gate"
        assert captured_action["action"] == "sell"

    def test_placeholder_action_prefers_alert_name_over_trend(
        self, bot_module, captured_action
    ):
        alert = make_placeholder_alert(trend="Phantom Shift Buy")
        alert["alert_name"] = "Phantom Shift Sell"

        result = bot_module.process_tradingview_alert(alert, "test-placeholder-name")

        assert result["gate"] == "trend_gate"
        assert captured_action["action"] == "sell"

    @pytest.mark.parametrize("trend", ["Unknown", "", None])
    def test_placeholder_action_without_side_rejects(
        self, bot_module, no_order_or_decider, trend
    ):
        result = bot_module.process_tradingview_alert(
            make_placeholder_alert(trend=trend), "test-placeholder-noside"
        )

        assert result == {
            "status": "rejected",
            "reason": "Unresolvable action in alert payload",
            "gate": "action_parse",
        }
        assert no_order_or_decider == []

    @pytest.mark.parametrize("bad_action", [None, "", "   ", "buy_now", "BUYISH?", 12345])
    def test_missing_or_garbage_action_rejects(
        self, bot_module, no_order_or_decider, bad_action
    ):
        alert = make_handler_alert()
        alert["action"] = bad_action
        alert["trend"] = "Neutral"

        result = bot_module.process_tradingview_alert(alert, "test-garbage-action")

        assert result["status"] == "rejected"
        assert result["gate"] == "action_parse"
        assert no_order_or_decider == []

    def test_rejection_is_written_to_the_alert_log(self, bot_module):
        bot_module.process_tradingview_alert(
            make_placeholder_alert(trend="Unknown"), "test-action-log"
        )

        logged = json.loads(Path(bot_module.ALERT_LOG).read_text().splitlines()[-1])
        assert logged["result"]["gate"] == "action_parse"
        assert logged["result"]["status"] == "rejected"

    @pytest.mark.parametrize("action", ["buy", "sell", " BUY ", "Sell"])
    def test_normalized_action_still_reaches_the_gates(
        self, bot_module, captured_action, action
    ):
        result = bot_module.process_tradingview_alert(
            make_handler_alert(action=action), "test-normalized"
        )

        assert result["gate"] == "trend_gate"
        assert captured_action["action"] == action.strip().lower()


def test_cooldown_state_round_trip_and_expiration(bot_module, tmp_path):
    bot_module._STOP_OUT_COOLDOWN.clear()
    bot_module.record_stop_out("XAUUSD.R")
    saved_expiry = bot_module._STOP_OUT_COOLDOWN["XAUUSD.R"]

    bot_module._STOP_OUT_COOLDOWN.clear()
    bot_module._init_cooldown_from_state()
    active, remaining = bot_module.is_in_cooldown("XAUUSD.R")
    assert active
    assert 0 < remaining <= bot_module.COOLDOWN_AFTER_STOP_MINUTES * 60
    assert bot_module._STOP_OUT_COOLDOWN["XAUUSD.R"] == saved_expiry

    bot_module._STOP_OUT_COOLDOWN["XAUUSD.R"] = datetime.now().timestamp() - 1
    active, remaining = bot_module.is_in_cooldown("XAUUSD.R")
    assert not active
    assert remaining is None
    state = json.loads(Path(bot_module.STATE_FILE).read_text())
    assert state["stop_out_cooldowns"] == {}


class TestSecondStageStopLock:
    def test_usd_lock_price_for_buy_and_sell(self, bot_module):
        assert bot_module.calculate_profit_lock_stop("GE", "buy", 100.0, 1.0) == 100.5
        assert bot_module.calculate_profit_lock_stop("GE", "sell", 100.0, 1.0) == 99.5

    def test_eur_conversion_is_used_in_lock_distance(self, bot_module):
        price = bot_module.calculate_profit_lock_stop("LVMH", "buy", 100.0, 2.0)

        assert price == 100.23

    def test_jpy_point_value_is_used_in_lock_distance(self, bot_module, monkeypatch):
        monkeypatch.setattr(bot_module, "get_point_value", lambda symbol: 650.0)

        buy = bot_module.calculate_profit_lock_stop("USDJPY.R", "buy", 150.0, 2.0)
        sell = bot_module.calculate_profit_lock_stop("USDJPY.R", "sell", 150.0, 2.0)

        assert buy == 150.04
        assert sell == 149.96

    def test_tick_rounding_clamps_lock_to_breakeven_or_better(self, bot_module, monkeypatch):
        monkeypatch.setattr(bot_module, "get_point_value", lambda symbol: 1_000_000_000.0)

        buy = bot_module.calculate_profit_lock_stop("GE", "buy", 100.005, 1.0)
        sell = bot_module.calculate_profit_lock_stop("GE", "sell", 100.005, 1.0)

        assert buy >= 100.005
        assert sell <= 100.005

    def test_stage_selection_preserves_breakeven_threshold(self, bot_module):
        assert bot_module.trailing_stop_stage(74.99) == 0
        assert bot_module.trailing_stop_stage(75.0) == 1
        assert bot_module.trailing_stop_stage(80.0) == 1
        assert bot_module.trailing_stop_stage(89.99) == 1
        assert bot_module.trailing_stop_stage(90.0) == 2
        assert bot_module.trailing_stop_stage(95.0) == 2

    def test_direct_jump_moves_straight_to_lock_once(self, bot_module, monkeypatch):
        calls = []
        monkeypatch.setattr(bot_module, "tl", type("Broker", (), {
            "modify_position": lambda self, position_id, params: calls.append((position_id, params)) or True
        })())
        monkeypatch.setattr(bot_module, "resolve_symbol_for_instrument", lambda instrument_id: "GE")
        positions = pd.DataFrame([{
            "id": 501,
            "tradableInstrumentId": bot_module.get_instrument_id("GE"),
            "side": "buy",
            "qty": 1.0,
            "avgPrice": 100.0,
            "unrealizedPl": 95.0,
        }])

        asyncio.run(bot_module.check_and_apply_trailing_stops(positions))
        asyncio.run(bot_module.check_and_apply_trailing_stops(positions))

        assert len(calls) == 1
        assert calls[0][1]["stopLoss"] == 100.5
        assert bot_module._trailing_stop_stages[501] == 2

    def test_breakeven_stage_does_not_require_lock_inputs(self, bot_module, monkeypatch):
        calls = []
        monkeypatch.setattr(bot_module, "tl", type("Broker", (), {
            "modify_position": lambda self, position_id, params: calls.append((position_id, params)) or True
        })())
        monkeypatch.setattr(
            bot_module,
            "resolve_symbol_for_instrument",
            lambda instrument_id: pytest.fail("breakeven stage must not resolve symbol"),
        )
        positions = pd.DataFrame([{
            "id": 505,
            "tradableInstrumentId": -1,
            "side": "buy",
            "avgPrice": 100.0,
            "unrealizedPl": 80.0,
        }])

        asyncio.run(bot_module.check_and_apply_trailing_stops(positions))

        assert calls[0][1]["stopLoss"] == 100.0

    def test_breakeven_then_lock_never_downgrades_after_profit_dip(self, bot_module, monkeypatch):
        calls = []
        monkeypatch.setattr(bot_module, "tl", type("Broker", (), {
            "modify_position": lambda self, position_id, params: calls.append((position_id, params)) or True
        })())
        monkeypatch.setattr(bot_module, "resolve_symbol_for_instrument", lambda instrument_id: "GE")
        positions = pd.DataFrame([{
            "id": 502,
            "tradableInstrumentId": bot_module.get_instrument_id("GE"),
            "side": "buy",
            "qty": 1.0,
            "avgPrice": 100.0,
            "unrealizedPl": 80.0,
        }])

        asyncio.run(bot_module.check_and_apply_trailing_stops(positions))
        positions.loc[0, "unrealizedPl"] = 95.0
        asyncio.run(bot_module.check_and_apply_trailing_stops(positions))
        positions.loc[0, "unrealizedPl"] = 80.0
        asyncio.run(bot_module.check_and_apply_trailing_stops(positions))

        assert [call[1]["stopLoss"] for call in calls] == [100.0, 100.5]
        assert bot_module._trailing_stop_stages[502] == 2

    def test_persisted_lock_stage_survives_restart(self, bot_module, monkeypatch):
        calls = []
        monkeypatch.setattr(bot_module, "tl", type("Broker", (), {
            "modify_position": lambda self, position_id, params: calls.append((position_id, params)) or True
        })())
        monkeypatch.setattr(bot_module, "resolve_symbol_for_instrument", lambda instrument_id: "GE")
        positions = pd.DataFrame([{
            "id": 503,
            "tradableInstrumentId": bot_module.get_instrument_id("GE"),
            "side": "sell",
            "qty": 1.0,
            "avgPrice": 100.0,
            "unrealizedPl": 95.0,
        }])

        asyncio.run(bot_module.check_and_apply_trailing_stops(positions))
        bot_module._trailing_stop_stages.clear()
        bot_module._be_applied_positions.clear()
        bot_module._init_trailing_stop_stages()
        positions.loc[0, "unrealizedPl"] = 80.0
        asyncio.run(bot_module.check_and_apply_trailing_stops(positions))

        assert [call[1]["stopLoss"] for call in calls] == [99.5]
        assert bot_module._trailing_stop_stages[503] == 2

    def test_existing_better_broker_stop_is_not_modified(self, bot_module, monkeypatch):
        calls = []
        monkeypatch.setattr(bot_module, "tl", type("Broker", (), {
            "modify_position": lambda self, position_id, params: calls.append((position_id, params)) or True
        })())
        monkeypatch.setattr(bot_module, "resolve_symbol_for_instrument", lambda instrument_id: "GE")
        positions = pd.DataFrame([{
            "id": 504,
            "tradableInstrumentId": bot_module.get_instrument_id("GE"),
            "side": "buy",
            "qty": 1.0,
            "avgPrice": 100.0,
            "unrealizedPl": 80.0,
            "stopLoss": 101.0,
        }])

        asyncio.run(bot_module.check_and_apply_trailing_stops(positions))

        assert calls == []
        assert bot_module._trailing_stop_stages[504] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])