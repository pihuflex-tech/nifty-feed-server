"""
scrip_master.py
Fetches OpenAPI instrument master and filters precisely 92 curated tokens
for Angel One SmartAPI 2.0 Mode 2 streaming.
Locks strike generation strictly to 92 tokens.
"""

import os
import json
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any

SCRIP_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
OUTPUT_FILE = "filtered_nifty_scrips.json"

# Fixed Indices (NSE Cash, Exchange Type = 1) - 3 Tokens
INDICES_TOKENS = {
    "NIFTY_50": {"token": "99926000", "symbol": "Nifty 50", "exch_seg": "nse_cm"},
    "INDIA_VIX": {"token": "99926017", "symbol": "India VIX", "exch_seg": "nse_cm"},
    "BANKNIFTY": {"token": "99926009", "symbol": "Nifty Bank", "exch_seg": "nse_cm"}
}

# Top Heavyweights (NSE Cash) - 4 Tokens
HEAVYWEIGHT_SYMBOLS = ["RELIANCE-EQ", "HDFCBANK-EQ", "ICICIBANK-EQ", "INFY-EQ"]


def get_market_now() -> datetime:
    """Returns current IST datetime."""
    utc_now = datetime.now(timezone.utc)
    ist_tz = timezone(timedelta(hours=5, minutes=30))
    return utc_now.astimezone(ist_tz)


def fetch_scrip_master() -> list:
    """Downloads Angel One OpenAPIScripMaster JSON."""
    print("[*] Downloading Angel One OpenAPIScripMaster.json...")
    req = urllib.request.Request(
        SCRIP_MASTER_URL,
        headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    print(f"[+] Download complete. Total instruments: {len(data)}")
    return data


def parse_expiry(expiry_str: str) -> datetime:
    """Parses Angel One expiry string into datetime."""
    for fmt in ("%d%b%Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(expiry_str.strip().upper(), fmt)
        except ValueError:
            pass
    raise ValueError(f"Unrecognized date format: {expiry_str}")


def get_atm_strike(nifty_spot: float = 22800.0) -> int:
    """Rounds spot to nearest 50 strike."""
    return int(round(nifty_spot / 50.0) * 50)


def build_filtered_tokens(all_scrips: list, estimated_spot: float = 22800.0) -> dict:
    today = get_market_now().date()
    atm = get_atm_strike(estimated_spot)
    print(f"[*] Base Reference ATM Strike: {atm}")

    tokens_meta = {}
    tokens_by_seg = {"nse_cm": [], "nse_fo": []}

    # 1. Spot & Sentiment Indices (3 Tokens)
    for name, item in INDICES_TOKENS.items():
        t = str(item["token"])
        tokens_meta[t] = {
            "name": name,
            "symbol": item["symbol"],
            "exch_seg": item["exch_seg"],
            "category": "index"
        }
        tokens_by_seg[item["exch_seg"]].append(t)

    # 2. Top Heavyweights (4 Tokens)
    for scrip in all_scrips:
        if scrip.get("exch_seg") == "NSE" and scrip.get("symbol") in HEAVYWEIGHT_SYMBOLS:
            t = str(scrip["token"])
            tokens_meta[t] = {
                "name": scrip.get("name", ""),
                "symbol": scrip.get("symbol", ""),
                "exch_seg": "nse_cm",
                "category": "heavyweight"
            }
            tokens_by_seg["nse_cm"].append(t)

    # 3. Filter Nifty Derivatives
    nifty_options = []
    nifty_futures = []

    for scrip in all_scrips:
        if scrip.get("exch_seg") != "NFO" or scrip.get("name") != "NIFTY":
            continue

        instrumenttype = scrip.get("instrumenttype", "")
        if instrumenttype in ("OPTIDX", "OPTSTK"):
            try:
                exp_dt = parse_expiry(scrip["expiry"])
                if exp_dt.date() >= today:
                    nifty_options.append((exp_dt, scrip))
            except Exception:
                continue
        elif instrumenttype in ("FUTIDX", "FUTSTK"):
            try:
                exp_dt = parse_expiry(scrip["expiry"])
                if exp_dt.date() >= today:
                    nifty_futures.append((exp_dt, scrip))
            except Exception:
                continue

    # 4. Nifty Current Month Futures (1 Token)
    nifty_futures.sort(key=lambda x: x[0])
    if nifty_futures:
        fut_dt, fut_scrip = nifty_futures[0]
        t = str(fut_scrip["token"])
        tokens_meta[t] = {
            "name": "NIFTY_FUT",
            "symbol": fut_scrip["symbol"],
            "expiry": fut_dt.strftime("%d%b%Y"),
            "exch_seg": "nse_fo",
            "category": "futures"
        }
        tokens_by_seg["nse_fo"].append(t)

    # 5. Extract Expiries
    unique_expiries = sorted(list({x[0] for x in nifty_options}))
    if len(unique_expiries) < 2:
        raise RuntimeError("Insufficient active option expiries found in scrip master.")

    current_expiry = unique_expiries[0]
    next_expiry = unique_expiries[1]

    # Detect Monthly Expiry
    monthly_expiry = None
    for exp in reversed(unique_expiries):
        if exp.month == current_expiry.month:
            monthly_expiry = exp
            break
    if not monthly_expiry or monthly_expiry in (current_expiry, next_expiry):
        monthly_expiry = unique_expiries[min(len(unique_expiries) - 1, 3)]

    print(f"[+] Current Expiry: {current_expiry.strftime('%d-%b-%Y')}")
    print(f"[+] Next Expiry:    {next_expiry.strftime('%d-%b-%Y')}")
    print(f"[+] Monthly Expiry: {monthly_expiry.strftime('%d-%b-%Y')}")

    # Build Map: (date, strike_int, 'CE'/'PE') -> scrip
    opt_map = {}
    for exp_dt, scrip in nifty_options:
        try:
            raw_strike = float(scrip.get("strike", 0.0))
            strike = int(raw_strike / 100) if raw_strike > 100000 else int(raw_strike)
            opt_type = scrip.get("symbol", "")[-2:].upper()
            opt_map[(exp_dt.date(), strike, opt_type)] = scrip
        except Exception:
            continue

    def add_option_tokens(target_exp_date, strikes_list, category_name):
        added = 0
        for s in strikes_list:
            for ot in ("CE", "PE"):
                key = (target_exp_date, s, ot)
                if key in opt_map:
                    scrip = opt_map[key]
                    t = str(scrip["token"])
                    if t not in tokens_meta:
                        tokens_meta[t] = {
                            "symbol": scrip["symbol"],
                            "strike": s,
                            "option_type": ot,
                            "expiry": target_exp_date.strftime("%d%b%Y"),
                            "exch_seg": "nse_fo",
                            "category": category_name
                        }
                        tokens_by_seg["nse_fo"].append(t)
                        added += 1
                else:
                    print(f"[!] Warning: Missing strike {s} {ot} for {target_exp_date}")
        return added

    # -------------------------------------------------------------
    # 92 Token Allocation Logic
    # -------------------------------------------------------------

    # A. Current Expiry: 44 Tokens (22 Strikes x 2)
    # 1. Base ATM ± 9 Strikes: ATM(1) + 9 ऊपर + 9 नीचे = 19 strikes (38 tokens)
    curr_base_strikes = [atm]
    for i in range(1, 10):
        curr_base_strikes.append(atm + (i * 50))
        curr_base_strikes.append(atm - (i * 50))
    add_option_tokens(current_expiry.date(), sorted(curr_base_strikes), "options_current")

    # 2. Outer Margins / Wings: 3 स्ट्राइक्स (6 tokens) -> कुल 38 + 6 = 44 tokens
    # ATM-600, ATM+600, और ATM+650 (जो बेस ±450 से बाहर हैं)
    wing_strikes = [atm - 600, atm + 600, atm + 650]
    add_option_tokens(current_expiry.date(), wing_strikes, "options_wings")

    # B. Next Weekly Expiry: 20 Tokens (10 Strikes x 2)
    next_strikes = [atm + (i * 50) for i in range(-4, 6)]
    add_option_tokens(next_expiry.date(), sorted(next_strikes), "options_next")

    # C. Monthly Expiry: 20 Tokens (10 Strikes x 2)
    monthly_strikes = [atm + (i * 50) for i in range(-4, 6)]
    add_option_tokens(monthly_expiry.date(), sorted(monthly_strikes), "options_monthly")

    return {
        "generated_at": get_market_now().isoformat(),
        "total_tokens": len(tokens_meta),
        "tokens_by_seg": tokens_by_seg,
        "instruments": tokens_meta
    }