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

TARGET = "216172782135434833"
START = 0
END = 0

orders = tl.get_all_orders(start_timestamp=START, end_timestamp=END, history=True)
print("=== ORDER COUNT:", len(orders), "===")
if len(orders):
    print(orders.to_string())
    blob = orders.astype(str)
    hit = orders[blob.apply(lambda c: c.str.contains(TARGET, na=False)).any(axis=1)]
    print("\n=== MATCHES FOR TARGET POSITION:", len(hit), "===")
    if len(hit):
        print(hit.to_string())
    for col in orders.columns:
        if blob[col].str.contains("XPD", case=False, na=False).any():
            sub = orders[blob[col].str.contains("XPD", case=False, na=False)]
            print(f"\n--- XPD orders (col={col}) ---")
            print(sub.to_string())

pos = tl.get_all_positions()
print("\n=== OPEN POSITIONS:", len(pos), "===")
if len(pos):
    print(pos.to_string())
