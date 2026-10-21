"""
websocket_feed.py
Angel One SmartAPI WebSocket 2.0 Ingestion and Reconnection Engine.
Mode 2 (Quote Mode): Streams LTP, Volume, OI, High/Low, Close, Net Change.
Handles paise-to-rupee scaling, VIX normalization, and multi-asset routing.
"""

import sys
import time
import json
import random
import struct
from datetime import datetime
from typing import Dict, List, Tuple, Any, Optional

import config
import security
import scrip_master
import broadcaster

try:
    from SmartApi import SmartConnect
    from SmartApi.smartWebSocketV2 import SmartWebSocketV2
except ImportError:
    try:
        from smartapi import SmartConnect
        from smartapi.smartWebSocketV2 import SmartWebSocketV2
    except ImportError:
        print("[!] Error: smartapi-python not installed. Run: pip install -r requirements.txt")
        sys.exit(1)

import pyotp

# Binary Mode 2 Format Specifications (Fallback for raw byte streams)
MODE_2_HEADER_FORMAT = "<BB25sqq"
MODE_2_HEADER_SIZE = struct.calcsize(MODE_2_HEADER_FORMAT)  # 43 bytes
MODE_2_QUOTE_FORMAT = "<qqqqddqqqqqqq"
MODE_2_QUOTE_SIZE = struct.calcsize(MODE_2_QUOTE_FORMAT)    # 104 bytes
TOTAL_MODE_2_SIZE = MODE_2_HEADER_SIZE + MODE_2_QUOTE_SIZE  # 147 bytes

# Global state for dynamic multi-token routing & resubscription
ACTIVE_SWS_STATE: Dict[str, Any] = {
    "instance": None,
    "tokens_by_seg": {"nse_cm": [], "nse_fo": []},
    "token_metadata": {},
}


def calculate_backoff_delay(
    attempt: int,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    multiplier: float = 2.0,
    jitter: float = 0.5,
) -> float:
    """Calculates bounded exponential backoff delay with random jitter."""
    delay = min(base_delay * (multiplier ** (attempt - 1)), max_delay)
    if jitter > 0:
        delay += random.uniform(0, jitter)
    return round(delay, 2)


def unpack_raw_mode2_binary(binary_data: bytes) -> Optional[dict]:
    """Fallback unpacker for raw 147-byte Mode 2 binary tick packets."""
    if len(binary_data) < TOTAL_MODE_2_SIZE:
        return None

    try:
        header = struct.unpack(MODE_2_HEADER_FORMAT, binary_data[:MODE_2_HEADER_SIZE])
        token = header[2].decode("utf-8").strip("\x00").strip()

        quote = struct.unpack(MODE_2_QUOTE_FORMAT, binary_data[MODE_2_HEADER_SIZE:TOTAL_MODE_2_SIZE])
        ltp = quote[0] / 100.0
        ltq = quote[1]
        atp = quote[2] / 100.0
        volume = quote[3]
        open_price = quote[6] / 100.0
        high_price = quote[7] / 100.0
        low_price = quote[8] / 100.0
        close_price = quote[9] / 100.0
        open_interest = quote[11]
        oi_change_pct = quote[12] / 100.0

        net_change = round(ltp - close_price, 2) if close_price > 0 else 0.0

        return {
            "token": token,
            "ltp": round(ltp, 2),
            "ltq": ltq,
            "atp": round(atp, 2),
            "volume": volume,
            "open": round(open_price, 2),
            "high": round(high_price, 2),
            "low": round(low_price, 2),
            "close": round(close_price, 2),
            "net_change": net_change,
            "open_interest": open_interest,
            "oi_change_percent": oi_change_pct,
        }
    except Exception:
        return None


def normalize_tick_data(raw_msg: Any) -> Optional[dict]:
    """
    Normalizes SmartWebSocketV2 Mode 2 output (dict or raw binary) into Rupee values.
    Divides paise prices by 100.0 uniformly across all equity and derivative contracts.
    """
    if isinstance(raw_msg, bytes):
        return unpack_raw_mode2_binary(raw_msg)

    if isinstance(raw_msg, str):
        try:
            raw_msg = json.loads(raw_msg)
        except Exception:
            return None

    if not isinstance(raw_msg, dict):
        return None

    token = str(raw_msg.get("token", "")).strip()
    raw_ltp = raw_msg.get("last_traded_price")
    if not token or raw_ltp is None:
        return None

    try:
        # All Mode 2 prices arrive in integer paise from exchange feed
        ltp = round(float(raw_ltp) / 100.0, 2)
        if ltp <= 0:
            return None

        raw_close = raw_msg.get("closed_price") or raw_msg.get("close", 0.0)
        close_price = round(float(raw_close) / 100.0, 2)

        raw_high = raw_msg.get("high_price_of_the_day") or raw_msg.get("high", 0.0)
        high_price = round(float(raw_high) / 100.0, 2)

        raw_low = raw_msg.get("low_price_of_the_day") or raw_msg.get("low", 0.0)
        low_price = round(float(raw_low) / 100.0, 2)

        volume = int(raw_msg.get("volume_trade_for_the_day") or raw_msg.get("volume", 0))
        open_interest = int(raw_msg.get("open_interest") or raw_msg.get("oi", 0))
        oi_change_pct = float(raw_msg.get("open_interest_change_percentage") or raw_msg.get("oi_change", 0.0))

        net_change = round(ltp - close_price, 2) if close_price > 0 else 0.0

        return {
            "token": token,
            "ltp": ltp,
            "volume": volume,
            "open_interest": open_interest,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "net_change": net_change,
            "oi_change_percent": oi_change_pct,
        }
    except (ValueError, TypeError):
        return None


def process_tick_message(message: Any, meta_map: Dict[str, dict]) -> bool:
    """Parses and routes Mode 2 ticks to broadcaster across asset categories."""
    if not message:
        return False

    if isinstance(message, list):
        processed_any = False
        for m in message:
            if process_tick_message(m, meta_map):
                processed_any = True
        return processed_any

    tick = normalize_tick_data(message)
    if not tick:
        return False

    token = tick["token"]
    if token not in meta_map:
        return False

    info = meta_map[token]
    category = info.get("category", "")
    ts_now = scrip_master.get_market_now().strftime("%H:%M:%S")

    # 1. Spot & Indices
    if category == "index":
        if info.get("name") == "NIFTY_50":
            broadcaster.update_spot(
                tick["ltp"],
                ts_now,
                high=tick["high"],
                low=tick["low"],
                net_change=tick["net_change"]
            )
        elif info.get("name") == "INDIA_VIX":
            # Auto-Scale: If VIX points arrived unscaled (>100), adjust to standard index points
            vix_ltp = tick["ltp"] / 100.0 if tick["ltp"] > 100.0 else tick["ltp"]
            broadcaster.update_vix(round(vix_ltp, 2), tick["net_change"])
        elif info.get("name") == "BANKNIFTY":
            broadcaster.update_banknifty(tick["ltp"], tick["net_change"])
        return True

    # 2. Market Breadth Heavyweights
    elif category == "heavyweight":
        broadcaster.update_heavyweight(
            info["symbol"],
            tick["ltp"],
            tick["net_change"],
            tick["volume"]
        )
        return True

    # 3. Nifty Futures
    elif category == "futures":
        broadcaster.update_futures(
            tick["ltp"],
            tick["open_interest"],
            tick["volume"],
            tick["net_change"]
        )
        return True

    # 4. Multi-Expiry Options
    elif category.startswith("options_"):
        strike_str = str(info["strike"])
        opt_type = info["option_type"]
        expiry = info.get("expiry", "")
        broadcaster.update_option_quote(
            category=category,
            expiry=expiry,
            strike=strike_str,
            opt_type=opt_type,
            ltp=tick["ltp"],
            oi=tick["open_interest"],
            volume=tick["volume"],
            high=tick["high"],
            low=tick["low"],
            net_change=tick["net_change"]
        )
        return True

    return False


def subscribe_all_tokens(ws: Any, tokens_by_seg: dict, chunk_size: int = 50, chunk_interval: float = 0.05):
    """Subscribes to all 92 curated tokens in Mode 2 (Quote Mode)."""
    # Exchange Type 1: NSE Cash (Indices + Heavyweights)
    cm_tokens = tokens_by_seg.get("nse_cm", [])
    if cm_tokens:
        for i in range(0, len(cm_tokens), chunk_size):
            chunk = cm_tokens[i:i + chunk_size]
            ws.subscribe(
                correlation_id=f"sub_cm_{i}",
                mode=2,
                token_list=[{"exchangeType": 1, "tokens": chunk}],
            )
            time.sleep(chunk_interval)

    # Exchange Type 2: NSE F&O (Futures + Options)
    fo_tokens = tokens_by_seg.get("nse_fo", [])
    if fo_tokens:
        for i in range(0, len(fo_tokens), chunk_size):
            chunk = fo_tokens[i:i + chunk_size]
            ws.subscribe(
                correlation_id=f"sub_fo_{i}",
                mode=2,
                token_list=[{"exchangeType": 2, "tokens": chunk}],
            )
            time.sleep(chunk_interval)

    print(f"[*] Subscribed {len(cm_tokens)} Cash + {len(fo_tokens)} F&O tokens in Mode 2 (Quote).")


def generate_session() -> Tuple[str, str, float]:
    """Authenticates via TOTP, seeds Spot via REST, and returns (jwt_token, feed_token, seeded_spot)."""
    security.configure_safe_logging()

    valid, missing = config.validate_credentials()
    if not valid:
        raise ValueError(f"Missing or placeholder credentials in .env: {', '.join(missing)}")

    broadcaster.set_feed_status("CONNECTING")
    print("[*] Authenticating with Angel One SmartConnect...")
    smart = SmartConnect(api_key=config.API_KEY)
    totp_code = pyotp.TOTP(config.TOTP_SECRET).now()
    session = smart.generateSession(config.CLIENT_CODE, config.PIN, totp_code)

    if not session or not session.get("status"):
        msg = session.get("message", "Unknown error") if isinstance(session, dict) else str(session)
        broadcaster.set_feed_status("DISCONNECTED", error_msg=msg)
        raise RuntimeError(f"Angel One Login Failed: {msg}")

    jwt_token = session.get("data", {}).get("jwtToken")
    feed_token = session.get("data", {}).get("feedToken")
    if not jwt_token or not feed_token:
        raise RuntimeError("Angel One session response missing jwtToken or feedToken")
    print("[*] Authentication Successful.")

    seeded_spot = 0.0
    try:
        spot_res = smart.ltpData("NSE", "NIFTY", config.NIFTY_SPOT_TOKEN)
        if spot_res and spot_res.get("status") and spot_res.get("data"):
            raw_spot = float(spot_res["data"].get("ltp", 0.0))
            if raw_spot > 0:
                seeded_spot = round(raw_spot, 2)
                broadcaster.update_spot(seeded_spot, scrip_master.get_market_now().strftime("%H:%M:%S"))
                print(f"[*] Live Nifty 50 Spot fetched via REST: ₹{seeded_spot}")
    except Exception as e:
        print(f"[!] Initial REST Spot fetch skipped ({e}).")

    return jwt_token, feed_token, seeded_spot


def start_stream(
    jwt_token: str,
    feed_token: str,
    tokens_by_seg: dict,
    token_metadata: dict,
    chunk_size: int = 50,
    chunk_interval: float = 0.05,
) -> None:
    """Connects to SmartWebSocketV2 in Mode 2 and manages resilient exponential reconnects."""
    ACTIVE_SWS_STATE["tokens_by_seg"] = dict(tokens_by_seg)
    ACTIVE_SWS_STATE["token_metadata"] = dict(token_metadata)

    def on_data(*args: Any, **kwargs: Any) -> None:
        message = args[1] if len(args) > 1 else (args[0] if args else None)
        process_tick_message(message, ACTIVE_SWS_STATE["token_metadata"])

    def on_open(*args: Any, **kwargs: Any) -> None:
        ws = ACTIVE_SWS_STATE["instance"] or (args[0] if args else None)
        if not ws:
            return
        print("[*] SmartWebSocketV2 Connected in Mode 2! Subscribing tokens...")
        broadcaster.set_feed_status("CONNECTED")
        subscribe_all_tokens(ws, ACTIVE_SWS_STATE["tokens_by_seg"], chunk_size, chunk_interval)

    def on_error(*args: Any, **kwargs: Any) -> None:
        err = args[1] if len(args) > 1 else (args[0] if args else "Unknown WS Error")
        print(f"[!] WebSocket Warning: {security.redact_string(str(err))}")
        broadcaster.set_feed_status("RECONNECTING", error_msg=str(err))

    def on_close(*args: Any, **kwargs: Any) -> None:
        print("[!] WebSocket Connection Closed.")
        broadcaster.set_feed_status("DISCONNECTED")

    attempt = 1
    session_failures = 0

    while True:
        connect_start = time.time()
        try:
            if session_failures >= 3:
                print("[*] Refreshing session tokens after repeated failures...")
                jwt_token, feed_token, _ = generate_session()
                session_failures = 0

            broadcaster.set_feed_status("CONNECTING")
            sws = SmartWebSocketV2(
                auth_token=jwt_token,
                api_key=config.API_KEY,
                client_code=config.CLIENT_CODE,
                feed_token=feed_token,
            )
            ACTIVE_SWS_STATE["instance"] = sws
            sws.on_open = on_open
            sws.on_data = on_data
            sws.on_error = on_error
            sws.on_close = on_close
            sws.connect()

            if time.time() - connect_start > 30:
                attempt = 1
                session_failures = 0
            else:
                attempt += 1
                session_failures += 1

            broadcaster.record_reconnect()
            delay = calculate_backoff_delay(attempt)
            print(f"[*] Reconnecting in {delay:.1f}s (Attempt {attempt})...")
            time.sleep(delay)

        except KeyboardInterrupt:
            print("\n[*] Shutting down WebSocket stream cleanly...")
            broadcaster.set_feed_status("DISCONNECTED")
            break
        except Exception as e:
            attempt += 1
            session_failures += 1
            broadcaster.record_reconnect()
            delay = calculate_backoff_delay(attempt)
            err_msg = security.redact_string(str(e))
            print(f"[!] WebSocket error ({attempt}): {err_msg}. Retrying in {delay:.1f}s...")
            time.sleep(delay)