import os
import pandas as pd
from tradelocker import TLAPI

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 300)

tl = TLAPI(
    environment=os.getenv("TL_ENV", "https://demo.tradelocker.com"),
    username=os.getenv("TL_USER"),
    password=os.getenv("TL_PASS"),
    server=os.getenv("TL_SERVER"),
)
tl.get_all_accounts()

orders = tl.get_all_orders(history=True)
print("total orders:", len(orders))

uid = tl.get_instrument_id_from_symbol_name("USDJPY.R")
print("USDJPY.R instrument id:", uid)

sub = orders[orders["tradableInstrumentId"] == uid].copy()
print("\nUSDJPY.R order count:", len(sub))
if len(sub):
    from datetime import datetime, timezone
    sub["ts"] = sub["createdDate"].apply(
        lambda v: datetime.fromtimestamp(int(v) / 1000, tz=timezone.utc)
    )
    sub = sub.sort_values("ts")
    print("\n--- USDJPY.R orders (oldest first) ---")
    print(sub[["ts", "id", "side", "type", "status", "qty", "price", "avgPrice"]].to_string(index=False))

# Compare with a symbol that does trade
for sym in ("GBPJPY.R", "XPDUSD.R", "UKOIL.R"):
    i = tl.get_instrument_id_from_symbol_name(sym)
    s = orders[orders["tradableInstrumentId"] == i]
    if len(s):
        last = s["createdDate"].max()
        from datetime import datetime, timezone
        print(f"\n{sym:12} id={i} orders={len(s):4} last={datetime.fromtimestamp(int(last)/1000, tz=timezone.utc)}")
    else:
        print(f"\n{sym:12} id={i} orders=0")
