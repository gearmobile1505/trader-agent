#!/usr/bin/env python3
"""Stop-tightening MAE evaluation (PR #11).

Reuses scripts/loss_study.py for broker-reconstructed trade paths, then
simulates hard dollar stops at -$50 / -$75 / -$100 (current) and reports
per-level winners killed, loser savings, net P&L delta, expectancy delta,
win-rate delta, and per-symbol breakdown.
"""

from __future__ import annotations

import argparse
import json
import os
import pickle
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from scripts.loss_study import (
    BrokerHistoryRateLimited,
    HISTORY_REQUEST_MIN_INTERVAL_SECONDS,
    _fetch_history,
    _point_values_for_bars,
    analyze_with_broker,
    excursion_from_bars,
    load_successful_alerts,
    map_alerts_to_positions,
    normalize_bars,
    outcome_bucket,
    position_summary,
    recovery_rows,
    study_recommendation,
    summarize_study,
)

# ---------------------------------------------------------------------------
# Live bot constants (scripts/main_cfd_5m.py) — frozen risk parameters
# ---------------------------------------------------------------------------
TARGET_DOLLAR_RISK = 100.0
TRAILING_SL_BE_PROFIT = 75.0       # SL moves to breakeven when P&L >= $75
TRAILING_SL_LOCK_TRIGGER = 80.0    # profit-lock stage when P&L >= $80
TRAILING_SL_LOCK_DOLLARS = 50.0   # lock target after TP1
TP1_DOLLARS = 100.0               # take-profit after PR #8
STOP_LEVELS = (50.0, 75.0, 100.0) # simulated hard dollar stops
SLIPPAGE_HAIRCUT = 0.10           # 10% conservative slippage on stopped fills
EUR_USD_RATE = 1.08
JPY_CONTRACT_SIZE = 100_000.0


# ---------------------------------------------------------------------------
# Paced, resumable reconstruction
# ---------------------------------------------------------------------------
class StopTighteningRateLimited(BrokerHistoryRateLimited):
    """Raised when the broker rate-limits history calls mid-reconstruction."""


def _bars_cache_dir(output_dir: Path) -> Path:
    return output_dir / "bars_cache"


def _bars_cache_key(symbol: str, instrument_id: int, start: pd.Timestamp, end: pd.Timestamp) -> str:
    return f"{symbol}_{instrument_id}_{start.strftime('%Y%m%dT%H%M%SZ')}_{end.strftime('%Y%m%dT%H%M%SZ')}.parquet"


def _load_cached_bars(cache_dir: Path, key: str) -> pd.DataFrame | None:
    parquet_path = cache_dir / key
    pkl_path = cache_dir / key.replace(".parquet", ".pkl")
    for path in (parquet_path, pkl_path):
        if not path.exists():
            continue
        try:
            if path.suffix == ".parquet":
                return pd.read_parquet(path)
            return pickle.loads(path.read_bytes())
        except Exception:
            return None
    return None


def _save_cached_bars(cache_dir: Path, key: str, bars: pd.DataFrame) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / key
    try:
        bars.to_parquet(path, index=False)
    except Exception:
        # Parquet needs pyarrow/fastparquet which may be absent on the
        # evaluation server; fall back to pickle so checkpointing still works.
        path.with_suffix(".pkl").write_bytes(pickle.dumps(bars))


_last_history_request = 0.0


def _throttle_history(tl) -> None:
    """Paced fetch with per-symbol checkpointing to survive Cloudflare 1015."""
    global _last_history_request
    elapsed = time.monotonic() - _last_history_request
    if elapsed < HISTORY_REQUEST_MIN_INTERVAL_SECONDS:
        time.sleep(HISTORY_REQUEST_MIN_INTERVAL_SECONDS - elapsed)
    _last_history_request = time.monotonic()


def _fetch_with_cache(tl, instrument_id: int, start: pd.Timestamp, end: pd.Timestamp,
                      cache_dir: Path, symbol: str, resolution: str | None = None,
                      throttle: float | None = None) -> tuple[pd.DataFrame, str]:
    """Fetch price history, caching per symbol/time-window so runs resume."""
    if throttle is not None:
        global _last_history_request
        _last_history_request = 0.0
        HISTORY_REQUEST_MIN_INTERVAL_SECONDS = throttle  # noqa: SLF001
    key = _bars_cache_key(symbol, instrument_id, start, end)
    cached = _load_cached_bars(cache_dir, key)
    if cached is not None and not cached.empty:
        return cached, resolution or "1m"
    _throttle_history(tl)
    errors = []
    resolutions = (resolution,) if resolution else ("1m", "5m", "15m")
    for res in resolutions:
        try:
            history = tl.get_price_history(
                instrument_id=int(instrument_id),
                resolution=res,
                start_timestamp=int(start.timestamp() * 1000),
                end_timestamp=int(end.timestamp() * 1000),
            )
            if history is not None and not history.empty:
                normalized = normalize_bars(history)
                _save_cached_bars(cache_dir, key, normalized)
                return normalized, res
        except Exception as exc:
            message = str(exc).lower()
            if "1015" in message or "rate limit" in message or "too many requests" in message:
                raise StopTighteningRateLimited(
                    "broker history rate-limited (HTTP/Cloudflare 1015); stopped further requests"
                ) from exc
            errors.append(f"{res}:{type(exc).__name__}")
    raise RuntimeError("price history unavailable (" + ", ".join(errors) + ")")

# ---------------------------------------------------------------------------
# Owner manual-trade exclusion
# ---------------------------------------------------------------------------
OWNER_MANUAL_TRADES = [
    {"symbol": "EURUSD.R", "date": "2026-10-07", "pnl": 493.00},
    {"symbol": "EURJPY.R", "date": "2026-10-07", "pnl": 369.24},
]


def _is_owner_manual_trade(trade: dict[str, Any]) -> bool:
    """Exclude the owner's two manual EUR trades of 2026-10-07."""
    entry_time = trade.get("entry_time")
    if entry_time is None or pd.isna(entry_time):
        return False
    entry_date = pd.Timestamp(entry_time).strftime("%Y-%m-%d")
    for manual in OWNER_MANUAL_TRADES:
        if trade["symbol"] == manual["symbol"] and entry_date == manual["date"]:
            return True
    return False


# ---------------------------------------------------------------------------
# Stop-level simulation
# ---------------------------------------------------------------------------
def _walk_stop_level(path: list[dict[str, Any]], stop_dollars: float,
                     be_trigger: float, profit_lock_trigger: float,
                     tp_dollars: float) -> dict[str, Any]:
    """Walk a reconstructed P&L path with a hard dollar stop.

    Returns the simulated outcome. A tighter initial stop only applies if the
    trade never reached the breakeven/profit-lock trigger first; otherwise the
    actual outcome is preserved (the tighter stop never came into play).
    """
    be_reached = False
    profit_lock_reached = False
    tp_reached = False
    stop_hit = False
    stop_time = None
    stop_pnl = None

    for point in path:
        pnl = point["pnl"]
        if pnl <= -stop_dollars and not be_reached and not profit_lock_reached:
            stop_hit = True
            stop_time = point["time"]
            stop_pnl = -stop_dollars * (1.0 + SLIPPAGE_HAIRCUT)
            break
        if pnl >= be_trigger:
            be_reached = True
        if pnl >= profit_lock_trigger:
            profit_lock_reached = True
        if pnl >= tp_dollars:
            tp_reached = True
            break

    return {
        "stop_hit": stop_hit,
        "stop_time": stop_time,
        "stop_pnl": stop_pnl,
        "be_reached_before_stop": be_reached,
        "profit_lock_reached_before_stop": profit_lock_reached,
        "tp_reached": tp_reached,
    }


def simulate_stop_levels(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Simulate hard dollar stops at each level in STOP_LEVELS."""
    overall_rows: list[dict[str, Any]] = []
    by_symbol_rows: list[dict[str, Any]] = []
    symbols = sorted({trade["symbol"] for trade in trades})

    for level in STOP_LEVELS:
        for symbol in ["ALL", *symbols]:
            subset = trades if symbol == "ALL" else [t for t in trades if t["symbol"] == symbol]
            killed = []
            killed_damage = 0.0
            saved = 0.0
            delta = 0.0
            wins_killed = 0
            losses_saved = 0
            stopped_trades = 0
            be_shielded = 0

            for trade in subset:
                if level >= TARGET_DOLLAR_RISK:
                    # Baseline/reference level: the natural full-SL exit. No
                    # simulated intervention, so delta is zero by construction.
                    continue
                result = _walk_stop_level(
                    trade["path"], level,
                    TRAILING_SL_BE_PROFIT, TRAILING_SL_LOCK_TRIGGER, TP1_DOLLARS,
                )
                if not result["stop_hit"]:
                    if result["be_reached_before_stop"] or result["profit_lock_reached_before_stop"]:
                        be_shielded += 1
                    continue
                stopped_trades += 1
                actual = trade["net_pnl"]
                simulated = result["stop_pnl"]
                change = simulated - actual
                delta += change
                if actual > 0:
                    wins_killed += 1
                    killed_damage += actual - simulated
                    killed.append(trade)
                else:
                    losses_saved += 1
                    saved += max(change, 0.0)

            row = {
                "stop_dollars": level,
                "symbol": symbol,
                "sample_size": len(subset),
                "trades_stopped": stopped_trades,
                "winners_killed": wins_killed,
                "losers_saved": losses_saved,
                "killed_damage": round(killed_damage, 2),
                "losses_saved_dollars": round(saved, 2),
                "net_pnl_delta": round(delta, 2),
                "be_shielded": be_shielded,
            }
            (overall_rows if symbol == "ALL" else by_symbol_rows).append(row)
    return overall_rows, by_symbol_rows


# ---------------------------------------------------------------------------
# Paced reconstruction (reuses loss_study.py helpers)
# ---------------------------------------------------------------------------
def _build_client():
    env_path = os.getenv("TRADER_AGENT_ENV_FILE")
    load_dotenv(env_path if env_path else None)
    from tradelocker import TLAPI
    return TLAPI(
        environment=os.getenv("TL_ENV", "https://demo.tradelocker.com"),
        username=os.getenv("TL_USER"),
        password=os.getenv("TL_PASS"),
        server=os.getenv("TL_SERVER"),
        log_level="warning",
    )


def reconstruct_trades(alert_paths: list[Path], output_dir: Path,
                       throttle: float | None = None) -> list[dict[str, Any]]:
    """Reconstruct bot trade paths with pacing + per-symbol checkpointing."""
    cache_dir = _bars_cache_dir(output_dir)
    tl = _build_client()
    alerts, load_stats = load_successful_alerts(alert_paths)
    if alerts:
        start_timestamp = int(min(a.timestamp for a in alerts).timestamp() * 1000)
        end_timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)
    else:
        start_timestamp = 0
        end_timestamp = 0
    orders = tl.get_all_orders(start_timestamp=start_timestamp, end_timestamp=end_timestamp, history=True)
    instruments = tl.get_all_instruments()
    instrument_ids = dict(zip(instruments["name"].astype(str), instruments["tradableInstrumentId"].astype(int)))
    linked, unmatched_count, matched_by_time = map_alerts_to_positions(alerts, orders, instrument_ids)
    # TradeLocker's get_all_executions() only returns the *current* session's
    # fills (64 rows here), which silently drops 67/94 closed positions. The
    # full history is in get_all_orders(history=True) — every Filled order row
    # already carries avgPrice/filledQty/side/createdDate/positionId, so we
    # build the executions DataFrame from those instead. This is the same
    # FIFO math position_summary() uses; verified to close all 94 linked
    # positions on the live server.
    filled = orders[orders["status"].astype(str).str.lower() == "filled"].copy()
    executions = pd.DataFrame({
        "id": filled["id"],
        "price": filled["avgPrice"],
        "side": filled["side"],
        "createdDate": filled["createdDate"],
        "qty": filled["filledQty"],
        "orderId": filled["id"],
        "positionId": filled["positionId"],
        "tradableInstrumentId": filled["tradableInstrumentId"],
    })

    trades: list[dict[str, Any]] = []
    missing_execution = 0
    missing_history = 0
    rate_limit_hit = False
    resolutions: dict[str, int] = {}
    usd_jpy_id = instrument_ids.get("USDJPY.R")
    jpy_cache: dict[str, pd.DataFrame] = {}

    for position_id, alert in linked.items():
        order_summary = position_summary(executions, position_id)
        if order_summary is None:
            missing_execution += 1
            continue
        instrument_id = instrument_ids.get(alert.symbol)
        if instrument_id is None:
            missing_history += 1
            continue
        try:
            details = tl.get_instrument_details(int(instrument_id))
            trade_duration = (order_summary["exit_time"] - order_summary["entry_time"]).total_seconds()
            # Scratch fills (entry and exit in the same millisecond, e.g. a
            # breakeven stop filled instantly) have no in-lifetime bars at any
            # resolution — the broker returns empty history. Skip them rather
            # than counting as a data failure; they are BE scratches anyway.
            if trade_duration < 60:
                missing_history += 1
                continue
            bars, resolution = _fetch_with_cache(
                tl, instrument_id, order_summary["entry_time"], order_summary["exit_time"],
                cache_dir, alert.symbol, resolution=None, throttle=throttle,
            )
            resolutions[resolution] = resolutions.get(resolution, 0) + 1
            jpy_bars = None
            if str(details.get("quotingCurrency") or "").upper() == "JPY" and usd_jpy_id:
                jpy_key = f"USDJPY_{usd_jpy_id}"
                if jpy_key in jpy_cache:
                    jpy_bars = jpy_cache[jpy_key]
                else:
                    jpy_bars, _ = _fetch_with_cache(
                        tl, int(usd_jpy_id), order_summary["entry_time"], order_summary["exit_time"],
                        cache_dir, "USDJPY.R", resolution=None, throttle=throttle,
                    )
                    jpy_cache[jpy_key] = jpy_bars
            # excursion_from_bars() filters bars to those wholly inside the
            # position lifetime (excluding entry/exit overlap). Point values
            # must be computed for that same filtered set, otherwise the
            # series length mismatches and the study silently drops the trade.
            bar_seconds = {"1m": 60, "5m": 300, "15m": 900}[resolution]
            lifetime_bars = normalize_bars(bars).copy()
            lifetime_bars["bar_close_time"] = lifetime_bars["time"] + pd.to_timedelta(bar_seconds, unit="s")
            lifetime_bars = lifetime_bars[
                (lifetime_bars["time"] >= order_summary["entry_time"])
                & (lifetime_bars["bar_close_time"] <= order_summary["exit_time"])
            ]
            if lifetime_bars.empty:
                missing_history += 1
                continue
            factors = _point_values_for_bars(alert.symbol, details, tl, jpy_bars, lifetime_bars["time"])
            excursion = excursion_from_bars(
                bars, order_summary["side"], order_summary["entry_price"],
                order_summary["quantity"], factors, order_summary["entry_time"],
                order_summary["exit_time"], bar_seconds=bar_seconds,
            )
            close_times = pd.Series([c["time"] for c in order_summary["realized_components"]])
            close_factors = _point_values_for_bars(alert.symbol, details, tl, jpy_bars, close_times)
            realized_pnl = sum(
                c["pnl_price_units"] * float(f) for c, f in zip(order_summary["realized_components"], close_factors)
            )
            fees = order_summary["entry_fees"] + order_summary["exit_fees"]
            net_pnl = realized_pnl - fees
            if "positionId" in orders.columns and "status" in orders.columns:
                exit_orders = orders[
                    (orders["positionId"].map(str) == position_id)
                    & (orders["status"].astype(str).str.lower() == "filled")
                ]
                sort_col = next((n for n in ("lastModified", "filledAt", "createdDate") if n in exit_orders.columns), None)
                if sort_col and not exit_orders.empty:
                    exit_orders = exit_orders.sort_values(sort_col)
            else:
                exit_orders = pd.DataFrame()
            exit_order_type = str(exit_orders.iloc[-1].get("type", "")) if not exit_orders.empty else ""
            trades.append({
                **order_summary,
                **excursion,
                "symbol": alert.symbol,
                "alert_order_id": alert.order_id,
                "net_pnl": net_pnl,
                "realized_gross_pnl": realized_pnl,
                "fees": fees,
                "fees_available": order_summary["fees_available"],
                "exit_order_type": exit_order_type,
                "outcome_bucket": outcome_bucket(net_pnl, exit_order_type),
                "resolution": resolution,
                "requested_quantity": alert.quantity,
            })
        except StopTighteningRateLimited as exc:
            missing_history += 1
            rate_limit_hit = True
            print(f"[STOP TIGHTENING] {exc}", flush=True)
            break
        except Exception as exc:
            missing_history += 1
            print(f"[STOP TIGHTENING] {alert.symbol} position {position_id}: {exc}", flush=True)

    stats = {
        "successful_alerts": len(alerts),
        "linked_positions": len(linked),
        "unmatched_alerts": unmatched_count,
        "matched_by_time_fallback": matched_by_time,
        "analyzed_trades": len(trades),
        "missing_execution_positions": missing_execution,
        "missing_price_history_positions": missing_history,
        "rate_limit_hit": rate_limit_hit,
        "price_history_resolutions": resolutions,
        "owner_manual_trades_excluded": sum(1 for t in trades if _is_owner_manual_trade(t)),
    }
    # Exclude owner manual trades from the bot baseline
    bot_trades = [t for t in trades if not _is_owner_manual_trade(t)]
    return bot_trades, stats


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _expectancy(trades: list[dict[str, Any]]) -> float:
    if not trades:
        return 0.0
    return sum(t["net_pnl"] for t in trades) / len(trades)


def _win_rate(trades: list[dict[str, Any]]) -> float:
    if not trades:
        return 0.0
    return sum(1 for t in trades if t["net_pnl"] > 0) / len(trades) * 100.0


def _print_report(report: dict[str, Any]) -> None:
    trades = report["trades"]
    stats = report["reconstruction_stats"]
    print("=" * 72)
    print("STOP-TIGHTENING MAE EVALUATION (PR #11)")
    print("=" * 72)
    print(f"Successful alerts: {stats['successful_alerts']}")
    print(f"Matched alerts to positions: {stats['linked_positions']} "
          f"(unmatched: {stats['unmatched_alerts']}, time-fallback: {stats['matched_by_time_fallback']})")
    print(f"Bot trades analyzed (owner manual excluded): {stats['analyzed_trades']}")
    print(f"Missing execution groups: {stats['missing_execution_positions']}; "
          f"missing price paths: {stats['missing_price_history_positions']}")
    print(f"Rate limit hit: {stats['rate_limit_hit']}")
    print(f"Price-history resolutions: {stats['price_history_resolutions']}")
    print(f"Owner manual trades excluded: {stats['owner_manual_trades_excluded']}")
    print()

    baseline_pnl = sum(t["net_pnl"] for t in trades)
    baseline_wr = _win_rate(trades)
    baseline_exp = _expectancy(trades)
    print(f"BASELINE (-$100 stop): net P&L ${baseline_pnl:.2f}, "
          f"win rate {baseline_wr:.1f}%, expectancy ${baseline_exp:.2f}")
    print()

    print("STUDY 1 — MAE distributions (winners vs losers):")
    print(pd.DataFrame(report["study"]["mae_mfe_distribution"]).to_string(index=False))
    print()
    print("Recovery probabilities, conditioned on crossing adverse level:")
    print(pd.DataFrame(report["study"]["recovery"]).to_string(index=False))
    print()

    print("STUDY 2 — stop-level simulation:")
    overall = pd.DataFrame(report["stop_overall"])
    by_sym = pd.DataFrame(report["stop_by_symbol"])
    print(overall.to_string(index=False))
    print()
    print("Per-symbol breakdown:")
    print(by_sym.to_string(index=False))
    print()

    # Sweet-spot verdict
    print("SWEET-SPOT VERDICT:")
    best = None
    for row in report["stop_overall"]:
        if row["symbol"] == "ALL" and row["stop_dollars"] != 100.0:
            if best is None or row["net_pnl_delta"] > best["net_pnl_delta"]:
                best = row
    if best is None:
        print("No tighter stop level beats the current -$100 on net. Recommendation: don't tighten.")
    else:
        verdict = (
            f"Tightening to -${best['stop_dollars']:.0f} is the sweet spot: "
            f"net P&L delta ${best['net_pnl_delta']:+.2f} vs baseline, "
            f"{best['winners_killed']} winners killed (damage ${best['killed_damage']:.2f}), "
            f"{best['losers_saved']} losers saved (${best['losses_saved_dollars']:.2f})."
        )
        print(verdict)
    print()
    print("Recommendation:", report["recommendation"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Stop-tightening MAE evaluation (PR #11)")
    repo_root = Path(__file__).resolve().parent.parent
    parser.add_argument("--alerts-log", nargs="+", type=Path,
                        default=[repo_root / "alerts_log.jsonl", repo_root / "data" / "alerts_log.jsonl"])
    parser.add_argument("--output-dir", type=Path, default=repo_root / "stop_tightening_output")
    parser.add_argument("--throttle", type=float, default=None,
                        help="Override HISTORY_REQUEST_MIN_INTERVAL_SECONDS for this run")
    args = parser.parse_args()

    trades, stats = reconstruct_trades(args.alerts_log, args.output_dir, throttle=args.throttle)
    study = summarize_study(trades)
    stop_overall, stop_by_symbol = simulate_stop_levels(trades)
    fees_ok = bool(trades) and all(t.get("fees_available") for t in trades)
    report = {
        "input_logs": [str(p) for p in args.alerts_log],
        "reconstruction_stats": stats,
        "fees_available_for_all_trades": fees_ok,
        "pnl_basis": "net_after_recorded_fees" if fees_ok else "gross_or_partially_fee_adjusted",
        "study": study,
        "stop_overall": stop_overall,
        "stop_by_symbol": stop_by_symbol,
        "trades": trades,
    }
    report["recommendation"] = study_recommendation(trades, [], fees_ok)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "stop_tightening.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    pd.DataFrame(trades).drop(columns=["path"], errors="ignore").to_csv(
        args.output_dir / "stop_tightening_trades.csv", index=False
    )
    pd.DataFrame(study["recovery"]).to_csv(args.output_dir / "stop_tightening_recovery.csv", index=False)
    pd.DataFrame(stop_overall).to_csv(args.output_dir / "stop_tightening_overall.csv", index=False)
    pd.DataFrame(stop_by_symbol).to_csv(args.output_dir / "stop_tightening_by_symbol.csv", index=False)
    _print_report(report)


if __name__ == "__main__":
    main()
