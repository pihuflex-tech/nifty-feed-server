import os
import json
import time
from datetime import datetime
from urllib.request import urlopen, Request
import config

def load_or_download_scrips():
    """Fetches the master JSON from Angel One CDN with a 12-hour TTL disk cache."""
    if os.path.exists(config.SCRIP_CACHE_FILE):
        file_age = (time.time() - os.path.getmtime(config.SCRIP_CACHE_FILE)) / 3600.0
        if file_age < config.CACHE_EXPIRY_HOURS:
            try:
                print(f"[*] Reading cached master file ({config.SCRIP_CACHE_FILE})...")
                with open(config.SCRIP_CACHE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass

    print("[*] Downloading latest OpenAPIScripMaster.json from Angel One...")
    req = Request(config.SCRIP_MASTER_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=45) as resp:
        raw_bytes = resp.read()
        scrip_data = json.loads(raw_bytes.decode("utf-8"))

    with open(config.SCRIP_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(scrip_data, f)
    print("[*] Scrip master cached successfully.")
    return scrip_data

def filter_nearest_nifty_options():
    """
    Fast-loads today's filtered NIFTY option chain if already cached today,
    otherwise parses OpenAPIScripMaster.json and caches the 15 KB subset.
    """
    today_str = datetime.now().strftime("%Y-%m-%d")

    # Fast path: Check if we already filtered today's NIFTY contracts
    if os.path.exists(config.FILTERED_CACHE_FILE):
        try:
            with open(config.FILTERED_CACHE_FILE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("cache_date") == today_str and cached.get("contracts"):
                print(f"[*] Instant-loaded {len(cached['contracts'])} strikes for expiry {cached['expiry']} from fast cache.")
                # Convert token_map lists back to tuples
                token_map = {k: (v[0], v[1]) for k, v in cached["token_map"].items()}
                return cached["contracts"], token_map, cached["tokens_to_subscribe"]
        except Exception:
            pass

    scrip_data = load_or_download_scrips()
    today = datetime.now().date()
    nifty_options = []

    print("[*] Filtering scrips for active NIFTY options...")
    for item in scrip_data:
        if (
            item.get("exch_seg") == "NFO"
            and item.get("instrumenttype") == "OPTIDX"
            and item.get("name") == "NIFTY"
        ):
            exp_str = (item.get("expiry") or "").strip().upper()
            if not exp_str:
                continue
            try:
                exp_date = (
                    datetime.strptime(exp_str, "%d%b%Y").date()
                    if len(exp_str) == 9
                    else datetime.strptime(exp_str, "%Y-%m-%d").date()
                )
                if exp_date >= today:
                    item["_parsed_expiry"] = exp_date
                    nifty_options.append(item)
            except ValueError:
                continue

    # Immediately free the 30 MB master list from RAM
    del scrip_data

    if not nifty_options:
        raise RuntimeError("No active NIFTY options found in OpenAPIScripMaster.json.")

    nearest_expiry = min(item["_parsed_expiry"] for item in nifty_options)
    print(f"[*] Selected Nearest Active Expiry: {nearest_expiry}")

    current_series = [item for item in nifty_options if item["_parsed_expiry"] == nearest_expiry]
    del nifty_options

    temp_contracts = {}
    token_map = {}
    tokens_to_subscribe = [config.NIFTY_SPOT_TOKEN]

    for item in current_series:
        token = str(item.get("token", "")).strip()
        symbol = str(item.get("symbol", "")).strip()
        opt_type = "CE" if symbol.endswith("CE") else ("PE" if symbol.endswith("PE") else None)
        if not token or not opt_type:
            continue

        try:
            raw_strike = float(item.get("strike", 0.0))
        except (ValueError, TypeError):
            continue

        # Angel One stores NFO strikes in paise (e.g. 2450000.0 -> 24500)
        strike = int(round(raw_strike / 100.0)) if raw_strike > 100000 else int(round(raw_strike))
        if strike <= 0:
            continue

        if strike not in temp_contracts:
            temp_contracts[strike] = {
                "CE": {"token": None, "symbol": None, "ltp": None},
                "PE": {"token": None, "symbol": None, "ltp": None}
            }

        temp_contracts[strike][opt_type] = {
            "token": token,
            "symbol": symbol,
            "ltp": None
        }

        token_map[token] = (str(strike), opt_type)
        if token not in tokens_to_subscribe:
            tokens_to_subscribe.append(token)

    # Sort strikes numerically before converting keys to strings
    contracts_template = {str(k): temp_contracts[k] for k in sorted(temp_contracts.keys())}

    # Save 15 KB fast cache for instant restarts today
    try:
        with open(config.FILTERED_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "cache_date": today_str,
                "expiry": str(nearest_expiry),
                "contracts": contracts_template,
                "token_map": token_map,
                "tokens_to_subscribe": tokens_to_subscribe
            }, f)
    except Exception:
        pass

    print(f"[*] Pre-allocated {len(contracts_template)} sorted strikes ({len(tokens_to_subscribe)} tokens).")
    return contracts_template, token_map, tokens_to_subscribe