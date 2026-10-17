"""
Angel One Instrument Master Downloader, Parser, and Fast-Cache Manager
Handles CDN download, nearest/target expiry selection, strike parsing, and atomic caching.
"""

import os
import json
import time
from datetime import datetime, date, time as dt_time
from typing import Dict, List, Optional, Tuple, Any
from urllib.request import urlopen, Request

import config


def get_market_timezone():
    """Returns ZoneInfo or static UTC+05:30 fallback for Termux environments without tzdata."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(config.MARKET_TIMEZONE)
    except Exception:
        from datetime import timezone, timedelta
        return timezone(timedelta(hours=5, minutes=30))


def get_market_now(now_dt: Optional[datetime] = None) -> datetime:
    """Returns current datetime in market timezone (Asia/Kolkata)."""
    tz = get_market_timezone()
    if now_dt is not None:
        if now_dt.tzinfo is None:
            return now_dt.replace(tzinfo=tz)
        return now_dt
    return datetime.now(tz)


def get_session_cutoff_time() -> dt_time:
    """Parses session cutoff time (HH:MM:SS) from config."""
    parts = [int(p) for p in config.SESSION_CUTOFF_TIME.split(":")]
    return dt_time(parts[0], parts[1], parts[2] if len(parts) > 2 else 0)


def save_cache_atomic(filepath: str, data: Any) -> None:
    """Writes data to a temporary file in the target directory and replaces atomically."""
    dir_name = os.path.dirname(os.path.abspath(filepath))
    temp_path = os.path.join(dir_name, f".{os.path.basename(filepath)}.tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, filepath)
    except Exception:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise


def load_or_download_scrips() -> List[Dict[str, Any]]:
    """Fetches the master JSON from Angel One CDN with a TTL disk cache."""
    if os.path.exists(config.SCRIP_CACHE_FILE):
        file_age = (time.time() - os.path.getmtime(config.SCRIP_CACHE_FILE)) / 3600.0
        if file_age < config.CACHE_EXPIRY_HOURS:
            try:
                print(f"[*] Reading cached master file ({config.SCRIP_CACHE_FILE})...")
                with open(config.SCRIP_CACHE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[!] Warning: Corrupt master cache ({e}), re-downloading...")

    print("[*] Downloading latest OpenAPIScripMaster.json from Angel One CDN...")
    try:
        req = Request(config.SCRIP_MASTER_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=45) as resp:
            raw_bytes = resp.read()
            scrip_data = json.loads(raw_bytes.decode("utf-8"))

        try:
            save_cache_atomic(config.SCRIP_CACHE_FILE, scrip_data)
            print("[*] Master instruments cached successfully.")
        except Exception as e:
            print(f"[!] Warning: Could not write master cache file ({e}).")

        return scrip_data
    except Exception as download_err:
        # Fallback to existing disk cache if download fails (spotty mobile connection/offline mode)
        if os.path.exists(config.SCRIP_CACHE_FILE):
            print(f"[!] Warning: Master CDN download failed ({download_err}). Using existing disk cache fallback.")
            try:
                with open(config.SCRIP_CACHE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        raise download_err


def parse_expiry_date(exp_str: str) -> Optional[date]:
    """Parses Angel One expiry strings (e.g. 23OCT2026 or 2026-10-23)."""
    s = (exp_str or "").strip().upper()
    if not s:
        return None
    try:
        if len(s) == 9:
            return datetime.strptime(s, "%d%b%Y").date()
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def build_expiry_contracts(series_items: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, Tuple[str, str]], List[str]]:
    """Builds contracts dictionary, token map, and subscription tokens for a series of option items."""
    temp_contracts: Dict[int, Dict[str, Any]] = {}
    token_map: Dict[str, Tuple[str, str]] = {}
    tokens_to_subscribe: List[str] = [config.NIFTY_SPOT_TOKEN]

    for item in series_items:
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
                "PE": {"token": None, "symbol": None, "ltp": None},
            }

        temp_contracts[strike][opt_type] = {
            "token": token,
            "symbol": symbol,
            "ltp": None,
        }

        token_map[token] = (str(strike), opt_type)
        if token not in tokens_to_subscribe:
            tokens_to_subscribe.append(token)

    contracts_template = {str(k): temp_contracts[k] for k in sorted(temp_contracts.keys())}
    return contracts_template, token_map, tokens_to_subscribe


def filter_instruments_data(
    scrip_data: List[Dict[str, Any]],
    now_dt: Optional[datetime] = None,
    target_expiry: Optional[date] = None,
    return_all_cache: bool = False,
):
    """
    Pure filtering logic for NIFTY options based on current market time or target expiry.
    Filters the top 3 active upcoming expiries.
    Returns (contracts_template, token_map, tokens_to_subscribe, selected_expiry, all_active_expiries).
    """
    now = get_market_now(now_dt)
    today = now.date()
    cutoff_time = get_session_cutoff_time()

    nifty_options: List[Dict[str, Any]] = []

    for item in scrip_data:
        if (
            item.get("exch_seg") == "NFO"
            and item.get("instrumenttype") == "OPTIDX"
            and item.get("name") == "NIFTY"
        ):
            exp_date = parse_expiry_date(item.get("expiry", ""))
            if not exp_date:
                continue

            # Expiry selection policy:
            # 1. Future date: active
            # 2. Today: active ONLY if market time is before session cutoff
            # 3. Past date or today after cutoff: expired
            if exp_date > today or (exp_date == today and now.time() < cutoff_time):
                item["_parsed_expiry"] = exp_date
                nifty_options.append(item)

    if not nifty_options:
        raise RuntimeError("No active NIFTY options found matching current market session.")

    # Normalize target_expiry if provided as datetime or string
    if isinstance(target_expiry, datetime):
        target_expiry = target_expiry.date()
    elif isinstance(target_expiry, str):
        target_expiry = parse_expiry_date(target_expiry)

    # Fetch top 3 active expiries
    all_active_expiries = sorted(list(set(item["_parsed_expiry"] for item in nifty_options)))[:3]

    if target_expiry and target_expiry in all_active_expiries:
        selected_expiry = target_expiry
    else:
        selected_expiry = all_active_expiries[0]

    # Pre-compute contracts and token maps for all top 3 active expiries
    expiries_cache: Dict[str, Dict[str, Any]] = {}
    for exp in all_active_expiries:
        exp_str = str(exp)
        series_items = [item for item in nifty_options if item["_parsed_expiry"] == exp]
        c_tmpl, t_map, s_tokens = build_expiry_contracts(series_items)
        expiries_cache[exp_str] = {
            "contracts": c_tmpl,
            "token_map": t_map,
            "tokens_to_subscribe": s_tokens,
        }

    selected_exp_str = str(selected_expiry)
    selected_data = expiries_cache[selected_exp_str]
    contracts_template = selected_data["contracts"]
    token_map = selected_data["token_map"]
    tokens_to_subscribe = selected_data["tokens_to_subscribe"]

    if return_all_cache:
        return contracts_template, token_map, tokens_to_subscribe, selected_expiry, all_active_expiries, expiries_cache
    return contracts_template, token_map, tokens_to_subscribe, selected_expiry, all_active_expiries


def filter_nearest_nifty_options(
    now_dt: Optional[datetime] = None,
    target_expiry: Optional[date] = None,
) -> Tuple[Dict[str, Any], Dict[str, Tuple[str, str]], List[str], str, List[str]]:
    """
    Loads filtered NIFTY option chain. Uses fast-cache when available and valid for requested expiry,
    otherwise parses master instruments and saves atomic fast-cache with the top 3 active expiries.
    Returns (contracts_template, token_map, tokens_to_subscribe, selected_expiry_str, available_expiries_str).
    """
    now = get_market_now(now_dt)
    today_str = now.strftime("%Y-%m-%d")
    cutoff_time = get_session_cutoff_time()

    # Normalize target_expiry if provided as datetime or string
    if isinstance(target_expiry, datetime):
        target_expiry = target_expiry.date()
    elif isinstance(target_expiry, str):
        target_expiry = parse_expiry_date(target_expiry)

    # Fast path: Validate cached contracts
    if os.path.exists(config.FILTERED_CACHE_FILE):
        try:
            with open(config.FILTERED_CACHE_FILE, "r", encoding="utf-8") as f:
                cached = json.load(f)

            cached_date = cached.get("cache_date")
            cached_avail = cached.get("available_expiries", [])
            cached_expiries_data = cached.get("expiries_data", {})

            # Ensure available expiries are active (not expired today after cutoff)
            valid_avail = []
            for exp_s in cached_avail:
                d = datetime.strptime(exp_s, "%Y-%m-%d").date()
                if d > now.date() or (d == now.date() and now.time() < cutoff_time):
                    valid_avail.append(exp_s)

            if cached_date == today_str and len(valid_avail) > 0:
                target_str = str(target_expiry) if target_expiry else valid_avail[0]

                if target_str in cached_expiries_data:
                    exp_info = cached_expiries_data[target_str]
                    contracts = exp_info["contracts"]
                    token_map = {k: (v[0], v[1]) for k, v in exp_info["token_map"].items()}
                    subs = exp_info["tokens_to_subscribe"]
                    print(
                        f"[*] Instant-loaded {len(contracts)} strikes for expiry "
                        f"{target_str} from 3-expiry fast-cache."
                    )
                    return contracts, token_map, subs, target_str, valid_avail[:3]

                # If requested target is explicitly outside available active expiries for today, reject early
                if target_expiry and target_str not in valid_avail and len(valid_avail) >= 3:
                    raise ValueError(f"Requested expiry {target_str} is not in available active expiries: {valid_avail[:3]}")

                # Fallback for single cached expiry
                cached_exp_str = cached.get("expiry")
                if (target_str == cached_exp_str) and cached.get("contracts") and cached.get("token_map"):
                    token_map = {k: (v[0], v[1]) for k, v in cached["token_map"].items()}
                    return cached["contracts"], token_map, cached.get("tokens_to_subscribe", []), cached_exp_str, valid_avail[:3]
        except Exception as e:
            if isinstance(e, ValueError) and "not in available active expiries" in str(e):
                raise
            print(f"[!] Fast-cache validation note: ({e}), falling back to master scrip parse...")

    # Slow path: parse master instruments
    scrip_data = load_or_download_scrips()
    print("[*] Filtering master instruments for top 3 active NIFTY expiries...")
    contracts_template, token_map, tokens_to_subscribe, selected_expiry, all_active_expiries, expiries_cache = filter_instruments_data(
        scrip_data, now_dt=now, target_expiry=target_expiry, return_all_cache=True
    )
    del scrip_data  # Free 39 MB list from RAM immediately

    selected_exp_str = str(selected_expiry)
    avail_exp_strings = [str(d) for d in all_active_expiries]
    print(f"[*] Active Expiry: {selected_exp_str} (Available 3 Expiries: {', '.join(avail_exp_strings)})")

    # Write atomic fast-cache containing all 3 active expiries
    try:
        cache_payload = {
            "cache_date": today_str,
            "expiry": selected_exp_str,
            "available_expiries": avail_exp_strings,
            "contracts": contracts_template,
            "token_map": token_map,
            "tokens_to_subscribe": tokens_to_subscribe,
            "expiries_data": expiries_cache,
        }
        save_cache_atomic(config.FILTERED_CACHE_FILE, cache_payload)
        print(f"[*] Fast-cache saved atomically with 3 expiries ({len(contracts_template)} strikes each).")
    except Exception as e:
        print(f"[!] Warning: Could not write fast-cache ({e}).")

    return contracts_template, token_map, tokens_to_subscribe, selected_exp_str, avail_exp_strings