"""Tests for scripts/trend_scanner.py (GATESFX 30m/1H trend scanner).

Classifier tests run on synthetic candle series; the TradeLocker client is
stubbed for the instrument-mapping tests. No network, no credentials.
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import trend_scanner as ts


import math
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import trend_scanner as ts


def wave_candles(n=90, base=100.0, slope=0.35, amp=2.2, period=9,
                 step=1800, start_ts=1_700_000_000):
    """Candles from a sine wave on a linear drift.

    With slope > 0 both swing highs and swing lows rise leg over leg;
    slope < 0 mirrors. Wick sizes are jittered (seeded) so adjacent
    candles never tie at a turning point, as on real price data.
    """
    rng = random.Random(42)
    candles = []
    price = base
    for t in range(n):
        c = base + slope * t + amp * math.sin(2 * math.pi * t / period)
        o = price
        candles.append((start_ts + t * step, o,
                        max(o, c) + 0.3 + 0.2 * rng.random(),
                        min(o, c) - 0.3 - 0.2 * rng.random(), c))
        price = c
    return candles


def test_fractal_swings_finds_single_peak_and_trough():
    # (open, high, low, close); one peak at index 4, one trough at index 8.
    # Lows/highs are explicit so no adjacent candle ties the extremes.
    rows = [
        (10.0, 10.2, 9.9, 10.0),
        (10.0, 11.2, 9.95, 11.0),
        (11.0, 12.2, 10.9, 12.0),
        (12.0, 13.2, 11.9, 13.0),
        (13.0, 14.2, 12.9, 14.0),
        (14.0, 14.0, 12.9, 13.0),
        (13.0, 13.1, 11.9, 12.0),
        (12.0, 12.1, 10.9, 11.0),
        (11.0, 11.1, 9.8, 10.0),
        (10.0, 11.2, 10.0, 11.0),
        (11.0, 12.2, 10.9, 12.0),
        (12.0, 12.1, 10.9, 11.0),
        (11.0, 11.1, 9.9, 10.0),
    ]
    candles = [(i * 60, o, h, l, c) for i, (o, h, l, c) in enumerate(rows)]
    highs, lows = ts.fractal_swings(candles, k=3)
    assert [p for _, p, _ in highs] == [14.2]
    assert [p for _, p, _ in lows] == [9.8]


def test_atr_known_series():
    candles = [
        (0, 100.0, 101.0, 99.0, 100.0),
        (60, 100.0, 103.0, 99.0, 102.0),   # TR = max(4, 3, 1) = 4
        (120, 102.0, 104.0, 101.0, 103.0),  # TR = max(3, 2, 1) = 3
    ]
    assert ts.atr(candles) == 3.5


def test_classify_very_clear_up():
    candles = wave_candles(slope=0.35)
    highs, lows = ts.fractal_swings(candles, k=3)
    tier, direction, _ = ts.classify(candles, highs, lows)
    assert tier == "very_clear_up"
    assert direction == "up"


def test_classify_very_clear_down():
    candles = wave_candles(slope=-0.35)
    highs, lows = ts.fractal_swings(candles, k=3)
    tier, direction, _ = ts.classify(candles, highs, lows)
    assert tier == "very_clear_down"
    assert direction == "down"


def test_classify_range_is_no_clear_trend():
    # Decaying-amplitude wave, no drift: highs fall, lows rise.
    rng = random.Random(7)
    candles = []
    price = 100.0
    for t in range(90):
        c = 100.0 + 2.2 * (0.995 ** t) * math.sin(2 * math.pi * t / 9)
        o = price
        candles.append((1_700_000_000 + t * 1800, o,
                        max(o, c) + 0.3 + 0.2 * rng.random(),
                        min(o, c) - 0.3 - 0.2 * rng.random(), c))
        price = c
    highs, lows = ts.fractal_swings(candles, k=3)
    tier, direction, _ = ts.classify(candles, highs, lows)
    assert tier == "no_clear_trend"
    assert direction is None


def test_classify_trending_up_when_too_few_swings_for_tier1():
    candles = wave_candles(n=22, slope=0.5, amp=2.0)
    highs, lows = ts.fractal_swings(candles, k=3)
    assert len(highs) < 3 or len(lows) < 3  # tier 1 out of reach
    tier, direction, _ = ts.classify(candles, highs, lows)
    assert tier == "trending_up"
    assert direction == "up"


def test_build_record_shape_for_uptrend():
    rec = ts.build_record(wave_candles(slope=0.35))
    assert rec["tier"] == "very_clear_up"
    assert rec["key_level"]["side"] == "swing_low"
    assert rec["distance_pct"] > 0
    assert rec["candles_since_trend_start"] is not None
    assert len(rec["swing_highs"]) == 3
    assert len(rec["swing_lows"]) == 3


class _StubTL:
    def __init__(self, records):
        self._records = records

    def get_all_instruments(self):
        return self._records


def test_resolve_instruments_suffix_and_missing():
    records = [
        {"name": "EURUSD.R", "tradableInstrumentId": 1},
        {"name": "XAUUSD.R", "tradableInstrumentId": 2},
        {"name": "US30.R", "tradableInstrumentId": 3},
    ]
    stub = _StubTL(records)
    resolved = ts.resolve_instruments(
        stub, universe=["EURUSD", "XAUUSD.R", "US30.R", "UKOIL.R"])
    assert resolved["EURUSD"] == (1, "EURUSD.R")     # suffix added
    assert resolved["XAUUSD.R"] == (2, "XAUUSD.R")   # exact
    assert resolved["US30.R"] == (3, "US30.R")
    assert resolved["UKOIL.R"] is None               # absent -> None
