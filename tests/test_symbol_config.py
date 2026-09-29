"""Config regressions that silently stopped a symbol from trading.

Both cases share a failure mode: nothing errors, the webhook is accepted, the
pipeline runs to completion, and the trade is quietly dropped. Only the
rejection reason in the alert log reveals it.
"""

import pandas as pd

from scripts import main_cfd_5m as m
from scripts.main_cfd_5m import TOP_SYMBOLS, is_session_active, map_symbol


# --- USDJPY session coverage -------------------------------------------
# This pair ran ASIA-only from the initial commit until 2026-09-22, so every
# alert arriving outside 20:00-06:00 ET was rejected as "Outside trading
# session". Order history confirms it: last filled 2026-09-18, nothing since.
def test_usdjpy_is_asia_and_eu_only():
    assert TOP_SYMBOLS["USDJPY.R"]["sessions"] == ["ASIA", "EU"]


def test_usdjpy_no_longer_claims_ny_sessions():
    sessions = TOP_SYMBOLS["USDJPY.R"]["sessions"]
    assert "NY" not in sessions
    assert "NY_EARLY" not in sessions
    assert "NY_MORNING" not in sessions


def test_every_configured_session_name_is_known():
    """A typo in a session name silently falls back to (0, 24) = always open."""
    for symbol, cfg in TOP_SYMBOLS.items():
        for session in cfg.get("sessions", []):
            assert session in m.SESSIONS_ET, (
                f"{symbol} references unknown session {session!r}"
            )


def test_usdjpy_session_windows_do_not_overlap():
    """ASIA 20-06 and EU 03-11 overlap 03:00-06:00 ET."""
    from scripts.main_cfd_5m import SESSIONS_ET

    asia = SESSIONS_ET["ASIA"]
    eu = SESSIONS_ET["EU"]
    assert asia[0] > asia[1] and eu[0] < eu[1], "ASIA is overnight, EU is not"
    # Overlap exists by design, so the pair is tradable across the handover.
    assert asia[1] > eu[0]


def test_usdjpy_active_now_under_current_config():
    """Sanity check that the configured sessions are not self-contradictory."""
    assert isinstance(is_session_active(TOP_SYMBOLS["USDJPY.R"]), bool)


# --- XPTUSD alias ------------------------------------------------------
# A platinum alert used to execute as palladium. These lock that shut.
def test_xptusd_does_not_map_to_palladium():
    assert map_symbol("XPTUSD") != "XPDUSD.R"


def test_xptusd_is_rejected_as_not_approved():
    mapped = map_symbol("XPTUSD")
    assert mapped not in TOP_SYMBOLS, (
        f"XPTUSD maps to {mapped}, which is not in TOP_SYMBOLS, so validate_entry "
        "must reject it"
    )


def test_xpdusd_still_maps_to_palladium():
    assert map_symbol("XPDUSD") == "XPDUSD.R"


def test_map_symbol_strips_pinescript_prefix():
    # Only the Pine "1!" prefix is handled. Exchange-qualified tickers such as
    # "OANDA:USDJPY" are not stripped and fall through unchanged, so they are
    # rejected downstream as not-approved rather than silently mis-mapped.
    assert map_symbol("1!USDJPY") == "USDJPY.R"
    assert map_symbol("USDJPY") == "USDJPY.R"
    assert map_symbol("OANDA:USDJPY") == "OANDA:USDJPY"


def test_no_alias_trades_a_different_metal_than_its_name():
    """The core hazard: an alert on one instrument executing another."""
    metal_aliases = {
        "XPDUSD": "XPDUSD.R",   # palladium
        "XAUUSD": "XAUUSD.R",   # gold
        "GC": "XAUUSD.R",       # gold futures
        "SI": "XAGUSD.R",       # silver futures
    }
    for tv, expected in metal_aliases.items():
        assert map_symbol(tv) == expected
    # Platinum must not land on palladium.
    assert map_symbol("XPTUSD") != "XPDUSD.R"


def test_tv_symbols_module_does_not_route_xptusd_to_palladium():
    """scripts/tv_symbols.py has its own independent map. Keep them in sync."""
    import ast
    import pathlib

    path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "tv_symbols.py"
    if not path.exists():
        return
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if (
                    isinstance(k, ast.Constant)
                    and k.value == "XPTUSD"
                    and isinstance(v, ast.Constant)
                    and v.value == "XPDUSD.R"
                ):
                    raise AssertionError(
                        "tv_symbols.py still maps XPTUSD -> XPDUSD.R"
                    )
