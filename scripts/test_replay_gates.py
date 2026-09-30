#!/usr/bin/env python3
"""
Pytest tests for entry vetting gates replay.

Run: pytest scripts/test_replay_gates.py -v
"""

import pytest
import json
import tempfile
import os
from datetime import datetime, timedelta
from pathlib import Path

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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])