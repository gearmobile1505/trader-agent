#!/usr/bin/env python3
"""GATESFX trend scanner - 30m + 1H trend structure on TradeLocker API data.

Replaces the Yahoo-based 4H trend radar (owner order, 2026-10-22; brief:
GitHub issue #15). Same fractal swing classification method as the radar,
run per timeframe on broker-feed candles pulled through the TradeLocker
API (the `tradelocker` package, same env/auth as the rest of this repo).

ANALYSIS ONLY. Read-only API usage: instruments, quotes, candle history.
This module never places, modifies, or closes orders, and nothing in the
entry/exit/management path imports it. It is not a live entry gate.

Usage:
    python3 scripts/trend_scanner.py              # scan, write, publish
    python3 scripts/trend_scanner.py --dry-run    # scan, print JSON only
    python3 scripts/trend_scanner.py --no-publish # scan + write files only

Environment:
    TL_ENV    TradeLocker environment (default https://demo.tradelocker.com)
    TL_USER / TL_PASS / TL_SERVER   account login (same as the bot)
    TREND_SCAN_OUT_DIR    where scan JSON is written (default ./trend_scans)
    TREND_SCAN_DATA_DIR   git checkout of the `trend-scan-data` branch used
                          for publishing (optional; publishing is skipped
                          with a log line when unset)

Server schedule (Mon-Fri, every 30 min):
    */30 * * * 1-5  cd /path/to/trader-agent && python3 scripts/trend_scanner.py
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    ET = timezone(timedelta(hours=-4))  # EDT fallback

# Radar display name -> resolved against the API instrument list at runtime
# (exact name, then +/- the ".R" suffix, then unique base-symbol match).
UNIVERSE = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
    "GBPJPY.R", "EURJPY", "EURGBP", "AUDJPY",
    "XAUUSD.R", "XPDUSD.R", "XAGUSD.R",
    "US30.R", "NAS100.R", "SPX500.R", "UKOIL.R",
]

TIMEFRAMES = {
    "30m": {"resolution": "30m", "seconds": 1800, "lookback_days": 30},
    "1h": {"resolution": "1H", "seconds": 3600, "lookback_days": 60},
}

DATA_SOURCE = "TradeLocker API (GATESFX demo) - broker feed"


# ---------------------------------------------------------------------------
# Classifier (pure functions; ported from the 4H radar's scan_4h.py so the
# two implementations agree tier-for-tier on identical candle series).
# Candles are (ts_seconds, open, high, low, close) tuples, oldest first.
# ---------------------------------------------------------------------------

def fractal_swings(candles, k=3):
    """Return (swing_highs, swing_lows): lists of (index, price, ts).

    A swing high is strictly the highest high of the (2k+1)-candle window
    (k left, itself, k right); mirror for lows.
    """
    highs, lows = [], []
    n = len(candles)
    for i in range(k, n - k):
        hi = candles[i][2]
        window = [candles[j][2] for j in range(i - k, i + k + 1)]
        if hi > max(window[:k] + window[k + 1:]):
            highs.append((i, hi, candles[i][0]))
    for i in range(k, n - k):
        lo = candles[i][3]
        window = [candles[j][3] for j in range(i - k, i + k + 1)]
        if lo < min(window[:k] + window[k + 1:]):
            lows.append((i, lo, candles[i][0]))
    return highs, lows


def merged_swings(highs, lows):
    """Time-ordered list of ('H'|'L', price, ts)."""
    seq = [("H", p, t) for _, p, t in highs] + [("L", p, t) for _, p, t in lows]
    seq.sort(key=lambda x: x[2])
    return seq


def atr(candles, period=14):
    """Average true range over the candles."""
    trs = []
    for i in range(1, len(candles)):
        _, _, h, l, _ = candles[i]
        pc = candles[i - 1][4]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if not trs:
        return 0.0
    return sum(trs[-period:]) / min(period, len(trs))


def violations(prices, direction):
    """Adjacent pairs moving against the trend. Ties are neutral."""
    n = 0
    for a, b in zip(prices, prices[1:]):
        if direction == "down" and b > a:
            n += 1
        elif direction == "up" and b < a:
            n += 1
    return n


def endpoint_moves(prices, direction):
    """Net movement over up to the last 6 swings goes with the trend."""
    seq = prices[-6:]
    if len(seq) < 3:
        return False
    return seq[-1] < seq[0] if direction == "down" else seq[-1] > seq[0]


def net_trend(prices, direction, max_violations):
    """Endpoint moves with the trend and violations stay within budget."""
    seq = prices[-6:]
    if len(seq) < 3:
        return False
    if direction == "down" and not seq[-1] < seq[0]:
        return False
    if direction == "up" and not seq[-1] > seq[0]:
        return False
    return violations(seq, direction) <= max_violations


def classify(candles, highs, lows):
    """Return (tier, direction, seq_swings_for_trend_start)."""
    if len(candles) < 7:
        return "no_clear_trend", None, []
    last_close = candles[-1][4]
    hp = [p for _, p, _ in highs]
    lp = [p for _, p, _ in lows]
    h6 = highs[-6:]
    l6 = lows[-6:]
    hp6 = [p for _, p, _ in h6]
    lp6 = [p for _, p, _ in l6]
    seq = merged_swings(highs, lows)
    # trend window starts at the oldest swing of the evaluated 6-swing window
    wstart = min([t for _, _, t in h6] + [t for _, _, t in l6]) if h6 and l6 else 0
    wseq = [s for s in seq if s[2] >= wstart]

    # --- Tier 1: very clear ---
    # The defining sequence (lows) must move net with the trend over up to
    # the last 6 swings with at most one violating pair; the confirming
    # sequence (highs) needs net endpoint movement only. Ties are neutral.
    # Total swing range must exceed 1x ATR so slow drift doesn't qualify.
    # No non-overlap rule: real trends retest.
    a = atr(candles)
    move = (max(hp6) - min(lp6)) if len(hp6) >= 3 and len(lp6) >= 3 else 0.0
    if (len(hp6) >= 3 and len(lp6) >= 3 and a > 0 and move > 1.0 * a
            and net_trend(lp6, "down", 1) and endpoint_moves(hp6, "down")
            and last_close < hp[-1]):
        return "very_clear_down", "down", wseq
    if (len(hp6) >= 3 and len(lp6) >= 3 and a > 0 and move > 1.0 * a
            and net_trend(lp6, "up", 1) and endpoint_moves(hp6, "up")
            and last_close > lp[-1]):
        return "very_clear_up", "up", wseq

    # --- Tier 2: trending (looser) ---
    if (len(hp) >= 2 and len(lp) >= 2
            and hp[-1] > hp[-2] and lp[-1] > lp[-2]
            and last_close > lp[-1]):
        return "trending_up", "up", [s for s in seq
                                     if s[2] >= min(highs[-2][2], highs[-1][2],
                                                    lows[-2][2], lows[-1][2])]
    if (len(hp) >= 2 and len(lp) >= 2
            and hp[-1] < hp[-2] and lp[-1] < lp[-2]
            and last_close < hp[-1]):
        return "trending_down", "down", [s for s in seq
                                         if s[2] >= min(highs[-2][2], highs[-1][2],
                                                        lows[-2][2], lows[-1][2])]
    return "no_clear_trend", None, []


def iso_utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_record(candles):
    """Classify one candle series into the per-timeframe report record."""
    if len(candles) < 7:
        raise RuntimeError(f"only {len(candles)} candles in series")
    highs, lows = fractal_swings(candles, k=3)
    tier, direction, seq = classify(candles, highs, lows)
    last_close = candles[-1][4]

    rec = {
        "status": "ok",
        "tier": tier,
        "direction": direction,
        "last_close": round(last_close, 5),
        "n_candles": len(candles),
        "swing_highs": [
            {"price": round(p, 5), "time_utc": iso_utc(t)}
            for _, p, t in highs[-3:]
        ] or None,
        "swing_lows": [
            {"price": round(p, 5), "time_utc": iso_utc(t)}
            for _, p, t in lows[-3:]
        ] or None,
        "trend_start_utc": None,
        "candles_since_trend_start": None,
        "key_level": None,
        "distance_pct": None,
    }
    # swing lists are chronological (oldest first); last entry = most recent
    if direction in ("up", "down") and seq:
        trend_start = min(t for _, _, t in seq)
        rec["trend_start_utc"] = iso_utc(trend_start)
        rec["candles_since_trend_start"] = sum(
            1 for c in candles if c[0] >= trend_start
        )
        if direction == "up":
            lvl_price, lvl_ts = lows[-1][1], lows[-1][2]  # most recent swing low
            rec["key_level"] = {
                "side": "swing_low",
                "price": round(lvl_price, 5),
                "time_utc": iso_utc(lvl_ts),
            }
        else:
            lvl_price, lvl_ts = highs[-1][1], highs[-1][2]  # most recent swing high
            rec["key_level"] = {
                "side": "swing_high",
                "price": round(lvl_price, 5),
                "time_utc": iso_utc(lvl_ts),
            }
        rec["distance_pct"] = round((last_close - lvl_price) / lvl_price * 100, 3)
    return rec


# ---------------------------------------------------------------------------
# TradeLocker API access (read-only). TLAPI is imported lazily so the pure
# classifier above can be imported and unit-tested without the package or
# any credentials present.
# ---------------------------------------------------------------------------

def make_client():
    from tradelocker import TLAPI  # lazy: see note above

    user = os.getenv("TL_USER")
    password = os.getenv("TL_PASS")
    server = os.getenv("TL_SERVER")
    if not (user and password and server):
        raise RuntimeError(
            "TL_USER / TL_PASS / TL_SERVER must be set in the environment"
        )
    return TLAPI(
        environment=os.getenv("TL_ENV", "https://demo.tradelocker.com"),
        username=user,
        password=password,
        server=server,
    )


def _instrument_records(tl):
    """get_all_instruments() as a list of plain dicts."""
    df = tl.get_all_instruments()
    if hasattr(df, "to_dict"):
        return df.to_dict("records")
    return list(df)


def resolve_instruments(tl, universe=UNIVERSE):
    """Map each radar display name to (instrument_id, api_name) or None.

    Match order per name: exact API name; name with/without the ".R"
    suffix; then a unique API name sharing the same base symbol (the part
    before the first "."). Ambiguity or absence resolves to None and is
    reported in the scan output rather than guessed.
    """
    records = _instrument_records(tl)
    by_name = {}
    for r in records:
        name = str(r.get("name", ""))
        if name:
            by_name[name] = int(r["tradableInstrumentId"])

    resolved = {}
    for display in universe:
        candidates = [display]
        if display.endswith(".R"):
            candidates.append(display[:-2])
        else:
            candidates.append(display + ".R")
        hit = next((c for c in candidates if c in by_name), None)
        if hit is None:
            base = display.split(".")[0]
            same_base = [n for n in by_name if n.split(".")[0] == base]
            if len(same_base) == 1:
                hit = same_base[0]
        resolved[display] = (by_name[hit], hit) if hit else None
    return resolved


def fetch_candles(tl, instrument_id, tf_key, now_ts):
    """Closed candles for one timeframe as (ts, o, h, l, c), oldest first.

    The still-forming final candle is dropped, matching the radar's rule
    of classifying on closed bars only.
    """
    spec = TIMEFRAMES[tf_key]
    end_ms = int(now_ts * 1000)
    start_ms = int((now_ts - spec["lookback_days"] * 86400) * 1000)
    hist = tl.get_price_history(
        instrument_id=instrument_id,
        resolution=spec["resolution"],
        start_timestamp=start_ms,
        end_timestamp=end_ms,
    )
    if hist is None or len(hist) == 0:
        return []
    rows = hist.to_dict("records") if hasattr(hist, "to_dict") else list(hist)
    candles = []
    for r in rows:
        ts = int(r["t"]) // 1000
        if ts + spec["seconds"] > now_ts:
            continue  # still forming
        candles.append((ts, float(r["o"]), float(r["h"]),
                        float(r["l"]), float(r["c"])))
    candles.sort(key=lambda c: c[0])
    return candles


def fetch_spread(tl, instrument_id):
    """Current ask - bid, or None when quotes are unavailable."""
    try:
        ask = tl.get_latest_asking_price(instrument_id)
        bid = tl.get_latest_bid_price(instrument_id)
        if ask is None or bid is None:
            return None
        return round(float(ask) - float(bid), 5)
    except Exception:
        return None


def run_scan(tl, now_ts=None):
    """One full scan of the universe on both timeframes."""
    now_ts = now_ts or time.time()
    now_utc = datetime.fromtimestamp(now_ts, timezone.utc)
    now_et = datetime.fromtimestamp(now_ts, ET)
    mapping = resolve_instruments(tl)

    symbols = {}
    for display in UNIVERSE:
        entry = {"api_name": None, "instrument_id": None, "spread": None}
        resolved = mapping.get(display)
        if resolved is None:
            for tf_key in TIMEFRAMES:
                entry[tf_key] = {
                    "status": "data_unavailable",
                    "error": "instrument not resolved in API instrument list",
                    "tier": "data_unavailable",
                    "direction": None,
                }
            symbols[display] = entry
            continue
        instrument_id, api_name = resolved
        entry["api_name"] = api_name
        entry["instrument_id"] = instrument_id
        entry["spread"] = fetch_spread(tl, instrument_id)
        for tf_key in TIMEFRAMES:
            try:
                candles = fetch_candles(tl, instrument_id, tf_key, now_ts)
                entry[tf_key] = build_record(candles)
                entry[tf_key]["candle_depth"] = len(candles)
            except Exception as e:
                entry[tf_key] = {
                    "status": "data_unavailable",
                    "error": str(e)[:160],
                    "tier": "data_unavailable",
                    "direction": None,
                }
        symbols[display] = entry

    return {
        "scan_time_et": now_et.strftime("%Y-%m-%dT%H:%M"),
        "scan_time_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "universe_size": len(UNIVERSE),
        "data_source": DATA_SOURCE,
        "instrument_mapping": {
            name: ({"api_name": m[1], "instrument_id": m[0]} if m else None)
            for name, m in mapping.items()
        },
        "symbols": symbols,
    }


# ---------------------------------------------------------------------------
# Output + publish
# ---------------------------------------------------------------------------

def write_outputs(report, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    now_et = datetime.now(ET)
    stamp = now_et.strftime("%Y-%m-%d_%H%M_ET")
    payload = json.dumps(report, indent=2)
    ts_path = os.path.join(out_dir, f"{stamp}.json")
    with open(ts_path, "w") as f:
        f.write(payload)
    with open(os.path.join(out_dir, "latest.json"), "w") as f:
        f.write(payload)
    return ts_path


def _prune_old(data_dir, keep_days=14):
    cutoff = datetime.now(ET).date() - timedelta(days=keep_days)
    for name in os.listdir(data_dir):
        if not name.endswith("_ET.json"):
            continue
        try:
            d = datetime.strptime(name[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < cutoff:
            os.remove(os.path.join(data_dir, name))


def publish(out_dir, data_dir):
    """Copy scan files into the trend-scan-data checkout and push.

    Returns a status string; failures are reported, never raised - a
    publish problem must not lose the scan itself.
    """
    if not data_dir:
        return "skipped (TREND_SCAN_DATA_DIR not set)"
    if not os.path.isdir(os.path.join(data_dir, ".git")):
        return f"skipped ({data_dir} is not a git checkout)"
    try:
        for name in os.listdir(out_dir):
            if name.endswith(".json"):
                shutil.copy2(os.path.join(out_dir, name),
                             os.path.join(data_dir, name))
        _prune_old(data_dir)

        def git(*args):
            return subprocess.run(["git", "-C", data_dir, *args],
                                  capture_output=True, text=True, timeout=120)

        git("add", "-A")
        status = git("status", "--porcelain")
        if not status.stdout.strip():
            return "no changes to publish"
        stamp = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")
        c = git("commit", "-m", f"trend scan {stamp}")
        if c.returncode != 0:
            return f"commit failed: {c.stderr.strip()[:160]}"
        p = git("push")
        if p.returncode != 0:
            return f"push failed: {p.stderr.strip()[:160]}"
        return "published"
    except Exception as e:
        return f"failed: {str(e)[:160]}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="print the report JSON; write and publish nothing")
    ap.add_argument("--no-publish", action="store_true",
                    help="write files but skip the data-branch publish")
    ap.add_argument("--out-dir", default=os.getenv(
        "TREND_SCAN_OUT_DIR", os.path.join(os.getcwd(), "trend_scans")))
    ap.add_argument("--data-dir", default=os.getenv("TREND_SCAN_DATA_DIR"))
    args = ap.parse_args(argv)

    tl = make_client()
    report = run_scan(tl)

    if args.dry_run:
        print(json.dumps(report, indent=2))
        return 0
    path = write_outputs(report, args.out_dir)
    print(f"wrote {path}")
    if args.no_publish:
        print("publish: skipped (--no-publish)")
    else:
        print(f"publish: {publish(args.out_dir, args.data_dir)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
