import sys
import time
import json
from datetime import datetime
import config
import broadcaster

try:
    from SmartApi import SmartConnect
    from SmartApi.smartWebSocketV2 import SmartWebSocketV2
except ImportError:
    try:
        from smartapi import SmartConnect
        from smartapi.smartWebSocketV2 import SmartWebSocketV2
    except ImportError:
        print("[!] Error: smartapi-python not installed. Run: pip install smartapi-python pyotp websocket-client")
        sys.exit(1)

import pyotp

def generate_session():
    """Authenticates via TOTP, returns (jwt_token, feed_token), and seeds initial Nifty Spot LTP."""
    missing = [
        name for name, val in [
            ("ANGEL_API_KEY", config.API_KEY),
            ("ANGEL_CLIENT_CODE", config.CLIENT_CODE),
            ("ANGEL_PIN", config.PIN),
            ("ANGEL_TOTP_SECRET", config.TOTP_SECRET),
        ] if not val or "your_" in val
    ]
    if missing:
        raise ValueError(f"Missing or placeholder credentials in .env: {', '.join(missing)}")

    print("[*] Authenticating with Angel One SmartConnect...")
    smart = SmartConnect(api_key=config.API_KEY)
    totp_code = pyotp.TOTP(config.TOTP_SECRET).now()
    session = smart.generateSession(config.CLIENT_CODE, config.PIN, totp_code)

    if not session or not session.get("status"):
        msg = session.get("message", "Unknown error") if isinstance(session, dict) else str(session)
        raise RuntimeError(f"Angel One Login Failed: {msg}")

    jwt_token = session["data"]["jwtToken"]
    feed_token = session["data"]["feedToken"]
    print("[*] Authentication Successful.")

    # Seed initial Nifty 50 Spot LTP via REST so Spot is never 0.0 before first WS tick
    try:
        spot_res = smart.ltpData("NSE", "NIFTY", config.NIFTY_SPOT_TOKEN)
        if spot_res and spot_res.get("status") and spot_res.get("data"):
            init_ltp = float(spot_res["data"].get("ltp", 0.0))
            if init_ltp > 0:
                broadcaster.update_spot(round(init_ltp, 2), datetime.now().strftime("%H:%M:%S"))
                print(f"[*] Initial Nifty 50 Spot seeded via REST: {init_ltp:.2f}")
    except Exception as e:
        print(f"[!] Note: Initial REST Spot seed skipped ({e}). Waiting for WebSocket tick...")

    return jwt_token, feed_token

def start_stream(jwt_token: str, feed_token: str, tokens_to_subscribe: list, token_map: dict):
    """Connects SmartWebSocketV2, chunks subscriptions in batches of 50, and auto-heals on drop."""
    correlation_id = "nifty_feed_hub"
    sws_ref = {"instance": None}

    def on_data(*args, **kwargs):
        message = args[1] if len(args) > 1 else (args[0] if args else None)
        if not message:
            return
        try:
            if isinstance(message, (str, bytes)):
                message = json.loads(message)
            if not isinstance(message, dict):
                return

            token = str(message.get("token", "")).strip()
            raw_ltp = message.get("last_traded_price")
            if not token or raw_ltp is None:
                return

            ltp = round(float(raw_ltp) / 100.0, 2)  # Convert paise to Rupees
            if ltp <= 0:
                return

            if token == config.NIFTY_SPOT_TOKEN:
                broadcaster.update_spot(ltp, datetime.now().strftime("%H:%M:%S"))
            elif token in token_map:
                strike_str, opt_type = token_map[token]
                broadcaster.update_contract(strike_str, opt_type, ltp)
        except Exception:
            pass

    def on_open(*args, **kwargs):
        ws = sws_ref["instance"] or (args[0] if args else None)
        if not ws:
            return
        print("[*] SmartWebSocketV2 Connected! Subscribing in 50-token chunks...")

        # 1. Subscribe Nifty 50 Spot (NSE Cash = exchangeType 1)
        ws.subscribe(
            correlation_id=f"{correlation_id}_spot",
            mode=1,
            token_list=[{"exchangeType": 1, "tokens": [config.NIFTY_SPOT_TOKEN]}]
        )

        # 2. Subscribe NFO Option Contracts in batches of 50 (NFO = exchangeType 2)
        nfo_tokens = [t for t in tokens_to_subscribe if t != config.NIFTY_SPOT_TOKEN]
        for i in range(0, len(nfo_tokens), 50):
            chunk = nfo_tokens[i:i + 50]
            ws.subscribe(
                correlation_id=f"{correlation_id}_opt_{i}",
                mode=1,
                token_list=[{"exchangeType": 2, "tokens": chunk}]
            )
            time.sleep(0.05)

        print(f"[*] Subscribed Spot + {len(nfo_tokens)} option contracts. Live ticks streaming!")

    def on_error(*args, **kwargs):
        err = args[1] if len(args) > 1 else (args[0] if args else "Unknown WS Error")
        print(f"[!] WebSocket Warning: {err}")

    def on_close(*args, **kwargs):
        print("[!] WebSocket Connection Closed.")

    fail_count = 0
    while True:
        try:
            if fail_count >= 3:
                print("[*] Refreshing Angel One session tokens after repeated disconnects...")
                jwt_token, feed_token = generate_session()
                fail_count = 0

            sws = SmartWebSocketV2(
                auth_token=jwt_token,
                api_key=config.API_KEY,
                client_code=config.CLIENT_CODE,
                feed_token=feed_token
            )
            sws_ref["instance"] = sws
            sws.on_open = on_open
            sws.on_data = on_data
            sws.on_error = on_error
            sws.on_close = on_close
            sws.connect()

            # If connect() returns, socket closed; wait before reconnecting
            fail_count += 1
            time.sleep(3)
        except KeyboardInterrupt:
            print("\n[*] Closing WebSocket stream...")
            break
        except Exception as e:
            fail_count += 1
            print(f"[!] WebSocket reconnect error ({fail_count}): {e}. Retrying in 5s...")
            time.sleep(5)