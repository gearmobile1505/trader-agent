#!/usr/bin/env python3
"""
Replay script for entry vetting gates.

Replays historical alerts_log.jsonl through gates 1-5, reporting
which trades each gate would have removed and the net P&L impact.

Usage:
    python scripts/replay_gates.py [--log-file PATH] [--days N]
"""

import os
import sys
import json
import argparse
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import the gating functions (we'll replicate logic here to avoid side effects)
# For now, we implement standalone replay logic


def parse_alert_line(line: str) -> dict | None:
    """Parse a single JSONL alert log line."""
    try:
        return json.loads(line.strip())
    except json.JSONDecodeError:
        return None


def get_tech_summary_from_alert(alert_data: dict) -> str:
    """Extract tech summary from alert result or default to Neutral."""
    result = alert_data.get("result", {})
    return result.get("technical_summary", "Neutral")


def check_trend_gate(action: str, tech_summary: str) -> tuple[bool, str]:
    """Gate 1: Hard trend gate."""
    tech = (tech_summary or "").strip().lower()
    action_lower = action.lower()
    
    if action_lower == "sell" and tech in ("buy", "strong buy"):
        return False, f"Trend gate: SELL rejected — tech='{tech_summary}'"
    if action_lower == "buy" and tech in ("sell", "strong sell"):
        return False, f"Trend gate: BUY rejected — tech='{tech_summary}'"
    return True, "OK"


def check_one_per_symbol_gate(alert_data: dict, open_positions: dict) -> tuple[bool, str]:
    """Gate 3: One position per symbol (same direction)."""
    tl_symbol = alert_data.get("result", {}).get("tl_symbol")
    action = alert_data.get("alert", {}).get("action", "").lower()
    
    if not tl_symbol or not action:
        return True, "OK"
    
    # Check if we already have a same-direction position
    for sym, pos_info in open_positions.items():
        if sym == tl_symbol and pos_info["side"].lower() == action:
            return False, f"One-per-symbol: {action.upper()} already open on {tl_symbol}"
    
    return True, "OK"


def check_cooldown_gate(alert_data: dict, stop_outs: dict) -> tuple[bool, str]:
    """Gate 2: Cooldown after stop-out (simplified - we check if same symbol had recent stop)."""
    tl_symbol = alert_data.get("result", {}).get("tl_symbol")
    if not tl_symbol:
        return True, "OK"
    
    if tl_symbol in stop_outs:
        # In real replay, we'd check timestamp - simplified here
        return False, f"Cooldown: {tl_symbol} had recent stop-out"
    
    return True, "OK"


def check_daily_loss_gate(alert_data: dict, daily_pnl_so_far: float) -> tuple[bool, str]:
    """Gate 5: Daily loss limit."""
    if daily_pnl_so_far <= -400:
        return False, f"Daily loss: ${daily_pnl_so_far:.2f} <= -$400"
    return True, "OK"


def check_alert_freshness(alert_data: dict) -> tuple[bool, str]:
    """Gate 4: Alert freshness (requires bar_time in payload)."""
    alert = alert_data.get("alert", {})
    bar_time = alert.get("bar_time")
    
    if not bar_time:
        return False, "Missing bar_time field"
    
    try:
        # Parse and check age (simplified)
        alert_time = datetime.fromisoformat(bar_time.replace("Z", "+00:00"))
        # Can't accurately check age without log timestamp, skip for replay
        return True, "OK"
    except Exception:
        return False, f"Invalid bar_time format: {bar_time}"


def replay_log(log_file: Path, days: int = 14):
    """Replay alerts through gates and report results."""
    
    from datetime import timezone
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    
    print(f"=== Replay Gates Analysis (last {days} days) ===")
    print(f"Log file: {log_file}")
    print(f"Cutoff: {cutoff.isoformat()}")
    print()
    
    gates = {
        "trend_gate": {"blocked": 0, "passed": 0, "blocked_pnl": 0.0, "passed_pnl": 0.0},
        "cooldown_gate": {"blocked": 0, "passed": 0, "blocked_pnl": 0.0, "passed_pnl": 0.0},
        "one_per_symbol": {"blocked": 0, "passed": 0, "blocked_pnl": 0.0, "passed_pnl": 0.0},
        "daily_loss": {"blocked": 0, "passed": 0, "blocked_pnl": 0.0, "passed_pnl": 0.0},
        "freshness": {"blocked": 0, "passed": 0, "blocked_pnl": 0.0, "passed_pnl": 0.0},
    }
    
    total_trades = 0
    total_pnl = 0.0
    
    # Track state for replay
    open_positions = {}  # tl_symbol -> {side, entry_time, pnl}
    stop_outs = {}       # tl_symbol -> stop_out_time
    daily_pnl_by_date = {}  # date -> pnl
    
    with open(log_file, "r") as f:
        for line_num, line in enumerate(f, 1):
            entry = parse_alert_line(line)
            if not entry:
                continue
            
            timestamp_str = entry.get("timestamp", "")
            try:
                log_time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                if log_time < cutoff:
                    continue
            except Exception as e:
                print(f"  [DEBUG] Line {line_num}: timestamp parse failed: {e}", flush=True)
                continue
            
            alert = entry.get("alert", {})
            result = entry.get("result", {})
            status = result.get("status", "")
            
            # Only analyze trades that were executed (success/blocked/broker_error)
            if status not in ("success", "blocked", "broker_error", "rejected"):
                continue
            
            total_trades += 1
            
            # Extract P&L
            pnl = 0.0
            if "net_pnl" in result:
                pnl = float(result["net_pnl"])
            elif "pnl" in result:
                pnl = float(result["pnl"])
            elif "unrealizedPl" in result:
                pnl = float(result["unrealizedPl"])
            elif "broker_response" in result and "pnl" in str(result["broker_response"]).lower():
                # Try to extract from broker response
                pass
            
            total_pnl += pnl
            
            # Track daily P&L
            log_date = log_time.date()
            daily_pnl_by_date[log_date] = daily_pnl_by_date.get(log_date, 0.0) + pnl
            daily_pnl_so_far = daily_pnl_by_date[log_date]
            
            # Extract key fields
            action = alert.get("action", "").lower()
            tl_symbol = result.get("tl_symbol") or alert.get("tl_symbol")
            tech_summary = get_tech_summary_from_alert(entry)
            
            # Gate 1: Trend gate
            trend_ok, trend_reason = check_trend_gate(action, tech_summary)
            if trend_ok:
                gates["trend_gate"]["passed"] += 1
                gates["trend_gate"]["passed_pnl"] += pnl
            else:
                gates["trend_gate"]["blocked"] += 1
                gates["trend_gate"]["blocked_pnl"] += pnl
            
            # Gate 2: Cooldown (simplified - check if same symbol had recent stop)
            cooldown_ok, cooldown_reason = check_cooldown_gate(entry, stop_outs)
            if cooldown_ok:
                gates["cooldown_gate"]["passed"] += 1
                gates["cooldown_gate"]["passed_pnl"] += pnl
            else:
                gates["cooldown_gate"]["blocked"] += 1
                gates["cooldown_gate"]["blocked_pnl"] += pnl
            
            # Gate 3: One per symbol
            one_ok, one_reason = check_one_per_symbol_gate(entry, open_positions)
            if one_ok:
                gates["one_per_symbol"]["passed"] += 1
                gates["one_per_symbol"]["passed_pnl"] += pnl
            else:
                gates["one_per_symbol"]["blocked"] += 1
                gates["one_per_symbol"]["blocked_pnl"] += pnl
            
            # Gate 4: Alert freshness
            fresh_ok, fresh_reason = check_alert_freshness(entry)
            if fresh_ok:
                gates["freshness"]["passed"] += 1
                gates["freshness"]["passed_pnl"] += pnl
            else:
                gates["freshness"]["blocked"] += 1
                gates["freshness"]["blocked_pnl"] += pnl
            
            # Gate 5: Daily loss
            dl_ok, dl_reason = check_daily_loss_gate(entry, daily_pnl_so_far)
            if dl_ok:
                gates["daily_loss"]["passed"] += 1
                gates["daily_loss"]["passed_pnl"] += pnl
            else:
                gates["daily_loss"]["blocked"] += 1
                gates["daily_loss"]["blocked_pnl"] += pnl
            
            # Update state for next iteration (simplified)
            if status == "success" and tl_symbol:
                open_positions[tl_symbol] = {"side": action, "entry_time": log_time, "pnl": pnl}
            
            # Track stop-outs (negative P&L on success = likely stop)
            if status == "success" and pnl < -50:  # Heuristic: big loss = stop-out
                if tl_symbol:
                    stop_outs[tl_symbol] = log_time
    
    # Print results
    print(f"Total trades analyzed: {total_trades}")
    print(f"Total net P&L: ${total_pnl:.2f}")
    print()
    
    print("=== Gate-by-Gate Results ===")
    for gate_name, stats in gates.items():
        blocked = stats["blocked"]
        passed = stats["passed"]
        blocked_pnl = stats["blocked_pnl"]
        passed_pnl = stats["passed_pnl"]
        total_gate = blocked + passed
        
        if total_gate > 0:
            block_rate = blocked / total_gate * 100
            pnl_saved = -blocked_pnl if blocked_pnl < 0 else 0
            print(f"\n{gate_name}:")
            print(f"  Blocked: {blocked} ({block_rate:.1f}%) | Passed: {passed}")
            print(f"  Blocked P&L: ${blocked_pnl:.2f} | Passed P&L: ${passed_pnl:.2f}")
            if blocked_pnl < 0:
                print(f"  ★ P&L saved by gate: ${-blocked_pnl:.2f}")
    
    # Combined effect (approximate - gates overlap)
    print("\n=== Combined Gate Analysis ===")
    # For combined, we'd need to simulate sequentially - simplified here
    print("Note: Gates overlap; combined effect requires sequential simulation.")
    
    return gates


def main():
    parser = argparse.ArgumentParser(description="Replay alerts through vetting gates")
    parser.add_argument("--log-file", default="/opt/trader-agent/scripts/alerts_log.jsonl",
                        help="Path to alerts_log.jsonl")
    parser.add_argument("--days", type=int, default=14, help="Days of history to replay")
    args = parser.parse_args()
    
    log_file = Path(args.log_file)
    if not log_file.exists():
        print(f"Log file not found: {log_file}")
        print("Try: --log-file /path/to/alerts_log.jsonl")
        sys.exit(1)
    
    replay_log(log_file, args.days)


if __name__ == "__main__":
    main()