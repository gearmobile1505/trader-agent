# Script Flowcharts

This document summarizes the runtime flow of each script in the project, using Mermaid flowcharts based on the actual logic in the code.

---

## [scripts/main.py](scripts/main.py)

```mermaid
flowchart TD
    A[Start FastAPI app] --> B[Receive POST /webhook]
    B --> C[Parse JSON payload]
    C --> D{Required fields present?}
    D -- No --> E[Return error / invalid request]
    D -- Yes --> F[Calculate position size from entry and stop]
    F --> G[Build strict Ollama risk prompt]
    G --> H[Call llama3 via ollama.chat]
    H --> I{Decision contains APPROVED?}
    I -- No --> J[Return blocked status]
    I -- Yes --> K[Resolve instrument ID]
    K --> L[Create TradeLocker market order]
    L --> M[Return success or broker error]
```

---

## [scripts/main_cfd.py](scripts/main_cfd.py)

```mermaid
flowchart TD
    A[Start FastAPI app] --> B[Read broker credentials from .env]
    B --> C[Initialize TradeLocker client]
    C --> D[Receive POST /webhook]
    D --> E[Parse action, ticker, entry, SL, trend]
    E --> F[Map TradingView symbol to TradeLocker CFD symbol]
    F --> G[Calculate risk-based lot size]
    G --> H[Build AI validation prompt]
    H --> I[Call Ollama]
    I --> J{APPROVED?}
    J -- No --> K[Return blocked]
    J -- Yes --> L[Lookup instrument by mapped symbol]
    L --> M[Create broker order]
    M --> N[Return success / broker response]

    D --> O[GET /instruments]
    O --> P[List available instruments]
    D --> Q[GET /symbols/{asset_class}]
    Q --> R[Filter instruments by asset class]
```

---

## [scripts/main_cfd_5m.py](scripts/main_cfd_5m.py)

```mermaid
flowchart TD
    A[Start FastAPI app] --> B[Load alert log config and strategy params]
    B --> C[Initialize TradeLocker client]
    C --> D[Define approved 5M symbol set]
    D --> E[Startup event begins trailing-stop monitor loop]
    E --> F[Receive POST /webhook]
    F --> G[Read raw JSON and fix malformed TradingView content]
    G --> H[Normalize action and parse entry / SL values]
    H --> I[Map ticker to TradeLocker symbol]
    I --> J{Symbol approved and session active?}
    J -- No --> K[Reject with reason and log alert]
    J -- Yes --> L{SL missing/invalid?}
    L -- Yes --> M[Auto-calculate SL from ATR * 3]
    M --> N[Round to tick size]
    L -- No --> N
    N --> O[Validate entry risk and session rules]
    O --> P[Calculate dynamic position size]
    P --> Q[Build AI risk prompt]
    Q --> R[Call Ollama]
    R --> S{APPROVED?}
    S -- No --> T[Return blocked and log alert]
    S -- Yes --> U[Lookup instrument ID]
    U --> V[Build TP / SL order params]
    V --> W[Create TradeLocker order]
    W --> X[Return order summary and log alert]

    E --> Y[Background task loops every 30s]
    Y --> Z[Fetch open positions]
    Z --> AA{Unrealized P&L >= trigger threshold?}
    AA -- Yes --> AB[Apply trailing stop modification]
    AA -- No --> AC[Continue monitoring]
```

---

## [scripts/daily_pnl.py](scripts/daily_pnl.py)

```mermaid
flowchart TD
    A[Run script or CLI command] --> B{--week flag set?}
    B -- Yes --> C[Call get_weekly_summary()]
    B -- No --> D[Call get_daily_pnl(target_date)]

    D --> E[Resolve Eastern Time target date]
    E --> F[Fetch all executions from TradeLocker]
    F --> G[Filter executions by target day]
    G --> H{Any trades found?}
    H -- No --> I[Print no trades and exit]
    H -- Yes --> J[Group executions by positionId]
    J --> K[Determine entry, exit, side, and P&L]
    K --> L[Resolve symbol name from instrument ID]
    L --> M[Build trade summary rows]
    M --> N[Calculate closed trades summary]
    N --> O[Print daily P&L report]
    O --> P[Check loss limit threshold]
    P --> Q[Save CSV file]
    Q --> R[Return DataFrame]

    C --> S[Loop through recent trading days]
    S --> T[Call get_daily_pnl() for each day]
    T --> U[Aggregate daily P&L into weekly summary]
    U --> V[Print weekly summary table]
```

---

## [scripts/phantom_scanner.py](scripts/phantom_scanner.py)

```mermaid
flowchart TD
    A[Run scanner CLI] --> B[Parse arguments: days, resolution, symbols, output]
    B --> C[Fetch all CFD instruments from TradeLocker]
    C --> D{Target symbols provided?}
    D -- Yes --> E[Filter to requested symbols]
    D -- No --> F[Use all CFD instruments]
    E --> G[Fetch historical price history]
    F --> G
    G --> H[Normalize OHLCV columns]
    H --> I[Calculate Phantom Shift signals]
    I --> J[Calculate Phantom Oscillator signals]
    J --> K[Combine strategy signals]
    K --> L[Run backtest for Shift, Oscillator, Combo strategies]
    L --> M[Collect performance metrics]
    M --> N{Any results?}
    N -- No --> O[Print no results]
    N -- Yes --> P[Rank and display strategy results]
    P --> Q{--output specified?}
    Q -- Yes --> R[Save results to CSV]
    Q -- No --> S[Exit]
```

---

## [scripts/swing_analysis.py](scripts/swing_analysis.py)

```mermaid
flowchart TD
    A[Run swing analysis] --> B[Load TradeLocker credentials]
    B --> C[Initialize TradeLocker client]
    C --> D[Loop symbols in TOP_SYMBOLS]
    D --> E[Lookup instrument ID]
    E --> F[Fetch 5-minute price history]
    F --> G{Enough data?}
    G -- No --> H[Skip symbol]
    G -- Yes --> I[Convert to DataFrame and set time index]
    I --> J[Find swing highs and lows]
    J --> K[Compute bullish and bearish move metrics]
    K --> L[Compute percentile targets (P50-P90)]
    L --> M[Store per-symbol results]
    M --> N[Print summary table]
    N --> O[Print recommended TP levels]
    O --> P[Print detailed per-symbol stats]
```

---

## [scripts/tv_symbols.py](scripts/tv_symbols.py)

```mermaid
flowchart TD
    A[Run symbol mapping tool] --> B[Load TL_TO_TV + FUTURES_TO_CFD mapping tables]
    B --> C[Group symbols by category]
    C --> D[Print indices, metals, energies, forex, crypto, stocks, futures]
    D --> E[Display usage guidance for TradingView alerts]
    E --> F[Show example JSON payloads]
    F --> G[Exit]
```

---

## [scripts/test_cfd.py](scripts/test_cfd.py)

```mermaid
flowchart TD
    A[Run CFD test script] --> B[Load .env and TradeLocker client]
    B --> C[Define symbol mapping and point values]
    C --> D[Build test cases for common futures and CFD symbols]
    D --> E[For each test case]
    E --> F[Map TradingView ticker to TradeLocker symbol]
    F --> G[Compute point value and lot size]
    G --> H[Print symbol, distance, and calculated lots]
    H --> I[Check whether mapped symbols exist in instrument list]
    I --> J[Print success or not-found results]
    J --> K[Exit]
```

---

## Overall project flow

```mermaid
flowchart TD
    A[TradingView Signal] --> B[Webhook receives alert]
    B --> C[Normalize symbol and parse fields]
    C --> D[Validate symbol + session + stop logic]
    D --> E[Risk sizing and AI approval]
    E --> F{Approved?}
    F -- No --> G[Reject and log]
    F -- Yes --> H[TradeLocker order placement]
    H --> I[Position monitoring + trailing stop]
    I --> J[Daily P&L / reporting]
    J --> K[Strategy research and backtesting]
```

This flow reflects the main trading loop in the repo: signal ingestion, validation, execution, monitoring, and analysis.
