import importlib
import json
import os
import sys

import pandas as pd
import pytest

from scripts.loss_study import (
    BrokerHistoryRateLimited,
    _fetch_history,
    excursion_from_bars,
    load_successful_alerts,
    map_alerts_to_positions,
    position_summary,
    recovery_rows,
    study_recommendation,
    simulate_time_stops,
    summarize_study,
)


def test_load_successful_alerts_resolves_live_placeholder_action(tmp_path):
    alerts_path = tmp_path / "alerts.jsonl"
    alerts_path.write_text(json.dumps({
        "timestamp": "2026-10-01T14:40:06Z",
        "alert": {
            "action": "{{strategy.order.action}}",
            "ticker": "XPDUSD",
            "trend": "Phantom Shift Buy",
        },
        "result": {
            "status": "success",
            "tl_symbol": "XPDUSD.R",
            "executed_quantity": 0.1,
            "broker_response": "216172782136416669",
        },
    }) + "\n", encoding="utf-8")

    alerts, stats = load_successful_alerts([alerts_path])

    assert len(alerts) == 1
    assert alerts[0].action == "buy"
    assert alerts[0].order_id == "216172782136416669"
    assert stats["lines"] == 1
    assert stats["unlinked"] == 0


def test_only_alerts_linked_to_broker_order_position_are_kept():
    from scripts.loss_study import AlertTrade

    alerts = [
        AlertTrade("entry-order", pd.Timestamp("2026-10-01T10:00:00Z"), "US30.R", "sell", 0.01),
        AlertTrade("missing-order", pd.Timestamp("2026-10-01T10:01:00Z"), "GE", "buy", 0.1),
    ]
    orders = pd.DataFrame([
        {"id": "entry-order", "positionId": 77},
    ])

    linked, unmatched, fallback_matches = map_alerts_to_positions(alerts, orders)

    assert list(linked) == ["77"]
    assert unmatched == 1
    assert fallback_matches == 0


def test_alert_link_falls_back_to_nearby_filled_order_by_symbol_side_and_size():
    from scripts.loss_study import AlertTrade

    alert_time = pd.Timestamp("2026-10-01T10:00:00Z")
    alerts = [AlertTrade("response-not-order-id", alert_time, "US30.R", "sell", 0.01)]
    orders = pd.DataFrame([{
        "id": "history-order-id",
        "positionId": 88,
        "tradableInstrumentId": 50,
        "side": "sell",
        "qty": 0.01,
        "createdDate": int(alert_time.timestamp() * 1000) + 2000,
        "status": "Filled",
    }])

    linked, unmatched, fallback_matches = map_alerts_to_positions(
        alerts, orders, {"US30.R": 50}
    )

    assert list(linked) == ["88"]
    assert unmatched == 0
    assert fallback_matches == 1


def test_position_summary_matches_execution_pnl_and_fees():
    executions = pd.DataFrame([
        {"positionId": 12, "createdDate": 1790848800000, "side": "buy", "qty": 1.0, "price": 100.0, "commission": -0.2},
        {"positionId": 12, "createdDate": 1790849400000, "side": "sell", "qty": 1.0, "price": 110.0, "commission": -0.3},
    ])

    trade = position_summary(executions, "12")

    assert trade["gross_pnl_price_units"] == 10.0
    assert trade["entry_price"] == 100.0
    assert trade["quantity"] == 1.0
    assert trade["entry_fees"] == pytest.approx(0.2)
    assert trade["exit_fees"] == pytest.approx(0.3)
    assert trade["fees_available"]


def test_excursion_from_bars_computes_mae_mfe_and_path():
    bars = pd.DataFrame([
        {"t": 1790848800000, "h": 101.0, "l": 99.0, "c": 100.0},
        {"t": 1790848860000, "h": 104.0, "l": 95.0, "c": 102.0},
        {"t": 1790848920000, "h": 103.0, "l": 98.0, "c": 101.0},
    ])
    start = pd.Timestamp("2026-10-01T10:00:00Z")
    end = pd.Timestamp("2026-10-01T10:02:00Z")

    result = excursion_from_bars(bars, "buy", 100.0, 1.0, 1.0, start, end)

    assert result["mae"] == -5.0
    assert result["mfe"] == 4.0
    assert result["gross_pnl_from_prices"] == 2.0
    assert len(result["path"]) == 2
    assert result["path"][-1]["time"] == end


def test_excursion_supports_dynamic_point_value_per_bar():
    bars = pd.DataFrame([
        {"t": 1790848800000, "h": 101.0, "l": 99.0, "c": 101.0},
        {"t": 1790848860000, "h": 102.0, "l": 98.0, "c": 102.0},
    ])
    start = pd.Timestamp("2026-10-01T10:00:00Z")
    end = pd.Timestamp("2026-10-01T10:02:00Z")

    result = excursion_from_bars(bars, "buy", 100.0, 1.0, [2.0, 3.0], start, end)

    assert result["gross_pnl_from_prices"] == 6.0
    assert result["mfe"] == 6.0


def test_recovery_table_uses_only_trades_that_reached_threshold():
    trades = [
        {"symbol": "A", "mae": -30.0, "net_pnl": 5.0},
        {"symbol": "A", "mae": -60.0, "net_pnl": -4.0},
        {"symbol": "B", "mae": -80.0, "net_pnl": 10.0},
    ]

    table = recovery_rows(trades)
    row = next(row for row in table if row["symbol"] == "ALL" and row["adverse_threshold"] == 50)

    assert row["sample_size"] == 2
    assert row["recovered"] == 1
    assert row["recovery_probability"] == 0.5


def test_full_sl_loser_post_mae_recovery_is_counted_separately():
    trades = [{
        "symbol": "XAUUSD.R",
        "net_pnl": -100.0,
        "mae": -80.0,
        "mfe": 10.0,
        "mae_time": pd.Timestamp("2026-10-01T10:15:00Z"),
        "path": [
            {"time": pd.Timestamp("2026-10-01T10:15:00Z"), "pnl": -80.0},
            {"time": pd.Timestamp("2026-10-01T10:20:00Z"), "pnl": 1.0},
            {"time": pd.Timestamp("2026-10-01T10:30:00Z"), "pnl": -100.0},
        ],
        "outcome_bucket": "full_sl_loss",
    }]

    summary = summarize_study(trades)

    assert summary["full_sl_losers_reaching_minus_75"] == 1
    assert summary["full_sl_losers_recovering_to_breakeven_after_minus_75"] == 1
    assert summary["recovery_after_minus_75_fraction"] == 1.0


def test_recommendation_refuses_small_in_sample_grid():
    result = study_recommendation(
        trades=[{"net_pnl": 20.0}],
        time_stop_rows=[{"net_pnl_delta": 100.0, "minutes": 15, "minimum_unrealized": 0}],
        fees_available_for_all=True,
    )

    assert result.startswith("No change recommended")


def test_time_stop_simulation_reports_winners_turned_scratch_and_losses_saved():
    entry = pd.Timestamp("2026-10-01T10:00:00Z")
    trade = {
        "symbol": "GE",
        "entry_time": entry,
        "exit_time": entry + pd.Timedelta(minutes=60),
        "net_pnl": 40.0,
        "quantity": 2.0,
        "entry_fees": 0.0,
        "path": [
            {"time": entry + pd.Timedelta(minutes=15), "pnl": -5.0},
            {"time": entry + pd.Timedelta(minutes=60), "pnl": 40.0},
        ],
    }

    held_trade = {
        "symbol": "GE",
        "entry_time": entry,
        "exit_time": entry + pd.Timedelta(minutes=60),
        "net_pnl": 10.0,
        "quantity": 1.0,
        "entry_fees": 0.0,
        "path": [
            {"time": entry + pd.Timedelta(minutes=15), "pnl": 5.0},
            {"time": entry + pd.Timedelta(minutes=60), "pnl": 10.0},
        ],
    }

    overall, _ = simulate_time_stops([trade, held_trade], fee_per_lot=0.5)
    row = next(row for row in overall if row["minutes"] == 15 and row["minimum_unrealized"] == 0)

    assert row["scratched"] == 1
    assert row["winners_turned_scratches"] == 1
    assert row["foregone_winner_pnl"] == pytest.approx(46.0)
    assert row["net_pnl_delta"] == pytest.approx(-46.0)


def test_position_pnl_sampler_is_failure_safe(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AI_TIMEOUT_SECONDS", "15")
    monkeypatch.setenv("AI_MAX_PREDICT", "96")
    monkeypatch.setenv("AI_MAX_CONCURRENCY", "2")
    monkeypatch.setenv("AI_QUEUE_TIMEOUT_SECONDS", "30")
    tradelocker = importlib.import_module("tradelocker")
    monkeypatch.setattr(tradelocker, "TLAPI", lambda **kwargs: object())
    module = importlib.import_module("scripts.main_cfd_5m")

    log_path = tmp_path / "pnl.jsonl"
    monkeypatch.setattr(module, "POSITION_PNL_LOG", str(log_path))
    module.log_position_pnl_samples([{
        "position_id": 4,
        "symbol": "US30.R",
        "unrealized_pnl": -25.0,
    }])
    row = json.loads(log_path.read_text().splitlines()[0])
    assert row["position_id"] == 4
    assert row["unrealized_pnl"] == -25.0
    assert row["timestamp"]

    monkeypatch.setattr(module, "POSITION_PNL_LOG", str(tmp_path / "missing" / "pnl.jsonl"))
    module.log_position_pnl_samples([{"position_id": 5, "symbol": "GE", "unrealized_pnl": 1.0}])
    assert "[POSITION P&L LOG]" in capsys.readouterr().out


def test_history_fetch_stops_resolution_fallback_on_rate_limit():
    class RateLimitedBroker:
        def __init__(self):
            self.calls = 0

        def get_price_history(self, **kwargs):
            self.calls += 1
            raise RuntimeError("Cloudflare error 1015: You are being rate limited")

    broker = RateLimitedBroker()
    start = pd.Timestamp("2026-10-01T10:00:00Z")
    end = start + pd.Timedelta(minutes=10)

    with pytest.raises(BrokerHistoryRateLimited):
        _fetch_history(broker, 10, start, end)

    assert broker.calls == 1


# ---------------------------------------------------------------------------
# Stop-tightening evaluation (PR #11)
# ---------------------------------------------------------------------------
from scripts.stop_tightening_eval import (
    STOP_LEVELS,
    _is_owner_manual_trade,
    _walk_stop_level,
    simulate_stop_levels,
)


def _path(points):
    return [{"time": pd.Timestamp(t), "pnl": p} for t, p in points]


def test_stop_level_constants_match_live_bot():
    assert STOP_LEVELS == (50.0, 75.0, 100.0)


def test_walk_stop_level_kills_winner_before_be_trigger():
    entry = pd.Timestamp("2026-10-01T10:00:00Z")
    path = _path([
        (entry + pd.Timedelta(minutes=5), -30.0),
        (entry + pd.Timedelta(minutes=10), -55.0),   # crosses -$50 before BE
        (entry + pd.Timedelta(minutes=20), 100.0),
    ])
    result = _walk_stop_level(path, 50.0, 75.0, 80.0, 100.0)
    assert result["stop_hit"] is True
    assert result["stop_pnl"] == pytest.approx(-55.0)
    assert result["be_reached_before_stop"] is False


def test_walk_stop_level_shielded_by_breakeven_trigger():
    entry = pd.Timestamp("2026-10-01T10:00:00Z")
    path = _path([
        (entry + pd.Timedelta(minutes=5), -40.0),
        (entry + pd.Timedelta(minutes=10), 80.0),    # hits BE trigger first
        (entry + pd.Timedelta(minutes=20), -60.0),   # would cross -$50 later
        (entry + pd.Timedelta(minutes=30), 100.0),
    ])
    result = _walk_stop_level(path, 50.0, 75.0, 80.0, 100.0)
    assert result["stop_hit"] is False
    assert result["be_reached_before_stop"] is True


def test_walk_stop_level_shielded_by_profit_lock_trigger():
    entry = pd.Timestamp("2026-10-01T10:00:00Z")
    path = _path([
        (entry + pd.Timedelta(minutes=5), 85.0),     # profit-lock trigger
        (entry + pd.Timedelta(minutes=20), -60.0),   # would cross -$50 later
        (entry + pd.Timedelta(minutes=30), 100.0),
    ])
    result = _walk_stop_level(path, 50.0, 75.0, 80.0, 100.0)
    assert result["stop_hit"] is False
    assert result["profit_lock_reached_before_stop"] is True


def test_simulate_stop_levels_reports_killed_winners_and_saved_losers():
    entry = pd.Timestamp("2026-10-01T10:00:00Z")
    winner = {
        "symbol": "GE", "entry_time": entry,
        "exit_time": entry + pd.Timedelta(minutes=60),
        "net_pnl": 100.0, "quantity": 1.0, "entry_fees": 0.0,
        "path": _path([
            (entry + pd.Timedelta(minutes=5), -30.0),
            (entry + pd.Timedelta(minutes=10), -55.0),
            (entry + pd.Timedelta(minutes=60), 100.0),
        ]),
    }
    loser = {
        "symbol": "GE", "entry_time": entry,
        "exit_time": entry + pd.Timedelta(minutes=60),
        "net_pnl": -100.0, "quantity": 1.0, "entry_fees": 0.0,
        "path": _path([
            (entry + pd.Timedelta(minutes=5), -40.0),
            (entry + pd.Timedelta(minutes=10), -55.0),
            (entry + pd.Timedelta(minutes=60), -100.0),
        ]),
    }
    overall, by_symbol = simulate_stop_levels([winner, loser])
    row = next(r for r in overall if r["stop_dollars"] == 50.0 and r["symbol"] == "ALL")
    assert row["winners_killed"] == 1
    assert row["losers_saved"] == 1
    assert row["killed_damage"] == pytest.approx(100.0 - (-55.0))
    assert row["losses_saved_dollars"] == pytest.approx((-55.0) - (-100.0))


def test_simulate_stop_levels_baseline_is_zero_delta():
    entry = pd.Timestamp("2026-10-01T10:00:00Z")
    trades = [{
        "symbol": "GE", "entry_time": entry,
        "exit_time": entry + pd.Timedelta(minutes=60),
        "net_pnl": -100.0, "quantity": 1.0, "entry_fees": 0.0,
        "path": _path([
            (entry + pd.Timedelta(minutes=5), -40.0),
            (entry + pd.Timedelta(minutes=60), -100.0),
        ]),
    }]
    overall, _ = simulate_stop_levels(trades)
    baseline = next(r for r in overall if r["stop_dollars"] == 100.0 and r["symbol"] == "ALL")
    assert baseline["net_pnl_delta"] == 0.0
    assert baseline["trades_stopped"] == 0


def test_owner_manual_trade_exclusion():
    trade = {
        "symbol": "EURUSD.R",
        "entry_time": pd.Timestamp("2026-10-07T09:00:00Z"),
        "net_pnl": 493.0,
    }
    assert _is_owner_manual_trade(trade) is True
    trade["symbol"] = "XAUUSD.R"
    assert _is_owner_manual_trade(trade) is False


# ---------------------------------------------------------------------------
# PR #11 follow-up: per-symbol windows, 1015 backoff, 1H resolution
# ---------------------------------------------------------------------------
from scripts.stop_tightening_eval import (
    BAR_SECONDS_BY_RESOLUTION,
    PRICE_HISTORY_RESOLUTIONS,
    RATE_LIMIT_BACKOFF_SECONDS,
    RATE_LIMIT_MAX_RETRIES,
    _fetch_symbol_window,
)


def test_resolution_chain_includes_1h():
    assert "1H" in PRICE_HISTORY_RESOLUTIONS
    assert PRICE_HISTORY_RESOLUTIONS == ("1m", "5m", "15m", "1H")
    assert BAR_SECONDS_BY_RESOLUTION["1H"] == 3600


def test_rate_limit_backoff_constants():
    assert RATE_LIMIT_BACKOFF_SECONDS == 60.0
    assert RATE_LIMIT_MAX_RETRIES == 3


def test_fetch_symbol_window_uses_cache_and_skips_empty(monkeypatch, tmp_path):
    monkeypatch.setattr("scripts.stop_tightening_eval.time.sleep", lambda *_: None)
    monkeypatch.setattr("scripts.stop_tightening_eval.HISTORY_REQUEST_MIN_INTERVAL_SECONDS", 0.0)
    calls = []

    class FakeTL:
        def get_price_history(self, **kwargs):
            calls.append(kwargs)
            return None  # empty -> should keep trying resolutions

    start = pd.Timestamp("2026-10-01T10:00:00Z")
    end = start + pd.Timedelta(hours=2)
    with pytest.raises(RuntimeError):
        _fetch_symbol_window(FakeTL(), 13119, "UKOIL.R", start, end, tmp_path)
    # 4 resolutions x (1 initial + 3 retries) = 16 attempts
    assert len(calls) == 4 * (1 + RATE_LIMIT_MAX_RETRIES)


def test_fetch_symbol_window_returns_cached_without_calling(monkeypatch, tmp_path):
    import pickle
    cache_dir = tmp_path
    bars = pd.DataFrame({"time": [1], "high": [2], "low": [1], "close": [1.5]})
    (cache_dir / "UKOIL.R_13119_20261001T100000Z_20261001T120000Z.pkl").write_bytes(
        pickle.dumps(bars)
    )
    called = []

    class FakeTL:
        def get_price_history(self, **kwargs):
            called.append(kwargs)
            return None

    start = pd.Timestamp("2026-10-01T10:00:00Z")
    end = start + pd.Timedelta(hours=2)
    df, res = _fetch_symbol_window(FakeTL(), 13119, "UKOIL.R", start, end, cache_dir)
    assert called == []
    assert res == "cached"
    assert len(df) == 1


def test_fetch_symbol_window_retries_on_1015(monkeypatch, tmp_path):
    attempts = {"n": 0}

    class FakeTL:
        def get_price_history(self, **kwargs):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise RuntimeError("Cloudflare error 1015: You are being rate limited")
            return pd.DataFrame({"t": [1], "h": [2], "l": [1], "c": [1.5]})

    monkeypatch.setattr("scripts.stop_tightening_eval.time.sleep", lambda *_: None)
    start = pd.Timestamp("2026-10-01T10:00:00Z")
    end = start + pd.Timedelta(hours=2)
    df, res = _fetch_symbol_window(FakeTL(), 13119, "UKOIL.R", start, end, tmp_path)
    assert attempts["n"] == 3
    assert res == "1m"
    assert len(df) == 1
