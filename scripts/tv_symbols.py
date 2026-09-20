#!/usr/bin/env python3
"""
Mapping between TradeLocker CFD symbols and TradingView tickers.
Run this to see what symbols to use in TradingView alerts.
"""

# TradeLocker -> TradingView symbol mapping
TL_TO_TV = {
    # ===== INDICES (Cash CFDs) =====
    "US30.R":     "US30",           # Dow Jones Industrial Average (Wall Street 30)
    "SPX500.R":   "US500",          # S&P 500 (US SPX 500)
    "NAS100.R":   "US100",          # Nasdaq 100 (US Tech 100)
    "DE30.R":     "DE30",           # Germany 30 (DAX)
    "UK100.R":    "UK100",          # UK 100 (FTSE 100)
    "F40.R":      "FR40",           # France 40 (CAC 40)
    "ES35.R":     "ES35",           # Spain 35 (IBEX 35)
    "AUS200.R":   "AUS200",         # Australia 200 (ASX 200)
    "JP225.R":    "JP225",          # Japan 225 (Nikkei 225)
    
    # Alternative TradingView symbols for indices:
    # US30.R    -> "DJI", "YM1!" (futures), "US30"
    # SPX500.R  -> "SPX", "ES1!" (futures), "US500"
    # NAS100.R  -> "NDX", "NQ1!" (futures), "US100"
    # DE30.R    -> "DAX", "DE30"
    # UK100.R   -> "UK100", "FTSE"
    # JP225.R   -> "NIKKEI", "JP225"

    # ===== METALS =====
    "XAUUSD.R":   "XAUUSD",         # Gold vs USD (Spot Gold)
    "XAGUSD.R":   "XAGUSD",         # Silver vs USD (Spot Silver)
    "XPTUSD.R":   "XPTUSD",         # Platinum vs USD
    "XPDUSD.R":   "XPDUSD",         # Palladium vs USD
    "XAUEUR.R":   "XAUEUR",         # Gold vs EUR
    "XAGEUR.R":   "XAGEUR",         # Silver vs EUR

    # ===== ENERGIES =====
    "USOIL.R":    "USOIL",          # WTI Crude Oil (US Oil)
    "UKOIL.R":    "UKOIL",          # Brent Crude Oil (UK Oil)
    "NGAS.R":     "NGAS",           # Natural Gas (Henry Hub)

    # Alternative TradingView symbols:
    # USOIL.R   -> "CL1!" (WTI futures), "USOIL", "XTIUSD"
    # UKOIL.R   -> "BRN1!" (Brent futures), "UKOIL", "XBRUSD"
    # NGAS.R    -> "NG1!" (NatGas futures), "NGAS"

    # ===== FOREX MAJORS =====
    "EURUSD.R":   "EURUSD",         # Euro / US Dollar
    "GBPUSD.R":   "GBPUSD",         # British Pound / US Dollar
    "USDJPY.R":   "USDJPY",         # US Dollar / Japanese Yen
    "USDCHF.R":   "USDCHF",         # US Dollar / Swiss Franc
    "AUDUSD.R":   "AUDUSD",         # Australian Dollar / US Dollar
    "USDCAD.R":   "USDCAD",         # US Dollar / Canadian Dollar
    "NZDUSD.R":   "NZDUSD",         # New Zealand Dollar / US Dollar

    # ===== FOREX MINORS =====
    "EURGBP.R":   "EURGBP",         # Euro / British Pound
    "EURJPY.R":   "EURJPY",         # Euro / Japanese Yen
    "GBPJPY.R":   "GBPJPY",         # British Pound / Japanese Yen
    "EURCHF.R":   "EURCHF",         # Euro / Swiss Franc
    "GBPCHF.R":   "GBPCHF",         # British Pound / Swiss Franc
    "AUDJPY.R":   "AUDJPY",         # Australian Dollar / Japanese Yen
    "CADJPY.R":   "CADJPY",         # Canadian Dollar / Japanese Yen
    "CHFJPY.R":   "CHFJPY",         # Swiss Franc / Japanese Yen
    "EURAUD.R":   "EURAUD",         # Euro / Australian Dollar
    "EURCAD.R":   "EURCAD",         # Euro / Canadian Dollar
    "GBPAUD.R":   "GBPAUD",         # British Pound / Australian Dollar
    "GBPCAD.R":   "GBPCAD",         # British Pound / Canadian Dollar
    "NZDJPY.R":   "NZDJPY",         # New Zealand Dollar / Japanese Yen
    "AUDCAD.R":   "AUDCAD",         # Australian Dollar / Canadian Dollar
    "AUDCHF.R":   "AUDCHF",         # Australian Dollar / Swiss Franc
    "AUDNZD.R":   "AUDNZD",         # Australian Dollar / NZ Dollar
    "CADCHF.R":   "CADCHF",         # Canadian Dollar / Swiss Franc
    "NZDCAD.R":   "NZDCAD",         # NZ Dollar / Canadian Dollar
    "NZDCHF.R":   "NZDCHF",         # NZ Dollar / Swiss Franc
    "EURNZD.R":   "EURNZD",         # Euro / NZ Dollar
    "GBPNZD.R":   "GBPNZD",         # British Pound / NZ Dollar

    # ===== FOREX EXOTICS (subset) =====
    "EURTRY.R":   "EURTRY",         # Euro / Turkish Lira
    "USDTRY.R":   "USDTRY",         # US Dollar / Turkish Lira
    "GBPTRY.R":   "GBPTRY",         # British Pound / Turkish Lira
    "EURMXN.R":   "EURMXN",         # Euro / Mexican Peso
    "USDMXN.R":   "USDMXN",         # US Dollar / Mexican Peso
    "USDZAR.R":   "USDZAR",         # US Dollar / South African Rand
    "EURZAR.R":   "EURZAR",         # Euro / South African Rand
    "USDCNH.R":   "USDCNH",         # US Dollar / Chinese Yuan (Offshore)
    "USDHKD.R":   "USDHKD",         # US Dollar / Hong Kong Dollar
    "USDSGD.R":   "USDSGD",         # US Dollar / Singapore Dollar
    "USDSEK.R":   "USDSEK",         # US Dollar / Swedish Krona
    "USDNOK.R":   "USDNOK",         # US Dollar / Norwegian Krone
    "USDDKK.R":   "USDDKK",         # US Dollar / Danish Krone
    "USDPLN.R":   "USDPLN",         # US Dollar / Polish Zloty
    "USDHUF.R":   "USDHUF",         # US Dollar / Hungarian Forint
    "USDCZK.R":   "USDCZK",         # US Dollar / Czech Koruna

    # ===== CRYPTO =====
    "BTCUSD":     "BTCUSD",         # Bitcoin / USD
    "ETHUSD":     "ETHUSD",         # Ethereum / USD
    "XRPUSD":     "XRPUSD",         # Ripple / USD
    "ADAUSD":     "ADAUSD",         # Cardano / USD
    "DOGEUSD":    "DOGEUSD",        # Dogecoin / USD
    "LTCUSD":     "LTCUSD",         # Litecoin / USD
    "BCHUSD":     "BCHUSD",         # Bitcoin Cash / USD
    "BTCEUR":     "BTCEUR",         # Bitcoin / EUR
    "ETHEUR":     "ETHEUR",         # Ethereum / EUR

    # Alternative TradingView crypto symbols:
    # BTCUSD -> "BTCUSDT" (Binance), "BTCUSD" (Coinbase/Bybit), "BTC"
    # ETHUSD -> "ETHUSDT", "ETHUSD", "ETH"

    # ===== US STOCKS (CFD) =====
    "APPLE":      "AAPL",           # Apple Inc
    "MICROSOFT":  "MSFT",           # Microsoft Corp
    "AMAZON":     "AMZN",           # Amazon.com
    "TESLA":      "TSLA",           # Tesla Inc
    "ALPHABET-C": "GOOG",           # Alphabet Class C (Google)
    "META":       "META",           # Meta Platforms
    "NVDA":       "NVDA",           # NVIDIA
    "NFLX":       "NFLX",           # Netflix
    "JPMORGAN":   "JPM",            # JPMorgan Chase
    "JOHNSON":    "JNJ",            # Johnson & Johnson
    "VISA":       "V",              # Visa Inc
    "MASTERCARD": "MA",             # Mastercard (may be MSTRCARD)
    "INTEL":      "INTC",           # Intel Corp
    "CISCO":      "CSCO",           # Cisco Systems
    "IBM":        "IBM",            # IBM
    "BOEING":     "BA",             # Boeing
    "EXXON":      "XOM",            # Exxon Mobil
    "CHEVRON":    "CVX",            # Chevron
    "PFIZER":     "PFE",            # Pfizer
    "COCA-COLA":  "KO",             # Coca-Cola
    "MCDONALDS":  "MCD",            # McDonald's
    "DISNEY":     "DIS",            # Disney (may not be in demo)
    "NETFLIX":    "NFLX",           # Netflix
    "ORACLE":     "ORCL",           # Oracle
    "ADOBE":      "ADBE",           # Adobe (may not be in demo)
    "SALESFORCE": "CRM",            # Salesforce (may not be in demo)

    # ===== EU STOCKS (CFD) =====
    "SIEMENS":    "SIE",            # Siemens AG (XETRA)
    "SAP":        "SAP",            # SAP SE (may not be in demo)
    "ALLIANZ":    "ALV",            # Allianz SE
    "VOLKSWAGEN": "VOW3",           # Volkswagen
    "BMW":        "BMW",            # BMW
    "DAIMLER":    "MBG",            # Mercedes-Benz Group
    "DEUTSCHE-BK": "DBK",           # Deutsche Bank
    "COMMERZBANK": "CBK",           # Commerzbank
    "BAYER":      "BAYN",           # Bayer
    "BASF":       "BAS",            # BASF (may not be in demo)
    "ADIDAS":     "ADS",            # Adidas
    "LVMH":       "MC",             # LVMH (Euronext Paris)
    "TOTAL":      "TTE",            # TotalEnergies
    "SOCIETE":    "GLE",            # Societe Generale
    "BNP":        "BNP",            # BNP Paribas
    "AIR-FRANCE": "AF",             # Air France-KLM
    "LUFTHANSA":  "LHA",            # Lufthansa
    "TELEFONICA": "TEF",            # Telefonica
    "SANTANDER":  "SAN",            # Banco Santander
}

# Futures -> CFD mapping (for users trading futures on TradingView but executing CFDs)
FUTURES_TO_CFD = {
    "MNQ1!": "NAS100.R",   # Micro E-mini Nasdaq -> Nasdaq 100 CFD
    "MES1!": "SPX500.R",   # Micro E-mini S&P 500 -> S&P 500 CFD
    "MYM1!": "US30.R",     # Micro E-mini Dow -> US 30 CFD
    "M2K1!": "US30.R",     # Micro E-mini Russell -> US 30 CFD (closest)
    "NQ1!":  "NAS100.R",   # E-mini Nasdaq -> Nasdaq 100 CFD
    "ES1!":  "SPX500.R",   # E-mini S&P 500 -> S&P 500 CFD
    "YM1!":  "US30.R",     # E-mini Dow -> US 30 CFD
    "RTY1!": "US30.R",     # E-mini Russell -> US 30 CFD
    "CL1!":  "USOIL.R",    # WTI Crude -> US Oil CFD
    "GC1!":  "XAUUSD.R",   # Gold -> Gold CFD
    "SI1!":  "XAGUSD.R",   # Silver -> Silver CFD
    "HG1!":  "XAUUSD.R",   # Copper -> Gold CFD (proxy)
    "ZB1!":  "XAUUSD.R",   # 30Y Bond -> Gold CFD (proxy)
    "ZN1!":  "XAUUSD.R",   # 10Y Note -> Gold CFD (proxy)
    "ZF1!":  "XAUUSD.R",   # 5Y Note -> Gold CFD (proxy)
    "ZT1!":  "XAUUSD.R",   # 2Y Note -> Gold CFD (proxy)
    "6E1!":  "EURUSD.R",   # Euro FX -> EURUSD CFD
    "6J1!":  "USDJPY.R",   # Japanese Yen -> USDJPY CFD
    "6B1!":  "GBPUSD.R",   # British Pound -> GBPUSD CFD
    "6A1!":  "AUDUSD.R",   # Australian Dollar -> AUDUSD CFD
    "6C1!":  "USDCAD.R",   # Canadian Dollar -> USDCAD CFD
    "6N1!":  "NZDUSD.R",   # NZ Dollar -> NZDUSD CFD
}

def print_mapping(title: str, mapping: dict, cols: int = 3):
    """Print mapping in columns."""
    print(f"\n{'='*60}")
    print(f"{title}")
    print(f"{'='*60}")
    items = list(mapping.items())
    for i in range(0, len(items), cols):
        row = []
        for j in range(cols):
            if i + j < len(items):
                tl, tv = items[i + j]
                row.append(f"{tl:<16} -> {tv}")
        print("   ".join(row))

if __name__ == "__main__":
    print("📊 TRADELOCKER CFD → TRADINGVIEW SYMBOL MAPPING")
    print("Use the RIGHT column symbols in your TradingView alerts!")
    
    print_mapping("INDICES", {k:v for k,v in TL_TO_TV.items() if k.endswith('.R') and any(x in k for x in ['US30','SPX','NAS','DE30','UK100','F40','ES35','AUS200','JP225'])})
    print_mapping("METALS", {k:v for k,v in TL_TO_TV.items() if 'XAU' in k or 'XAG' in k or 'XPT' in k or 'XPD' in k})
    print_mapping("ENERGIES", {k:v for k,v in TL_TO_TV.items() if 'OIL' in k or 'NGAS' in k})
    print_mapping("FOREX MAJORS", {k:v for k,v in TL_TO_TV.items() if k in ['EURUSD.R','GBPUSD.R','USDJPY.R','USDCHF.R','AUDUSD.R','USDCAD.R','NZDUSD.R']})
    print_mapping("FOREX MINORS", {k:v for k,v in TL_TO_TV.items() if k not in ['EURUSD.R','GBPUSD.R','USDJPY.R','USDCHF.R','AUDUSD.R','USDCAD.R','NZDUSD.R'] and 'TRY' not in k and 'MXN' not in k and 'ZAR' not in k and 'CNH' not in k and 'HKD' not in k and 'SGD' not in k and 'SEK' not in k and 'NOK' not in k and 'DKK' not in k and 'PLN' not in k and 'HUF' not in k and 'CZK' not in k})
    print_mapping("FOREX EXOTICS", {k:v for k,v in TL_TO_TV.items() if any(x in k for x in ['TRY','MXN','ZAR','CNH','HKD','SGD','SEK','NOK','DKK','PLN','HUF','CZK'])})
    print_mapping("CRYPTO", {k:v for k,v in TL_TO_TV.items() if k in ['BTCUSD','ETHUSD','XRPUSD','ADAUSD','DOGEUSD','LTCUSD','BCHUSD','BTCEUR','ETHEUR']})
    print_mapping("US STOCKS", {k:v for k,v in TL_TO_TV.items() if k in ['APPLE','MICROSOFT','AMAZON','TESLA','ALPHABET-C','META','NVDA','NFLX','JPMORGAN','JOHNSON','VISA','INTEL','CISCO','IBM','BOEING','EXXON','CHEVRON','PFIZER','COCA-COLA','MCDONALDS','ORACLE']})
    print_mapping("EU STOCKS", {k:v for k,v in TL_TO_TV.items() if k in ['SIEMENS','ALLIANZ','VOLKSWAGEN','BMW','DAIMLER','DEUTSCHE-BK','COMMERZBANK','BAYER','ADIDAS','LVMH','TOTAL','SOCIETE','BNP','AIR-FRANCE','LUFTHANSA','TELEFONICA','SANTANDER']})
    print_mapping("FUTURES → CFD (for TradingView futures charts)", FUTURES_TO_CFD)

    print("\n" + "="*60)
    print("💡 USAGE IN TRADINGVIEW:")
    print("="*60)
    print("1. For FUTURES charts (MNQ, MES, MYM, CL, GC, etc.):")
    print("   - Use the futures symbol in your chart (e.g., MNQ1!)")
    print("   - In alert JSON, send the futures symbol")
    print("   - The webhook will auto-map to CFD equivalent")
    print()
    print("2. For CFD/SPOT charts (US100, US500, XAUUSD, EURUSD, etc.):")
    print("   - Use the TradingView symbol from RIGHT column above")
    print("   - In alert JSON, send that same symbol")
    print("   - The webhook will use it directly")
    print()
    print("3. Example alert JSON for MNQ1! chart:")
    print('   {"action": "buy", "ticker": "MNQ1!", "indicator_value": 19500, "suggested_sl": 19450, "trend": "Bullish"}')
    print("   -> Auto-executes as NAS100.R on TradeLocker")
    print()
    print("4. Example alert JSON for US100 chart:")
    print('   {"action": "buy", "ticker": "US100", "indicator_value": 19500, "suggested_sl": 19450, "trend": "Bullish"}')
    print("   -> Executes as NAS100.R on TradeLocker")