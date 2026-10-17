"""
Angel One SmartAPI WebSocket 2.0 Ingestion and Reconnection Engine
Handles TOTP session generation, chunked subscriptions, binary tick processing,
dynamic expiry resubscriptions, and resilient exponential backoff reconnects.
"""

import sys
import time
import json
import random
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

# Global reference for dynamic subscription management across expiry switches
ACTIVE_SWS_STATE: Dict[str, Any] = {
    "instance": None,
    "tokens": [],
    "token_map": {},
}


def calculate_backoff_delay(
    attempt: int,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    multiplier: float = 2.0,
    jitter: float = 0.5,
) -> float:
    """Calculates bounded exponential backoff delay with optional random jitter."""
    delay = min(base_delay * (multiplier ** (attempt - 1)), max_delay)
    if jitter > 0:
        delay += random.uniform(0, jitter)
    return round(delay, 2)


def process_tick_message(message: Any, token_map: Dict[str, Tuple[str, str]]) -> bool:
    """
    Parses and routes a tick message (or list of tick messages) from SmartWebSocketV2 to broadcaster.
    Converts integer paise to float Rupees. Returns True if at least one tick was processed.
    """
    if not message:
        return False

    # Handle batch ticks if received as a list
    if isinstance(message, list):
        processed_any = False
        for m in message:
            if process_tick_message(m, token_map):
                processed_any = True
        return processed_any

    try:
        if isinstance(message, (str, bytes)):
            message = json.loads(message)
        if not isinstance(message, dict):
            return False

        token = str(message.get("token", "")).strip()
        raw_ltp = message.get("last_traded_price")
        if not token or raw_ltp is None:
            return False

        try:
            raw_ltp_float = float(raw_ltp)
        except (ValueError, TypeError):
            return False

        # Reject negative or zero prices
        if raw_ltp_float <= 0:
            return False

        ltp = round(raw_ltp_float / 100.0, 2)  # Convert paise to Rupees

        if token == config.NIFTY_SPOT_TOKEN:
            broadcaster.update_spot(ltp, scrip_master.get_market_now().strftime("%H:%M:%S"))
            return True
        elif token in token_map:
            strike_str, opt_type = token_map[token]
            broadcaster.update_contract(strike_str, opt_type, ltp)
            return True

        return False
    except Exception:
        return False


def update_subscriptions(
    new_tokens: List[str],
    new_token_map: Dict[str, Tuple[str, str]],
    chunk_size: int = 50,
    chunk_interval: float = 0.05,
) -> None:
    """Dynamically resubscribes to a new set of option contracts when switching expiries."""
    ws = ACTIVE_SWS_STATE.get("instance")
    old_tokens = ACTIVE_SWS_STATE.get("tokens", [])

    # Update active token mapping in-place so incoming ticks route to new strikes
    ACTIVE_SWS_STATE["token_map"] = dict(new_token_map)
    ACTIVE_SWS_STATE["tokens"] = list(new_tokens)

    if ws and hasattr(ws, "unsubscribe") and hasattr(ws, "subscribe"):
        try:
            old_nfo = [t for t in old_tokens if t != config.NIFTY_SPOT_TOKEN]
            for i in range(0, len(old_nfo), chunk_size):
                chunk = old_nfo[i:i + chunk_size]
                ws.unsubscribe(
                    correlation_id=f"unsub_opt_{i}",
                    mode=1,
                    token_list=[{"exchangeType": 2, "tokens": chunk}],
                )
                time.sleep(chunk_interval)

            new_nfo = [t for t in new_tokens if t != config.NIFTY_SPOT_TOKEN]
            for i in range(0, len(new_nfo), chunk_size):
                chunk = new_nfo[i:i + chunk_size]
                ws.subscribe(
                    correlation_id=f"sub_opt_{i}",
                    mode=1,
                    token_list=[{"exchangeType": 2, "tokens": chunk}],
                )
                time.sleep(chunk_interval)

            print(f"[*] Resubscribed to {len(new_nfo)} options for new expiry.")
        except Exception as e:
            print(f"[!] Note on subscription switch: {e}")


def generate_session() -> Tuple[str, str]:
    """
    Authenticates with Angel One via TOTP, seeds initial Nifty Spot LTP,
    and returns (jwt_token, feed_token).
    """
    # Enforce safe logging before initializing broker SDK
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

    # Seed initial Spot LTP via REST so Spot is populated before WebSocket ticks arrive
    try:
        spot_res = smart.ltpData("NSE", "NIFTY", config.NIFTY_SPOT_TOKEN)
        if spot_res and spot_res.get("status") and spot_res.get("data"):
            init_ltp = float(spot_res["data"].get("ltp", 0.0))
            if init_ltp > 0:
                broadcaster.update_spot(round(init_ltp, 2), scrip_master.get_market_now().strftime("%H:%M:%S"))
                print(f"[*] Initial Nifty 50 Spot seeded via REST: {init_ltp:.2f}")
    except Exception as e:
        print(f"[!] Note: Initial REST Spot seed skipped ({e}). Waiting for WebSocket tick...")

    return jwt_token, feed_token


def start_stream(
    jwt_token: str,
    feed_token: str,
    tokens_to_subscribe: List[str],
    token_map: Dict[str, Tuple[str, str]],
    chunk_size: int = 50,
    chunk_interval: float = 0.05,
) -> None:
    """
    Connects to SmartWebSocketV2, chunks subscriptions, and maintains
    a resilient bounded exponential backoff reconnection loop.
    """
    correlation_id = "nifty_feed_hub"
    ACTIVE_SWS_STATE["tokens"] = list(tokens_to_subscribe)
    ACTIVE_SWS_STATE["token_map"] = dict(token_map)

    def on_data(*args: Any, **kwargs: Any) -> None:
        message = args[1] if len(args) > 1 else (args[0] if args else None)
        process_tick_message(message, ACTIVE_SWS_STATE["token_map"])

    def on_open(*args: Any, **kwargs: Any) -> None:
        ws = ACTIVE_SWS_STATE["instance"] or (args[0] if args else None)
        if not ws:
            return
        print(f"[*] SmartWebSocketV2 Connected! Subscribing in {chunk_size}-token chunks...")
        broadcaster.set_feed_status("CONNECTED")

        # 1. Subscribe Nifty 50 Spot (NSE Cash = Exchange Type 1)
        ws.subscribe(
            correlation_id=f"{correlation_id}_spot",
            mode=1,
            token_list=[{"exchangeType": 1, "tokens": [config.NIFTY_SPOT_TOKEN]}],
        )

        # 2. Subscribe NFO Option Contracts in batches (NFO = Exchange Type 2)
        nfo_tokens = [t for t in ACTIVE_SWS_STATE["tokens"] if t != config.NIFTY_SPOT_TOKEN]
        for i in range(0, len(nfo_tokens), chunk_size):
            chunk = nfo_tokens[i:i + chunk_size]
            ws.subscribe(
                correlation_id=f"{correlation_id}_opt_{i}",
                mode=1,
                token_list=[{"exchangeType": 2, "tokens": chunk}],
            )
            time.sleep(chunk_interval)

        print(f"[*] Subscribed Spot + {len(nfo_tokens)} option contracts. Live ticks streaming!")

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
            # Refresh session tokens after 3 consecutive failures
            if session_failures >= 3:
                print("[*] Refreshing session tokens after repeated failures...")
                jwt_token, feed_token = generate_session()
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

            # If connection was maintained for over 30 seconds, reset backoff attempt counter
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