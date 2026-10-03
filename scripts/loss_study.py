#!/usr/bin/env python3
"""Reconstruct bot trade excursions and simulate time stops from TradeLocker history."""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from dotenv import load_dotenv

ET = "America/New_York"
TIME_STOP_MINUTES = (15, 30, 45, 60)
TIME_STOP_THRESHOLDS = (0.0, 10.0, 25.0)
RECOVERY_LEVELS = (25.0, 50.0, 75.0)
EUR_USD_RATE = 1.08
JPY_CONTRACT_SIZE = 100_000.0
USD_POINT_VALUE = {
    "XPDUSD.R": 100.0,
    "ALPHABET-C": 100.0,
    "UKOIL.R": 1000.0,
    "GE": 100.0,
    "US30.R": 100.0,
    "NAS100.R": 100.0,
    "SPX500.R": 100.0,
    "XAUUSD.R": 100.0,
    "LVMH": 100.0,
    "SIEMENS": 100.0,
    "GBPJPY.R": JPY_CONTRACT_SIZE,
    "USDJPY.R": JPY_CONTRACT_SIZE,
}
JPY_SYMBOLS = {"GBPJPY.R", "USDJPY.R"}
EUR_SYMBOLS = {"LVMH", "SIEMENS"}
FEE_COLUMNS = ("commission", "commissions", "fee", "fees", "commissionAmount")
OUTCOME_BUCKETS = ("full_sl_loss", "breakeven_scratch", "small_win", "tp_win", "profit_lock")
ORDER_ID_PATTERN = re.compile(r"\d{10,}")


@dataclass
class AlertTrade:
    order_id: str
    timestamp: pd.Timestamp
    symbol: str
    action: str
    quantity: float


def normalize_id(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    try:
        numeric = float(value)
        if numeric.is_integer():
            return str(int(numeric))
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def broker_order_id(response: Any) -> str | None:
    """Extract the numeric order id returned by TLAPI.create_order()."""
    match = ORDER_ID_PATTERN.search(str(response or ""))
    return match.group(0) if match else None


def _execution_fee(row, fee_column: str | None) -> float:
    if fee_column is None:
        return 0.0
    try:
        value = float(getattr(row, fee_column))
    except (AttributeError, TypeError, ValueError):
        return 0.0
    return abs(value) if np.isfinite(value) else 0.0


def load_successful_alerts(paths: list[Path]) -> tuple[list[AlertTrade], dict[str, int]]:
    """Load only successful, position-sized alerts with a traceable broker order id."""
    stats = {"lines": 0, "malformed": 0, "not_success": 0, "unlinked": 0, "duplicates": 0}
    alerts: dict[str, AlertTrade] = {}
    for path in paths:
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as source:
            for line in source:
                stats["lines"] += 1
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    stats["malformed"] += 1
                    continue
                result = record.get("result") or {}
                if result.get("status") != "success":
                    stats["not_success"] += 1
                    continue
                order_id = broker_order_id(result.get("broker_response"))
                alert = record.get("alert") or {}
                symbol = result.get("tl_symbol")
                raw_action = alert.get("action")
                action = raw_action.strip().lower() if isinstance(raw_action, str) else ""
                alert_name = str(alert.get("alert_name") or alert.get("name") or alert.get("alertName") or "").lower()
                trend = str(alert.get("trend") or "").lower()
                if action not in ("buy", "sell"):
                    if "buy" in alert_name or "bullish" in alert_name or "long" in alert_name:
                        action = "buy"
                    elif "sell" in alert_name or "bearish" in alert_name or "short" in alert_name:
                        action = "sell"
                    elif "buy" in trend or "bullish" in trend:
                        action = "buy"
                    elif "sell" in trend or "bearish" in trend:
                        action = "sell"
                try:
                    quantity = float(result.get("executed_quantity"))
                    timestamp = pd.to_datetime(record.get("timestamp"), utc=True, errors="raise")
                except (TypeError, ValueError):
                    quantity = 0.0
                    timestamp = pd.NaT
                if not order_id or not symbol or action not in ("buy", "sell") or quantity <= 0 or pd.isna(timestamp):
                    stats["unlinked"] += 1
                    continue
                if order_id in alerts:
                    stats["duplicates"] += 1
                    continue
                alerts[order_id] = AlertTrade(order_id, timestamp, symbol, action, quantity)
    return list(alerts.values()), stats


def map_alerts_to_positions(alerts: list[AlertTrade], orders: pd.DataFrame) -> tuple[dict[str, AlertTrade], int]:
    """Join successful alerts to their filled entry order and broker position id."""
    required = {"id", "positionId"}
    if orders is None or orders.empty or not required.issubset(orders.columns):
        return {}, len(alerts)
    by_order_id = {
        normalize_id(row.get("id")): normalize_id(row.get("positionId"))
        for _, row in orders.iterrows()
        if normalize_id(row.get("id")) and normalize_id(row.get("positionId"))
    }
    linked = {}
    for alert in alerts:
        position_id = by_order_id.get(alert.order_id)
        if position_id:
            linked[position_id] = alert
    return linked, len(alerts) - len(linked)


def position_summary(executions: pd.DataFrame, position_id: str) -> dict[str, Any] | None:
    """Summarize one fully closed position from its executions using FIFO matching."""
    required = {"positionId", "createdDate", "side", "qty", "price"}
    if executions is None or executions.empty or not required.issubset(executions.columns):
        return None
    group = executions[executions["positionId"].map(normalize_id) == position_id].copy()
    if group.empty:
        return None
    group["time"] = pd.to_datetime(pd.to_numeric(group["createdDate"], errors="coerce"), unit="ms", utc=True, errors="coerce")
    group["qty"] = pd.to_numeric(group["qty"], errors="coerce")
    group["price"] = pd.to_numeric(group["price"], errors="coerce")
    group = group.dropna(subset=["time", "qty", "price"]).sort_values("time")
    if group.empty:
        return None

    lots: list[list[Any]] = []
    side = None
    entry_time = None
    entry_qty = 0.0
    entry_value = 0.0
    realized = 0.0
    realized_components = []
    exit_time = None
    fee_column = next((column for column in FEE_COLUMNS if column in group.columns), None)
    fees_available = fee_column is not None and group[fee_column].notna().all()
    entry_fees = 0.0
    exit_fees = 0.0
    exit_types: list[str] = []

    for row in group.itertuples(index=False):
        execution_side = str(row.side).strip().lower()
        quantity = float(row.qty)
        price = float(row.price)
        if execution_side not in ("buy", "sell") or quantity <= 0:
            continue
        if side is None:
            side = execution_side
            entry_time = row.time
        if execution_side == side:
            lots.append([quantity, price])
            entry_qty += quantity
            entry_value += quantity * price
            entry_fees += _execution_fee(row, fee_column)
            continue

        remaining = quantity
        exit_fees += _execution_fee(row, fee_column)
        while remaining > 1e-9 and lots:
            lot_qty, lot_price = lots[0]
            matched = min(remaining, lot_qty)
            component = ((price - lot_price) if side == "buy" else (lot_price - price)) * matched
            realized += component
            realized_components.append({"time": row.time, "pnl_price_units": component})
            remaining -= matched
            lot_qty -= matched
            if lot_qty <= 1e-9:
                lots.pop(0)
            else:
                lots[0][0] = lot_qty
        if not lots:
            exit_time = row.time

    if lots or side is None or entry_qty <= 0 or entry_time is None or exit_time is None:
        return None
    return {
        "position_id": position_id,
        "side": side,
        "entry_time": entry_time,
        "exit_time": exit_time,
        "entry_price": entry_value / entry_qty,
        "quantity": entry_qty,
        "gross_pnl_price_units": realized,
        "realized_components": realized_components,
        "entry_fees": entry_fees,
        "exit_fees": exit_fees,
        "fees_available": bool(fees_available),
    }


def _timestamp_column(bars: pd.DataFrame) -> str:
    for candidate in ("t", "time", "timestamp", "createdDate"):
        if candidate in bars.columns:
            return candidate
    raise ValueError("price history has no recognized timestamp column")


def normalize_bars(bars: pd.DataFrame) -> pd.DataFrame:
    if bars is None or bars.empty:
        return pd.DataFrame(columns=["time", "high", "low", "close"])
    bars = bars.copy()
    time_column = _timestamp_column(bars)
    time_values = pd.to_numeric(bars[time_column], errors="coerce")
    bars["time"] = pd.to_datetime(time_values, unit="ms", utc=True, errors="coerce")
    for target, candidates in {
        "high": ("h", "high"),
        "low": ("l", "low"),
        "close": ("c", "close"),
    }.items():
        source = next((candidate for candidate in candidates if candidate in bars.columns), None)
        bars[target] = pd.to_numeric(bars[source], errors="coerce") if source else np.nan
    return bars.dropna(subset=["time", "high", "low", "close"]).sort_values("time")


def usd_per_price_unit(symbol: str, usd_jpy_rate: float | None = None) -> float:
    """Mirror the live TP point-value conversion for supported bot symbols."""
    point_value = USD_POINT_VALUE.get(symbol)
    if point_value is None:
        raise ValueError(f"No live-equivalent point-value mapping for {symbol}")
    if symbol in JPY_SYMBOLS:
        if not usd_jpy_rate or usd_jpy_rate <= 0:
            raise ValueError("USDJPY conversion history unavailable for JPY symbol")
        return point_value / usd_jpy_rate
    if symbol in EUR_SYMBOLS:
        return point_value * EUR_USD_RATE
    return point_value


def excursion_from_bars(
    bars: pd.DataFrame,
    side: str,
    entry_price: float,
    quantity: float,
    point_value: float | list[float] | np.ndarray,
    entry_time: pd.Timestamp,
    exit_time: pd.Timestamp,
) -> dict[str, Any] | None:
    """Compute close-path, adverse, and favorable P&L from OHLC bars."""
    normalized = normalize_bars(bars)
    normalized = normalized[(normalized["time"] >= entry_time) & (normalized["time"] <= exit_time)]
    if normalized.empty:
        return None
    sign = 1.0 if side == "buy" else -1.0
    point_values = np.asarray(point_value, dtype=float)
    if point_values.ndim == 0:
        point_values = np.full(len(normalized), float(point_values))
    if len(point_values) != len(normalized):
        raise ValueError("point-value series must align with price bars")
    closes = sign * (normalized["close"].to_numpy() - entry_price) * quantity * point_values
    adverse_prices = normalized["low"].to_numpy() if side == "buy" else normalized["high"].to_numpy()
    favorable_prices = normalized["high"].to_numpy() if side == "buy" else normalized["low"].to_numpy()
    adverse = sign * (adverse_prices - entry_price) * quantity * point_values
    favorable = sign * (favorable_prices - entry_price) * quantity * point_values
    adverse_index = int(np.argmin(adverse))
    path = [{"time": time, "pnl": float(pnl)} for time, pnl in zip(normalized["time"], closes)]
    return {
        "mae": min(0.0, float(np.min(adverse))),
        "mae_time": normalized["time"].iloc[adverse_index],
        "mfe": max(0.0, float(np.max(favorable))),
        "gross_pnl_from_prices": float(closes[-1]),
        "path": path,
        "resolution_bars": len(normalized),
    }


def outcome_bucket(net_pnl: float, exit_order_type: str | None) -> str:
    order_type = (exit_order_type or "").lower()
    if "limit" in order_type and net_pnl > 0:
        return "tp_win"
    if "stop" in order_type:
        if net_pnl < -5.0:
            return "full_sl_loss"
        if net_pnl <= 5.0:
            return "breakeven_scratch"
        if net_pnl >= 50.0:
            return "profit_lock"
    if abs(net_pnl) <= 5.0:
        return "breakeven_scratch"
    if net_pnl < 0:
        return "full_sl_loss"
    return "small_win"


def recovery_rows(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    symbols = sorted({trade["symbol"] for trade in trades})
    for symbol in ["ALL", *symbols]:
        subset = trades if symbol == "ALL" else [trade for trade in trades if trade["symbol"] == symbol]
        for level in RECOVERY_LEVELS:
            reached = [trade for trade in subset if trade["mae"] <= -level]
            recovered = sum(trade["net_pnl"] >= 0 for trade in reached)
            rows.append({
                "symbol": symbol,
                "adverse_threshold": level,
                "sample_size": len(reached),
                "recovered": recovered,
                "recovery_probability": recovered / len(reached) if reached else None,
            })
    return rows


def excursion_distributions(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for label, subset in (
        ("winners", [trade for trade in trades if trade["net_pnl"] >= 0]),
        ("losers", [trade for trade in trades if trade["net_pnl"] < 0]),
    ):
        values = [trade["mae"] for trade in subset]
        rows.append({
            "group": label,
            "sample_size": len(values),
            "mae_p25": float(np.percentile(values, 25)) if values else None,
            "mae_median": float(np.percentile(values, 50)) if values else None,
            "mae_p75": float(np.percentile(values, 75)) if values else None,
            "mfe_p25": float(np.percentile([trade["mfe"] for trade in subset], 25)) if values else None,
            "mfe_median": float(np.percentile([trade["mfe"] for trade in subset], 50)) if values else None,
            "mfe_p75": float(np.percentile([trade["mfe"] for trade in subset], 75)) if values else None,
        })
    return rows


def simulate_time_stops(
    trades: list[dict[str, Any]],
    fee_per_lot: float | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Simulate scratches at the first observed bar at/after T when close P&L < P."""
    overall = []
    by_symbol = []
    symbols = sorted({trade["symbol"] for trade in trades})
    for minutes in TIME_STOP_MINUTES:
        for threshold in TIME_STOP_THRESHOLDS:
            for symbol in ["ALL", *symbols]:
                subset = trades if symbol == "ALL" else [trade for trade in trades if trade["symbol"] == symbol]
                scratched = []
                delta = 0.0
                foregone = 0.0
                saved = 0.0
                for trade in subset:
                    cutoff = trade["entry_time"] + pd.Timedelta(minutes=minutes)
                    sample = next((point for point in trade["path"] if point["time"] >= cutoff), None)
                    if sample is None or sample["time"] >= trade["exit_time"] or sample["pnl"] >= threshold:
                        continue
                    fee = (fee_per_lot * trade["quantity"]) if fee_per_lot is not None else 0.0
                    scratch_net = sample["pnl"] - trade.get("entry_fees", 0.0) - fee
                    change = scratch_net - trade["net_pnl"]
                    delta += change
                    scratched.append(trade)
                    if trade["net_pnl"] > 0:
                        foregone += max(trade["net_pnl"] - scratch_net, 0.0)
                    else:
                        saved += max(change, 0.0)
                row = {
                    "minutes": minutes,
                    "minimum_unrealized": threshold,
                    "symbol": symbol,
                    "sample_size": len(subset),
                    "scratched": len(scratched),
                    "winners_turned_scratches": sum(trade["net_pnl"] > 0 for trade in scratched),
                    "foregone_winner_pnl": foregone,
                    "losers_scratched": sum(trade["net_pnl"] < 0 for trade in scratched),
                    "losses_saved": saved,
                    "net_pnl_delta": delta,
                    "fees_estimated": fee_per_lot is not None,
                }
                (overall if symbol == "ALL" else by_symbol).append(row)
    return overall, by_symbol


def summarize_study(trades: list[dict[str, Any]]) -> dict[str, Any]:
    buckets = {bucket: 0 for bucket in OUTCOME_BUCKETS}
    buckets_by_symbol = {
        symbol: {bucket: 0 for bucket in OUTCOME_BUCKETS}
        for symbol in sorted({trade["symbol"] for trade in trades})
    }
    for trade in trades:
        buckets[trade["outcome_bucket"]] = buckets.get(trade["outcome_bucket"], 0) + 1
        symbol_buckets = buckets_by_symbol.setdefault(trade["symbol"], {})
        symbol_buckets[trade["outcome_bucket"]] = symbol_buckets.get(trade["outcome_bucket"], 0) + 1
    full_losses = [trade for trade in trades if trade["outcome_bucket"] == "full_sl_loss"]
    deep_losses = [trade for trade in full_losses if trade["mae"] <= -75.0]
    recovered_after_minus_75 = sum(
        any(point["time"] > trade["mae_time"] and point["pnl"] >= 0 for point in trade["path"])
        for trade in deep_losses
    )
    recovery = recovery_rows(trades)
    collapse = next((
        row for row in recovery
        if row["symbol"] == "ALL"
        and row["sample_size"] >= 30
        and row["recovery_probability"] is not None
        and row["recovery_probability"] <= 0.20
    ), None)
    return {
        "trade_count": len(trades),
        "bucket_counts": buckets,
        "bucket_counts_by_symbol": buckets_by_symbol,
        "mae_mfe_distribution": excursion_distributions(trades),
        "recovery": recovery,
        "recovery_collapse_level": collapse["adverse_threshold"] if collapse else None,
        "recovery_collapse_sample_size": collapse["sample_size"] if collapse else 0,
        "full_sl_losers_reaching_minus_75": len(deep_losses),
        "full_sl_loser_count": len(full_losses),
        "full_sl_losers_recovering_to_breakeven_after_minus_75": recovered_after_minus_75,
        "recovery_after_minus_75_fraction": recovered_after_minus_75 / len(deep_losses) if deep_losses else None,
    }


def study_recommendation(
    trades: list[dict[str, Any]],
    time_stop_rows: list[dict[str, Any]],
    fees_available_for_all: bool,
    minimum_stable_sample: int = 30,
) -> str:
    """Avoid recommending a rule from small samples or gross-only simulations."""
    if len(trades) < minimum_stable_sample:
        return (
            f"No change recommended: only {len(trades)} fully linked trade paths; "
            f"at least {minimum_stable_sample} are required for a stable first-pass estimate."
        )
    if not fees_available_for_all:
        return "No change recommended: commission/fee data is incomplete, so net time-stop effects are unknown."
    candidates = [row for row in time_stop_rows if row["net_pnl_delta"] > 0]
    if not candidates:
        return "No change recommended: no time-stop grid cell improved net P&L over baseline."
    best = max(candidates, key=lambda row: row["net_pnl_delta"])
    return (
        "Research candidate only (not a live-rule recommendation): scratch at "
        f"{best['minutes']} minutes when unrealized P&L is below +${best['minimum_unrealized']:.0f}; "
        f"in-sample net delta +${best['net_pnl_delta']:.2f}. Validate out-of-sample before any rule change."
    )


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


def _fetch_history(tl, instrument_id: int, start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, str]:
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    errors = []
    for resolution in ("1m", "5m", "15m"):
        try:
            history = tl.get_price_history(
                instrument_id=int(instrument_id),
                resolution=resolution,
                start_timestamp=start_ms,
                end_timestamp=end_ms,
            )
            if history is not None and not history.empty:
                return history, resolution
        except Exception as exc:
            errors.append(f"{resolution}:{type(exc).__name__}")
    raise RuntimeError("price history unavailable (" + ", ".join(errors) + ")")


def _point_values_for_bars(
    symbol: str,
    details: dict,
    tl,
    usd_jpy_history: pd.DataFrame | None,
    timestamps: pd.Series,
) -> np.ndarray:
    currency = str(details.get("quotingCurrency") or "USD").upper()
    lot_size = float(details.get("lotSize") or USD_POINT_VALUE.get(symbol, 1.0))
    if currency == "JPY":
        if usd_jpy_history is not None and not usd_jpy_history.empty:
            rates = normalize_bars(usd_jpy_history)[["time", "close"]].sort_values("time")
            aligned = pd.merge_asof(
                pd.DataFrame({"time": timestamps}).sort_values("time"),
                rates,
                on="time",
                direction="backward",
            )["close"]
            if aligned.isna().any():
                raise ValueError("USDJPY conversion history does not cover every trade bar")
            return JPY_CONTRACT_SIZE / aligned.to_numpy(dtype=float)
        else:
            usd_jpy_id = int(tl.get_instrument_id_from_symbol_name("USDJPY.R"))
            rate = float(tl.get_latest_bid_price(usd_jpy_id))
            return np.full(len(timestamps), usd_per_price_unit(symbol, rate))
    if currency == "EUR":
        return np.full(len(timestamps), USD_POINT_VALUE.get(symbol, lot_size) * EUR_USD_RATE)
    if symbol in USD_POINT_VALUE:
        return np.full(len(timestamps), USD_POINT_VALUE[symbol])
    return np.full(len(timestamps), lot_size)


def analyze_with_broker(alert_paths: list[Path], output_dir: Path) -> dict[str, Any]:
    tl = _build_client()
    alerts, load_stats = load_successful_alerts(alert_paths)
    if alerts:
        start_timestamp = int(min(alert.timestamp for alert in alerts).timestamp() * 1000)
        end_timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)
    else:
        start_timestamp = 0
        end_timestamp = 0
    orders = tl.get_all_orders(
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        history=True,
    )
    linked, unmatched_count = map_alerts_to_positions(alerts, orders)
    load_stats["successful_alerts"] = len(alerts)
    load_stats["unmatched_alerts"] = unmatched_count
    executions = tl.get_all_executions()
    instruments = tl.get_all_instruments()
    instrument_ids = dict(zip(instruments["name"].astype(str), instruments["tradableInstrumentId"].astype(int)))

    trades = []
    missing_execution_positions = 0
    missing_history_positions = 0
    resolutions: dict[str, int] = {}
    usd_jpy_id = instrument_ids.get("USDJPY.R")
    for position_id, alert in linked.items():
        order_summary = position_summary(executions, position_id)
        if order_summary is None:
            missing_execution_positions += 1
            continue
        instrument_id = instrument_ids.get(alert.symbol)
        if instrument_id is None:
            missing_history_positions += 1
            continue
        details = tl.get_instrument_details(int(instrument_id))
        try:
            bars, resolution = _fetch_history(
                tl, instrument_id, order_summary["entry_time"], order_summary["exit_time"]
            )
            resolutions[resolution] = resolutions.get(resolution, 0) + 1
            jpy_bars = None
            if str(details.get("quotingCurrency") or "").upper() == "JPY" and usd_jpy_id:
                jpy_bars, _ = _fetch_history(
                    tl, int(usd_jpy_id), order_summary["entry_time"], order_summary["exit_time"]
                )
            normalized = normalize_bars(bars)
            factors = _point_values_for_bars(
                alert.symbol, details, tl, jpy_bars, normalized["time"]
            )
            excursion = excursion_from_bars(
                bars,
                order_summary["side"],
                order_summary["entry_price"],
                order_summary["quantity"],
                factors,
                order_summary["entry_time"],
                order_summary["exit_time"],
            )
            if excursion is None:
                missing_history_positions += 1
                continue
            close_times = pd.Series([component["time"] for component in order_summary["realized_components"]])
            close_factors = _point_values_for_bars(alert.symbol, details, tl, jpy_bars, close_times)
            realized_pnl = sum(
                component["pnl_price_units"] * float(factor)
                for component, factor in zip(order_summary["realized_components"], close_factors)
            )
            fees_available = order_summary["fees_available"]
            fees = order_summary["entry_fees"] + order_summary["exit_fees"]
            net_pnl = realized_pnl - fees
            if "positionId" in orders.columns and "status" in orders.columns:
                exit_orders = orders[
                    (orders["positionId"].map(normalize_id) == position_id)
                    & (orders["status"].astype(str).str.lower() == "filled")
                ]
                sort_column = next((name for name in ("lastModified", "filledAt", "createdDate") if name in exit_orders.columns), None)
                if sort_column and not exit_orders.empty:
                    exit_orders = exit_orders.sort_values(sort_column)
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
                "fees_available": fees_available,
                "exit_order_type": exit_order_type,
                "outcome_bucket": outcome_bucket(net_pnl, exit_order_type),
                "resolution": resolution,
                "requested_quantity": alert.quantity,
            })
        except Exception as exc:
            missing_history_positions += 1
            print(f"[LOSS STUDY] {alert.symbol} position {position_id}: {exc}", flush=True)

    if trades:
        close_fee_rates = [
            trade["exit_fees"] / trade["quantity"]
            for trade in trades if trade["fees_available"] and trade["quantity"] > 0
        ]
        fee_per_lot = statistics.median(close_fee_rates) if close_fee_rates else None
    else:
        fee_per_lot = None
    study = summarize_study(trades)
    overall_time, symbol_time = simulate_time_stops(trades, fee_per_lot)
    report = {
        "input_logs": [str(path) for path in alert_paths],
        "log_stats": load_stats,
        "linked_positions": len(linked),
        "analyzed_trades": len(trades),
        "missing_execution_positions": missing_execution_positions,
        "missing_price_history_positions": missing_history_positions,
        "price_history_resolutions": resolutions,
        "fees_available_for_all_trades": bool(trades) and all(trade["fees_available"] for trade in trades),
        "scratch_exit_fee_per_lot_estimate": fee_per_lot,
        "study": study,
        "time_stop_overall": overall_time,
        "time_stop_by_symbol": symbol_time,
        "trades": trades,
    }
    report["recommendation"] = study_recommendation(
        trades,
        overall_time,
        bool(trades) and all(trade["fees_available"] for trade in trades),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "loss_study.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    pd.DataFrame(trades).drop(columns=["path"], errors="ignore").to_csv(output_dir / "loss_study_trades.csv", index=False)
    pd.DataFrame(study["recovery"]).to_csv(output_dir / "loss_study_recovery.csv", index=False)
    pd.DataFrame(overall_time).to_csv(output_dir / "loss_study_time_stop_overall.csv", index=False)
    pd.DataFrame(symbol_time).to_csv(output_dir / "loss_study_time_stop_by_symbol.csv", index=False)
    print_report(report)
    return report


def print_report(report: dict[str, Any]) -> None:
    print("LOSS STUDY — BROKER-RECONSTRUCTED BOT TRADES")
    print(f"Successful alerts: {report['log_stats'].get('successful_alerts', 0)}")
    print(f"Matched alerts to positions: {report['linked_positions']} (unmatched: {report['log_stats'].get('unmatched_alerts', 0)})")
    print(f"Analyzed closed trades with price paths: {report['analyzed_trades']}")
    print(f"Baseline net P&L: ${sum(trade['net_pnl'] for trade in report['trades']):.2f}")
    print(f"Missing execution groups: {report['missing_execution_positions']}; missing price paths: {report['missing_price_history_positions']}")
    print(f"Price-history resolutions: {report['price_history_resolutions']}")
    print(f"Execution fee data available for every trade: {report['fees_available_for_all_trades']}")
    print("Outcome buckets (sample sizes):", report["study"]["bucket_counts"])
    print("Outcome buckets by symbol:", report["study"]["bucket_counts_by_symbol"])
    print("MAE/MFE distributions:")
    print(pd.DataFrame(report["study"]["mae_mfe_distribution"]).to_string(index=False))
    print("Recovery probabilities, conditioned on crossing adverse level:")
    print(pd.DataFrame(report["study"]["recovery"]).to_string(index=False))
    print("Time-stop grid (delta vs baseline):")
    print(pd.DataFrame(report["time_stop_overall"]).to_string(index=False))
    print("Time-stop per-symbol grid:")
    print(pd.DataFrame(report["time_stop_by_symbol"]).to_string(index=False))
    fraction = report["study"]["recovery_after_minus_75_fraction"]
    print(f"Full-SL losers reaching -$75: {report['study']['full_sl_losers_reaching_minus_75']}/{report['study']['full_sl_loser_count']}")
    print(
        "Full-SL losers that later recovered to breakeven after MAE <= -$75: "
        f"{report['study']['full_sl_losers_recovering_to_breakeven_after_minus_75']}"
    )
    print(f"Recovery rate among those: {fraction if fraction is not None else 'n/a'}")
    collapse = report["study"]["recovery_collapse_level"]
    print(
        "Recovery collapse: "
        + (f"first supported level is -${collapse:.0f} (n={report['study']['recovery_collapse_sample_size']})"
           if collapse is not None else "not established (no overall threshold has n>=30 and recovery <=20%)")
    )
    print("Recommendation:", report["recommendation"])
    if not report["fees_available_for_all_trades"]:
        print("WARNING: broker history lacks complete fees; time-stop comparisons are gross/partially fee-adjusted, not exact net of fees.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    repo_root = Path(__file__).resolve().parent.parent
    parser.add_argument(
        "--alerts-log", nargs="+",
        type=Path,
        default=[repo_root / "alerts_log.jsonl", repo_root / "data" / "alerts_log.jsonl"],
    )
    parser.add_argument("--output-dir", type=Path, default=repo_root / "loss_study_output")
    args = parser.parse_args()
    analyze_with_broker(args.alerts_log, args.output_dir)


if __name__ == "__main__":
    main()
